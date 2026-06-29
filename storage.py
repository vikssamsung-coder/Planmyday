"""Storage layer — reads/writes Excel files in a local workspace folder.

Phase 1 of the spec is local OneDrive sync. To keep the first cut runnable
anywhere, the workspace defaults to ./workspace. Point STORAGE_BASE_DIR at your
synced OneDrive folder to use it for real — the method names stay the same, so
swapping to Graph API later won't touch app logic.

Layout created on first run:
  workspace/
    _common/users_master.xlsx
    <user_key>/monthly_targets.xlsx, monthly_plan.xlsx, tasks.xlsx, day_updates.xlsx
"""

import os
import tempfile
from datetime import datetime
import pandas as pd

import schemas
import paths

try:
    import gsheets
except Exception:
    gsheets = None

try:
    import db
except Exception:
    db = None


def _use_pg():
    """True when the Postgres (Neon) backend should handle storage: the db module is
    importable AND a connection is configured. When True it takes precedence over both
    the local-Excel and Google-Sheets backends."""
    return db is not None and db.enabled()


def _route(path):
    """Derive (spreadsheet_title, tab) from a workspace file path:
    .../_common/users_master.xlsx -> ("_common", "users_master")
    .../<user_key>/tasks.xlsx     -> ("<user_key>", "tasks")"""
    title = os.path.basename(os.path.dirname(path))
    tab = os.path.splitext(os.path.basename(path))[0]
    return title, tab


def _cloud():
    return gsheets is not None and gsheets.enabled()

try:
    from filelock import FileLock
except Exception:  # filelock optional; degrade to a no-op context manager
    class FileLock:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _common_dir():
    d = paths.common_dir()
    os.makedirs(d, exist_ok=True)
    return d


def _user_dir(user_key):
    d = paths.user_dir(user_key)
    os.makedirs(d, exist_ok=True)
    return d


# ---- short-TTL read cache (cuts Google Sheets API calls so we don't hit the 429
# 'reads per minute' quota). Keyed by (title, tab). Writes refresh the cache entry so a
# read right after a write doesn't cost an API call. TTL is short so other sessions'
# changes still appear within a few seconds.
import time as _time
_CACHE = {}          # {(title, tab): (timestamp, df_copy)}
_CACHE_TTL = 45.0    # seconds


def _cache_get(title, tab):
    hit = _CACHE.get((title, tab))
    if hit and (_time.time() - hit[0]) < _CACHE_TTL:
        return hit[1].copy()
    return None


def _cache_put(title, tab, df):
    _CACHE[(title, tab)] = (_time.time(), df.copy())


def _cache_clear():
    _CACHE.clear()


# ---- storage mode (environment-aware) --------------------------------------------------
# LOCAL machine  -> local files (instant, no API quota, persistent disk).
# Streamlit CLOUD -> Google Sheets directly, because the cloud filesystem is EPHEMERAL
#                    (it resets on every redeploy, so local files would be lost). The 45s
#                    read cache below keeps the Sheets API call volume well under quota.

def _on_cloud_host():
    """True when running on Streamlit Cloud (ephemeral filesystem) rather than a local
    machine. Streamlit Cloud runs the app from /mount/src/…"""
    cwd = os.getcwd()
    return (cwd.startswith("/mount/src") or cwd.startswith("/app")
            or os.environ.get("PMD_CLOUD") == "1")


def local_first():
    """Use local files on a personal machine; use Google Sheets on Streamlit Cloud (where
    the disk is ephemeral). Falls back to local if Sheets isn't configured."""
    if _on_cloud_host() and _cloud():
        return False
    return True


def _read(path, columns):
    # Postgres (Neon) backend — used on Cloud when NEON_DATABASE_URL is configured.
    if _use_pg():
        title, tab = _route(path)
        # serve from the short-lived cache first — Streamlit reruns the whole script on every
        # interaction and reads the same tables repeatedly; without this each read is a fresh
        # round-trip to Neon (which may be far away), which is the main source of slowness.
        cached = _cache_get(title, tab)
        if cached is not None:
            for c in columns:
                if c not in cached.columns:
                    cached[c] = ""
            return cached[columns]
        try:
            df = db.read_table(tab, title, columns)
            _cache_put(title, tab, df)
            return df
        except Exception:
            # if a Postgres read fails, fall through to the existing backends below
            pass
    if _cloud() and not local_first():
        title, tab = _route(path)
        cached = _cache_get(title, tab)
        if cached is not None:
            for c in columns:
                if c not in cached.columns:
                    cached[c] = ""
            return cached[columns]
        df = gsheets.read_df(title, tab, columns)
        _cache_put(title, tab, df)
        return df
    # local (default)
    if not os.path.exists(path):
        return pd.DataFrame(columns=columns)
    try:
        df = pd.read_excel(path, dtype=str).fillna("")
    except Exception:
        return pd.DataFrame(columns=columns)
    for c in columns:
        if c not in df.columns:
            df[c] = ""
    return df[columns]


def _write(path, df, columns):
    """Atomic, lock-guarded write. Postgres on Cloud when configured; else Sheets on
    Cloud; else local Excel (with a later Sheets backup)."""
    for c in columns:
        if c not in df.columns:
            df[c] = ""
    # Postgres (Neon) backend
    if _use_pg():
        title, tab = _route(path)
        db.write_table(tab, title, df, columns)
        _cache_put(title, tab, df[columns])   # keep cache fresh = next read needs no round-trip
        return
    if _cloud() and not local_first():
        title, tab = _route(path)
        gsheets.set_df(title, tab, df, columns)
        _cache_put(title, tab, df[columns])
        return
    # local (default)
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    with FileLock(path + ".lock"):
        fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=folder)
        os.close(fd)
        try:
            df[columns].to_excel(tmp, index=False)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


# ---- periodic backup: push local files to Google Sheets ------------------------------

def _sync_state_path():
    return os.path.join(paths.common_dir(), "sync_state.json")


def sync_to_sheets(user_key=None, force=False):
    """Push local .xlsx files up to Google Sheets as a backup mirror. Only files changed
    since the last sync (by modified-time) are pushed, unless force=True. Pushes the given
    user's folder + _common (or all users when user_key is None).
    Returns (pushed, skipped, error_str)."""
    if gsheets is None or not gsheets.enabled():
        return 0, 0, "Google Sheets backup isn't configured (add SHEET_ID + credentials)."
    import json
    import glob
    state_path = _sync_state_path()
    try:
        state = json.load(open(state_path)) if os.path.exists(state_path) else {}
    except Exception:
        state = {}

    dirs = [paths.common_dir()]
    if user_key:
        dirs.append(_user_dir(user_key))
    else:
        for d in glob.glob(os.path.join(paths.base_dir(), "*")):
            if os.path.isdir(d):
                dirs.append(d)

    pushed = skipped = 0
    errs = []
    for d in dirs:
        for fp in glob.glob(os.path.join(d, "*.xlsx")):
            try:
                mtime = os.path.getmtime(fp)
                if not force and state.get(fp) == mtime:
                    skipped += 1
                    continue
                title = os.path.basename(os.path.dirname(fp))
                tab = os.path.splitext(os.path.basename(fp))[0]
                df = pd.read_excel(fp, dtype=str).fillna("")
                gsheets.set_df(title, tab, df, list(df.columns))
                state[fp] = mtime
                pushed += 1
            except Exception as e:
                errs.append(f"{os.path.basename(fp)}: {e}")

    # NOTE: role-prompt files (role_prompts/*.md) are NOT synced here — they live in the
    # GitHub repo (version-controlled). Edit + commit + push to update them.
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        json.dump(state, open(state_path, "w"))
    except Exception:
        pass
    return pushed, skipped, ("; ".join(errs) if errs else "")


def restore_from_sheets(user_key):
    """Pull this user's tabs (and _common) from Google Sheets back into local files — for
    a fresh/wiped machine. Overwrites local files. Returns (restored, error_str)."""
    if gsheets is None or not gsheets.enabled():
        return 0, "Google Sheets isn't configured."
    try:
        book = gsheets._book()
        tabs = [ws.title for ws in book.worksheets()]
    except Exception as e:
        return 0, f"Couldn't open the backup: {e}"
    restored = 0
    errs = []
    wanted_prefixes = (f"{user_key}__", "_common__")
    for full in tabs:
        if not full.startswith(wanted_prefixes):
            continue
        try:
            title, _, tab = full.partition("__")
            ws = book.worksheet(full)
            values = ws.get_all_values()
            if not values:
                continue
            cols = values[0]
            rows = values[1:]
            df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
            folder = os.path.join(paths.base_dir(), title)
            os.makedirs(folder, exist_ok=True)
            df.to_excel(os.path.join(folder, f"{tab}.xlsx"), index=False)
            restored += 1
        except Exception as e:
            errs.append(f"{full}: {e}")
    _cache_clear()
    return restored, ("; ".join(errs) if errs else "")


