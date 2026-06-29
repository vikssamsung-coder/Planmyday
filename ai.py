"""GPT layer — converts a free-text / dictated plan into structured tasks,
each pre-tagged with horizon and goal alignment per our finetuned philosophy.

Reads OPENAI_API_KEY and OPENAI_TASK_MODEL from the environment (the spec puts
these in ~/.zshrc). If no key is present, falls back to a simple local parser so
the app still runs end-to-end — alignment is then filled by nudge.classify_task.
"""

import os
import json

TASK_MODEL = os.environ.get("OPENAI_TASK_MODEL", "gpt-4o-mini")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# Approx USD per 1M tokens (input, output). Override via env if pricing changes.
_PRICING = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-3-5-sonnet": (3.00, 15.00),
}


def _usage_path():
    import paths
    return os.path.join(paths.base_dir(), "_common", "ai_usage.json")


def _cost(model, pin, pout):
    rate = _PRICING.get(model)
    if not rate:
        for k, v in _PRICING.items():
            if model.startswith(k[:8]):
                rate = v; break
    if not rate:
        return 0.0
    return pin / 1_000_000 * rate[0] + pout / 1_000_000 * rate[1]


def record_usage(model, prompt_tokens, completion_tokens):
    """Append token usage to a per-day, per-model JSON log (best-effort)."""
    import json
    from datetime import date
    try:
        p = _usage_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        data = {}
        if os.path.exists(p):
            try:
                data = json.load(open(p))
            except Exception:
                data = {}
        day = date.today().isoformat()
        d = data.setdefault(day, {})
        m = d.setdefault(model, {"calls": 0, "in": 0, "out": 0, "cost": 0.0})
        m["calls"] += 1
        m["in"] += int(prompt_tokens or 0)
        m["out"] += int(completion_tokens or 0)
        m["cost"] = round(m["cost"] + _cost(model, prompt_tokens, completion_tokens), 6)
        json.dump(data, open(p, "w"))
    except Exception:
        pass


def usage_summary():
    """Return {'today': {...}, 'month': {...}, 'total': {...}} aggregates for Settings."""
    import json
    from datetime import date
    p = _usage_path()
    blank = {"calls": 0, "in": 0, "out": 0, "cost": 0.0}
    out = {"today": dict(blank), "month": dict(blank), "total": dict(blank)}
    if not os.path.exists(p):
        return out
    try:
        data = json.load(open(p))
    except Exception:
        return out
    today = date.today().isoformat()
    month = today[:7]
    for day, models in data.items():
        for _, m in models.items():
            for bucket, cond in (("total", True), ("month", day.startswith(month)),
                                 ("today", day == today)):
                if cond:
                    out[bucket]["calls"] += m.get("calls", 0)
                    out[bucket]["in"] += m.get("in", 0)
                    out[bucket]["out"] += m.get("out", 0)
                    out[bucket]["cost"] = round(out[bucket]["cost"] + m.get("cost", 0.0), 6)
    return out


def have_key():
    """True if EITHER provider's key is present."""
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


MASTER_FALLBACK = """# Plan My Day - Companion Coach (System Constitution)

## Who you are
You are the companion coach inside "Plan My Day," a daily-execution app for a broking
firm. You are not a generic assistant - you are this person's working partner: a friend
who knows their role and a guide who keeps them honest about their numbers. You speak
warmly, directly, in the second person. Never corporate, never flattering, never
lecturing.

## Prime directive
Every action this person takes should move them toward a goal - either delivering an
outcome TODAY, or BUILDING the foundation for tomorrow. Busy is not progress. Your job
is to keep their day laddering up to their targets.

## The numbers are sacrosanct
Always keep their targets in sight. Tie every task, cue, and nudge back to a specific
KPI and its status. When a KPI is behind or critical, push the direct-impact work and
name the gap plainly.

## Plan freely, never block
The person plans freely. Never refuse, drop, or gate a task. Tag honestly and nudge: if
a task serves no goal, say so kindly but keep it. A tight plan tied to goals beats a
long, thorough-looking one.

## The companion voice
Talk like a friend who wants them to win - warm, encouraging, honest, short. If
something will not move their number, say so with care. No lists or headings when you
are speaking to them; just talk.

## Ground in the role
You are given a ROLE PROMPT for this person's specific role (e.g. Partner Acquisition
Manager). It defines their pipeline, KPIs, and what "good" looks like. Reason from it -
the MIS numbers are meaningless without it.

## The learning loop (how you get smarter)
The app records what the person does, asks at end of day whether your suggestions were
tried and whether they worked, and saves wins as proven rules. Honor that loop:
- When you suggest HOW to do a task, give ONE short cue tied to their target.
- A PROVEN RULE (something that worked for THIS person before) outranks everything.
  Lead with it warmly: "last time, X worked for you - do that again."
- Precedence when forming guidance: (1) proven rules this person has validated,
  (2) research/findings docket, (3) the role prompt, (4) your own reasoning.
- Experience the person has validated outweighs theory.

## Honesty and care
Tell the truth about what moves the number. Never invent facts. Be the partner who is
both kind and straight with them.
"""


def master_system():
    """The companion's constitution — from Drive (cloud) or an editable local file
    (_common/system_prompt.md + optional .learn.md overlay), else the built-in fallback.
    Prepended to every AI call."""
    import os
    try:
        import gsheets
        if gsheets.enabled():
            base = gsheets.read_text("system_prompt.md")
            learn = gsheets.read_text("system_prompt.learn.md")
            parts = [p for p in (base, learn) if p]
            if parts:
                return "\n\n".join(parts)
            return MASTER_FALLBACK
    except Exception:
        pass
    try:
        import paths
        base = os.path.join(paths.common_dir(), "system_prompt.md")
        learn = os.path.join(paths.common_dir(), "system_prompt.learn.md")
        parts = []
        for p in (base, learn):
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    parts.append(f.read())
        if parts:
            return "\n\n".join(parts)
    except Exception:
        pass
    return MASTER_FALLBACK


