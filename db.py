"""
db.py — Postgres (Neon) connection layer for Plan My Day.

Stage 2 of the Sheets/Excel -> Postgres migration.

What this gives the rest of the app:
  * A pooled psycopg3 connection to Neon, configured from Streamlit secrets (or env).
  * read_table / write_table / one-shot helpers that storage.py routes to, so the
    100+ storage functions don't change.
  * The same "per-user whole-table" semantics the Excel/Sheets layer used: a read
    returns one user's rows as a DataFrame; a write replaces that user's rows
    atomically (DELETE + INSERT inside a transaction). This keeps the migration
    drop-in and avoids cross-user clobbering (different users = different rows).

Connection string: put it in Streamlit secrets as NEON_DATABASE_URL (preferred) or
in the env var of the same name. NEVER hard-code it. Use the Neon POOLED endpoint
(…-pooler.…) for serverless concurrency.

Enabled only when a URL is present AND psycopg imports — otherwise enabled() is
False and storage.py falls back to its existing local/Sheets behaviour, so nothing
breaks if Neon isn't configured yet.
"""

import os
import threading
import pandas as pd

try:
    import psycopg
    from psycopg_pool import ConnectionPool
    _HAVE_PG = True
except Exception:
    _HAVE_PG = False


# ----------------------------------------------------------------- configuration

def _url():
    """The Neon connection string, from Streamlit secrets first, then env. '' if unset."""
    # secrets (only when running under Streamlit)
    try:
        import streamlit as st
        u = st.secrets.get("NEON_DATABASE_URL", "")
        if u:
            return str(u).strip()
    except Exception:
        pass
    return os.environ.get("NEON_DATABASE_URL", "").strip()


_pool = None
_pool_lock = threading.Lock()
_enabled_cache = None


def enabled():
    """True when Postgres is usable: psycopg present AND a URL configured.
    NEVER caches a False result — a transient False (e.g. secrets not loaded yet on the
    first call, or a momentary import issue) must not stick for the whole session and
    silently send every write to local files. Only a True is cached."""
    global _enabled_cache
    if _enabled_cache:
        return True
    _enabled_cache = bool(_HAVE_PG and _url())
    return _enabled_cache


def diagnostics():
    """Plain facts about the DB connection, for troubleshooting 'saved locally' issues."""
    try:
        url_ok = bool(_url())
    except Exception:
        url_ok = False
    return {"psycopg_imported": bool(_HAVE_PG), "url_present": url_ok, "enabled": enabled()}


