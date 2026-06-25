"""
master_ingest.py — reference reader for the Master app.

Point it at a folder where you've saved the emailed report workbooks
(PlanMyDay_<user>_<date>.xlsx). It validates each against the contract,
dedupes by (user_key, report_date) keeping the freshest, and returns tidy
combined DataFrames you can build a dashboard on.

This is deliberately small and dependency-light (pandas only) so it drops
straight into your Master app.

    from master_ingest import ingest_folder
    data = ingest_folder("~/PlanMyDay_Inbox")
    data["scorecard"]   # every user's latest KPI standing
    data["tasks"]       # all tasks across the team, with alignment tags
    data["meta"]        # who reported, when, for which date
"""

import os
import glob
import pandas as pd

REPORT_VERSION_SUPPORTED = {"1.0"}
REQUIRED_SHEETS = {"meta", "scorecard", "tasks", "day_update"}


def _read_one(path):
    """Read & validate a single report. Returns dict of frames or raises."""
    try:
        sheets = pd.read_excel(path, sheet_name=None, dtype=str)
    except Exception as e:
        raise ValueError(f"unreadable workbook: {e}")

    missing = REQUIRED_SHEETS - set(sheets.keys())
    if missing:
        raise ValueError(f"missing sheets {missing}")

    meta = sheets["meta"]
    if meta.empty:
        raise ValueError("empty meta sheet")
    ver = str(meta.iloc[0].get("report_version", "")).strip()
    if ver not in REPORT_VERSION_SUPPORTED:
        raise ValueError(f"unsupported report_version {ver!r}")

    # Trust the meta sheet over the filename for identity.
    m = meta.iloc[0]
    ident = {"user_key": str(m["user_key"]).strip(),
             "report_date": str(m["report_date"]).strip(),
             "name": str(m.get("name", "")).strip(),
             "role": str(m.get("role", "")).strip(),
             "generated_at": str(m.get("generated_at", "")).strip(),
             "source_file": os.path.basename(path)}
    return ident, sheets


def ingest_folder(folder):
    """Read every PlanMyDay_*.xlsx in folder. Returns combined frames + skipped list."""
    folder = os.path.expanduser(folder)
    paths = sorted(glob.glob(os.path.join(folder, "PlanMyDay_*.xlsx")))

    chosen = {}          # (user, date) -> (generated_at, ident, sheets)
    skipped = []
    for p in paths:
        try:
            ident, sheets = _read_one(p)
        except ValueError as e:
            skipped.append((os.path.basename(p), str(e)))
            continue
        key = (ident["user_key"], ident["report_date"])
        prev = chosen.get(key)
        if prev is None or ident["generated_at"] > prev[0]:
            chosen[key] = (ident["generated_at"], ident, sheets)

    meta_rows, sc_rows, task_rows, upd_rows = [], [], [], []
    for _, ident, sheets in chosen.values():
        tag = {"user_key": ident["user_key"], "name": ident["name"],
               "role": ident["role"], "report_date": ident["report_date"]}
        meta_rows.append({**tag, "generated_at": ident["generated_at"],
                          "source_file": ident["source_file"]})
        for df, bucket in [(sheets["scorecard"], sc_rows),
                           (sheets["tasks"], task_rows),
                           (sheets["day_update"], upd_rows)]:
            for rec in df.to_dict("records"):
                bucket.append({**tag, **rec})

    return {
        "meta": pd.DataFrame(meta_rows),
        "scorecard": pd.DataFrame(sc_rows),
        "tasks": pd.DataFrame(task_rows),
        "day_update": pd.DataFrame(upd_rows),
        "skipped": skipped,
    }


if __name__ == "__main__":
    import sys
    data = ingest_folder(sys.argv[1] if len(sys.argv) > 1 else ".")
    print(f"users reporting : {data['meta']['user_key'].nunique() if not data['meta'].empty else 0}")
    print(f"reports ingested: {len(data['meta'])}")
    print(f"tasks total     : {len(data['tasks'])}")
    if data["skipped"]:
        print("skipped:")
        for f, why in data["skipped"]:
            print(f"  - {f}: {why}")