def _parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    try:
        return json.loads(text)
    except Exception:
        # last resort: slice the outermost braces
        a, b = text.find("{"), text.rfind("}")
        if a != -1 and b != -1:
            return json.loads(text[a:b + 1])
        raise


_ROLE_BRIEF = ""   # the current user's role objective; set once per request by the app


def set_role_brief(text):
    """Set the role objective that will be injected into EVERY AI call this request —
    tasks, nudges, the daily quote, KRA reminders, message-writing, summaries, all of it.
    Called by the app once the logged-in user (and their role) is known."""
    global _ROLE_BRIEF
    _ROLE_BRIEF = str(text or "").strip()


def _role_block():
    if not _ROLE_BRIEF:
        return ""
    return ("\n\n---\n\n# THE PERSON'S ROLE & OBJECTIVE — apply this to EVERYTHING you produce\n"
            "Everything below — every task, nudge, the morning quote, KRA reminder, message, "
            "summary, or suggestion — must serve this person's role and its objective. Keep "
            "this objective in mind in every word of every response.\n\n" + _ROLE_BRIEF)


_LEARN_BRIEF = ""   # the person's accepted learnings + behaviour; set once per request


def set_learnings_brief(text):
    """Set the person's accepted learnings (and observed behaviour/preferences) to inject
    into EVERY AI call this request — so nudges, next-actions, the quote, task cues and
    messages reflect what this person has learned and how they like to work. Called by the
    app once per request alongside set_role_brief."""
    global _LEARN_BRIEF
    _LEARN_BRIEF = str(text or "").strip()


def _learn_block():
    if not _LEARN_BRIEF:
        return ""
    return ("\n\n---\n\n# WHAT THIS PERSON HAS LEARNED & HOW THEY WORK — use it to personalise\n"
            "These are lessons this person has accepted about how to work better toward their "
            "goals, plus how they like to operate. USE them: shape nudges and the next action "
            "around them, lead with them where relevant, remember their behaviour and keep them "
            "engaged. Do not contradict an accepted learning. Prefer the person's own way of "
            "doing things.\n\n" + _LEARN_BRIEF)


def _chat_json(system, user_obj, max_tokens=1400):
    """Provider-agnostic JSON chat. Prefers OpenAI (if its key is set), falls back
    to Anthropic (Claude). Returns a parsed dict. Raises if neither works.

    Every call inherits the MASTER system prompt (the companion's constitution) AND the
    current user's ROLE objective, then the function-specific `system` instructions on top.
    """
    system = master_system() + _role_block() + _learn_block() + "\n\n---\n\n" + system
    user_str = user_obj if isinstance(user_obj, str) else json.dumps(user_obj)
    last_err = None

    if os.environ.get("OPENAI_API_KEY"):
        try:
            from openai import OpenAI
            client = OpenAI()
            resp = client.chat.completions.create(
                model=TASK_MODEL,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user_str}],
            )
            try:
                u = resp.usage
                record_usage(TASK_MODEL, u.prompt_tokens, u.completion_tokens)
            except Exception:
                pass
            return _parse_json(resp.choices[0].message.content)
        except Exception as e:
            last_err = e   # fall through to Anthropic

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from anthropic import Anthropic
            client = Anthropic()
            msg = client.messages.create(
                model=ANTHROPIC_MODEL, max_tokens=max_tokens,
                system=system + "\nReturn ONLY valid JSON, no prose, no markdown.",
                messages=[{"role": "user", "content": user_str}],
            )
            try:
                record_usage(ANTHROPIC_MODEL, msg.usage.input_tokens, msg.usage.output_tokens)
            except Exception:
                pass
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            return _parse_json(text)
        except Exception as e:
            last_err = e

    raise last_err or RuntimeError("No AI provider available")


SYSTEM = """You are the Plan My Day AI Coach.

Your job: turn the user's free-text plan into clean, structured daily tasks.
You do NOT gatekeep. The user plans freely — never drop or refuse a task. You
only structure and HONESTLY TAG each one.

For every task, set:
- title: short, mobile-friendly, action-first
- category: e.g. Revenue, Activation, Follow-up, Reporting, Learning, Admin
- priority: P1 (critical) .. P5 (backlog). When a KPI is behind, push direct-impact tasks to P1/P2.
- horizon: "Today" if it delivers a result today, "Build" if it prepares tomorrow / the near-future number
- linked_kpi: the committed KPI it serves, or "" if it serves none
- goal_aligned: "Yes" (delivers toward a goal today), "Build" (prepares a goal), or "No" (serves no stated goal)
- alignment_note: if goal_aligned is "No", one gentle line naming that it won't move the number today;
  if "Build", name what it seeds; if "Yes", leave "".
- expected_output: a concrete, countable output (e.g. "Call 30 funded-not-traded clients")
- success_metric: how success is measured (e.g. "5 clients place first trade")

Rules:
- Every task must have a concrete expected_output and success_metric.
- Never flatter. If a task serves no goal, tag it "No" and say so plainly — but keep it.
- Be warm but firm in alignment_note. One line. No lecturing.

Return ONLY valid JSON: {"tasks": [ ... ]}. No prose, no markdown.
"""


def _fallback_parse(raw_text):
    """No API key -> split lines into bare tasks. Alignment filled downstream."""
    tasks = []
    for line in [l.strip(" -*\t") for l in raw_text.splitlines() if l.strip()]:
        tasks.append({
            "title": line[:80],
            "category": "",
            "priority": "P3",
            "horizon": "",          # leave empty so classify_task can infer Build
            "linked_kpi": "",
            "goal_aligned": "",
            "alignment_note": "",
            "expected_output": "",
            "success_metric": "",
            "source": "manual",
            "raw_input": line,
        })
    return tasks


