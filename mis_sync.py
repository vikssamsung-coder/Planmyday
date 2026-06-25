"""MIS sync — pull achievement numbers from a OneDrive Excel into the app.

Two ways in, both land in the same parser:
  1. A OneDrive / SharePoint *shared link* (no Microsoft login needed if the file is
     shared "anyone with the link can view"). We convert it to a direct download and
     fetch the .xlsx.
  2. A manual upload of the .xlsx (works immediately, good for testing the format).

Expected sheet format (simple + robust): one row per user per KPI, with columns
  user_key | kpi_name | achieved
(a 'name' column is accepted instead of user_key). A WIDE sheet — one row per user,
one column per KPI — is also handled by melting KPI-looking columns.

Matching is normalized (case/spacing-insensitive), so 'Accounts Opened' == 'accounts
opened'. apply() writes each (user, kpi, achieved) into the targets store via
storage.set_achieved, so the dashboard and Monthly scorecard reflect it.
"""

import base64
import io

import pandas as pd

import storage
import nudge


def direct_download_url(share_url):
    """Convert a OneDrive/SharePoint share link to a direct-download URL.
    (Matches the approach verified working against the Bigul SharePoint link.)"""
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
    u = (share_url or "").strip()
    if not u:
        return u
    host = urlparse(u).netloc.lower()
    if "1drv.ms" in host or "onedrive.live.com" in host:
        b64 = base64.b64encode(u.encode("utf-8")).decode("utf-8")
        b64 = b64.replace("/", "_").replace("+", "-").rstrip("=")
        return f"https://api.onedrive.com/v1.0/shares/u!{b64}/root/content"
    if "sharepoint.com" in host:
        parsed = urlparse(u)
        query = parse_qs(parsed.query)
        query["download"] = ["1"]
        flat = {k: v[0] for k, v in query.items()}
        return urlunparse(parsed._replace(query=urlencode(flat)))
    return u


def fetch_excel(share_url):
    """Download the Excel bytes from a shared link. Raises on failure."""
    import requests
    url = direct_download_url(share_url)
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, allow_redirects=True, timeout=30, headers=headers)
    r.raise_for_status()
    ctype = r.headers.get("Content-Type", "")
    if "text/html" in ctype.lower():
        raise RuntimeError("The link returned a web page, not a file. Make sure it's shared "
                           "'Anyone with the link can view' and points to an .xlsx.")
    return r.content


def _find_month(raw):
    """Find the report month: a cell labelled 'Month:' with a date beside it, else the
    first date cell. Returns 'YYYY-MM' or ''."""
    import datetime as _dt
    for i in range(min(8, len(raw))):
        for j in range(raw.shape[1]):
            v = str(raw.iloc[i, j]).strip().lower()
            if v.startswith("month"):
                for k in range(j + 1, raw.shape[1]):
                    cell = raw.iloc[i, k]
                    if isinstance(cell, (pd.Timestamp, _dt.date)):
                        return pd.Timestamp(cell).strftime("%Y-%m")
                    try:
                        return pd.Timestamp(str(cell)).strftime("%Y-%m")
                    except Exception:
                        continue
    for i in range(len(raw)):
        for j in range(raw.shape[1]):
            if isinstance(raw.iloc[i, j], pd.Timestamp):
                return raw.iloc[i, j].strftime("%Y-%m")
    return ""


def list_sheets(xlsx_bytes):
    """Tab names in the workbook (so the app can show them and let the user pick)."""
    try:
        return pd.ExcelFile(io.BytesIO(xlsx_bytes)).sheet_names
    except Exception:
        return []


def parse_sheet(xlsx_bytes, sheet):
    """Parse the KPI/Target/Achieved block from ONE named sheet.
    Returns {'month','kpis','tab'} or {'error':...}."""
    try:
        raw = pd.ExcelFile(io.BytesIO(xlsx_bytes)).parse(sheet, header=None).fillna("")
    except Exception as e:
        return {"error": f"Couldn't open tab '{sheet}': {e}", "kpis": []}
    kpis = _parse_block(raw)
    if kpis:
        return {"month": _find_month(raw), "kpis": kpis, "tab": sheet}
    return {"error": f"No KPI / Target / Achieved block on tab '{sheet}'.", "kpis": []}


