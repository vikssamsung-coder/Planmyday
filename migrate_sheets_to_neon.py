"""
migrate_sheets_to_neon.py — migrate your ACTUAL Google Sheet into Neon Postgres.

Why a second migration script:
  Your live Google Sheet stores each table as its own tab, named:
     users_master                 (the roster — a BARE name, no prefix)
     <user>__<table>              (e.g. rinku__tasks, ketki__meetings, savita__day_goals)
  The generic migrate_to_neon.py reads through storage.py, which prefixes the users tab
  as "_common__users_master" — that tab doesn't exist in your sheet, so it found nothing.
  This script reads the tabs by their REAL names and discovers every user from the tab
  names directly (so users present only as data tabs — e.g. ketki, savita — are included,
  even if they aren't in users_master).

USAGE (on your Mac, with the working secrets file in place):
    cd "/Users/viks/Documents/Plan My Day"
    export NEON_DATABASE_URL=$(python3 -c "import tomllib;print(tomllib.load(open('.streamlit/secrets.toml','rb'))['NEON_DATABASE_URL'])")
    python3 migrate_sheets_to_neon.py

Safe to re-run: each user's rows in a table are replaced, never duplicated.
"""

import sys
import pandas as pd

import gsheets
import db
import schemas
import storage

# Sheet table-name -> (Neon write tab, schema columns). Most are 1:1; the roster tab is
# named users_master and maps (inside db.write_table) to the Neon `users` table.
SCHEMA_COLS = {
    "users_master":     schemas.USERS,
    "team_roster":      schemas.TEAM_ROSTER,
    "tasks":            schemas.TASKS,
    "monthly_targets":  schemas.MONTHLY_TARGETS,
    "monthly_plan":     schemas.MONTHLY_PLAN,
    "day_goals":        schemas.DAY_GOALS,
    "day_updates":      schemas.DAY_UPDATES,
    "task_log":         schemas.TASK_LOG,
    "meetings":         schemas.MEETINGS,
    "partners":         schemas.PARTNERS,
    "msg_schedules":    schemas.MSG_SCHEDULES,
    "msg_outbox":       schemas.MSG_OUTBOX,
    "monthly_progress": schemas.MONTHLY_PROGRESS,
    "outcomes":         schemas.OUTCOMES,
    "coach_rules":      schemas.COACH_RULES,
    "daily_logs":       schemas.DAILY_LOGS,
    "learnings":        schemas.LEARNINGS,
    "task_updates":     schemas.TASK_UPDATES,
    "dsr_log":          schemas.DSR_LOG,
    "closed_days":      schemas.CLOSED_DAYS,
    "step_templates":   schemas.STEP_TEMPLATES,
    "popup_counts":     schemas.POPUP_COUNTS,
    "mis_brief":        storage._MIS_BRIEF_COLS,
    "mis_snapshots":    storage._MIS_SNAP_COLS,
    "learnings_digest": schemas.LEARNINGS_DIGEST,
}

# Bare (non-prefixed) tabs that are global roster/shared tables.
GLOBAL_BARE = {"users_master", "team_roster"}


def _read_tab_raw(book, tabname):
    """Read a worksheet by its EXACT name into a DataFrame (header row = columns)."""
    import gspread
    try:
        ws = book.worksheet(tabname)
    except gspread.WorksheetNotFound:
        return None
    vals = ws.get_all_values()
    if not vals:
        return pd.DataFrame()
    header = vals[0]
    width = len(header)
    rows = [r + [""] * (width - len(r)) for r in vals[1:]]
    return pd.DataFrame(rows, columns=header)


def main():
    if not db.enabled():
        print("ERROR: Neon not configured (NEON_DATABASE_URL missing or psycopg absent).")
        sys.exit(1)
    if not gsheets.enabled():
        print("ERROR: Google Sheets not configured/authenticating. Fix the secrets first.")
        sys.exit(1)

    # make sure the schema exists (idempotent)
    try:
        db.init_schema()
        print("Schema ensured in Neon.\n")
    except Exception as e:
        print(f"(Could not auto-create schema: {e}; assuming it already exists.)\n")

    book = gsheets._book()
    tabs = [ws.title for ws in book.worksheets()]
    print(f"Spreadsheet: {book.title}  |  {len(tabs)} tabs\n")

    # discover users from the {user}__{table} tab names
    users = sorted({t.split("__", 1)[0] for t in tabs if "__" in t})
    print(f"Users discovered from tabs: {users}\n")

    grand = 0
    skipped_tabs = []

    # 1) global roster tabs (bare names)
    for bare in ("users_master", "team_roster"):
        if bare in tabs:
            df = _read_tab_raw(book, bare)
            n = 0 if df is None or df.empty else len(df)
            if n:
                db.write_table(bare, "", df, SCHEMA_COLS[bare])
            print(f"  [global] {bare:18} {n:5d} rows")
            grand += n

    # 2) per-user data tabs: {user}__{table}
    print()
    per_table_totals = {}
    for t in sorted(tabs):
        if "__" not in t:
            continue
        user, table = t.split("__", 1)
        if table not in SCHEMA_COLS:
            skipped_tabs.append(t)
            continue
        df = _read_tab_raw(book, t)
        if df is None or df.empty:
            continue
        db.write_table(table, user, df, SCHEMA_COLS[table])
        per_table_totals[table] = per_table_totals.get(table, 0) + len(df)
        grand += len(df)
        print(f"  [{user}] {table:18} {len(df):5d} rows")

    print(f"\nDONE — {grand} rows migrated into Neon.")
    if skipped_tabs:
        print(f"Skipped {len(skipped_tabs)} unrecognised tab(s): {skipped_tabs}")
    print("Re-running is safe (replaces each user's rows, never duplicates).")

    # quick verification read-back
    try:
        import db as _db
        with _db._get_pool().connection() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM users")
            nu = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM tasks")
            nt = cur.fetchone()[0]
        print(f"\nVerify in Neon now: users={nu}, tasks={nt}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