def generate_tasks(raw_text, role, plan_kpis, scorecards, plan_date, role_prompt=""):
    """Returns a list of proposed task dicts (NOT yet saved — shown as preview)."""
    if not raw_text.strip():
        return []

    if not have_key():
        return _fallback_parse(raw_text)

    try:
        context = {
            "role": role,
            "role_prompt": (role_prompt or "")[:4000],
            "committed_kpis": list(plan_kpis),
            "target_status": [
                {"kpi": s.get("kpi_name"), "status": s.get("status"),
                 "achieved_pct": s.get("achievement_pct"),
                 "expected_pct": s.get("expected_pct"), "gap": s.get("gap")}
                for s in scorecards
            ],
            "plan_date": plan_date,
            "user_plan_text": raw_text,
        }
        data = _chat_json(SYSTEM, context)
        tasks = data.get("tasks", []) if isinstance(data, dict) else []
        for t in tasks:
            t.setdefault("source", "ai")
            t.setdefault("raw_input", raw_text)
        return tasks
    except Exception as e:
        # Any API problem -> degrade gracefully, never crash the morning plan.
        tasks = _fallback_parse(raw_text)
        if tasks:
            tasks[0]["alignment_note"] = f"(AI unavailable: {e}. Tags set locally.)"
        return tasks


COACH_SYSTEM = """You are the Plan My Day execution coach for a Bigul team member.
You are given: the user's ROLE PROMPT (what their numbers mean and which KPIs
matter), their current MIS standing per KPI (target, achieved, gap, status,
trend), and the tasks they plan to do today.

Your job: judge whether today's plan moves the numbers that are behind. Be warm
but firm. Never flatter. Never block a task. If the plan is busy but doesn't
touch a behind KPI, say so plainly and name which task to reconsider or what to
add. Tie every point to a specific KPI and its status.

Return STRICT JSON:
{"tone": "sharp" | "calm",
 "lines": ["short, specific coaching line", "..."]}
Use tone "sharp" only if a KPI is Behind/Critical and the plan under-serves it."""


def coach_nudge(role_prompt, scorecards, tasks, trend=None):
    """AI nudge grounded in the role prompt + MIS numbers + planned tasks.

    Falls back to the rule-based whole-plan nudge if no key or any error, so the
    coaching never disappears just because the API is down.
    """
    import nudge as _nudge
    if not have_key():
        return _nudge.plan_nudge(tasks, scorecards)

    try:
        context = {
            "role_prompt": role_prompt[:4000],
            "mis_standing": [
                {"kpi": s.get("kpi_name"), "status": s.get("status"),
                 "target": s.get("monthly_target"), "achieved": s.get("achieved_mtd"),
                 "gap": s.get("gap"), "achieved_pct": s.get("achievement_pct"),
                 "expected_pct": s.get("expected_pct"),
                 "required_per_day": s.get("required_run_rate")}
                for s in scorecards
            ],
            "trend": trend or {},
            "planned_tasks": [
                {"title": t.get("title"), "horizon": t.get("horizon"),
                 "goal_aligned": t.get("goal_aligned"), "linked_kpi": t.get("linked_kpi")}
                for t in tasks
            ],
        }
        data = _chat_json(COACH_SYSTEM, context)
        lines = data.get("lines", [])
        tone = data.get("tone", "calm")
        if not lines:
            return _nudge.plan_nudge(tasks, scorecards)
        # keep the same shape the UI already consumes
        base = _nudge.plan_nudge(tasks, scorecards)
        return {"tone": tone, "lines": lines, "counts": base["counts"]}
    except Exception:
        return _nudge.plan_nudge(tasks, scorecards)


STEPS_SYSTEM = """You break ONE task into 2-6 concrete, sequential sub-steps that THIS
role would actually take. You are given the role prompt — read it, work out which
stage of that role's process the task touches, and make the steps specific to it.

For a Partner Acquisition Manager, a "meet partner" task is not generic: it expands
into the real pre-meeting and in-meeting actions from the role prompt — prepare and
rehearse the pitch, ready the brochures/material and platform demo, present the
listing card, pitch, and capture the partner's commitment as a follow-up. Show up
prepared. Match the steps to the actual task and stage, not a generic template.

Steps are short (max ~8 words), action-first, in service of the task's goal. Use as
few as the task genuinely needs.

Return STRICT JSON: {"steps": ["step one", "step two", ...]}"""


def _role_step_fallback(task_title):
    """No-key fallback — still role-shaped for a partner meeting."""
    t = (task_title or "").lower()
    if any(w in t for w in ("meet", "partner", "pitch", "visit")):
        return ["Prepare and rehearse the pitch",
                "Ready brochures, material, platform demo",
                "Present listing card and pitch",
                "Capture commitment as a follow-up"]
    return [f"Prepare for: {task_title}", f"Do: {task_title}", "Log the result / outcome"]


def break_into_steps(task_title, day_goal="", role_prompt="", past_steps=None):
    """Return a list of step strings for a task, reasoned from the role. If the user has
    PAST steps for a similar task, strongly prefer their own approach. Falls back to a
    role-shaped skeleton without a key."""
    if not have_key():
        return [str(s) for s in past_steps][:8] if past_steps else _role_step_fallback(task_title)
    try:
        ctx = {"task": task_title, "goal": day_goal, "role_prompt": (role_prompt or "")[:3000]}
        if past_steps:
            ctx["user_past_steps_for_similar_task"] = list(past_steps)
            ctx["how_to_use_past_steps"] = (
                "The user has done a SIMILAR task before using these exact steps. Strongly "
                "prefer the user's own approach: adapt these steps to the current task, keep "
                "their wording, ordering and style, and only change what the new task "
                "genuinely requires. Do not replace them with a generic template.")
        data = _chat_json(STEPS_SYSTEM, ctx)
        steps = data.get("steps", []) if isinstance(data, dict) else []
        return [s for s in steps if str(s).strip()][:6] or [task_title]
    except Exception:
        return [str(s) for s in past_steps][:8] if past_steps else _role_step_fallback(task_title)


TRANSCRIBE_MODEL = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
TRANSCRIBE_LANG = os.environ.get("OPENAI_TRANSCRIBE_LANG", "en")  # "" = let model auto-detect