def _parse_block(raw):
    """Find the KPI/Target/Achieved block in one sheet's raw grid. Returns kpis list or []."""
    for h in range(len(raw)):
        rowvals = [str(x).strip().lower() for x in raw.iloc[h].tolist()]
        if "kpi" in rowvals and any("achieved" in v for v in rowvals):
            ki = rowvals.index("kpi")
            ti = next((i for i, v in enumerate(rowvals) if "target" in v), ki + 1)
            ai = next((i for i, v in enumerate(rowvals) if "achieved" in v), ki + 2)
            kpis = []
            for r in range(h + 1, len(raw)):
                name = str(raw.iloc[r, ki]).strip()
                if not name:
                    break
                try:
                    tgt = float(str(raw.iloc[r, ti]).replace(",", "").replace("₹", "").strip())
                    ach = float(str(raw.iloc[r, ai]).replace(",", "").replace("₹", "").strip())
                except Exception:
                    break
                kpis.append({"name": name, "target": tgt, "achieved": ach})
            if kpis:
                return kpis
    return []


def _match_tab(sheet_names, user_key, user_name=""):
    """Pick the tab belonging to a user — matches the app username or full name
    (normalized, so 'Vikrant Dale' tab matches name, 'vikrant' tab matches key)."""
    keyn = nudge._normalize(user_key)
    namen = nudge._normalize(user_name)
    for s in sheet_names:
        sn = nudge._normalize(s)
        if sn == keyn or (namen and sn == namen):
            return s
    # looser: tab starts with the username or first name
    first = namen.split()[0] if namen else ""
    for s in sheet_names:
        sn = nudge._normalize(s)
        if keyn and sn.startswith(keyn):
            return s
        if first and sn.startswith(first):
            return s
    return None


def parse_summary(xlsx_bytes, user_key=None, user_name=""):
    """Parse the KPI SUMMARY block. If user_key is given, read ONLY that user's tab in
    the shared workbook (one tab per user). Returns
    {'month','kpis','tab'} or {'error': ...} or None."""
    xls = pd.ExcelFile(io.BytesIO(xlsx_bytes))

    if user_key:
        tab = _match_tab(xls.sheet_names, user_key, user_name)
        if not tab:
            return {"error": "No data found for your profile yet.", "kpis": []}
        raw = xls.parse(tab, header=None).fillna("")
        kpis = _parse_block(raw)
        if kpis:
            return {"month": _find_month(raw), "kpis": kpis, "tab": tab}
        return {"error": "No data found for your profile yet.", "kpis": []}

    # no user given → first sheet that has a block (single-person file)
    for sheet in xls.sheet_names:
        raw = xls.parse(sheet, header=None).fillna("")
        kpis = _parse_block(raw)
        if kpis:
            return {"month": _find_month(raw), "kpis": kpis, "tab": sheet}
    return None


def _to_long(df):
    """Normalize a parsed sheet to rows of (user, kpi, achieved)."""
    cols = {c.lower().strip(): c for c in df.columns}
    user_col = cols.get("user_key") or cols.get("name") or cols.get("user") or cols.get("rm")
    kpi_col = cols.get("kpi_name") or cols.get("kpi")
    ach_col = cols.get("achieved") or cols.get("achievement") or cols.get("mtd")

    rows = []
    if user_col and kpi_col and ach_col:                 # already long
        for _, r in df.iterrows():
            rows.append((str(r[user_col]).strip(), str(r[kpi_col]).strip(),
                         r[ach_col]))
    elif user_col:                                       # wide -> melt KPI columns
        kpi_cols = [c for c in df.columns if c != user_col]
        for _, r in df.iterrows():
            for c in kpi_cols:
                rows.append((str(r[user_col]).strip(), str(c).strip(), r[c]))
    return rows


