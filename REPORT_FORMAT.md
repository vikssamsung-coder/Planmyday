# Plan My Day — Daily Report Format (the Master-app contract)

**Version 1.0** · This document defines the workbook the User app produces and
the Master app consumes. Treat the sheet names and column order as an API.
When you change them, bump `report_version` in `report.py` and add the new
version to `REPORT_VERSION_SUPPORTED` in `master_ingest.py`.

---

## The flow

```
User app  ──downloads──▶  PlanMyDay_<user>_<date>.xlsx  ──email──▶  Master inbox folder
                                                                        │
                                                            master_ingest.ingest_folder()
                                                                        │
                                                                   your dashboard
```

No OneDrive write, no Graph, no share links. The email is the sync layer. The
User app can run anywhere (local or Streamlit Cloud) because the browser handles
the download.

## Filename

```
PlanMyDay_<user_key>_<YYYY-MM-DD>.xlsx
```

The Master reader globs `PlanMyDay_*.xlsx`. It does **not** trust the filename for
identity — it reads `user_key` and `report_date` from the `meta` sheet, so a
renamed file still ingests correctly. The filename is only a convenience filter.

## Sheets

Every report has five sheets. `Summary` is for the human who emails it; the other
four carry data with stable columns and **plain values (never formulas)** so
pandas reads them directly.

### `Summary` (human-facing — not parsed)
Formatted scorecard, a "today at a glance" block, and what-worked / what-didn't.
The Master app ignores this sheet; it exists so the person is comfortable sending
the file.

### `meta` (one row — the identity header)
| column | meaning |
|---|---|
| `report_version` | contract version, e.g. `1.0` |
| `app_version` | producing app version |
| `user_key` | stable user id — the join key |
| `name`, `role`, `department` | who |
| `report_date` | the day this report covers (YYYY-MM-DD) |
| `generated_at` | ISO timestamp — used to pick the freshest duplicate |
| `month` | YYYY-MM |

### `scorecard` (one row per KPI — snapshot as of `report_date`)
`kpi_name`, `monthly_target`, `achieved_mtd`, `target_unit`, `achievement_pct`,
`expected_pct`, `gap`, `required_run_rate`, `current_run_rate`,
`remaining_working_days`, `status`, `priority`

`status` ∈ {Ahead, On Track, Behind, Critical, No Target}.

### `tasks` (one row per task planned that day)
`task_id`, `title`, `category`, `priority`, `horizon`, `goal_aligned`,
`alignment_note`, `linked_kpi`, `expected_output`, `success_metric`, `status`,
`done_at`, `notes`

- `horizon` ∈ {Today, Build} — delivers now vs. prepares tomorrow.
- `goal_aligned` ∈ {Yes, Build, No} — **No** means the task was kept but flagged
  as not moving a goal (the team plans freely; nothing is blocked).
- `alignment_note` — the nudge text shown to the user.

### `day_update` (one row, may be empty)
`completed_tasks`, `pending_tasks`, `blocked_tasks`, `numbers_update`,
`what_worked`, `what_did_not_work`, `remarks`

## How the Master reader behaves

`ingest_folder(folder)` returns a dict of combined DataFrames — `meta`,
`scorecard`, `tasks`, `day_update` — each tagged with `user_key`, `name`, `role`,
`report_date`, plus a `skipped` list of `(filename, reason)` for files that
failed validation. It:

1. validates required sheets and a supported `report_version`,
2. dedupes by `(user_key, report_date)`, keeping the newest `generated_at`,
3. tags every data row with who/when so the frames are dashboard-ready.

## Dashboard ideas this contract already supports

- **Team scorecard** — latest `scorecard` row per user → who's Critical / Behind.
- **Goal-alignment heat** — share of `tasks` with `goal_aligned == "No"` per
  person → who's drifting from their numbers.
- **Today vs Build balance** — `horizon` split per user → all-delivery or
  all-prep days.
- **Completion** — `status == "Done"` over planned → execution rate.
- **What worked / didn't** — `day_update` text → a daily learnings feed.

## When you outgrow email

The contract doesn't care how files arrive. Swap the email step for a shared
folder, a Graph pull, or an upload endpoint later — as long as the files land in
a folder, `ingest_folder` is unchanged.
