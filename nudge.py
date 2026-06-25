"""The alignment nudge — this project's whole reason for existing.

Principle (our finetuned version): the team plans FREELY. Nothing is blocked.
But every task is classified, and where it doesn't line up with a goal, the app
says so with one gentle line. The nudge is dosed to the gap: louder when behind,
a quiet tag when on track.

Pure rule-based on purpose — alignment must work even with no GPT/API key.
"""

from datetime import date
import calendar


# ---------------------------------------------------------------- scorecard math

def working_days_in_month(year, month):
    total = 0
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        if date(year, month, day).weekday() < 5:  # Mon-Fri
            total += 1
    return total


def working_days_elapsed(year, month, today=None):
    today = today or date.today()
    elapsed = 0
    last = today.day if (today.year == year and today.month == month) else calendar.monthrange(year, month)[1]
    for day in range(1, last + 1):
        if date(year, month, day).weekday() < 5:
            elapsed += 1
    return elapsed


def score_kpi(monthly_target, achieved_mtd, year, month, today=None):
    """Returns the §19 scorecard numbers + a status label."""
    try:
        target = float(monthly_target or 0)
        achieved = float(achieved_mtd or 0)
    except (TypeError, ValueError):
        target, achieved = 0.0, 0.0

    total_wd = working_days_in_month(year, month)
    done_wd = max(working_days_elapsed(year, month, today), 1)
    remaining_wd = max(total_wd - done_wd, 1)

    achievement_pct = (achieved / target * 100) if target else 0.0
    expected_pct = (done_wd / total_wd * 100) if total_wd else 0.0
    gap = max(target - achieved, 0)
    required_run_rate = gap / remaining_wd
    current_run_rate = achieved / done_wd

    # Status = where you are vs where time says you should be.
    if target == 0:
        status = "No Target"
    elif achievement_pct >= expected_pct + 5:
        status = "Ahead"
    elif achievement_pct >= expected_pct - 5:
        status = "On Track"
    elif achievement_pct >= expected_pct - 20:
        status = "Behind"
    else:
        status = "Critical"

    return {
        "kpi_name": "",
        "monthly_target": target,
        "achieved_mtd": achieved,
        "achievement_pct": round(achievement_pct, 1),
        "expected_pct": round(expected_pct, 1),
        "gap": gap,
        "required_run_rate": round(required_run_rate, 1),
        "current_run_rate": round(current_run_rate, 1),
        "remaining_working_days": remaining_wd,
        "status": status,
    }


def worst_status(scorecards):
    """The single most-behind KPI drives how loud the nudge gets."""
    order = {"Critical": 0, "Behind": 1, "On Track": 2, "Ahead": 3, "No Target": 4}
    if not scorecards:
        return None
    return sorted(scorecards, key=lambda s: order.get(s["status"], 5))[0]


# ---------------------------------------------------------------- task alignment

_BUILD_HINTS = ("learn", "webinar", "course", "watch", "read", "study",
                "prepare", "prep", "set up", "setup", "research", "plan ",
                "template", "system", "relationship", "onboard")


def _keyword_link(title, plan_activities):
    """Best-effort link a free-text task to a committed activity/KPI by keywords.

    Used when GPT didn't set linked_kpi (e.g. local fallback mode). Matches the
    task title against each activity's words and its KPI name. Returns the KPI or "".
    """
    t = title.lower()
    best = ""
    for act in plan_activities:
        kpi = (act.get("linked_kpi") or "").strip()
        if not kpi:
            continue
        words = [w for w in (act.get("activity", "") + " " + kpi).lower()
                 .replace("-", " ").split() if len(w) > 3]
        if any(w in t for w in words):
            return kpi
        # weak signal: KPI name token appears
        if any(tok in t for tok in kpi.lower().split() if len(tok) > 3):
            best = best or kpi
    return best


def classify_task(task, plan_kpis, plan_activities=None):
    """Tag a single task. Never blocks — only sets goal_aligned + alignment_note.

    plan_kpis: set of KPI names the user committed to this month.
    plan_activities: optional list of committed-activity dicts for keyword linking.
    Fills goal_aligned / alignment_note / horizon only where not already set
    (GPT may have set them; manual / fallback tasks won't have).
    """
    linked = (task.get("linked_kpi") or "").strip()
    horizon = (task.get("horizon") or "").strip()

    # If no explicit KPI link, try a keyword match against committed activities.
    if not linked and plan_activities:
        linked = _keyword_link(task.get("title", ""), plan_activities)
        if linked:
            task["linked_kpi"] = linked

    # Infer Build horizon from learning/prep-style language if unset.
    if not horizon:
        title_l = task.get("title", "").lower()
        if any(h in title_l for h in _BUILD_HINTS):
            horizon = "Build"
            task["horizon"] = "Build"

    # Respect values already set (e.g. by GPT). Only fill what's missing.
    if not task.get("goal_aligned"):
        if linked and linked in plan_kpis:
            task["goal_aligned"] = "Build" if horizon == "Build" else "Yes"
        else:
            task["goal_aligned"] = "No"

    if not task.get("horizon"):
        task["horizon"] = "Today"  # default assumption; user can flip to Build
        horizon = "Today"

    if not task.get("alignment_note"):
        if task["goal_aligned"] == "No":
            task["alignment_note"] = (
                "No direct line to a goal — kept because you chose it. "
                "This won't move your numbers today."
            )
        elif task["goal_aligned"] == "Build":
            task["alignment_note"] = f"Build task — seeds {linked}, not today's number."
        else:
            task["alignment_note"] = ""
    return task