# ---------------------------------------------------------------- users

def _users_path():
    return os.path.join(_common_dir(), "users_master.xlsx")


def _secrets_users():
    """Read optional [[users]] blocks from Streamlit secrets, returned as a list of dicts.
    This lets logins be defined directly in secrets (durable on Streamlit Cloud) in
    addition to the users sheet. Safe if secrets or the block is absent."""
    try:
        import streamlit as st
        rows = st.secrets.get("users", [])
    except Exception:
        return []
    out = []
    for r in rows or []:
        try:
            out.append(dict(r))
        except Exception:
            continue
    return out


def get_users():
    """All logins, merged from the users sheet AND any [[users]] in Streamlit secrets.
    - user_key is normalised to lowercase (so it matches the lowercased login input).
    - On a user_key collision, secrets win, overlaying only the fields they specify onto
      the sheet row (fields omitted in secrets keep the sheet value).
    - A user with no `active` value defaults to active ("Yes")."""
    import pandas as pd
    sheet = _read(_users_path(), schemas.USERS)
    merged = {}
    if not sheet.empty:
        for _, r in sheet.iterrows():
            uk = str(r.get("user_key", "") or "").strip().lower()
            if not uk:
                continue
            row = {c: r.get(c, "") for c in schemas.USERS}
            row["user_key"] = uk
            merged[uk] = row
    for u in _secrets_users():
        uk = str(u.get("user_key", "") or "").strip().lower()
        if not uk:
            continue
        base = merged.get(uk, {c: "" for c in schemas.USERS})
        for c in schemas.USERS:
            v = u.get(c, None)
            if v not in (None, ""):
                base[c] = v
        base["user_key"] = uk
        if not str(base.get("active", "") or "").strip():
            base["active"] = "Yes"
        merged[uk] = base   # secrets override the sheet
    if not merged:
        return pd.DataFrame(columns=schemas.USERS)
    return pd.DataFrame(list(merged.values()), columns=schemas.USERS)


DEMO_USERS = {
    "nishi": {"user_key": "nishi", "name": "Nishi", "role": "sales_rm",
              "department": "Sales", "login_role": "member", "password": "nishi",
              "active": "Yes", "created_at": ""},
    "yash": {"user_key": "yash", "name": "Yash", "role": "trainer",
             "department": "Training", "login_role": "member", "password": "yash",
             "active": "Yes", "created_at": ""},
    "vikrant": {"user_key": "vikrant", "name": "Vikrant", "role": "lead",
                "department": "Leadership", "login_role": "lead", "password": "vikrant",
                "active": "Yes", "created_at": ""},
    "arjun": {"user_key": "arjun", "name": "Arjun", "role": "partner_acquisition",
              "department": "Partner Acquisition", "login_role": "member",
              "password": "arjun", "active": "Yes", "created_at": ""},
}


def get_user(user_key):
    uk = str(user_key or "").strip().lower()
    df = get_users()
    if not df.empty:
        row = df[df["user_key"] == uk]
        if not row.empty:
            return row.iloc[0].to_dict()
        return None   # users are configured (sheet/secrets) but this isn't one of them
    return DEMO_USERS.get(uk)   # nothing configured yet -> allow demo logins


def hash_password(password, salt=None):
    """Return a salted PBKDF2-SHA256 hash string: 'pbkdf2$<iterations>$<salt_hex>$<hash_hex>'.
    A random 16-byte salt is generated if none is given. Store this string as the user's
    password; the plaintext is never kept."""
    import hashlib
    import os as _os
    iterations = 200_000
    if salt is None:
        salt = _os.urandom(16)
    elif isinstance(salt, str):
        salt = bytes.fromhex(salt)
    dk = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password, stored):
    """Check a typed password against a stored value. Supports the salted hash format
    above; also accepts a legacy PLAINTEXT value (so existing logins keep working until
    their passwords are re-hashed). Uses a constant-time compare for the hash path."""
    import hashlib
    import hmac
    stored = str(stored or "")
    if stored.startswith("pbkdf2$"):
        try:
            _, iters, salt_hex, hash_hex = stored.split("$", 3)
            dk = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"),
                                     bytes.fromhex(salt_hex), int(iters))
            return hmac.compare_digest(dk.hex(), hash_hex)
        except Exception:
            return False
    # legacy plaintext fallback (re-hash these when convenient)
    return bool(stored) and str(password) == stored


def authenticate(user_key, password):
    """Verify a login. Returns the user dict on success, else None.
    Login ID = user_key (username); the password is checked against the stored hash
    (salted PBKDF2) — or a legacy plaintext value if that user hasn't been migrated yet."""
    u = get_user(user_key)
    if not u or str(u.get("active", "Yes")) != "Yes":
        return None
    if verify_password(password, u.get("password", "")):
        return u
    return None


# ---------------------------------------------------------------- targets

def _targets_path(user_key):
    return os.path.join(_user_dir(user_key), "monthly_targets.xlsx")


def _effort_kras_path(user_key):
    return os.path.join(_user_dir(user_key), "effort_kras.xlsx")


def get_effort_kras(user_key):
    """The user's own KRA list for the Effort matrix, ordered. Returns [] if not customised
    (caller then falls back to the monthly-target KPI names)."""
    df = _read(_effort_kras_path(user_key), schemas.EFFORT_KRAS)
    if df.empty:
        return []
    try:
        df = df.copy()
        df["__o"] = pd.to_numeric(df["sort_order"], errors="coerce").fillna(0)
        df = df.sort_values("__o")
    except Exception:
        pass
    out = []
    for n in df["kra_name"].tolist():
        n = str(n).strip()
        if n and n not in out:
            out.append(n)
    return out


def save_effort_kras(user_key, names):
    """Replace the user's Effort-matrix KRA list. Pass [] to clear (revert to target KPIs)."""
    rows = []
    seen = set()
    for i, n in enumerate(names):
        n = str(n).strip()
        if not n or n.lower() in seen:
            continue
        seen.add(n.lower())
        rows.append({"user_key": user_key, "kra_name": n, "sort_order": str(i),
                     "created_at": _now()})
    df = pd.DataFrame(rows, columns=schemas.EFFORT_KRAS) if rows \
        else pd.DataFrame(columns=schemas.EFFORT_KRAS)
    _write(_effort_kras_path(user_key), df, schemas.EFFORT_KRAS)


def get_targets(user_key, month):
    df = _read(_targets_path(user_key), schemas.MONTHLY_TARGETS)
    return df[df["month"] == month]


def save_targets(user_key, df):
    _write(_targets_path(user_key), df, schemas.MONTHLY_TARGETS)


def set_targets_from_mis(user_key, month, role, kpis):
    """Replace this month's target rows for the user with the MIS KPIs (name + monthly
    target + achievement). Makes the scorecard mirror the MIS exactly. kpis: list of
    {name, target, achieved}."""
    df = _read(_targets_path(user_key), schemas.MONTHLY_TARGETS)
    if not df.empty:
        df = df[df["month"] != month]
    def _fmt(x):
        try:
            x = float(x); return str(int(x)) if x == int(x) else str(x)
        except Exception:
            return str(x)
    newrows = []
    for k in kpis:
        row = {c: "" for c in schemas.MONTHLY_TARGETS}
        row.update({"month": month, "user_key": user_key, "role": role,
                    "kpi_name": k["name"], "monthly_target": _fmt(k["target"]),
                    "achieved_mtd": _fmt(k["achieved"]), "created_at": _now(),
                    "updated_at": _now()})
        newrows.append(row)
    df = pd.concat([df, pd.DataFrame(newrows)], ignore_index=True)
    _write(_targets_path(user_key), df, schemas.MONTHLY_TARGETS)
    return len(newrows)


def set_achieved(user_key, month, kpi_name, value):
    """Update achieved_mtd for one KPI (used by the MIS sync). Matches KPI by normalized
    name. Returns True if a row was updated."""
    import nudge as _n
    df = _read(_targets_path(user_key), schemas.MONTHLY_TARGETS)
    if df.empty:
        return False
    want = _n._normalize(kpi_name)
    mask = (df["month"] == month) & (df["kpi_name"].astype(str).apply(_n._normalize) == want)
    if not mask.any():
        return False
    df.loc[mask, "achieved_mtd"] = str(value)
    df.loc[mask, "updated_at"] = _now()
    _write(_targets_path(user_key), df, schemas.MONTHLY_TARGETS)
    return True


# ---------------------------------------------------------------- monthly plan

def _plan_path(user_key):
    return os.path.join(_user_dir(user_key), "monthly_plan.xlsx")


def get_plan(user_key, month):
    df = _read(_plan_path(user_key), schemas.MONTHLY_PLAN)
    return df[df["month"] == month]


