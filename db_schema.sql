-- ============================================================================
--  Plan My Day — Postgres schema (Neon).  Auto-generated from schemas.py.
--  One shared schema; user_key on every per-user table (team_roster is global).
--  All value columns TEXT (matches how the app treats them) so storage.py is
--  unchanged. Composite PKs (user_key, id) where the id is only unique per user.
--  Idempotent: safe to run repeatedly.
-- ============================================================================

CREATE TABLE IF NOT EXISTS users (
    user_key TEXT,
    name TEXT,
    role TEXT,
    department TEXT,
    login_role TEXT,
    password TEXT,
    active TEXT,
    created_at TEXT,
    PRIMARY KEY (user_key)
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT;

CREATE TABLE IF NOT EXISTS dump_types (
    key TEXT,
    name TEXT,
    max_files INTEGER,
    handler TEXT,
    active TEXT,
    sort_order INTEGER,
    updated_at TEXT,
    PRIMARY KEY (key)
);

CREATE TABLE IF NOT EXISTS mis_types (
    key TEXT,
    name TEXT,
    params_hint TEXT,
    handler TEXT,
    active TEXT,
    sort_order INTEGER,
    updated_at TEXT,
    PRIMARY KEY (key)
);

CREATE TABLE IF NOT EXISTS report_requests (
    req_id TEXT,
    user_key TEXT,
    requester_email TEXT,
    report_key TEXT,
    report_name TEXT,
    params TEXT,
    source TEXT,
    status TEXT,
    created_at TEXT,
    PRIMARY KEY (req_id)
);

CREATE TABLE IF NOT EXISTS mis_reports (
    report_key TEXT,
    mis_key TEXT,
    name TEXT,
    description TEXT,
    source_url TEXT,
    file_name TEXT,
    active TEXT,
    sort_order INTEGER,
    source_modified_at TEXT,
    last_checked_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (report_key)
);

CREATE TABLE IF NOT EXISTS mis_report_access (
    report_key TEXT,
    principal_type TEXT,
    principal TEXT,
    PRIMARY KEY (report_key, principal_type, principal)
);

CREATE TABLE IF NOT EXISTS team_roster (
    member_id TEXT,
    name TEXT,
    mobile TEXT,
    member_type TEXT,
    department TEXT,
    created_at TEXT,
    PRIMARY KEY (member_id)
);

CREATE TABLE IF NOT EXISTS monthly_targets (
    month TEXT,
    user_key TEXT,
    role TEXT,
    kpi_name TEXT,
    monthly_target TEXT,
    achieved_mtd TEXT,
    target_unit TEXT,
    priority TEXT,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (month, user_key, kpi_name)
);
CREATE INDEX IF NOT EXISTS ix_monthly_targets_user_key_month ON monthly_targets (user_key, month);

CREATE TABLE IF NOT EXISTS monthly_plan (
    month TEXT,
    user_key TEXT,
    role TEXT,
    activity_id TEXT,
    activity TEXT,
    impact_category TEXT,
    linked_kpi TEXT,
    daily_minimum_action TEXT,
    success_metric TEXT,
    status TEXT,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (user_key, activity_id)
);
CREATE INDEX IF NOT EXISTS ix_monthly_plan_user_key_month ON monthly_plan (user_key, month);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT,
    plan_date TEXT,
    user_key TEXT,
    title TEXT,
    category TEXT,
    priority TEXT,
    horizon TEXT,
    goal_aligned TEXT,
    alignment_note TEXT,
    linked_kpi TEXT,
    day_goal TEXT,
    steps_json TEXT,
    carried_from TEXT,
    meeting_id TEXT,
    followup_for TEXT,
    coach_cue TEXT,
    reviewed TEXT,
    collaborators TEXT,
    expected_output TEXT,
    success_metric TEXT,
    status TEXT,
    due_time TEXT,
    last_buzz_at TEXT,
    last_update_at TEXT,
    source TEXT,
    notes TEXT,
    raw_input TEXT,
    created_at TEXT,
    updated_at TEXT,
    done_at TEXT,
    kra_resolved TEXT,
    PRIMARY KEY (user_key, task_id)
);
CREATE INDEX IF NOT EXISTS ix_tasks_user_key_plan_date ON tasks (user_key, plan_date);
CREATE INDEX IF NOT EXISTS ix_tasks_user_key_status ON tasks (user_key, status);
CREATE INDEX IF NOT EXISTS ix_tasks_meeting_id ON tasks (meeting_id);