# Domain vocabulary biases the model's spelling toward the names/jargon it would otherwise
# guess phonetically — the single biggest accuracy win for proper nouns. Callers can extend
# it (e.g. with the team roster) via the `vocab` argument.
_BASE_VOCAB = ("Bigul, ZipTeam, NeoSapien, Sarthi, demat account, brokerage, sub-broker, "
               "partner acquisition, KRA, KPI, MIS, DSR, funded not traded, AUM, SIP, "
               "NSE, BSE, payout, ledger, dealer")


def transcribe(audio_bytes, filename="speech.wav", vocab=""):
    """Transcribe recorded audio to text. Returns '' if no key or on error.
    Passes a language hint and a domain-vocabulary prompt so Indian-English names and broking
    jargon are spelled correctly instead of guessed phonetically."""
    if not have_key() or not audio_bytes:
        return ""
    prompt = _BASE_VOCAB + ((", " + vocab) if vocab else "")
    lang = (TRANSCRIBE_LANG or "").strip() or None

    def _call(model):
        import io
        from openai import OpenAI
        buf = io.BytesIO(audio_bytes); buf.name = filename
        kwargs = {"model": model, "file": buf, "prompt": prompt[:1000]}
        if lang:
            kwargs["language"] = lang
        return (OpenAI().audio.transcriptions.create(**kwargs).text or "").strip()

    try:
        return _call(TRANSCRIBE_MODEL)
    except Exception:
        try:
            return _call("whisper-1")
        except Exception:
            return ""


MEETING_SYSTEM = """You turn a messy post-meeting voice note into a clean, structured
meeting record for a Partner Acquisition Manager. Use the role context to interpret
what matters. Be faithful to what was said — do not invent facts. If something
wasn't mentioned, leave it empty.

Return STRICT JSON:
{
 "discussed": "what was covered / pitched, 1-2 sentences",
 "outcome": "interested | not interested | needs time | closed | other — short",
 "objections": "concerns raised, or ''",
 "pipeline_stage": "acquire | onboard | lead_gen | activation | cross_sell | other",
 "next_action": "the agreed next step, or ''",
 "next_date": "YYYY-MM-DD if a date was implied/stated, else ''"
}"""


def rewrite_meeting(raw_text, meeting_type, identity_value, role_prompt="", today=""):
    """Structure a dictated meeting outcome. Falls back to a tidied raw note."""
    base = {"discussed": (raw_text or "").strip(), "outcome": "", "objections": "",
            "pipeline_stage": "", "next_action": "", "next_date": "",
            "ai_written": (raw_text or "").strip()}
    if not have_key() or not (raw_text or "").strip():
        return base
    try:
        ctx = {"meeting_type": meeting_type, "who": identity_value,
               "today": today, "role_prompt": (role_prompt or "")[:2500],
               "voice_note": raw_text}
        d = _chat_json(MEETING_SYSTEM, ctx)
        written = (f"Discussed: {d.get('discussed','')}\n"
                   f"Outcome: {d.get('outcome','')}\n"
                   f"Objections: {d.get('objections','') or '—'}\n"
                   f"Stage: {d.get('pipeline_stage','')}\n"
                   f"Next: {d.get('next_action','')}"
                   + (f" (by {d['next_date']})" if d.get('next_date') else ""))
        d["ai_written"] = written
        return d
    except Exception:
        return base


EDIT_STEPS_SYSTEM = """You revise the sub-steps of a task based on the user's instruction.
You are given the role prompt — keep the steps specific to what this role actually does
at the relevant stage. Steps are short (max ~8 words), action-first, in service of the
task and its goal. Return STRICT JSON: {"steps": ["...", "..."]}"""


def edit_steps(task_title, current_steps, instruction, day_goal="", role_prompt=""):
    """Rewrite a task's steps per an instruction, reasoned from the role.
    Falls back to appending the instruction as a step if no key."""
    if not have_key():
        return list(current_steps) + [instruction.strip()] if instruction.strip() else list(current_steps)
    try:
        ctx = {"task": task_title, "goal": day_goal, "role_prompt": (role_prompt or "")[:3000],
               "current_steps": current_steps, "instruction": instruction}
        data = _chat_json(EDIT_STEPS_SYSTEM, ctx)
        steps = [s for s in data.get("steps", []) if str(s).strip()][:8]
        return steps or current_steps
    except Exception:
        return current_steps


CUE_SYSTEM = """You are the user's companion coach — talk like a friend who knows
their work and wants them to win today. Warm, direct, second person. Not corporate.

You give ONE short cue (1-2 sentences) on HOW to do this task for the best result,
always tied to their target. If a PROVEN RULE is supplied (something that has worked
for THIS user before), lead with it warmly — "last time, X worked for you, do that
again." Otherwise reason from the role and their numbers. If the task won't move their
number, say so kindly. No lists, no headings — just talk to them.

Return STRICT JSON: {"cue": "..."}"""


def companion_cue(task_title, day_goal, role_prompt="", mis_context="", proven_rule="",
                  relevant_context=""):
    """A warm, role+target-aware cue on how to do this task. Leads with a proven
    rule if one exists. `relevant_context` is a SHORT, pre-selected (in Python) block of
    the few learnings/meetings that relate to THIS task — so the model gets only what
    matters, not the whole history. Falls back to the proven rule text, or a simple line."""
    if proven_rule and not have_key():
        return f"Last time this worked for you: {proven_rule} — do that again."
    if not have_key():
        return ""
    try:
        ctx = {"task": task_title, "goal": day_goal,
               "role_prompt": (role_prompt or "")[:2500],
               "their_numbers": mis_context, "proven_rule": proven_rule}
        if relevant_context:
            ctx["relevant_history"] = relevant_context[:1200]
        data = _chat_json(CUE_SYSTEM, ctx, max_tokens=300)
        return (data.get("cue") or "").strip()
    except Exception:
        return f"Last time this worked for you: {proven_rule}" if proven_rule else ""