def save_plan(user_key, df):
    _write(_plan_path(user_key), df, schemas.MONTHLY_PLAN)


# ---------------------------------------------------------------- tasks

def _tasks_path(user_key):
    return os.path.join(_user_dir(user_key), "tasks.xlsx")


def get_tasks(user_key, plan_date=None):
    df = _read(_tasks_path(user_key), schemas.TASKS)
    if plan_date is not None:
        df = df[df["plan_date"] == plan_date]
    return df


def _norm_title(s):
    return " ".join(str(s or "").lower().split())


def open_task_exists(user_key, title):
    """True if an OPEN task with the same (normalised) title already exists — used to avoid
    creating duplicate open tasks."""
    df = _read(_tasks_path(user_key), schemas.TASKS)
    if df.empty or "status" not in df.columns:
        return False
    nt = _norm_title(title)
    if not nt:
        return False
    return any(_norm_title(t) == nt and str(s).strip() == "Open"
               for t, s in zip(df["title"], df["status"]))


def add_tasks(user_key, tasks, dedupe=True):
    """tasks: list of dicts. Assigns task_id + timestamps. Saves all (never blocks).
    When dedupe is True, a task whose title matches an existing OPEN task (case/space-
    insensitive) is skipped so duplicates don't pile up. Duplicates within the same batch
    are also collapsed. Returns the full tasks DataFrame."""
    df = _read(_tasks_path(user_key), schemas.TASKS)
    existing = len(df)
    open_titles = set()
    if dedupe and not df.empty and "status" in df.columns:
        open_titles = {_norm_title(t) for t, s in zip(df["title"], df["status"])
                       if str(s).strip() == "Open"}
    rows = []
    for t in tasks:
        nt = _norm_title(t.get("title", ""))
        if dedupe and nt and nt in open_titles:
            continue  # skip duplicate of an existing open task
        row = {c: "" for c in schemas.TASKS}
        row.update(t)
        row["task_id"] = t.get("task_id") or f"{user_key}_{existing + len(rows) + 1:04d}"
        row["user_key"] = user_key
        row["status"] = t.get("status") or "Open"
        row["created_at"] = _now()
        row["updated_at"] = _now()
        rows.append(row)
        if nt:
            open_titles.add(nt)  # collapse duplicates within this same batch too
    if rows:
        df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
        _write(_tasks_path(user_key), df, schemas.TASKS)
        for r in rows:
            log_task_event(user_key, r["task_id"], r.get("title", ""), "created",
                           r.get("day_goal", ""))
    return df


def update_task(user_key, task_id, **fields):
    df = _read(_tasks_path(user_key), schemas.TASKS)
    mask = df["task_id"] == task_id
    if not mask.any():
        return df
    for k, v in fields.items():
        if k in df.columns:
            df.loc[mask, k] = v
    df.loc[mask, "updated_at"] = _now()
    if fields.get("status") == "Done":
        df.loc[mask, "done_at"] = _now()
    _write(_tasks_path(user_key), df, schemas.TASKS)
    st = fields.get("status")
    if st in ("Done", "Dropped"):
        row = df[mask].iloc[0]
        log_task_event(user_key, task_id, row["title"],
                       "done" if st == "Done" else "deleted", row["day_goal"])
    return df


# ---------------------------------------------------------------- day updates

def _updates_path(user_key):
    return os.path.join(_user_dir(user_key), "day_updates.xlsx")


def save_day_update(user_key, update):
    df = _read(_updates_path(user_key), schemas.DAY_UPDATES)
    row = {c: "" for c in schemas.DAY_UPDATES}
    row.update(update)
    row["user_key"] = user_key
    row["created_at"] = _now()
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_updates_path(user_key), df, schemas.DAY_UPDATES)
    return df


def get_day_updates(user_key, date=None):
    df = _read(_updates_path(user_key), schemas.DAY_UPDATES)
    if date is not None:
        df = df[df["date"] == date]
    return df


# ---------------------------------------------------------------- role prompts

def _role_base_path(role):
    return os.path.join(paths.role_prompts_dir(), f"{role}.md")


def _role_learn_path(role):
    return os.path.join(paths.role_prompts_dir(), f"{role}.learn.md")


def _role_tweak_path(user_key):
    return os.path.join(paths.role_prompts_dir(), f"tweak_{user_key}.md")


