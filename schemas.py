"""Excel column definitions for Plan My Day (first cut).

Kept lean: only the sheets the core daily loop needs. The full spec has more,
but this is the runnable spine — Monthly scorecard -> Start/plan -> Tasks -> Update.
"""

USERS = [
    "user_key", "name", "role", "department",
    "login_role", "password", "active", "created_at",
]

# One row per KPI per user per month. achieved_mtd is editable on the Monthly page.
MONTHLY_TARGETS = [
    "month", "user_key", "role", "kpi_name",
    "monthly_target", "achieved_mtd", "target_unit", "priority",
    "created_at", "updated_at",
]

# The "North Star" activities — what the user committed to do for each KPI.
MONTHLY_PLAN = [
    "month", "user_key", "role", "activity_id", "activity",
    "impact_category",      # Direct / Indirect
    "linked_kpi", "daily_minimum_action", "success_metric",
    "status", "created_at", "updated_at",
]

# The heart of the agenda lives here: horizon + goal_aligned + alignment_note.
TASKS = [
    "task_id", "plan_date", "user_key", "title", "category",
    "priority",             # P1..P5
    "horizon",              # Today (delivers now) / Build (prepares tomorrow)
    "goal_aligned",         # Yes / Build / No
    "alignment_note",       # the gentle nudge text, shown on the card
    "linked_kpi",
    "day_goal",             # heading of the daily target this serves ("" = no goal)
    "steps_json",           # JSON list of {"text":..., "done":bool}
    "carried_from",         # original plan_date if this rolled over ("" = fresh today)
    "meeting_id",           # back-link if this task is a follow-up from a meeting
    "followup_for",         # identity (mobile/code/name) this follow-up concerns
    "coach_cue",            # the last companion nudge saved on this task
    "reviewed",             # "Yes" once the end-of-day ritual graded it
    "collaborators",        # JSON list of team member names sharing this task
    "expected_output", "success_metric",
    "status",               # Open / In Progress / Blocked / Done / Dropped
    "due_time",             # HH:MM the task/follow-up is due (")" = no specific time)
    "last_buzz_at",         # timestamp the buzzer last fired
    "last_update_at",       # timestamp the user last acted (remark) — the "acted" signal
    "source",               # ai / manual / carry_forward
    "notes", "raw_input",
    "created_at", "updated_at", "done_at",
]

# Daily targets — the 4 boxes at the top of the Today page. The gate: at least
# one of these must exist before dictate unlocks and tasks can be added.
DAY_GOALS = [
    "date", "user_key", "slot",       # slot 1..4
    "heading",                        # max 2 words
    "target_number",
    "created_at", "updated_at",
]

DAY_UPDATES = [
    "date", "user_key", "update_time",
    "completed_tasks", "pending_tasks", "blocked_tasks",
    "numbers_update", "what_worked", "what_did_not_work",
    "remarks", "created_at",
]

# Immutable task history — one row appended per event (never edited/deleted).
TASK_LOG = [
    "ts", "date", "user_key", "task_id", "title", "day_goal",
    "event",            # created / done / carried / deleted / steps_added
    "detail",
]

# Meeting / daily-log records. identity_type + identity_value keep the handle
# unambiguous (a raw mobile vs a partner code vs a name).
MEETINGS = [
    "meeting_id", "date", "user_key",
    "meeting_type",        # new_partner / existing_partner / client / internal
    "partner_name",        # the partner/person's name (label)
    "identity_type",       # mobile / partner_code / client_code / name
    "identity_value",
    "partner_identity",    # canonical phone:/code:/name: tag for cross-linking
    "discussed", "outcome", "objections", "pipeline_stage",
    "next_action", "next_date",
    "ai_written",          # the AI-structured summary text
    "raw_dictation",       # the original words, never discarded
    "followup_task_id",    # set once a follow-up is scheduled from this meeting
    "created_at", "updated_at",
]

# Partners the acquisition manager works (the recipient list for reminders).
PARTNERS = [
    "partner_id", "user_key", "name", "mobile",
    "contact_type",        # partner / team
    "role",                # free text (dept / designation)
    "code", "code_type",   # code_type: partner_code / client_code / ""
    "salutation",          # Sir / Mam / "" — used to address as "Name Sir" / "Name Mam"
    "notes", "created_at", "updated_at",
]

# Recurring message reminders. recipients = JSON list of partner_ids.
MSG_SCHEDULES = [
    "schedule_id", "user_key", "label", "message",
    "recipients",          # JSON list of partner_id
    "recurrence",          # once / daily / weekly
    "run_time", "run_date", "weekday",   # weekday 0-6 for weekly
    "active", "last_run_date", "created_at", "updated_at",
]

# Prepared message instances produced when a schedule fires (recorded, not sent).
MSG_OUTBOX = [
    "msg_id", "user_key", "schedule_id", "date",
    "recipient_name", "recipient_mobile", "message",
    "status",              # due / done / skipped
    "created_at",
]


# Date-wise planned vs achieved per KPI (the running record under each Monthly block).
MONTHLY_PROGRESS = [
    "date", "user_key", "month", "kpi_name",
    "planned", "achieved",
    "created_at", "updated_at",
]

# Shared team roster (lead-managed). member_type: team / partner.
TEAM_ROSTER = [
    "member_id", "name", "mobile", "member_type", "department", "created_at",
]

# Outcome ledger — the end-of-day evidence: did the user try the cue, and did it work.
OUTCOMES = [
    "outcome_id", "date", "user_key", "task_id", "task_title",
    "topic",               # normalized goal/category the task belongs to
    "cue",                 # the suggestion that was given
    "tried",               # Yes / No
    "result",              # success / partial / failure / ""
    "note", "created_at",
]

# Proven-rules store — distilled wins, promoted from successes. The companion
# leads with these next time the same topic comes up.
COACH_RULES = [
    "rule_id", "user_key", "role", "topic",
    "rule_text",
    "successes",           # count of confirmed successes
    "status",              # candidate (1 success) / tested (>=2)
    "last_used", "created_at", "updated_at",
]

# Dictated daily logs — raw, dated journal entries (voice -> transcript).
DAILY_LOGS = ["log_id", "date", "user_key", "partner_name", "partner_identity",
              "transcript", "created_at"]

# Learnings extracted from logs — go through a pending tray + contradiction gate before
# they're accepted and allowed to feed nudges.
LEARNINGS = [
    "learning_id", "date", "user_key", "source_log_id", "topic", "text",
    "status",          # pending / accepted / rejected / superseded
    "conflict_with",   # learning_id of an accepted learning this contradicts (if any)
    "note", "created_at", "decided_at",
]

# Task update remarks — the "act-to-stop" log. Adding one (timestamp >= due) silences
# the buzzer for that task.
TASK_UPDATES = ["update_id", "task_id", "user_key", "remark", "created_at"]

# Daily Status Report archive — the DSR's text content, one row per day. Syncs to Sheets
# via the normal backup, so the report is preserved in the cloud (silently).
DSR_LOG = ["date", "user_key", "report_text", "created_at"]

# Days the user has explicitly closed (via Close My Day → download report). Drives the
# "close your previous day first" gate.
CLOSED_DAYS = ["date", "user_key", "closed_at"]

# Steps the USER wrote/edited for a task — remembered and reused for similar future tasks.
STEP_TEMPLATES = ["template_id", "user_key", "topic", "task_title", "steps_json",
                  "source", "updated_at"]

# How many nudge popups have been shown to the user on a given day (cap = 4/day).
POPUP_COUNTS = ["date", "user_key", "count"]