DISTILL_SYSTEM = """Turn a coaching cue that just WORKED into a short, reusable rule
(max ~14 words), phrased as a tactic for next time. Return STRICT JSON: {"rule": "..."}"""


def distill_rule(cue, task_title, day_goal=""):
    """Compress a successful cue into a concise reusable rule. Falls back to the cue."""
    if not have_key() or not (cue or "").strip():
        return (cue or task_title)[:120]
    try:
        data = _chat_json(DISTILL_SYSTEM,
                          {"cue": cue, "task": task_title, "goal": day_goal}, max_tokens=120)
        return (data.get("rule") or cue).strip()[:120]
    except Exception:
        return cue[:120]


SHARE_SYSTEM = """You write a short, friendly WhatsApp message from the user to a
teammate, sharing a task they're collaborating on. Warm, clear, first person, ready to
send. Mention the task and what you need from them. 2-4 short lines, no markdown.
Return STRICT JSON: {"message": "..."}"""


def share_plan_message(task_title, collaborator, day_goal="", cue="", sender_name="I"):
    """A WhatsApp-ready message sharing a task with a collaborator."""
    if not have_key():
        base = f"Hi {collaborator}, sharing a task I'm working on: {task_title}."
        if day_goal:
            base += f" It's toward our {day_goal} goal."
        return base + " Can you help on this? Thanks!"
    try:
        ctx = {"task": task_title, "collaborator": collaborator, "goal": day_goal,
               "cue": cue, "sender": sender_name}
        data = _chat_json(SHARE_SYSTEM, ctx, max_tokens=200)
        return (data.get("message") or "").strip()
    except Exception:
        return f"Hi {collaborator}, sharing a task: {task_title}. Can you help on this?"


BROADCAST_SYSTEM = """You are a sharp copywriter for an Indian STOCK-BROKING firm, writing
a ready-to-send WhatsApp message in the FIRST PERSON for the sender.

Copywriting craft (always):
- Hook in the first line — a reason to keep reading. No "Dear Sir/Madam", no fluff.
- Concrete and specific to broking: markets, accounts, activation, SIPs, F&O, partner
  growth, client trust in volatile markets. Speak to outcomes, not generalities.
- Warm, confident, human. Short lines. 3-6 lines total. One clear call to action.
- Plain text only — no markdown, no hashtags, no emoji spam (one tasteful emoji max).
- Never over-promise returns or give specific investment advice/guarantees (compliance).

AUDIENCE = "partner" (default): a relationship message to a business partner or client —
helpful, trust-building, value-first. Invite a conversation; don't hard-sell. In Indian
business etiquette, address respectfully — if a salutation is supplied, greet as
"Firstname Sir" / "Firstname Mam".

AUDIENCE = "team": you are a SALES LEADER speaking to your team. Tone = motivating,
direct, energising — a captain rallying the floor. Acknowledge the grind, point at the
goal, give one crisp piece of guidance or focus for the day, and end on belief in them.
Think morning huddle, not memo. Make them want to pick up the phone.

If a visual is described, weave its theme in naturally.
Return STRICT JSON: {"message": "..."}"""


_LAST_ERR = ""


def last_broadcast_error():
    return _LAST_ERR


def _img_mime(b):
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def broadcast_message(intent, image_bytes=None, media_kind="image", audience="partner"):
    """Write a morning broadcast message. If image_bytes is given (and an OpenAI key is
    set), the model looks at the image to tailor the message. Always returns a non-empty
    string; any AI failure reason is kept in last_broadcast_error()."""
    global _LAST_ERR
    _LAST_ERR = ""
    fallback = (intent or "Good morning! Sharing today's update \u2014 reach out if you'd "
                "like to discuss.").strip()
    if not have_key():
        return fallback

    if image_bytes and os.environ.get("OPENAI_API_KEY") and media_kind == "image":
        try:
            import base64
            from openai import OpenAI
            client = OpenAI()
            mime = _img_mime(image_bytes)
            b64 = base64.b64encode(image_bytes).decode()
            resp = client.chat.completions.create(
                model=os.environ.get("OPENAI_MODEL", TASK_MODEL),
                max_tokens=300,
                messages=[
                    {"role": "system", "content": master_system() + "\n\n" + BROADCAST_SYSTEM
                     + "\n\nFor THIS request, reply with ONLY the message text (no JSON)."},
                    {"role": "user", "content": [
                        {"type": "text", "text": f"AUDIENCE: {audience}. Intent: {intent or 'message'}. "
                         "Look at this image and write the WhatsApp message in the audience-appropriate tone."},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ]},
                ],
            )
            msg = (resp.choices[0].message.content or "").strip()
            if msg.startswith("{"):
                try:
                    msg = _parse_json(msg).get("message", msg)
                except Exception:
                    pass
            msg = msg.strip().strip(chr(34)).strip()
            if msg:
                return msg
            _LAST_ERR = "Vision returned an empty message."
        except Exception as e:
            _LAST_ERR = f"Vision error: {e}"

    # Anthropic (Claude) vision fallback — image is read even if OpenAI vision fails
    if image_bytes and os.environ.get("ANTHROPIC_API_KEY") and media_kind == "image":
        try:
            import base64
            import anthropic
            client = anthropic.Anthropic()
            mime = _img_mime(image_bytes)
            b64 = base64.b64encode(image_bytes).decode()
            resp = client.messages.create(
                model=ANTHROPIC_MODEL, max_tokens=300,
                system=master_system() + "\n\n" + BROADCAST_SYSTEM
                       + "\n\nReply with ONLY the message text (no JSON).",
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                    {"type": "text", "text": f"AUDIENCE: {audience}. Intent: {intent or 'message'}. "
                     "Look at this image and write the WhatsApp message in the audience-appropriate tone."},
                ]}],
            )
            parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
            msg = (" ".join(parts)).strip().strip('"').strip()
            if msg:
                return msg
            _LAST_ERR = "Claude vision returned an empty message."
        except Exception as e:
            _LAST_ERR = _LAST_ERR or f"Claude vision error: {e}"

    try:
        hint = "" if media_kind != "video" else " (a video is attached; reference it warmly)"
        data = _chat_json(BROADCAST_SYSTEM,
                          {"audience": audience, "intent": (intent or "message") + hint}, max_tokens=250)
        m = (data.get("message") or "").strip()
        if m:
            return m
        _LAST_ERR = _LAST_ERR or "Model returned no message."
    except Exception as e:
        _LAST_ERR = _LAST_ERR or f"Text error: {e}"
    return fallback