def _read_role_file(filename):
    """Read a role-prompt file. Source of truth = the GitHub repo's role_prompts/ folder
    (you push changes there). For fast access we keep a local cache copy and refresh it
    whenever the committed repo file differs (newer commit overwrites the cached one).
    Falls back to the legacy workspace folder if the repo doesn't have the file."""
    repo_path = os.path.join(paths.role_prompts_dir(), filename)
    cache_path = os.path.join(paths.role_cache_dir(), filename)
    # 1) repo file present -> ensure the local cache mirrors it, then read the cache
    if os.path.exists(repo_path):
        try:
            with open(repo_path, "r", encoding="utf-8") as f:
                repo_txt = f.read()
        except Exception:
            repo_txt = ""
        cached_txt = None
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached_txt = f.read()
            except Exception:
                cached_txt = None
        if cached_txt != repo_txt:   # newer commit (or first run) -> overwrite cache
            try:
                os.makedirs(paths.role_cache_dir(), exist_ok=True)
                with open(cache_path, "w", encoding="utf-8") as f:
                    f.write(repo_txt)
            except Exception:
                pass
        return repo_txt
    # 2) no repo file -> use the cached copy if we have one
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    # 3) legacy workspace fallback
    wp = os.path.join(paths.workspace_role_prompts_dir(), filename)
    if os.path.exists(wp):
        try:
            with open(wp, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""
    return ""


def read_role_base(role):
    return _read_role_file(f"{role}.md")


def write_role_base(role, text):
    os.makedirs(paths.role_prompts_dir(), exist_ok=True)
    with open(_role_base_path(role), "w", encoding="utf-8") as f:
        f.write(text or "")


def read_role_tweak(user_key):
    return _read_role_file(f"tweak_{user_key}.md")


def write_role_tweak(user_key, text):
    os.makedirs(paths.role_prompts_dir(), exist_ok=True)
    with open(_role_tweak_path(user_key), "w", encoding="utf-8") as f:
        f.write(text or "")


def read_role_prompt(role, user_key=None):
    """The full role guidance injected into AI calls, stacked in three layers:
      1. base    — role_prompts/<role>.md          (shared role description; in GitHub)
      2. tweak   — role_prompts/tweak_<user>.md     (small per-person override; in GitHub)
      3. learn   — role_prompts/<role>.learn.md     (auto-built from this user's history)
    Source of truth for base + tweak is the GitHub repo (edit, commit, push). Later layers
    refine earlier ones. Any missing layer is simply skipped."""
    base = read_role_base(role)
    tweak = read_role_tweak(user_key) if user_key else ""
    learn = _read_role_file(f"{role}.learn.md")
    out = base or ""
    if tweak.strip():
        out += "\n\n## Personal notes for this team member\n" + tweak
    if learn.strip():
        out += "\n\n## Localised learning (from this user's history)\n" + learn
    return out


# ---------------------------------------------------------------- daily targets (the 4 boxes)

def _day_goals_path(user_key):
    return os.path.join(_user_dir(user_key), "day_goals.xlsx")


def get_day_goals(user_key, date):
    """Return the day's targets as a list of 4 dicts (slot, heading, target_number).

    Always returns exactly 4 slots so the UI can render 4 boxes; unset slots have
    empty heading/number.
    """
    df = _read(_day_goals_path(user_key), schemas.DAY_GOALS)
    df = df[df["date"] == date]
    by_slot = {str(r["slot"]): r for _, r in df.iterrows()}
    out = []
    for s in range(1, 5):
        r = by_slot.get(str(s))
        out.append({
            "slot": s,
            "heading": (r["heading"] if r is not None else "") or "",
            "target_number": (r["target_number"] if r is not None else "") or "",
        })
    return out


def save_day_goals(user_key, date, goals):
    """goals: list of {slot, heading, target_number}. Replaces the day's rows."""
    df = _read(_day_goals_path(user_key), schemas.DAY_GOALS)
    df = df[df["date"] != date]                      # drop old rows for this date
    rows = []
    for g in goals:
        heading = (g.get("heading") or "").strip()
        if not heading:
            continue                                  # skip empty slots
        # enforce the 2-word cap on the heading
        heading = " ".join(heading.split()[:2])
        rows.append({
            "date": date, "user_key": user_key, "slot": g.get("slot", ""),
            "heading": heading, "target_number": g.get("target_number", ""),
            "created_at": _now(), "updated_at": _now(),
        })
    if rows:
        df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
    _write(_day_goals_path(user_key), df, schemas.DAY_GOALS)
    return rows


def day_goal_headings(user_key, date):
    """Just the set headings (the dropdown choices for tasks). May be empty."""
    return [g["heading"] for g in get_day_goals(user_key, date) if g["heading"]]


def has_day_goals(user_key, date):
    """The gate: True once at least one target is set for the day."""
    return len(day_goal_headings(user_key, date)) > 0


# ---------------------------------------------------------------- carry-forward

import json as _json


def carry_forward(user_key, today):
    """Roll unfinished tasks from prior days into `today` as carried (orange) cards.

    Runs when the day opens. A task carries if it's not Done/Dropped and its
    plan_date is before today. Finished steps are kept; the card keeps its goal
    link; it's marked carried_from = original date. Idempotent: a task already
    moved to today won't be moved again.
    """
    df = _read(_tasks_path(user_key), schemas.TASKS)
    if df.empty:
        return 0
    moved = 0
    for idx, t in df.iterrows():
        if t["status"] in ("Done", "Dropped"):
            continue
        if not t["plan_date"] or t["plan_date"] >= today:
            continue
        # roll it forward in place: retag date, remember origin
        df.loc[idx, "carried_from"] = t["carried_from"] or t["plan_date"]
        df.loc[idx, "plan_date"] = today
        df.loc[idx, "updated_at"] = _now()
        log_task_event(user_key, t["task_id"], t["title"], "carried",
                       t["day_goal"], detail=f"from {df.loc[idx,'carried_from']}")
        moved += 1
    if moved:
        _write(_tasks_path(user_key), df, schemas.TASKS)
    return moved


# ---------------------------------------------------------------- task steps

def set_task_steps(user_key, task_id, steps):
    """steps: list of str OR list of {text, done}. Stores as JSON on the task."""
    norm = []
    for s in steps:
        if isinstance(s, dict):
            norm.append({"text": s.get("text", ""), "done": bool(s.get("done"))})
        else:
            norm.append({"text": str(s), "done": False})
    return update_task(user_key, task_id, steps_json=_json.dumps(norm))


def get_task_steps(user_key, task_id):
    df = _read(_tasks_path(user_key), schemas.TASKS)
    row = df[df["task_id"] == task_id]
    if row.empty:
        return []
    raw = row.iloc[0]["steps_json"] or ""
    try:
        return _json.loads(raw) if raw else []
    except Exception:
        return []


def toggle_step(user_key, task_id, step_index):
    steps = get_task_steps(user_key, task_id)
    if 0 <= step_index < len(steps):
        steps[step_index]["done"] = not steps[step_index]["done"]
        set_task_steps(user_key, task_id, steps)
    return steps


def set_step_done(user_key, task_id, step_index, value):
    """Set a step's done state to an absolute value (used by the checkbox callback)."""
    steps = get_task_steps(user_key, task_id)
    if 0 <= step_index < len(steps):
        steps[step_index]["done"] = bool(value)
        set_task_steps(user_key, task_id, steps)
    return steps


# ---------------------------------------------------------------- task log (history)

def _task_log_path(user_key):
    return os.path.join(_user_dir(user_key), "task_log.xlsx")


def log_task_event(user_key, task_id, title, event, day_goal="", detail=""):
    """Append an immutable history row. Never edits or deletes prior rows."""
    df = _read(_task_log_path(user_key), schemas.TASK_LOG)
    row = {
        "ts": _now(), "date": _now()[:10], "user_key": user_key,
        "task_id": task_id, "title": title, "day_goal": day_goal,
        "event": event, "detail": detail,
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_task_log_path(user_key), df, schemas.TASK_LOG)


def get_task_log(user_key, limit=200):
    df = _read(_task_log_path(user_key), schemas.TASK_LOG)
    if df.empty:
        return df
    return df.sort_values("ts", ascending=False).head(limit)


# ---------------------------------------------------------------- meetings / daily log

import re as _re_m

IDENTITY_LABELS = {
    "new_partner": ("mobile", "Mobile number"),
    "existing_partner": ("partner_code", "Partner code"),
    "client": ("client_code", "Client code"),
    "internal": ("name", "Team member name"),
}


def normalize_identity(meeting_type, value):
    """Return (identity_type, cleaned_value, warning). Light validation, never blocks."""
    itype = IDENTITY_LABELS.get(meeting_type, ("name", ""))[0]
    v = (value or "").strip()
    warn = ""
    if itype == "mobile":
        digits = _re_m.sub(r"\D", "", v)
        if digits and not (10 <= len(digits) <= 13):
            warn = "Mobile looks unusual — expected ~10 digits."
        v = digits or v
    elif itype in ("partner_code", "client_code"):
        v = _re_m.sub(r"\s+", "", v).upper()
        if v and not v.isalnum():
            warn = "Code should be letters/numbers only."
    return itype, v, warn


def _meetings_path(user_key):
    return os.path.join(_user_dir(user_key), "meetings.xlsx")


def save_meeting(user_key, meeting):
    """Append a meeting record. Assigns meeting_id + timestamps. Returns the id."""
    df = _read(_meetings_path(user_key), schemas.MEETINGS)
    mid = meeting.get("meeting_id") or f"{user_key}_m{len(df)+1:04d}"
    row = {c: "" for c in schemas.MEETINGS}
    row.update(meeting)
    row["meeting_id"] = mid
    row["user_key"] = user_key
    row["created_at"] = _now()
    row["updated_at"] = _now()
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_meetings_path(user_key), df, schemas.MEETINGS)
    return mid


def get_meetings(user_key, limit=200):
    df = _read(_meetings_path(user_key), schemas.MEETINGS)
    if df.empty:
        return df
    return df.sort_values("created_at", ascending=False).head(limit)


def link_meeting_followup(user_key, meeting_id, task_id):
    df = _read(_meetings_path(user_key), schemas.MEETINGS)
    mask = df["meeting_id"] == meeting_id
    if mask.any():
        df.loc[mask, "followup_task_id"] = task_id
        df.loc[mask, "updated_at"] = _now()
        _write(_meetings_path(user_key), df, schemas.MEETINGS)


def schedule_followup(user_key, date, identity_value, meeting_id, title, day_goal=""):
    """Create a future-dated task that surfaces on `date` (the scheduler).

    Because get_tasks filters by plan_date, a task dated in the future simply
    appears on that day; carry_forward rolls it on if missed. So a scheduled
    follow-up reuses the whole task machinery — Done, steps, log, carry.
    """
    task = {
        "title": title or f"Follow up: {identity_value}",
        "plan_date": date,
        "day_goal": day_goal,
        "horizon": "Today",
        "source": "follow_up",
        "meeting_id": meeting_id,
        "followup_for": identity_value,
    }
    df = add_tasks(user_key, [task])
    # the id just assigned is the last row for this user
    new_id = df.iloc[-1]["task_id"]
    if meeting_id:
        link_meeting_followup(user_key, meeting_id, new_id)
    return new_id


# ---------------------------------------------------------------- partners

def _partners_path(user_key):
    return os.path.join(_user_dir(user_key), "partners.xlsx")


def add_partner(user_key, name, mobile, code="", code_type="", notes="",
                contact_type="partner", role="", salutation=""):
    df = _read(_partners_path(user_key), schemas.PARTNERS)
    digits = _re_m.sub(r"\D", "", mobile or "")
    code = _re_m.sub(r"\s+", "", (code or "")).upper()
    row = {c: "" for c in schemas.PARTNERS}
    row.update({
        "partner_id": f"{user_key}_p{len(df)+1:04d}", "user_key": user_key,
        "name": (name or "").strip(), "mobile": digits or (mobile or "").strip(),
        "contact_type": contact_type, "role": role,
        "code": code, "code_type": code_type, "salutation": salutation, "notes": notes,
        "created_at": _now(), "updated_at": _now(),
    })
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_partners_path(user_key), df, schemas.PARTNERS)
    return row["partner_id"]


def _partner_identity(mobile="", code="", name=""):
    """The stable identity tag for a partner: phone primary, code secondary, name last."""
    digits = _re_m.sub(r"\D", "", mobile or "")
    if digits:
        return f"phone:{digits}"
    c = _re_m.sub(r"\s+", "", (code or "")).upper()
    if c:
        return f"code:{c}"
    return f"name:{(name or '').strip().lower()}"