CREATE TABLE IF NOT EXISTS day_goals (
    date TEXT,
    user_key TEXT,
    slot TEXT,
    heading TEXT,
    target_number TEXT,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (date, user_key, slot)
);
CREATE INDEX IF NOT EXISTS ix_day_goals_user_key_date ON day_goals (user_key, date);

CREATE TABLE IF NOT EXISTS day_updates (
    id BIGSERIAL PRIMARY KEY,
    date TEXT,
    user_key TEXT,
    update_time TEXT,
    completed_tasks TEXT,
    pending_tasks TEXT,
    blocked_tasks TEXT,
    numbers_update TEXT,
    what_worked TEXT,
    what_did_not_work TEXT,
    remarks TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_day_updates_user_key_date ON day_updates (user_key, date);

CREATE TABLE IF NOT EXISTS task_log (
    id BIGSERIAL PRIMARY KEY,
    ts TEXT,
    date TEXT,
    user_key TEXT,
    task_id TEXT,
    title TEXT,
    day_goal TEXT,
    event TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_task_log_user_key_date ON task_log (user_key, date);
CREATE INDEX IF NOT EXISTS ix_task_log_task_id ON task_log (task_id);

CREATE TABLE IF NOT EXISTS meetings (
    meeting_id TEXT,
    date TEXT,
    user_key TEXT,
    meeting_type TEXT,
    partner_name TEXT,
    identity_type TEXT,
    identity_value TEXT,
    partner_identity TEXT,
    discussed TEXT,
    outcome TEXT,
    objections TEXT,
    pipeline_stage TEXT,
    next_action TEXT,
    next_date TEXT,
    ai_written TEXT,
    raw_dictation TEXT,
    followup_task_id TEXT,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (user_key, meeting_id)
);
CREATE INDEX IF NOT EXISTS ix_meetings_user_key_date ON meetings (user_key, date);

CREATE TABLE IF NOT EXISTS partners (
    partner_id TEXT,
    user_key TEXT,
    name TEXT,
    mobile TEXT,
    contact_type TEXT,
    role TEXT,
    code TEXT,
    code_type TEXT,
    salutation TEXT,
    notes TEXT,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (user_key, partner_id)
);
CREATE INDEX IF NOT EXISTS ix_partners_user_key ON partners (user_key);

CREATE TABLE IF NOT EXISTS msg_schedules (
    schedule_id TEXT,
    user_key TEXT,
    label TEXT,
    message TEXT,
    recipients TEXT,
    recurrence TEXT,
    run_time TEXT,
    run_date TEXT,
    weekday TEXT,
    active TEXT,
    last_run_date TEXT,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (user_key, schedule_id)
);
CREATE INDEX IF NOT EXISTS ix_msg_schedules_user_key ON msg_schedules (user_key);

CREATE TABLE IF NOT EXISTS msg_outbox (
    msg_id TEXT,
    user_key TEXT,
    schedule_id TEXT,
    date TEXT,
    recipient_name TEXT,
    recipient_mobile TEXT,
    message TEXT,
    status TEXT,
    created_at TEXT,
    PRIMARY KEY (user_key, msg_id)
);
CREATE INDEX IF NOT EXISTS ix_msg_outbox_user_key_date ON msg_outbox (user_key, date);

CREATE TABLE IF NOT EXISTS monthly_progress (
    date TEXT,
    user_key TEXT,
    month TEXT,
    kpi_name TEXT,
    planned TEXT,
    achieved TEXT,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (date, user_key, kpi_name)
);
CREATE INDEX IF NOT EXISTS ix_monthly_progress_user_key_month ON monthly_progress (user_key, month);

CREATE TABLE IF NOT EXISTS login_log (
    user_key TEXT,
    day TEXT,
    last_at TEXT,
    count TEXT,
    PRIMARY KEY (user_key, day)
);
CREATE INDEX IF NOT EXISTS ix_login_log_user_key ON login_log (user_key);

CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id TEXT,
    date TEXT,
    user_key TEXT,
    task_id TEXT,
    task_title TEXT,
    topic TEXT,
    cue TEXT,
    tried TEXT,
    result TEXT,
    note TEXT,
    created_at TEXT,
    PRIMARY KEY (user_key, outcome_id)
);
CREATE INDEX IF NOT EXISTS ix_outcomes_user_key_date ON outcomes (user_key, date);
CREATE INDEX IF NOT EXISTS ix_outcomes_user_key_topic ON outcomes (user_key, topic);

CREATE TABLE IF NOT EXISTS coach_rules (
    rule_id TEXT,
    user_key TEXT,
    role TEXT,
    topic TEXT,
    rule_text TEXT,
    successes TEXT,
    status TEXT,
    last_used TEXT,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (user_key, rule_id)
);
CREATE INDEX IF NOT EXISTS ix_coach_rules_user_key_topic ON coach_rules (user_key, topic);

CREATE TABLE IF NOT EXISTS daily_logs (
    log_id TEXT,
    date TEXT,
    user_key TEXT,
    partner_name TEXT,
    partner_identity TEXT,
    transcript TEXT,
    created_at TEXT,
    PRIMARY KEY (user_key, log_id)
);
CREATE INDEX IF NOT EXISTS ix_daily_logs_user_key_date ON daily_logs (user_key, date);

CREATE TABLE IF NOT EXISTS learnings (
    learning_id TEXT,
    date TEXT,
    user_key TEXT,
    source_log_id TEXT,
    topic TEXT,
    text TEXT,
    status TEXT,
    conflict_with TEXT,
    note TEXT,
    created_at TEXT,
    decided_at TEXT,
    PRIMARY KEY (user_key, learning_id)
);
CREATE INDEX IF NOT EXISTS ix_learnings_user_key_status ON learnings (user_key, status);

CREATE TABLE IF NOT EXISTS task_updates (
    update_id TEXT,
    task_id TEXT,
    user_key TEXT,
    remark TEXT,
    created_at TEXT,
    PRIMARY KEY (user_key, update_id)
);
CREATE INDEX IF NOT EXISTS ix_task_updates_task_id ON task_updates (task_id);

CREATE TABLE IF NOT EXISTS dsr_log (
    date TEXT,
    user_key TEXT,
    report_text TEXT,
    created_at TEXT,
    PRIMARY KEY (date, user_key)
);

CREATE TABLE IF NOT EXISTS closed_days (
    date TEXT,
    user_key TEXT,
    closed_at TEXT,
    PRIMARY KEY (date, user_key)
);

CREATE TABLE IF NOT EXISTS ai_usage (
    day TEXT,
    model TEXT,
    calls TEXT,
    in_tokens TEXT,
    out_tokens TEXT,
    cost TEXT,
    user_key TEXT,
    updated_at TEXT,
    PRIMARY KEY (user_key, day, model)
);

CREATE TABLE IF NOT EXISTS content (
    content_id TEXT PRIMARY KEY,
    type TEXT,
    title TEXT,
    body TEXT,
    media_url TEXT,
    media_kind TEXT,
    target TEXT,
    status TEXT,
    priority TEXT,
    publish_at TEXT,
    expires_at TEXT,
    created_by TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS step_templates (
    template_id TEXT,
    user_key TEXT,
    topic TEXT,
    task_title TEXT,
    steps_json TEXT,
    source TEXT,
    updated_at TEXT,
    PRIMARY KEY (user_key, template_id)
);
CREATE INDEX IF NOT EXISTS ix_step_templates_user_key_topic ON step_templates (user_key, topic);

CREATE TABLE IF NOT EXISTS popup_counts (
    date TEXT,
    user_key TEXT,
    count TEXT,
    PRIMARY KEY (date, user_key)
);

CREATE TABLE IF NOT EXISTS mis_brief (
    date TEXT,
    user_key TEXT,
    brief TEXT,
    behind_csv TEXT,
    slipped_csv TEXT,
    created_at TEXT,
    PRIMARY KEY (date, user_key)
);

CREATE TABLE IF NOT EXISTS mis_snapshots (
    date TEXT,
    user_key TEXT,
    kpi_name TEXT,
    status TEXT,
    achieved TEXT,
    target TEXT,
    created_at TEXT,
    PRIMARY KEY (date, user_key, kpi_name)
);
CREATE INDEX IF NOT EXISTS ix_mis_snapshots_user_key_date ON mis_snapshots (user_key, date);


-- 25. Learnings digest (one distilled brief per user; derived from raw learnings) --------
CREATE TABLE IF NOT EXISTS learnings_digest (
    user_key     TEXT,
    brief        TEXT,
    source_count TEXT,
    updated_at   TEXT,
    PRIMARY KEY (user_key)
);

-- 26. Effort KRAs (a user's own KRA list for the energy matrix; survives MIS overwrites) ---
CREATE TABLE IF NOT EXISTS effort_kras (
    user_key   TEXT,
    kra_name   TEXT,
    sort_order TEXT,
    created_at TEXT,
    PRIMARY KEY (user_key, kra_name)
);