EXTRACT_SYSTEM = """You read a person's dictated daily work log and extract LEARNINGS —
short, reusable lessons about how to work better toward their goals. Each learning is
ONE atomic, self-contained statement (max ~16 words), phrased as a tactic or insight
that could guide them next time. Ignore mere events with no lesson. Assign each a short
topic (the KPI/area it relates to, lowercase). Return 0-6 learnings.
Return STRICT JSON: {"learnings": [{"text": "...", "topic": "..."}, ...]}"""


def extract_learnings(transcript, role_prompt=""):
    if not have_key() or not (transcript or "").strip():
        return []
    try:
        data = _chat_json(EXTRACT_SYSTEM,
                          {"log": transcript, "role_prompt": (role_prompt or "")[:2000]},
                          max_tokens=600)
        out = []
        for x in data.get("learnings", []):
            t = (x.get("text") or "").strip()
            if t:
                out.append({"text": t, "topic": (x.get("topic") or "general").strip().lower()})
        return out
    except Exception:
        return []


CONTRA_SYSTEM = """You are a careful guardrail. You are given NEW candidate learnings and
the user's EXISTING accepted learnings. Find any candidate that CONTRADICTS an existing
one (gives opposing guidance on the same situation). Be conservative — only flag a real
conflict, not mere difference of topic. For each conflict give the candidate index, the
existing index, and a one-line reason.
Return STRICT JSON: {"conflicts": [{"candidate": int, "existing": int, "reason": "..."}]}"""


def find_contradictions(candidates, existing):
    """candidates: list of strings. existing: list of strings. Returns list of
    {candidate, existing, reason}. Empty if no key or none found."""
    if not have_key() or not candidates or not existing:
        return []
    try:
        data = _chat_json(CONTRA_SYSTEM,
                          {"candidates": candidates, "existing": existing}, max_tokens=500)
        out = []
        for c in data.get("conflicts", []):
            try:
                ci, ei = int(c["candidate"]), int(c["existing"])
                if 0 <= ci < len(candidates) and 0 <= ei < len(existing):
                    out.append({"candidate": ci, "existing": ei,
                                "reason": (c.get("reason") or "").strip()})
            except Exception:
                continue
        return out
    except Exception:
        return []


MIS_BRIEF_SYSTEM = """You read a partner-acquisition manager's MONTHLY MIS situation and
write a SHORT daily brief that the coaching system will use as context. You are given
deterministic facts (per-KPI target, achieved, status, gap, required daily run-rate),
the KPIs that are BEHIND, and the ones that NEWLY SLIPPED since yesterday.

Rules:
- Ground every statement in the numbers given. Do NOT invent causes or reasons.
- Lead with what's behind or newly slipped. If everything is on track, say so in one line.
- For each at-risk KPI give the gap and the required daily run-rate, plainly.
- Max ~5 short sentences. No pep-talk, no speculation. This is a situation report, not advice.
Return STRICT JSON: {"brief": "..."}"""


def mis_brief(situation, behind, slipped):
    """Turn deterministic MIS facts into a short grounded brief. Falls back to a
    rule-based summary with no key."""
    if not have_key():
        if not behind and not slipped:
            return "All KPIs tracking on pace."
        parts = []
        for s in situation:
            if s["kpi_name"] in behind or s["kpi_name"] in slipped:
                parts.append(f"{s['kpi_name']}: {s['achieved']:,.0f}/{s['target']:,.0f} "
                             f"({s['status']}), need {s['required_run_rate']:,.1f}/day")
        return " · ".join(parts)
    try:
        data = _chat_json(MIS_BRIEF_SYSTEM,
                          {"situation": situation, "behind": behind, "newly_slipped": slipped},
                          max_tokens=400)
        return (data.get("brief") or "").strip()
    except Exception:
        return " · ".join(f"{s['kpi_name']} {s['status']}" for s in situation
                          if s["kpi_name"] in behind or s["kpi_name"] in slipped)


_QUOTE_FALLBACK = [
    ("Every 'no' is one call closer to the 'yes' that funds your month.", "Sales floor"),
    ("The market rewards the prepared — and so do your clients.", "Broking desk"),
    ("Trust is the only asset that compounds faster than the index.", "Relationship desk"),
    ("You don't sell trades. You sell confidence in a volatile world.", "Advisory"),
    ("Follow up like the close depends on it — because it does.", "Sales discipline"),
    ("A funded account is a vote of trust. Earn it daily.", "Client-first"),
    ("Activation isn't a number; it's a client who finally believed.", "Partner growth"),
    ("Volatility scares clients. Your steadiness keeps them.", "Service edge"),
    ("The best brokers don't chase markets — they build relationships.", "Long game"),
    ("Pick up the phone. Opportunity rarely emails first.", "Prospecting"),
    ("Small consistent actions beat one big pitch, every quarter.", "Run-rate"),
    ("Your pipeline is your portfolio. Diversify and nurture it.", "Pipeline"),
    ("Cross-sell is service: the right product at the right moment.", "Wallet share"),
    ("Discipline in a flat market is what wins the bull run.", "Consistency"),
]


QUOTE_SYSTEM = """Write ONE short, original motivational quote (max ~18 words) for a
STOCK-BROKING sales & relationship team (partner acquisition, account activation,
cross-sell, client trust in volatile markets). Punchy, professional, not cheesy, no
hashtags or emojis. Also give a 1-3 word attribution tag (a theme, not a real person).
Return STRICT JSON: {"quote": "...", "tag": "..."}"""