def parse(xlsx_bytes):
    """Return list of dicts: {user, kpi, achieved}. Reads all sheets, concatenates."""
    out = []
    xls = pd.ExcelFile(io.BytesIO(xlsx_bytes))
    for sheet in xls.sheet_names:
        df = xls.parse(sheet).fillna("")
        if df.empty:
            continue
        for user, kpi, ach in _to_long(df):
            try:
                val = float(str(ach).replace(",", "").strip() or 0)
            except Exception:
                continue
            if user and kpi:
                out.append({"user": user, "kpi": kpi, "achieved": val})
    return out


def _resolve_user_key(user_token):
    """Map a MIS user token (key or name) to a user_key in the app."""
    users = storage.get_users()
    if users.empty:
        return None
    tok = nudge._normalize(user_token)
    for _, u in users.iterrows():
        if nudge._normalize(u["user_key"]) == tok or nudge._normalize(u["name"]) == tok:
            return u["user_key"]
    return None


def apply(parsed_rows, month):
    """Write parsed achievements into each user's targets. Returns (applied, skipped, log)."""
    applied, skipped, log = 0, 0, []
    for row in parsed_rows:
        uk = _resolve_user_key(row["user"])
        if not uk:
            skipped += 1
            log.append(f"⚠️ no app user for '{row['user']}'")
            continue
        ok = storage.set_achieved(uk, month, row["kpi"], row["achieved"])
        if ok:
            applied += 1
            log.append(f"✅ {uk} · {row['kpi']} = {row['achieved']:g}")
        else:
            skipped += 1
            log.append(f"⚠️ {uk}: KPI '{row['kpi']}' not in targets")
    return applied, skipped, log


def mis_url():
    """Optional MIS share link from secrets (so it can be synced without pasting)."""
    try:
        import streamlit as st
        return str(st.secrets.get("MIS_SHARE_URL", "") or "")
    except Exception:
        return ""


# ----------------------------------------------------------------- sanity layer

def _fingerprint_path(user_key):
    import os
    return os.path.join(storage._user_dir(user_key), "mis_fingerprint.json")


def load_fingerprint(user_key):
    import json, os
    p = _fingerprint_path(user_key)
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            return {}
    return {}


def save_fingerprint(user_key, summary):
    import json
    fp = {"count": len(summary["kpis"]),
          "names": sorted(nudge._normalize(k["name"]) for k in summary["kpis"]),
          "month": summary.get("month", "")}
    try:
        json.dump(fp, open(_fingerprint_path(user_key), "w"))
    except Exception:
        pass


def sanity_check(summary, prev_fp):
    """Return a list of human-readable warnings. Empty list = looks fine to auto-apply."""
    warns = []
    kpis = summary.get("kpis", [])
    if not kpis:
        return ["No KPI/Target/Achieved block was found in the file."]

    # fewer KPIs than last good sync
    prev_n = prev_fp.get("count", 0)
    if prev_n and len(kpis) < prev_n:
        warns.append(f"Found {len(kpis)} KPI(s), but last sync had {prev_n}. "
                     "The block may have moved or headers changed.")

    # names changed vs last time
    if prev_fp.get("names"):
        now_names = sorted(nudge._normalize(k["name"]) for k in kpis)
        if now_names != prev_fp["names"]:
            warns.append("KPI names differ from the last sync — the source may have relabeled them.")

    # per-KPI numeric sanity
    for k in kpis:
        t, a = k.get("target", 0), k.get("achieved", 0)
        if not t or t <= 0:
            warns.append(f"‘{k['name']}’ has a missing/zero target ({t}) — a column may have shifted.")
        if a < 0:
            warns.append(f"‘{k['name']}’ has a negative achievement ({a}).")
        if t and a > t * 3:
            warns.append(f"‘{k['name']}’ achievement ({a:,.0f}) is >3× its target ({t:,.0f}) — "
                         "possibly the wrong column.")
    return warns
