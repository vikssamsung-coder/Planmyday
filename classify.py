"""
classify.py — sort each task into (effort type x KRA) for the "Where My Energy Goes" matrix.

All pure Python rules: free, instant, transparent, computed on the fly (no new table, no
AI cost). Tunable — edit the keyword lists below as the team's language evolves.

  * Effort type (the ROWS): what KIND of work the task is.
  * KRA (the COLUMNS): which outcome the task serves — matched against the user's actual
    monthly-target KPI names, plus a standing "Self-Improvement" column and "Unassigned".
"""

import re

# The effort-type rows, in display order.
EFFORT_ROWS = [
    "Followups",
    "Self Business",
    "Research / Analysis",
    "Meetings / Discussion",
    "Planning / Prep",
    "Execution / Admin",
]

LEARNING_KRA = "Self-Improvement"
UNASSIGNED = "Unassigned"

# Keyword rules for effort type. Checked in order; first match wins. Anything unmatched
# falls through to "Execution / Admin" (the do-the-work bucket).
_EFFORT_RULES = [
    ("Followups", [
        "follow up", "follow-up", "followup", "chase", "check in", "check-in", "checkin",
        "remind", "nudge", "call back", "callback", "reconnect", "revert", "circle back",
        "ping", "pending with", "awaiting", "chase up",
    ]),
    ("Self Business", [
        "prospect", "pitch", "acquire", "acquisition", "new partner", "onboard", "sign up",
        "sign-up", "signup", "lead gen", "lead-gen", "self source", "self-source", "source new",
        "cold call", "cold-call", "demo to", "convert", "open account", "open demat",
        "new client", "new account", "close deal", "win ", "pipeline", "approach new",
    ]),
    ("Research / Analysis", [
        "research", "analyse", "analyze", "analysis", "study", "review data", "compare",
        "evaluate", "explore", "market", "competitor", "competition", "investigate",
        "benchmark", "deep dive", "deep-dive", "understand the",
    ]),
    ("Meetings / Discussion", [
        "meeting", "meet ", "sync", "discuss", "align", "call with", "catch up", "catch-up",
        "1:1", "one on one", "one-on-one", "huddle", "standup", "stand-up", "review with",
        "present to", "presentation", "conference", "session with", "connect with team",
    ]),
    ("Planning / Prep", [
        "plan", "prepare", "prep ", "set goal", "set target", "schedule", "organize",
        "organise", "roadmap", "strategy", "strategize", "outline", "define ", "draft ",
        "structure", "design the", "set up", "set-up",
    ]),
]

# Explicit self-learning signals -> the Self-Improvement KRA (column), regardless of effort.
_SELF_LEARNING = [
    "learn ", "learning", "upskill", "up-skill", "course", "certification", "certify",
    "self improve", "self-improve", "improve my", "my skill", "read book", "read a book",
    "training myself", "self study", "self-study", "practice my", "develop my",
]


def _effort_text(task):
    # what KIND of work this is — judge from the task itself, NOT the KRA it serves
    # (including linked_kpi/day_goal here would let a KPI name like "New Partner" leak in).
    return " ".join(str(task.get(f, "") or "") for f in ("title", "category", "notes")).lower()


def _kra_text(task):
    return " ".join(str(task.get(f, "") or "") for f in
                     ("title", "day_goal", "linked_kpi", "category")).lower()


def effort_type(task):
    """Return one effort-type row for a task."""
    # strong structural signals first
    if str(task.get("followup_for", "")).strip():
        return "Followups"
    cat = str(task.get("category", "")).strip().lower()
    if cat in ("follow-up", "followup", "follow up"):
        return "Followups"
    if str(task.get("meeting_id", "")).strip():
        # a task spawned from a meeting is usually a follow-up action
        return "Followups"

    t = _effort_text(task)
    for label, kws in _EFFORT_RULES:
        if any(kw in t for kw in kws):
            return label
    return "Execution / Admin"


def _match_kpi(text, kpi_names):
    """Match a free-text goal/kpi string to one of the user's KPI names."""
    if not text:
        return None
    tl = text.strip().lower()
    for k in kpi_names:
        kl = str(k).strip().lower()
        if kl and (kl == tl or kl in tl or tl in kl):
            return k
    # token-overlap fallback
    tt = set(re.findall(r"[a-z0-9]+", tl))
    best, bestn = None, 0
    for k in kpi_names:
        kt = set(re.findall(r"[a-z0-9]+", str(k).lower()))
        n = len(tt & kt)
        if n > bestn:
            bestn, best = n, k
    return best if bestn > 0 else None


def kra_of(task, kpi_names):
    """Return the KRA (column) a task serves. Priority:
      1. kra_resolved — an explicit assignment from AI-at-close or a manual History override
      2. self-learning keywords -> Self-Improvement
      3. match linked_kpi / day_goal to a KPI name
      4. Unassigned
    """
    resolved = str(task.get("kra_resolved", "") or "").strip()
    if resolved:
        return resolved
    t = _kra_text(task)
    if any(k in t for k in _SELF_LEARNING):
        return LEARNING_KRA
    lk = str(task.get("linked_kpi", "")).strip()
    dg = str(task.get("day_goal", "")).strip()
    return (_match_kpi(lk, kpi_names) or _match_kpi(dg, kpi_names) or UNASSIGNED)


def unassigned_tasks(tasks_df, kpi_names):
    """Tasks that have no KRA yet (would land in 'Unassigned') — for AI/manual resolution.
    Returns a list of dicts."""
    out = []
    if tasks_df is None or len(tasks_df) == 0:
        return out
    for _, t in tasks_df.iterrows():
        if kra_of(t, kpi_names) == UNASSIGNED:
            out.append(t.to_dict())
    return out


def build_matrix(tasks_df, kpi_names):
    """Return (rows, cols, counts, row_tot, col_tot, grand) for the matrix.
    counts is a dict[row][col] = int."""
    cols = list(dict.fromkeys([str(k) for k in kpi_names if str(k).strip()]))
    for special in (LEARNING_KRA, UNASSIGNED):
        if special not in cols:
            cols.append(special)
    rows = list(EFFORT_ROWS)
    counts = {r: {c: 0 for c in cols} for r in rows}
    if tasks_df is not None and len(tasks_df):
        for _, t in tasks_df.iterrows():
            r = effort_type(t)
            c = kra_of(t, kpi_names)
            if c not in counts[r]:
                c = UNASSIGNED
            counts[r][c] += 1
    row_tot = {r: sum(counts[r].values()) for r in rows}
    col_tot = {c: sum(counts[r][c] for r in rows) for c in cols}
    grand = sum(row_tot.values())
    return rows, cols, counts, row_tot, col_tot, grand