def upsert_partner(user_key, name="", mobile="", code="", code_type="",
                   contact_type="partner", role="", notes="", salutation=""):
    """Find-or-create a partner by identity (phone → code → name). Updates missing fields
    on an existing record; creates a new one otherwise. Returns (partner_id, identity)."""
    df = _read(_partners_path(user_key), schemas.PARTNERS)
    digits = _re_m.sub(r"\D", "", mobile or "")
    codeU = _re_m.sub(r"\s+", "", (code or "")).upper()
    match_idx = None
    if not df.empty:
        if digits:
            m = df[df["mobile"].astype(str).str.replace(r"\D", "", regex=True) == digits]
            if not m.empty:
                match_idx = m.index[0]
        if match_idx is None and codeU:
            m = df[df["code"].astype(str).str.upper().str.replace(r"\s+", "", regex=True) == codeU]
            if not m.empty:
                match_idx = m.index[0]
        if match_idx is None and name:
            m = df[df["name"].astype(str).str.lower() == name.strip().lower()]
            if not m.empty:
                match_idx = m.index[0]

    if match_idx is not None:
        # fill in any missing fields without clobbering existing values
        for col, val in (("name", name), ("mobile", digits), ("code", codeU),
                         ("code_type", code_type), ("role", role), ("salutation", salutation)):
            if val and not str(df.loc[match_idx, col]).strip():
                df.loc[match_idx, col] = val
        df.loc[match_idx, "updated_at"] = _now()
        _write(_partners_path(user_key), df, schemas.PARTNERS)
        r = df.loc[match_idx]
        return r["partner_id"], _partner_identity(r["mobile"], r["code"], r["name"])

    pid = add_partner(user_key, name, mobile, code, code_type, notes, contact_type, role, salutation)
    return pid, _partner_identity(digits, codeU, name)


def find_partner_by_identity(user_key, identity):
    """Resolve a 'phone:/code:/name:' identity tag to a partner row dict, or None."""
    df = get_partners(user_key)
    if df.empty or not identity:
        return None
    kind, _, val = identity.partition(":")
    if kind == "phone":
        m = df[df["mobile"].astype(str).str.replace(r"\D", "", regex=True) == val]
    elif kind == "code":
        m = df[df["code"].astype(str).str.upper().str.replace(r"\s+", "", regex=True) == val.upper()]
    else:
        m = df[df["name"].astype(str).str.lower() == val.lower()]
    return None if m.empty else m.iloc[0].to_dict()


def get_partners(user_key):
    return _read(_partners_path(user_key), schemas.PARTNERS)


def partner_mobile(user_key, name):
    """Look up a Directory contact's mobile by name (case-insensitive)."""
    df = get_partners(user_key)
    if df.empty:
        return ""
    hit = df[df["name"].astype(str).str.lower() == str(name).lower()]
    return hit.iloc[0]["mobile"] if not hit.empty else ""


def partner_salutation(user_key, name="", mobile=""):
    """Look up a contact's Sir/Mam salutation by mobile (preferred) or name; '' if none."""
    df = get_partners(user_key)
    if df.empty:
        return ""
    digits = _re_m.sub(r"\D", "", mobile or "")
    if digits:
        m = df[df["mobile"].astype(str).str.replace(r"\D", "", regex=True) == digits]
        if not m.empty:
            return str(m.iloc[0].get("salutation", "") or "")
    if name:
        m = df[df["name"].astype(str).str.lower() == name.strip().lower()]
        if not m.empty:
            return str(m.iloc[0].get("salutation", "") or "")
    return ""


def last_meeting_for(user_key, name="", mobile=""):
    """Most recent meeting whose identity matches this contact's mobile or name."""
    df = get_meetings(user_key)
    if df.empty:
        return None
    idv = df["identity_value"].astype(str).str.lower()
    m = df[(idv == str(mobile).lower()) | (idv == str(name).lower())]
    if m.empty:
        return None
    m = m.sort_values("date")
    return m.iloc[-1].to_dict()


def personalise(message, name, last_meeting=None, salutation=""):
    """Greet respectfully. With a salutation (Sir/Mam) → 'Firstname Sir,'. Otherwise a
    warm 'Hi Firstname,'. Fills a {name} placeholder if present. Optionally adds a
    one-line callback to the last meeting's next action."""
    first = (str(name).split()[0] if name else "").strip()
    sal = (salutation or "").strip()
    greet_name = f"{first} {sal}" if (first and sal) else (first or "there")
    if "{name}" in message:
        out = message.replace("{name}", greet_name)
    else:
        if first and sal:
            out = f"{greet_name}," + "\n\n" + message
        else:
            out = (f"Hi {first}," if first else "Hi,") + "\n\n" + message
    if last_meeting:
        nxt = (last_meeting.get("next_action") or "").strip()
        outcome = (last_meeting.get("outcome") or "").strip()
        tail = nxt or outcome
        if tail:
            out += f"\n\nP.S. Following up on our last conversation — {tail}."
    return out


def remove_partner(user_key, partner_id):
    df = _read(_partners_path(user_key), schemas.PARTNERS)
    df = df[df["partner_id"] != partner_id]
    _write(_partners_path(user_key), df, schemas.PARTNERS)


# ---------------------------------------------------------------- message reminders

def _sched_path(user_key):
    return os.path.join(_user_dir(user_key), "msg_schedules.xlsx")


def _outbox_path(user_key):
    return os.path.join(_user_dir(user_key), "msg_outbox.xlsx")


def add_message_schedule(user_key, message, recipient_ids, recurrence="daily",
                         run_time="09:00", run_date="", weekday="", label=""):
    df = _read(_sched_path(user_key), schemas.MSG_SCHEDULES)
    row = {c: "" for c in schemas.MSG_SCHEDULES}
    row.update({
        "schedule_id": f"{user_key}_s{len(df)+1:04d}", "user_key": user_key,
        "label": label or (message or "")[:40], "message": message,
        "recipients": _json.dumps(list(recipient_ids)),
        "recurrence": recurrence, "run_time": run_time, "run_date": run_date,
        "weekday": str(weekday), "active": "Yes", "last_run_date": "",
        "created_at": _now(), "updated_at": _now(),
    })
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_sched_path(user_key), df, schemas.MSG_SCHEDULES)
    return row["schedule_id"]


def get_message_schedules(user_key):
    return _read(_sched_path(user_key), schemas.MSG_SCHEDULES)


def set_schedule_active(user_key, schedule_id, active):
    df = _read(_sched_path(user_key), schemas.MSG_SCHEDULES)
    df.loc[df["schedule_id"] == schedule_id, "active"] = "Yes" if active else "No"
    _write(_sched_path(user_key), df, schemas.MSG_SCHEDULES)


def delete_message_schedule(user_key, schedule_id):
    df = _read(_sched_path(user_key), schemas.MSG_SCHEDULES)
    df = df[df["schedule_id"] != schedule_id]
    _write(_sched_path(user_key), df, schemas.MSG_SCHEDULES)


def _due_today(sched, today, weekday):
    if str(sched.get("active")) != "Yes":
        return False
    if sched.get("last_run_date") == today:
        return False
    rec = sched.get("recurrence")
    if rec == "once":
        return sched.get("run_date") == today
    if rec == "daily":
        return True
    if rec == "weekly":
        return str(sched.get("weekday")) == str(weekday)
    return False


def run_due_message_schedules(user_key, today=None):
    """Prepare (record) messages for schedules due today. Does NOT send anything —
    Layer 1 only. Idempotent per day via last_run_date. Returns count prepared."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    weekday = datetime.strptime(today, "%Y-%m-%d").weekday()
    sched = _read(_sched_path(user_key), schemas.MSG_SCHEDULES)
    if sched.empty:
        return 0
    partners = get_partners(user_key)
    pmap = {r["partner_id"]: r for _, r in partners.iterrows()}
    outbox = _read(_outbox_path(user_key), schemas.MSG_OUTBOX)
    new_rows = []
    for idx, s in sched.iterrows():
        if not _due_today(s, today, weekday):
            continue
        try:
            rids = _json.loads(s.get("recipients") or "[]")
        except Exception:
            rids = []
        for rid in rids:
            p = pmap.get(rid)
            if p is None:
                continue
            new_rows.append({
                "msg_id": f"{user_key}_o{len(outbox)+len(new_rows)+1:05d}",
                "user_key": user_key, "schedule_id": s["schedule_id"], "date": today,
                "recipient_name": p["name"], "recipient_mobile": p["mobile"],
                "message": s["message"], "status": "due", "created_at": _now(),
            })
        sched.loc[idx, "last_run_date"] = today
        if s.get("recurrence") == "once":
            sched.loc[idx, "active"] = "No"
    if new_rows:
        outbox = pd.concat([outbox, pd.DataFrame(new_rows)], ignore_index=True)
        _write(_outbox_path(user_key), outbox, schemas.MSG_OUTBOX)
        _write(_sched_path(user_key), sched, schemas.MSG_SCHEDULES)
    elif not sched.empty:
        _write(_sched_path(user_key), sched, schemas.MSG_SCHEDULES)
    return len(new_rows)


def get_outbox(user_key, date=None):
    df = _read(_outbox_path(user_key), schemas.MSG_OUTBOX)
    if date is not None and not df.empty:
        df = df[df["date"] == date]
    return df


def mark_message(user_key, msg_id, status):
    df = _read(_outbox_path(user_key), schemas.MSG_OUTBOX)
    df.loc[df["msg_id"] == msg_id, "status"] = status
    _write(_outbox_path(user_key), df, schemas.MSG_OUTBOX)


# ---------------------------------------------------------------- step <-> task sync

def sync_task_from_steps(user_key, task_id):
    """After a step changes: task is Done iff ALL steps are done; otherwise Open.
    Never closes a task because of a single step. Returns the resulting status."""
    steps = get_task_steps(user_key, task_id)
    if not steps:
        return None
    df = _read(_tasks_path(user_key), schemas.TASKS)
    row = df[df["task_id"] == task_id]
    if row.empty:
        return None
    cur = row.iloc[0]["status"]
    all_done = all(s.get("done") for s in steps)
    if all_done and cur != "Done":
        update_task(user_key, task_id, status="Done")
        return "Done"
    if not all_done and cur == "Done":
        update_task(user_key, task_id, status="Open", done_at="")
        return "Open"
    return cur


def reopen_task(user_key, task_id):
    df = _read(_tasks_path(user_key), schemas.TASKS)
    row = df[df["task_id"] == task_id]
    if row.empty:
        return
    update_task(user_key, task_id, status="Open", done_at="")
    log_task_event(user_key, task_id, row.iloc[0]["title"], "reopened",
                   row.iloc[0]["day_goal"])


# ---------------------------------------------------------------- monthly progress

def _mprogress_path(user_key):
    return os.path.join(_user_dir(user_key), "monthly_progress.xlsx")


def record_monthly_progress(user_key, date, month, kpi_name, planned, achieved):
    """Upsert one row per (date, kpi): the day's planned vs achieved."""
    df = _read(_mprogress_path(user_key), schemas.MONTHLY_PROGRESS)
    mask = (df["date"] == date) & (df["kpi_name"] == kpi_name)
    if mask.any():
        df.loc[mask, ["planned", "achieved", "updated_at"]] = [planned, achieved, _now()]
    else:
        row = {c: "" for c in schemas.MONTHLY_PROGRESS}
        row.update({"date": date, "user_key": user_key, "month": month,
                    "kpi_name": kpi_name, "planned": planned, "achieved": achieved,
                    "created_at": _now(), "updated_at": _now()})
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_mprogress_path(user_key), df, schemas.MONTHLY_PROGRESS)