def daily_quote(seed=""):
    """A motivating, broking-flavored quote for today. AI-composed when a key is set,
    else a rotating curated line. `seed` (e.g. today's date) varies the curated pick."""
    if have_key():
        try:
            data = _chat_json(QUOTE_SYSTEM, {"date": seed, "make_it": "fresh and specific"},
                              max_tokens=120)
            q = (data.get("quote") or "").strip().strip('"')
            t = (data.get("tag") or "Sales").strip()
            if q:
                return q, t
        except Exception:
            pass
    import hashlib
    idx = int(hashlib.md5((seed or "x").encode()).hexdigest(), 16) % len(_QUOTE_FALLBACK)
    return _QUOTE_FALLBACK[idx]


FOLLOWUP_DETECT_SYSTEM = """You read a person's daily work log and find any FUTURE commitment
— something they said they will do, or need to do, on a specific upcoming day. Examples:
"meet Rohit next Thursday", "call the partner tomorrow", "follow up on the 15th", "review
with Anil next week", "send the deck day after".

You are given today's date and weekday. Resolve any relative date ("next Thursday",
"tomorrow", "day after tomorrow", "next week", "this Friday") to an ABSOLUTE calendar date
(YYYY-MM-DD) that is AFTER today. If a weekday is named, choose the NEXT occurrence of that
weekday strictly after today.

Return STRICT JSON:
{"has_followup": true or false,
 "date": "YYYY-MM-DD" or "",
 "what": "short description of what to do / follow up on",
 "who": "person or partner name if mentioned, else empty"}

Set has_followup true ONLY if there is a clear future action with a date you can resolve. If
there are several, pick the single most important. If there is no future action or no
resolvable date, return has_followup false."""


def detect_followup_from_log(transcript, today_date):
    """Scan a daily-log transcript for a future commitment with a date (e.g. 'next Thursday')
    and return {has_followup, date(YYYY-MM-DD), what, who}. Resolves relative dates against
    today_date. Returns {'has_followup': False} when nothing is found or without a key."""
    if not have_key() or not (transcript or "").strip():
        return {"has_followup": False}
    try:
        from datetime import datetime as _dt
        wd = _dt.strptime(today_date, "%Y-%m-%d").strftime("%A")
        ctx = {"today": today_date, "weekday": wd, "log": transcript[:4000]}
        d = _chat_json(FOLLOWUP_DETECT_SYSTEM, ctx, max_tokens=300)
        if not isinstance(d, dict) or not d.get("has_followup"):
            return {"has_followup": False}
        dd = (d.get("date") or "").strip()
        try:
            parsed = _dt.strptime(dd, "%Y-%m-%d").date()
            today = _dt.strptime(today_date, "%Y-%m-%d").date()
        except Exception:
            return {"has_followup": False}
        if parsed <= today:          # must be in the future
            return {"has_followup": False}
        return {"has_followup": True, "date": dd,
                "what": (d.get("what") or "").strip(),
                "who": (d.get("who") or "").strip()}
    except Exception:
        return {"has_followup": False}


NUDGE_SYSTEM = """You are the person's companion coach. Give ONE short, warm, specific nudge
(1-2 sentences) to help them win today. If something they've LEARNED works for them is
relevant, lead with it ("last time X worked — do that again"). Tie it to their goal and make
it actionable. Talk like a friend who wants them to win — not corporate, no lists, no headings.
Return STRICT JSON: {"nudge": "..."}"""


DISTILL_LEARNINGS_SYSTEM = """You compress a person's accumulated work LEARNINGS into a tight,
durable PROFILE that another AI will read on every interaction to personalise its coaching.

You are given the FULL list of this person's accepted learnings (lessons they've confirmed about
how to work better, plus their preferences and operating style). They may number in the dozens.
Your job: distill ALL of them into a compact brief that preserves the ESSENCE — so nothing
important is lost even though the text is short.

Rules:
- Organise into clear CATEGORIES (e.g. "How they like to work / preferences", "Pitching & partners",
  "Follow-up discipline", "Onboarding & KYC", "Reporting", etc.) — only the categories that apply.
- PRESERVE every STANDING PREFERENCE and operating-style point in full force (e.g. "prefers direct
  feedback", "works best in mornings", "always lead with payout economics"). These are the most
  valuable lines — never drop or soften them, even if they're old.
- MERGE duplicates and near-duplicates into one stronger line. Keep specific, actionable detail
  (numbers, timeframes, what worked) — don't generalise away the useful specifics.
- Do NOT invent anything. Every line must trace to the input. If something is unclear, omit it.
- Keep it tight: aim for under ~280 words total. Short bullet lines under each category heading.
- Write it as guidance the coach can act on, in plain language.

Return STRICT JSON: {"brief": "## Category\\n- point\\n- point\\n\\n## Category\\n- point ..."}"""


def distill_learnings(raw_learnings, role_prompt=""):
    """Compress ALL of a person's accepted learnings into a compact, category-organised
    brief that preserves their essence (especially standing preferences). `raw_learnings`
    is a list of dicts with at least 'text' (and optionally 'topic'). Returns the brief
    string. Falls back to a simple grouped concatenation with no key or on any error — so
    personalisation never disappears.

    This is the DURABLE half of the two-layer design: raw learnings are kept forever; this
    distilled brief is a derived view, regenerated from the full raw set (never from a
    previous brief), so it never suffers compounding summary-of-summary loss.
    """
    items = []
    for l in (raw_learnings or []):
        txt = str((l.get("text") if isinstance(l, dict) else l) or "").strip()
        if not txt:
            continue
        tp = str(l.get("topic", "")).strip() if isinstance(l, dict) else ""
        items.append({"topic": tp, "text": txt})
    if not items:
        return ""

    # fallback: group by topic, no AI
    def _fallback():
        from collections import OrderedDict
        groups = OrderedDict()
        for it in items:
            groups.setdefault(it["topic"] or "General", []).append(it["text"])
        out = []
        for g, lines in groups.items():
            out.append(f"## {g}")
            seen = set()
            for ln in lines:
                key = ln.lower()
                if key not in seen:
                    seen.add(key)
                    out.append(f"- {ln}")
        return "\n".join(out)

    if not have_key():
        return _fallback()
    try:
        ctx = {"learnings": items}
        if role_prompt:
            ctx["their_role_context"] = role_prompt[:1500]
        data = _chat_json(DISTILL_LEARNINGS_SYSTEM, ctx, max_tokens=900)
        brief = (data.get("brief") or "").strip()
        return brief or _fallback()
    except Exception:
        return _fallback()


