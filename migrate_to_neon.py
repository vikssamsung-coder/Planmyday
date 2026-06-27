"""
migrate_to_neon.py — move existing Plan My Day data into Neon Postgres.

Stage 4 of the migration. Reads your current data (local Excel workspace, OR Google
Sheets) and loads every row into the matching Postgres table.

It reuses storage.py's OWN readers, so whatever backend currently holds your data
(local files on your Mac, or Sheets on Cloud) is read correctly, and writes go to
Postgres via db.write_table. That means: run this ONCE, pointed at your old data,
with NEON_DATABASE_URL set, and it copies everything across.

USAGE (on your machine, after the schema exists in Neon):

    # 1) make sure the tables exist in Neon (run db_schema.sql in the Neon console once)
    # 2) point it at your OLD data and your Neon URL, then run:

    #   reading LOCAL Excel as the source:
    export NEON_DATABASE_URL="postgresql://...neon..."     # destination
    export PMD_MIGRATE_SOURCE="local"                       # source = local Excel
    export STORAGE_BASE_DIR="/Users/viks/Documents/Sarthi - Plan My Day"
    python migrate_to_neon.py

    #   OR reading Google SHEETS as the source (set up gcp_service_account + SHEET_ID
    #   in secrets/env as usual), then:
    export PMD_MIGRATE_SOURCE="sheets"
    python migrate_to_neon.py

The script is SAFE to re-run: each user's rows are replaced (not duplicated), because
it writes with the same whole-table-per-user semantics the app uses.

It prints a per-table, per-user summary and a final reconciliation count.
"""

import os
import sys

# Force storage to READ from the chosen source while it WRITES to Postgres. We do this
# by temporarily disabling the Postgres backend during reads, then enabling it for writes.
import schemas
import paths
import storage
import db


# -- the full catalogue of stores: (path_fn, columns, scope) -----------------------------
#    scope: "user"  -> path_fn(user_key)        (per-user table)
#           "global"-> path_fn()                (shared table: users, team_roster)
def _catalogue():
    s = storage
    return [
        # global
        (s._users_path,            schemas.USERS,             "global"),
        (s._team_path,             schemas.TEAM_ROSTER,       "global"),
        # per-user
        (s._tasks_path,            schemas.TASKS,             "user"),
        (s._targets_path,          schemas.MONTHLY_TARGETS,   "user"),
        (s._plan_path,             schemas.MONTHLY_PLAN,      "user"),
        (s._day_goals_path,        schemas.DAY_GOALS,         "user"),
        (s._updates_path,          schemas.DAY_UPDATES,       "user"),
        (s._task_log_path,         schemas.TASK_LOG,          "user"),
        (s._meetings_path,         schemas.MEETINGS,          "user"),
        (s._partners_path,         schemas.PARTNERS,          "user"),
        (s._sched_path,            schemas.MSG_SCHEDULES,     "user"),
        (s._outbox_path,           schemas.MSG_OUTBOX,        "user"),
        (s._mprogress_path,        schemas.MONTHLY_PROGRESS,  "user"),
        (s._outcomes_path,         schemas.OUTCOMES,          "user"),
        (s._rules_path,            schemas.COACH_RULES,       "user"),
        (s._daily_logs_path,       schemas.DAILY_LOGS,        "user"),
        (s._learnings_path,        schemas.LEARNINGS,         "user"),
        (s._task_updates_path,     schemas.TASK_UPDATES,      "user"),
        (s._dsr_log_path,          schemas.DSR_LOG,           "user"),
        (s._closed_days_path,      schemas.CLOSED_DAYS,       "user"),
        (s._step_templates_path,   schemas.STEP_TEMPLATES,    "user"),
        (s._popup_counts_path,     schemas.POPUP_COUNTS,      "user"),
        (s._mis_brief_path,        storage._MIS_BRIEF_COLS,   "user"),
        (s._mis_snapshot_path,     storage._MIS_SNAP_COLS,    "user"),
        (s._learnings_digest_path, schemas.LEARNINGS_DIGEST,  "user"),
    ]


def _all_user_keys():
    """Discover every user_key to migrate (from the users table of the SOURCE)."""
    df = _read_source(storage._users_path(), schemas.USERS, "global")
    if df.empty:
        return []
    return [u for u in df["user_key"].tolist() if str(u).strip()]


# -- source reads: temporarily turn the Postgres backend OFF so storage reads the OLD data
_SAVED_USE_PG = None


def _source_mode_on():
    """Disable Postgres so storage._read uses local Excel or Sheets (the source)."""
    global _SAVED_USE_PG
    _SAVED_USE_PG = storage._use_pg
    storage._use_pg = lambda: False


def _source_mode_off():
    """Restore the real _use_pg (Postgres on)."""
    global _SAVED_USE_PG
    if _SAVED_USE_PG is not None:
        storage._use_pg = _SAVED_USE_PG


def _read_source(path, columns, scope):
    _source_mode_on()
    try:
        return storage._read(path, columns)
    finally:
        _source_mode_off()


def main():
    if not db.enabled():
        print("ERROR: NEON_DATABASE_URL is not set (or psycopg missing). "
              "Set the destination Neon URL and re-run.")
        sys.exit(1)

    source = os.environ.get("PMD_MIGRATE_SOURCE", "local").lower()
    print(f"Source = {source!r}  ->  Destination = Neon Postgres")
    print(f"(reading source via storage.py; STORAGE_BASE_DIR={paths.base_dir()})\n")

    # make sure schema exists (idempotent)
    try:
        db.init_schema()
        print("Schema ensured in Neon.\n")
    except Exception as e:
        print(f"Could not auto-create schema ({e}); assuming it already exists.\n")

    users = _all_user_keys()
    print(f"Users found: {len(users)} -> {users}\n")

    grand = 0
    for path_fn, columns, scope in _catalogue():
        table_label = path_fn.__name__.replace("_path", "").lstrip("_")
        if scope == "global":
            df = _read_source(path_fn(), columns, scope)
            n = 0 if df.empty else len(df)
            if n:
                tab = storage._route(path_fn())[1]
                db.write_table(tab, "", df, columns)
            print(f"  [global] {table_label:18} {n:5d} rows")
            grand += n
        else:
            total = 0
            for uk in users:
                df = _read_source(path_fn(uk), columns, scope)
                if df.empty:
                    continue
                tab = storage._route(path_fn(uk))[1]
                db.write_table(tab, uk, df, columns)
                total += len(df)
            print(f"  [user]   {table_label:18} {total:5d} rows")
            grand += total

    print(f"\nDONE — {grand} rows migrated into Neon across {len(_catalogue())} tables.")
    print("Re-running is safe (it replaces each user's rows, never duplicates).")


if __name__ == "__main__":
    main()