def get_monthly_progress(user_key, kpi_name=None, month=None):
    df = _read(_mprogress_path(user_key), schemas.MONTHLY_PROGRESS)
    if df.empty:
        return df
    if kpi_name is not None:
        df = df[df["kpi_name"] == kpi_name]
    if month is not None:
        df = df[df["month"] == month]
    return df.sort_values("date", ascending=False)


# ---------------------------------------------------------------- team roster (shared, lead-managed)

def _team_path():
    import paths
    return os.path.join(paths.common_dir(), "team_roster.xlsx")


def add_team_member(name, mobile, member_type="team", department=""):
    df = _read(_team_path(), schemas.TEAM_ROSTER)
    digits = _re_m.sub(r"\D", "", mobile or "")
    row = {c: "" for c in schemas.TEAM_ROSTER}
    row.update({
        "member_id": f"tm{len(df)+1:04d}", "name": (name or "").strip(),
        "mobile": digits or (mobile or "").strip(),
        "member_type": member_type if member_type in ("team", "partner") else "team",
        "department": department, "created_at": _now(),
    })
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_team_path(), df, schemas.TEAM_ROSTER)
    return row["member_id"]


def get_team_roster():
    return _read(_team_path(), schemas.TEAM_ROSTER)


def remove_team_member(member_id):
    df = _read(_team_path(), schemas.TEAM_ROSTER)
    df = df[df["member_id"] != member_id]
    _write(_team_path(), df, schemas.TEAM_ROSTER)


def import_directory_excel(user_key, file, contact_type="partner"):
    """Upsert partners into the user's Directory from an .xlsx. Recognises name / mobile /
    code columns under common header variants. Uses identity matching (phone→code→name).
    Returns (added_or_updated, skipped)."""
    df = pd.read_excel(file)
    cols = {str(c).lower().strip(): c for c in df.columns}

    def pick(*names):
        for n in names:
            if n in cols:
                return cols[n]
        return None

    cn = pick("name", "partner name", "client name", "contact", "contact name")
    cp = pick("mobile", "phone", "number", "whatsapp", "mobile number", "contact number")
    cc = pick("code", "partner code", "client code", "ucc", "code/ucc")
    cr = pick("role", "designation", "department", "dept", "type")
    cs = pick("salutation", "honorific", "title", "gender", "sex")
    if not cn and not cp:
        raise ValueError("Need at least a Name or Mobile column in the sheet.")

    def _sal(v):
        t = str(v).strip().lower()
        if t in ("sir", "mr", "mr.", "m", "male"):
            return "Sir"
        if t in ("mam", "ma'am", "madam", "ms", "mrs", "mrs.", "ms.", "f", "female"):
            return "Mam"
        return ""

    done = skipped = 0
    for _, row in df.iterrows():
        name = "" if (cn is None or pd.isna(row[cn])) else str(row[cn]).strip()
        mobile = "" if (cp is None or pd.isna(row[cp])) else str(row[cp]).strip()
        code = "" if (cc is None or pd.isna(row[cc])) else str(row[cc]).strip()
        role = "" if (cr is None or pd.isna(row[cr])) else str(row[cr]).strip()
        sal = "" if (cs is None or pd.isna(row[cs])) else _sal(row[cs])
        if not (name or mobile or code):
            skipped += 1
            continue
        upsert_partner(user_key, name=name, mobile=mobile, code=code,
                       contact_type=contact_type, role=role, salutation=sal)
        done += 1
    return done, skipped


def import_team_excel(file, member_type="team"):
    """Upsert roster from an .xlsx by name. Recognises name / mobile / department
    columns under common header variants. Returns (added, updated)."""
    df = pd.read_excel(file)
    cols = {str(c).lower().strip(): c for c in df.columns}

    def pick(*names):
        for n in names:
            if n in cols:
                return cols[n]
        return None

    cn = pick("name", "employee name", "employee", "member", "partner name")
    cp = pick("mobile", "phone", "number", "contact", "whatsapp", "mobile number")
    cd = pick("department", "dept", "type")
    if not cn:
        raise ValueError("Couldn't find a 'Name' column in the sheet.")

    roster = _read(_team_path(), schemas.TEAM_ROSTER)
    by_name = {str(r["name"]).lower(): i for i, r in roster.iterrows()}
    added = updated = 0
    for _, row in df.iterrows():
        name = "" if pd.isna(row[cn]) else str(row[cn]).strip()
        if not name:
            continue
        mobile = "" if (cp is None or pd.isna(row[cp])) else _re_m.sub(r"\D", "", str(row[cp]))
        dept = "" if (cd is None or pd.isna(row[cd])) else str(row[cd]).strip()
        key = name.lower()
        if key in by_name:
            i = by_name[key]
            roster.loc[i, ["mobile", "department"]] = [mobile or roster.loc[i, "mobile"], dept]
            updated += 1
        else:
            roster = pd.concat([roster, pd.DataFrame([{
                "member_id": f"tm{len(roster)+1:04d}", "name": name, "mobile": mobile,
                "member_type": member_type, "department": dept, "created_at": _now(),
            }])], ignore_index=True)
            by_name[key] = len(roster) - 1
            added += 1
    _write(_team_path(), roster, schemas.TEAM_ROSTER)
    return added, updated


# ================================================================ companion learning loop

def _topic_of(day_goal, category=""):
    """Normalized key linking a task to its rules (the KPI it serves, else category)."""
    import nudge as _n
    t = _n._normalize(day_goal) or _n._normalize(category) or "general"
    return t


# ---- outcome ledger (store B) ----

def _outcomes_path(user_key):
    return os.path.join(_user_dir(user_key), "outcomes.xlsx")


def add_outcome(user_key, date, task_id, task_title, topic, cue, tried, result, note=""):
    df = _read(_outcomes_path(user_key), schemas.OUTCOMES)
    # one outcome per task per day — upsert
    mask = (df["date"] == date) & (df["task_id"] == task_id)
    row = {
        "outcome_id": f"{user_key}_oc{len(df)+1:05d}", "date": date, "user_key": user_key,
        "task_id": task_id, "task_title": task_title, "topic": topic, "cue": cue,
        "tried": tried, "result": result, "note": note, "created_at": _now(),
    }
    if mask.any():
        for k, v in row.items():
            if k != "outcome_id":
                df.loc[mask, k] = v
    else:
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_outcomes_path(user_key), df, schemas.OUTCOMES)
    return row["outcome_id"]


def get_outcomes(user_key, date=None):
    df = _read(_outcomes_path(user_key), schemas.OUTCOMES)
    if date is not None and not df.empty:
        df = df[df["date"] == date]
    return df


# ---- proven-rules store (store C) ----