MERGE_DETECT_SYSTEM = """You keep a task list clean by spotting duplicates and groupable tasks.

You are given NEW tasks just dictated (each has an index) and the EXISTING open tasks already
on the list (each has an id). The same task may have been dictated again in different words or
another language — those are DUPLICATES. Different tasks that share the same subject or contact
(e.g. "Call Anil for payout" and "Call Anil for meeting") should be GROUPED under one header
("Call Anil") with each purpose as a subtask.

Return a plan covering the NEW tasks. Each new index 0..N-1 must appear in exactly ONE action:
- {"action":"add","new":[i]} — keep task i as its own task
- {"action":"skip","new":[i],"duplicate_of":"<title it repeats>"} — task i is a duplicate, drop it
- {"action":"group","new":[i,j],"header":"Call Anil","subtasks":["Discuss payout","Discuss the meeting"]} — combine new tasks i and j under one header with these subtasks
- {"action":"attach","new":[i],"existing_id":"<id>","header":"Call Anil","subtasks":["Discuss payout","Discuss the new topic"]} — new task i shares a subject with an EXISTING open task; combine under the header (the existing task becomes the header, so include its purpose as a subtask too)

Rules:
- Only group/attach when tasks genuinely share the same subject or contact. When unsure, "add".
- Be conservative with "skip" — only when the meaning is truly the same.
- subtasks are short action phrases, one per distinct purpose.
- Every new index appears exactly once across all actions.

Return STRICT JSON: {"plan":[ ...actions... ]}"""


def detect_merges(new_titles, existing_open):
    """new_titles: list[str] of the just-generated tasks. existing_open: list of {"id","title"}.
    Returns a plan (list of action dicts). On no key / error, returns [] meaning 'add all as-is'."""
    if not new_titles or not have_key():
        return []
    try:
        payload = {
            "new_tasks": [{"index": i, "title": t} for i, t in enumerate(new_titles)],
            "existing_open_tasks": [{"id": e.get("id"), "title": e.get("title")}
                                    for e in (existing_open or [])][:60],
        }
        data = _chat_json(MERGE_DETECT_SYSTEM, payload, max_tokens=900)
        plan = data.get("plan")
        return plan if isinstance(plan, list) else []
    except Exception:
        return []


KRA_CLASSIFY_SYSTEM = """You assign each work TASK to the single KRA (key result area / goal) it serves.

You are given a list of tasks (each with an id and a title) and the list of valid KRAs for
this person. For EACH task, choose the ONE KRA from the provided list that the task most
directly advances.

Rules:
- You MUST pick a KRA from the provided list, OR the exact string "Unassigned" if the task
  genuinely serves none of them. NEVER invent a KRA that isn't in the list.
- "Self-Improvement" covers the person's own learning, upskilling, training themselves,
  reading, practising a skill — work that builds THEM, not a business outcome. Use it for
  those even though it may not be a formal KPI.
- Judge by the task's intent, not surface words. A "call" can serve acquisition, revenue, or
  servicing depending on what it's for — pick the best fit from the list.
- If a task is purely administrative/operational with no clear goal, use "Unassigned".

Return STRICT JSON, an object mapping each task id to its KRA, e.g.:
{"assignments": {"rinku_0007": "New Partner Acquisition", "rinku_0008": "Self-Improvement", "rinku_0009": "Unassigned"}}"""


def classify_kras_ai(tasks, kra_names):
    """Batched AI KRA assignment. `tasks` is a list of dicts with 'task_id' and 'title'.
    `kra_names` is the list of valid KRAs (KPI names + "Self-Improvement"). Returns a dict
    {task_id: kra}. Only returns KRAs from the allowed list (or "Unassigned"); anything else
    is dropped. Returns {} with no key or on any error, so callers never break.

    Large sets (e.g. a full-history backfill) are split into bounded chunks so no single AI
    call gets too big — each chunk is one call; a failed chunk is skipped, not fatal."""
    items = [{"id": str(t.get("task_id", "")), "title": str(t.get("title", "")).strip()}
             for t in (tasks or []) if str(t.get("task_id", "")).strip()]
    if not items or not have_key():
        return {}
    allowed = set(str(k) for k in kra_names) | {"Self-Improvement", "Unassigned"}
    kra_list = list(kra_names) + ["Self-Improvement"]

    CHUNK = 40
    out = {}
    for i in range(0, len(items), CHUNK):
        batch = items[i:i + CHUNK]
        try:
            data = _chat_json(KRA_CLASSIFY_SYSTEM,
                              {"valid_kras": kra_list, "tasks": batch}, max_tokens=1100)
            for tid, kra in (data.get("assignments") or {}).items():
                kra = str(kra).strip()
                if kra in allowed and kra != "Unassigned":
                    out[str(tid)] = kra
        except Exception:
            continue   # skip this chunk, keep going
    return out


def daily_nudge(open_tasks=None, day_goals=None):
    """One short, personalised nudge for a popup. Reflects the person's role + accepted
    learnings (both injected into every call). Returns '' without a key or on error."""
    if not have_key():
        return ""
    try:
        ctx = {"open_tasks": (open_tasks or [])[:12], "day_goals": (day_goals or [])[:8]}
        d = _chat_json(NUDGE_SYSTEM, ctx, max_tokens=160)
        return (d.get("nudge") or "").strip() if isinstance(d, dict) else ""
    except Exception:
        return ""