def _get_pool():
    """Lazily create a small connection pool sized for many concurrent users."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool(
                    conninfo=_url(),
                    min_size=1,
                    max_size=int(os.environ.get("PMD_PG_POOL_MAX", "10")),
                    max_idle=300,        # recycle idle conns (Neon closes them anyway)
                    timeout=30,
                    kwargs={"autocommit": False},
                    open=True,
                )
    return _pool


def init_schema(ddl_path=None):
    """Run the schema DDL (idempotent). Optional convenience — usually you run the
    SQL in the Neon console once. Returns True on success."""
    path = ddl_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "db_schema.sql")
    with open(path, "r") as f:
        ddl = f.read()
    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
    return True


# ----------------------------------------------------------------- table metadata

# Tab/filename (from storage._route) -> Postgres table name. Almost all match 1:1;
# the only rename is the users sheet.
TAB_TO_TABLE = {
    "users_master": "users",
    "dump_types": "dump_types",
    "mis_types": "mis_types",
    "report_requests": "report_requests",
}

# Tables that are GLOBAL/shared (no user_key filter): everyone reads/writes all rows.
GLOBAL_TABLES = {"users", "team_roster", "content", "dump_types", "mis_types"}

# Cache of each table's real columns (so we only write columns that exist, and ignore
# surrogate auto-id columns like task_log.id on insert). Filled on first use per table.
_cols_cache = {}


def _table_for(tab):
    return TAB_TO_TABLE.get(tab, tab)


def table_columns(table, refresh=False):
    """The actual column names of a table. Cached — but NEVER caches an empty result,
    because an empty list here silently breaks writes (write_table would compute zero
    columns and swallow the row). If a lookup returns nothing (e.g. the table didn't
    exist yet), we don't cache it, so the next call re-queries once it does exist.
    Pass refresh=True to bypass the cache."""
    if not refresh and _cols_cache.get(table):
        return _cols_cache[table]
    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
                (table,))
            cols = [r[0] for r in cur.fetchall()]
    if cols:                       # only cache a real, non-empty result
        _cols_cache[table] = cols
    return cols


# Cache of each table's primary-key columns (used to dedupe a write batch so two rows
# sharing a PK can't trip a UniqueViolation on INSERT). Filled on first use per table.
_pk_cache = {}


def table_pk(table):
    """Primary-key column names of a table (cached). [] if the table has no PK."""
    if table in _pk_cache:
        return _pk_cache[table]
    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                "AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = %s::regclass AND i.indisprimary",
                (f'"{table}"',))
            pk = [r[0] for r in cur.fetchall()]
    _pk_cache[table] = pk
    return pk


# ----------------------------------------------------------------- read / write

def read_table(tab, user_key, columns):
    """Return rows as a DataFrame with exactly `columns` (string-typed, NaN->'').
    Per-user tables are filtered by user_key; global tables return all rows."""
    table = _table_for(tab)
    is_global = table in GLOBAL_TABLES
    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            if is_global:
                cur.execute(f'SELECT * FROM "{table}"')
            else:
                cur.execute(f'SELECT * FROM "{table}" WHERE user_key = %s', (user_key,))
            rows = cur.fetchall()
            colnames = [d.name for d in cur.description]
    df = pd.DataFrame(rows, columns=colnames) if rows else pd.DataFrame(columns=colnames)
    # coerce to strings + ensure every requested schema column exists
    for c in columns:
        if c not in df.columns:
            df[c] = ""
    if not df.empty:
        df = df.astype(object).where(pd.notnull(df), "")
        for c in columns:
            df[c] = df[c].map(lambda v: "" if v is None else str(v))
    return df[columns]


def read_all(tab, columns):
    """Every row of a table across ALL users (admin/global read) — used to surface the
    progress that desktop machines sync back on close-day. Same shaping as read_table."""
    table = _table_for(tab)
    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f'SELECT * FROM "{table}"')
            rows = cur.fetchall()
            colnames = [d.name for d in cur.description]
    df = pd.DataFrame(rows, columns=colnames) if rows else pd.DataFrame(columns=colnames)
    for c in columns:
        if c not in df.columns:
            df[c] = ""
    if not df.empty:
        df = df.astype(object).where(pd.notnull(df), "")
        for c in columns:
            df[c] = df[c].map(lambda v: "" if v is None else str(v))
    return df[columns]


def write_table(tab, user_key, df, columns):
    """Replace this user's rows in the table with `df` (atomic). Global tables replace
    all rows. Mirrors the old 'write the whole sheet' semantics, so storage.py is
    unchanged. Only writes columns that exist in the table."""
    table = _table_for(tab)
    is_global = table in GLOBAL_TABLES
    real_cols = [c for c in table_columns(table) if c != "id"]   # skip surrogate serial
    write_cols = [c for c in columns if c in real_cols]
    if not write_cols:
        # Cache may be stale (table created after an earlier empty lookup). Refresh once.
        real_cols = [c for c in table_columns(table, refresh=True) if c != "id"]
        write_cols = [c for c in columns if c in real_cols]
    if not write_cols:
        # Fail loudly rather than silently swallowing the row (the old bug).
        raise RuntimeError(
            f"table '{table}' has no writable columns matching {columns} — does the table "
            f"exist? Run the Database schema button (Admin -> Users) to create it.")

    # normalise the frame to strings on the columns we will write
    out = df.copy()
    for c in write_cols:
        if c not in out.columns:
            out[c] = ""
    if not out.empty:
        out = out.astype(object).where(pd.notnull(out), "")

    # Drop duplicate primary-key rows before INSERT. The DELETE below clears whatever
    # is already in the DB, so a UniqueViolation here can only come from two rows
    # inside `out` sharing a PK (e.g. two day_goals on the same slot). keep="last"
    # means the most recent edit wins.
    if not out.empty:
        pk = [c for c in table_pk(table) if c in out.columns]
        if pk:
            out = out.drop_duplicates(subset=pk, keep="last")

    placeholders = ", ".join(["%s"] * len(write_cols))
    collist = ", ".join(f'"{c}"' for c in write_cols)
    insert_sql = f'INSERT INTO "{table}" ({collist}) VALUES ({placeholders})'

    with _get_pool().connection() as conn:
        with conn.transaction():            # atomic: delete + insert together
            with conn.cursor() as cur:
                if is_global:
                    cur.execute(f'DELETE FROM "{table}"')
                else:
                    cur.execute(f'DELETE FROM "{table}" WHERE user_key = %s', (user_key,))
                if not out.empty:
                    data = [
                        tuple("" if out.iloc[i][c] is None else str(out.iloc[i][c])
                              for c in write_cols)
                        for i in range(len(out))
                    ]
                    cur.executemany(insert_sql, data)
    return True


def ping():
    """Quick connectivity check — returns True if a trivial query succeeds."""
    try:
        with _get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone()[0] == 1
    except Exception:
        return False