def _rules_path(user_key):
    return os.path.join(_user_dir(user_key), "coach_rules.xlsx")


def get_rules(user_key, topic=None):
    df = _read(_rules_path(user_key), schemas.COACH_RULES)
    if topic is not None and not df.empty:
        df = df[df["topic"] == topic]
    return df


def best_rule(user_key, topic):
    """The strongest rule for a topic: tested first, then by success count. Returns
    a dict or None. This is what the companion leads with."""
    df = get_rules(user_key, topic)
    if df.empty:
        return None
    df = df.copy()
    df["successes_n"] = pd.to_numeric(df["successes"], errors="coerce").fillna(0)
    df["tested_rank"] = (df["status"] == "tested").astype(int)
    df = df.sort_values(["tested_rank", "successes_n"], ascending=False)
    r = df.iloc[0]
    return {"rule_text": r["rule_text"], "status": r["status"],
            "successes": int(r["successes_n"]), "rule_id": r["rule_id"]}


def promote_rule(user_key, role, topic, rule_text):
    """Record a success for a topic. First success -> candidate; 2+ -> tested.
    Matches an existing rule by topic (keeps one evolving rule per topic)."""
    df = _read(_rules_path(user_key), schemas.COACH_RULES)
    mask = (df["topic"] == topic) if not df.empty else None
    if mask is not None and mask.any():
        i = df[mask].index[0]
        n = int(pd.to_numeric(df.loc[i, "successes"], errors="coerce") or 0) + 1
        df.loc[i, "successes"] = str(n)
        df.loc[i, "status"] = "tested" if n >= 2 else "candidate"
        df.loc[i, "rule_text"] = rule_text or df.loc[i, "rule_text"]
        df.loc[i, "last_used"] = _now()
        df.loc[i, "updated_at"] = _now()
        rid = df.loc[i, "rule_id"]
    else:
        rid = f"{user_key}_rule{len(df)+1:04d}"
        row = {c: "" for c in schemas.COACH_RULES}
        row.update({"rule_id": rid, "user_key": user_key, "role": role, "topic": topic,
                    "rule_text": rule_text, "successes": "1", "status": "candidate",
                    "last_used": _now(), "created_at": _now(), "updated_at": _now()})
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_rules_path(user_key), df, schemas.COACH_RULES)
    return rid


# ---------------------------------------------------------------- collaborators + WhatsApp

def set_task_collaborators(user_key, task_id, names):
    return update_task(user_key, task_id, collaborators=_json.dumps(list(names)))


def get_task_collaborators(user_key, task_id):
    df = _read(_tasks_path(user_key), schemas.TASKS)
    if "collaborators" not in df.columns:
        return []
    row = df[df["task_id"] == task_id]
    if row.empty:
        return []
    raw = row.iloc[0].get("collaborators", "") or ""
    try:
        return _json.loads(raw) if raw else []
    except Exception:
        return []


def team_member_mobile(name):
    """Look up a team/partner member's mobile by name (case-insensitive)."""
    df = get_team_roster()
    if df.empty:
        return ""
    hit = df[df["name"].astype(str).str.lower() == str(name).lower()]
    return hit.iloc[0]["mobile"] if not hit.empty else ""


def wa_link(mobile, text):
    """Build a WhatsApp click-to-send link (text only — deep links can't attach media).
    Normalizes Indian numbers: strips a leading 0, prepends 91 to bare 10-digit numbers."""
    import urllib.parse as _u
    digits = _re_m.sub(r"\D", "", str(mobile or ""))
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        digits = "91" + digits
    return f"https://wa.me/{digits}?text={_u.quote(text or '')}"


# ================================================================ self-learning (Layer A)

def _daily_logs_path(user_key):
    return os.path.join(_user_dir(user_key), "daily_logs.xlsx")


def save_daily_log(user_key, date, transcript, partner_name="", partner_mobile="",
                   partner_code=""):
    """Save a dated log. If a partner is named, tag it with the partner identity and —
    when a phone is present — upsert the partner into the Directory so it's reusable."""
    identity = ""
    if partner_name or partner_mobile or partner_code:
        if partner_mobile or partner_code:
            _pid, identity = upsert_partner(user_key, name=partner_name,
                                            mobile=partner_mobile, code=partner_code)
        else:
            identity = _partner_identity("", "", partner_name)
    df = _read(_daily_logs_path(user_key), schemas.DAILY_LOGS)
    row = {"log_id": f"{user_key}_log{len(df)+1:05d}", "date": date, "user_key": user_key,
           "partner_name": partner_name, "partner_identity": identity,
           "transcript": transcript, "created_at": _now()}
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_daily_logs_path(user_key), df, schemas.DAILY_LOGS)
    return row["log_id"]


def get_daily_logs(user_key):
    df = _read(_daily_logs_path(user_key), schemas.DAILY_LOGS)
    return df.sort_values("date", ascending=False) if not df.empty else df


def get_daily_log(user_key, log_id):
    df = _read(_daily_logs_path(user_key), schemas.DAILY_LOGS)
    row = df[df["log_id"] == log_id]
    return None if row.empty else row.iloc[0].to_dict()


def _learnings_path(user_key):
    return os.path.join(_user_dir(user_key), "learnings.xlsx")


def add_learning(user_key, date, source_log_id, topic, text, status="pending",
                 conflict_with="", note=""):
    df = _read(_learnings_path(user_key), schemas.LEARNINGS)
    row = {c: "" for c in schemas.LEARNINGS}
    row.update({"learning_id": f"{user_key}_lrn{len(df)+1:05d}", "date": date,
                "user_key": user_key, "source_log_id": source_log_id, "topic": topic,
                "text": text, "status": status, "conflict_with": conflict_with,
                "note": note, "created_at": _now(), "decided_at": ""})
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_learnings_path(user_key), df, schemas.LEARNINGS)
    return row["learning_id"]


def get_learnings(user_key, status=None):
    df = _read(_learnings_path(user_key), schemas.LEARNINGS)
    if status is not None and not df.empty:
        df = df[df["status"] == status]
    return df


def update_learning(user_key, learning_id, **fields):
    df = _read(_learnings_path(user_key), schemas.LEARNINGS)
    i = df.index[df["learning_id"] == learning_id]
    if len(i):
        for k, v in fields.items():
            if k in df.columns:
                df.loc[i[0], k] = v
        _write(_learnings_path(user_key), df, schemas.LEARNINGS)


def accept_learning(user_key, learning_id, role="", supersede_id=""):
    """Mark a learning accepted, optionally supersede a conflicting one, and promote it
    into the proven-rules store so the companion's nudge starts using it."""
    df = _read(_learnings_path(user_key), schemas.LEARNINGS)
    row = df[df["learning_id"] == learning_id]
    if row.empty:
        return
    r = row.iloc[0]
    update_learning(user_key, learning_id, status="accepted", decided_at=_now())
    if supersede_id:
        update_learning(user_key, supersede_id, status="superseded", decided_at=_now())
    topic = r["topic"] or _topic_of("", "")
    promote_rule(user_key, role, topic, r["text"])


# ================================================================ MIS daily brief (KB)

def _mis_snapshot_path(user_key):
    return os.path.join(_user_dir(user_key), "mis_snapshots.xlsx")


_MIS_SNAP_COLS = ["date", "user_key", "kpi_name", "status", "achieved", "target", "created_at"]


def save_mis_snapshot(user_key, date, situation):
    """situation: list of {kpi_name, status, achieved, target}. One snapshot per day."""
    df = _read(_mis_snapshot_path(user_key), _MIS_SNAP_COLS)
    if not df.empty:
        df = df[df["date"] != date]
    rows = [{"date": date, "user_key": user_key, "kpi_name": s["kpi_name"],
             "status": s["status"], "achieved": str(s["achieved"]),
             "target": str(s["target"]), "created_at": _now()} for s in situation]
    df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
    _write(_mis_snapshot_path(user_key), df, _MIS_SNAP_COLS)


def get_mis_snapshot(user_key, date):
    df = _read(_mis_snapshot_path(user_key), _MIS_SNAP_COLS)
    d = df[df["date"] == date] if not df.empty else df
    return {r["kpi_name"]: r["status"] for _, r in d.iterrows()} if not d.empty else {}


_MIS_BRIEF_COLS = ["date", "user_key", "brief", "behind_csv", "slipped_csv", "created_at"]


def save_mis_brief(user_key, date, brief, behind, slipped):
    df = _read(_mis_brief_path(user_key), _MIS_BRIEF_COLS)
    if not df.empty:
        df = df[df["date"] != date]
    row = {"date": date, "user_key": user_key, "brief": brief,
           "behind_csv": "; ".join(behind), "slipped_csv": "; ".join(slipped),
           "created_at": _now()}
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_mis_brief_path(user_key), df, _MIS_BRIEF_COLS)