def plan_nudge(tasks, scorecards):
    """The whole-plan nudge — the most valuable interruption.

    A stray soft task is forgivable. A PATTERN of unlinked tasks on a behind day
    is the thing worth one honest sentence. Dosed to the worst KPI's status.
    """
    unlinked = [t for t in tasks if t.get("goal_aligned") == "No"]
    today_tasks = [t for t in tasks if t.get("horizon") == "Today"]
    build_tasks = [t for t in tasks if t.get("horizon") == "Build"]
    total = len(tasks)

    worst = worst_status(scorecards)
    status = worst["status"] if worst else "No Target"
    kpi = worst["kpi_name"] if worst else "your target"

    lines = []
    tone = "quiet"

    # 1. Alignment pressure, dosed to the gap.
    if total and len(unlinked) >= 2:
        if status in ("Behind", "Critical"):
            tone = "sharp"
            lines.append(
                f"{len(unlinked)} of {total} tasks today don't touch a goal — "
                f"and you're {status.lower()} on {kpi}. Sure these are today's?"
            )
        else:
            lines.append(
                f"{len(unlinked)} of {total} tasks aren't linked to a goal. "
                f"Fine if they matter for another reason — just naming it."
            )
    elif len(unlinked) == 1:
        lines.append("One task isn't linked to a goal. Kept — just flagged.")

    # 2. Horizon balance — not all delivery, not all prep.
    if total >= 3:
        if not build_tasks:
            lines.append("All delivery, nothing building tomorrow's number — consider one Build task.")
        elif not today_tasks:
            lines.append("All prep, nothing delivering today — add at least one Today task.")

    # 3. Direct-impact pressure when behind.
    if status in ("Behind", "Critical"):
        direct = [t for t in tasks if t.get("goal_aligned") in ("Yes", "Build")]
        if not direct:
            tone = "sharp"
            lines.append(f"Nothing here moves {kpi}, and that's the number you're behind on.")

    if not lines:
        lines.append("Plan looks aligned. Tags are set — go execute.")
        tone = "quiet"

    return {"tone": tone, "lines": lines,
            "counts": {"total": total, "unlinked": len(unlinked),
                       "today": len(today_tasks), "build": len(build_tasks)}}


# ---------------------------------------------------------------- goal matching

import re as _re


def _normalize(s):
    """Lowercase, strip, drop leading numbers/units so '15 Accounts' ~ 'accounts'."""
    s = str(s or "").lower().strip()
    s = _re.sub(r"^[\d,.\sk)l]+", "", s)        # strip leading "15 ", "200k " etc.
    s = _re.sub(r"[^a-z\s]", " ", s)
    return _re.sub(r"\s+", " ", s).strip()


def goal_match(text, headings):
    """Return the heading that best matches `text`, or '' if none.

    Tolerant: matches if either normalized string contains the other, or they
    share a significant word. So a task about 'open 15 accounts' links to a target
    headed 'Accounts' or '15 Accounts'.
    """
    nt = _normalize(text)
    if not nt:
        return ""
    best = ""
    for h in headings:
        nh = _normalize(h)
        if not nh:
            continue
        if nh in nt or nt in nh:
            return h
        # shared meaningful word (>=4 chars to avoid 'the', 'and')
        tw = {w for w in nt.split() if len(w) >= 4}
        hw = {w for w in nh.split() if len(w) >= 4}
        if tw & hw:
            best = best or h
    return best


def goal_served(task_goal, headings):
    """Does this task's day_goal count as serving one of the headings? (normalized)"""
    if not task_goal:
        return None
    ng = _normalize(task_goal)
    for h in headings:
        nh = _normalize(h)
        if nh and (nh in ng or ng in nh or ({w for w in ng.split() if len(w) >= 4}
                                            & {w for w in nh.split() if len(w) >= 4})):
            return h
    return None


_STATUS_RANK = {"Ahead": 0, "On Track": 1, "Behind": 2, "Critical": 3, "No Target": 1}


def kpi_situation(targets_df, year, month, today=None):
    """Deterministic per-KPI situation from the synced targets. Returns list of dicts."""
    out = []
    for _, r in targets_df.iterrows():
        s = score_kpi(r["monthly_target"], r["achieved_mtd"], year, month, today)
        out.append({"kpi_name": r["kpi_name"], "status": s["status"],
                    "achieved": s["achieved_mtd"], "target": s["monthly_target"],
                    "achievement_pct": s["achievement_pct"], "gap": s["gap"],
                    "required_run_rate": s["required_run_rate"]})
    return out


def newly_slipped(situation, prev_snapshot):
    """KPIs whose status got worse than yesterday's snapshot."""
    slipped = []
    for s in situation:
        prev = prev_snapshot.get(s["kpi_name"])
        if prev and _STATUS_RANK.get(s["status"], 1) > _STATUS_RANK.get(prev, 1):
            slipped.append(s["kpi_name"])
    return slipped


def behind_kpis(situation):
    return [s["kpi_name"] for s in situation if s["status"] in ("Behind", "Critical")]