def _mis_brief_path(user_key):
    return os.path.join(_user_dir(user_key), "mis_brief.xlsx")


def get_mis_brief(user_key, date):
    df = _read(_mis_brief_path(user_key), _MIS_BRIEF_COLS)
    r = df[df["date"] == date] if not df.empty else df
    return None if r.empty else r.iloc[0].to_dict()


def latest_mis_brief(user_key):
    df = _read(_mis_brief_path(user_key), _MIS_BRIEF_COLS)
    return None if df.empty else df.sort_values("date").iloc[-1].to_dict()


# ================================================================ scheduling + buzzer

def _task_updates_path(user_key):
    return os.path.join(_user_dir(user_key), "task_updates.xlsx")


def add_task_update(user_key, task_id, remark):
    """Log a remark against a task (the 'act-to-stop' signal) and stamp the task."""
    df = _read(_task_updates_path(user_key), schemas.TASK_UPDATES)
    row = {"update_id": f"{user_key}_upd{len(df)+1:05d}", "task_id": task_id,
           "user_key": user_key, "remark": remark, "created_at": _now()}
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_task_updates_path(user_key), df, schemas.TASK_UPDATES)
    update_task(user_key, task_id, last_update_at=_now())
    return row["update_id"]


def get_task_updates(user_key, task_id):
    df = _read(_task_updates_path(user_key), schemas.TASK_UPDATES)
    d = df[df["task_id"] == task_id] if not df.empty else df
    return d.sort_values("created_at") if not d.empty else d


def _due_dt(task):
    """Combine plan_date + due_time into a datetime; None if no time set."""
    import datetime as _dt
    t = (task.get("due_time") or "").strip()
    d = (task.get("plan_date") or "").strip()
    if not t or not d:
        return None
    for fmt in ("%H:%M", "%I:%M %p", "%H:%M:%S"):
        try:
            tm = _dt.datetime.strptime(t, fmt).time()
            return _dt.datetime.combine(_dt.date.fromisoformat(d), tm)
        except Exception:
            continue
    return None


def due_buzzing_tasks(user_key, today_str, now=None):
    """Tasks that are due (time passed), not done, and NOT acted-on since due —
    and whose 5-minute re-nag window has elapsed. Returns list of task dicts."""
    import datetime as _dt
    now = now or _dt.datetime.now()
    df = get_tasks(user_key, today_str)
    out = []
    if df.empty:
        return out
    for _, r in df.iterrows():
        t = r.to_dict()
        if t.get("status") in ("Done", "Dropped"):
            continue
        due = _due_dt(t)
        if not due or now < due:
            continue
        # acted since due? (a remark logged at/after due silences it)
        last_up = (t.get("last_update_at") or "").strip()
        if last_up:
            try:
                if _dt.datetime.fromisoformat(last_up) >= due:
                    continue
            except Exception:
                pass
        # 5-minute re-nag gate
        lb = (t.get("last_buzz_at") or "").strip()
        if lb:
            try:
                if (now - _dt.datetime.fromisoformat(lb)).total_seconds() < 300:
                    continue
            except Exception:
                pass
        out.append(t)
    return out


def mark_buzzed(user_key, task_id):
    update_task(user_key, task_id, last_buzz_at=_now())


def reopen_task(user_key, task_id):
    """Bring a Done/Dropped task back to active."""
    update_task(user_key, task_id, status="Open", done_at="", reviewed="No")


# ---------------------------------------------------------------- DSR archive

def _dsr_log_path(user_key):
    return os.path.join(_user_dir(user_key), "dsr_log.xlsx")


def save_dsr(user_key, date, report_text):
    """Save the day's DSR text (one row per day; overwrites if it already exists). Local
    write; the normal sync mirrors it to Google Sheets silently."""
    df = _read(_dsr_log_path(user_key), schemas.DSR_LOG)
    df = df[df["date"] != date] if not df.empty else df
    row = {"date": date, "user_key": user_key, "report_text": report_text, "created_at": _now()}
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_dsr_log_path(user_key), df, schemas.DSR_LOG)


def get_dsr(user_key, date):
    df = _read(_dsr_log_path(user_key), schemas.DSR_LOG)
    hit = df[df["date"] == date] if not df.empty else df
    return hit.iloc[0]["report_text"] if not hit.empty else ""


# ---------------------------------------------------------------- day close tracking

def _closed_days_path(user_key):
    return os.path.join(_user_dir(user_key), "closed_days.xlsx")


def mark_day_closed(user_key, date):
    df = _read(_closed_days_path(user_key), schemas.CLOSED_DAYS)
    if not df.empty and (df["date"] == date).any():
        return
    row = {"date": date, "user_key": user_key, "closed_at": _now()}
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_closed_days_path(user_key), df, schemas.CLOSED_DAYS)


def is_day_closed(user_key, date):
    df = _read(_closed_days_path(user_key), schemas.CLOSED_DAYS)
    return (not df.empty) and (df["date"] == date).any()


# ---------------------------------------------------------------- step memory (reuse)

def _step_templates_path(user_key):
    return os.path.join(_user_dir(user_key), "step_templates.xlsx")


def _norm_title(s):
    import re
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def save_step_template(user_key, topic, task_title, steps):
    """Remember the steps the USER used for a task (keyed by topic + title), so a similar
    future task can reuse the person's own approach. Latest wins per (topic, title)."""
    texts = []
    for s in steps or []:
        t = s.get("text") if isinstance(s, dict) else str(s)
        if str(t).strip():
            texts.append(str(t).strip())
    if not texts:
        return
    df = _read(_step_templates_path(user_key), schemas.STEP_TEMPLATES)
    nt = _norm_title(task_title)
    if not df.empty:
        keep = ~((df["topic"] == (topic or "")) & (df["task_title"].apply(_norm_title) == nt))
        df = df[keep]
    row = {"template_id": f"{user_key}_stp{len(df)+1:05d}", "user_key": user_key,
           "topic": topic or "", "task_title": task_title or "",
           "steps_json": _json.dumps(texts), "source": "user", "updated_at": _now()}
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_step_templates_path(user_key), df, schemas.STEP_TEMPLATES)


def find_step_template(user_key, topic, task_title):
    """Find the person's most relevant past steps for a similar task. Matches on topic, then
    ranks by title word-overlap. Returns (steps_list, matched_title) or ([], '')."""
    df = _read(_step_templates_path(user_key), schemas.STEP_TEMPLATES)
    if df.empty:
        return [], ""
    target = set(_norm_title(task_title).split())

    def _ov(r):
        return len(target & set(_norm_title(r["task_title"]).split()))

    cand = df.copy()
    cand["_ov"] = cand.apply(_ov, axis=1)
    cand["_same_topic"] = (cand["topic"] == (topic or "")).astype(int)
    cand = cand.sort_values(["_same_topic", "_ov", "updated_at"],
                            ascending=[False, False, False])
    best = cand.iloc[0]
    # require real relevance: same topic, or at least one shared title word
    if best["_same_topic"] == 1 or best["_ov"] >= 1:
        try:
            return _json.loads(best["steps_json"]), best["task_title"]
        except Exception:
            return [], ""
    return [], ""


# ---------------------------------------------------------------- nudge popup cap

def _popup_counts_path(user_key):
    return os.path.join(_user_dir(user_key), "popup_counts.xlsx")


def get_popup_count(user_key, date):
    df = _read(_popup_counts_path(user_key), schemas.POPUP_COUNTS)
    if df.empty:
        return 0
    row = df[df["date"] == date]
    if row.empty:
        return 0
    try:
        return int(row.iloc[0]["count"])
    except Exception:
        return 0


def bump_popup_count(user_key, date):
    df = _read(_popup_counts_path(user_key), schemas.POPUP_COUNTS)
    cur = get_popup_count(user_key, date)
    df = df[df["date"] != date] if not df.empty else df
    df = pd.concat([df, pd.DataFrame([{"date": date, "user_key": user_key,
                                       "count": str(cur + 1)}])], ignore_index=True)
    _write(_popup_counts_path(user_key), df, schemas.POPUP_COUNTS)
    return cur + 1


def _learnings_digest_path(user_key):
    return os.path.join(_user_dir(user_key), "learnings_digest.xlsx")


def get_learnings_digest(user_key):
    """The stored distilled learnings brief (or None). dict: brief, source_count, updated_at."""
    df = _read(_learnings_digest_path(user_key), schemas.LEARNINGS_DIGEST)
    return None if df.empty else df.iloc[0].to_dict()


def save_learnings_digest(user_key, brief, source_count):
    """Store the distilled brief (one row per user — replaces any prior brief)."""
    df = pd.DataFrame([{"user_key": user_key, "brief": str(brief or ""),
                        "source_count": str(source_count), "updated_at": _now()}])
    _write(_learnings_digest_path(user_key), df, schemas.LEARNINGS_DIGEST)
