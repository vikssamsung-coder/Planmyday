"""Plan My Day — Desktop console.

Split-screen daily working view:
  Header nav (big spaced tabs, burger overflow)
  Today tab: LEFT half = MIS dashboard (numbers) · RIGHT half = plan + tasks
  Monthly · Learning · Team (lead) open as full-width views

The AI reads the MIS numbers + the role prompt + the planned tasks and nudges;
nothing is ever blocked.

Run:  streamlit run app.py
"""

from datetime import datetime, date

# macOS + threads: system proxy detection (_scproxy/SystemConfiguration) is not safe to
# call from a worker thread, which is where Streamlit runs the script — it hard-crashes
# Python ("trace trap"). Bypassing proxy detection makes network libs (gspread/Drive)
# safe in that thread. Must be set before any networking import.
import os as _osenv
_osenv.environ.setdefault("no_proxy", "*")
_osenv.environ.setdefault("NO_PROXY", "*")

import streamlit as st
import pandas as pd

import storage
import nudge
import ai
import report
import workspace as ws
import style
import dsr

st.set_page_config(page_title="Plan My Day", page_icon="🌅", layout="wide")

# On Streamlit Cloud, API keys live in st.secrets (not env). Bridge them to os.environ
# so ai.py (which reads env) works both locally and on cloud.
import os as _os
try:
    for _k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_MODEL", "ANTHROPIC_MODEL"):
        if _k in st.secrets and not _os.environ.get(_k):
            _os.environ[_k] = str(st.secrets[_k])
except Exception:
    pass

# Optional polished header nav. Falls back to st.tabs-style radio if absent.
try:
    from streamlit_option_menu import option_menu
    HAVE_OPTION_MENU = True
except Exception:
    HAVE_OPTION_MENU = False

TODAY = date.today()
TODAY_STR = TODAY.strftime("%Y-%m-%d")
MONTH = TODAY.strftime("%Y-%m")

# First launch: build the workspace on disk (folders + Excel + md + role prompts),
# seed demo data if empty. Idempotent — runs once per session, creates only what's missing.
if "workspace_ready" not in st.session_state:
    try:
        st.session_state.ws_report = ws.ensure_workspace()
    except Exception as e:
        st.session_state.ws_report = {"base_dir": "?", "created": [], "error": str(e)}
    st.session_state.workspace_ready = True


# ---------------------------------------------------------------- helpers

def committed_kpis(user_key):
    plan = storage.get_plan(user_key, MONTH)
    targets = storage.get_targets(user_key, MONTH)
    kpis = set(plan["linked_kpi"].tolist()) | set(targets["kpi_name"].tolist())
    return {k for k in kpis if k}


def committed_activities(user_key):
    plan = storage.get_plan(user_key, MONTH)
    return plan[["activity", "linked_kpi"]].to_dict("records")


def scorecards(user_key):
    targets = storage.get_targets(user_key, MONTH)
    out = []
    for _, r in targets.iterrows():
        s = nudge.score_kpi(r["monthly_target"], r["achieved_mtd"], TODAY.year, TODAY.month)
        s["kpi_name"] = r["kpi_name"]
        s["priority"] = r["priority"]
        s["target_unit"] = r["target_unit"]
        out.append(s)
    return out


def status_color(status):
    return {"Ahead": "🟢", "On Track": "🟢", "Behind": "🟠",
            "Critical": "🔴", "No Target": "⚪"}.get(status, "⚪")


def horizon_badge(h):
    return "⚡ Today" if h == "Today" else "🌱 Build"


def aligned_badge(a):
    return {"Yes": "✅ Aligned", "Build": "🌱 Build", "No": "⚠️ Unlinked"}.get(a, "")


# ---------------------------------------------------------------- login

def login_view():
    st.title("🌅 Plan My Day")
    st.caption("Your daily execution coach. Plan freely — we'll keep you honest about the goal.")
    with st.container(border=True):
        uk = st.text_input("Username").strip().lower()
        pw = st.text_input("Password", type="password")
        if st.button("Log in", use_container_width=True, type="primary"):
            user = storage.authenticate(uk, pw)
            if user:
                st.session_state.user = user
                st.rerun()
            else:
                st.error("Wrong username or password.")


# ================================================================ MIS dashboard (LEFT half)

@st.cache_data(show_spinner=False)
def _baby_gif_uri():
    """Return the 'guess who's coming' GIF as a data URI. Uses the GIF embedded in
    assets_data.py first (so it rides along with the .py files and never goes missing),
    falling back to the assets/baby.gif file if the module isn't present."""
    try:
        import assets_data
        if getattr(assets_data, "BABY_GIF_B64", ""):
            return "data:image/gif;base64," + assets_data.BABY_GIF_B64
    except Exception:
        pass
    import base64, os
    p = os.path.join(os.path.dirname(__file__), "assets", "baby.gif")
    try:
        with open(p, "rb") as f:
            return "data:image/gif;base64," + base64.b64encode(f.read()).decode()
    except Exception:
        return ""


def _mis_coming_soon(title, subtitle, points, tiles=4, emoji="📊", status="syncing your data… ✨"):
    """A fun, Gen-Z 'coming soon' state for MIS views while the backend is being connected:
    animated rainbow gradient, floating blobs, sparkles, a dancing GIF, gradient text, emoji
    chips, and a shimmer 'syncing' bar — plus skeleton preview tiles for the future layout."""
    css = """
<style>
@keyframes csGrad{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}
@keyframes csBlob{0%,100%{transform:translate(0,0) scale(1)}33%{transform:translate(16px,-12px) scale(1.12)}66%{transform:translate(-12px,10px) scale(.92)}}
@keyframes csTwinkle{0%,100%{opacity:.2;transform:scale(.7)}50%{opacity:1;transform:scale(1.25)}}
@keyframes csBounce{0%,100%{transform:translateY(0)}50%{transform:translateY(-9px)}}
@keyframes csBar{0%{left:-45%}100%{left:100%}}
@keyframes csDot{0%,100%{box-shadow:0 0 0 0 rgba(0,255,193,.65)}70%{box-shadow:0 0 0 9px rgba(0,255,193,0)}}
@keyframes csShimmer{0%{background-position:-220px 0}100%{background-position:220px 0}}
.cs-hero{position:relative;overflow:hidden;border-radius:22px;padding:30px 22px;margin:6px 0 14px;text-align:center;
  background:linear-gradient(120deg,#5367FC,#7C3AED,#EC4899,#00FFC1,#5367FC);background-size:300% 300%;
  animation:csGrad 8s ease infinite;color:#fff;}
.cs-blob{position:absolute;border-radius:50%;filter:blur(36px);opacity:.55;pointer-events:none;}
.cs-blob.b1{width:170px;height:170px;background:#00FFC1;top:-46px;left:-34px;animation:csBlob 9s ease-in-out infinite;}
.cs-blob.b2{width:150px;height:150px;background:#A855F7;bottom:-44px;right:-24px;animation:csBlob 12s ease-in-out infinite reverse;}
.cs-spark{position:absolute;font-size:14px;animation:csTwinkle 2.4s ease-in-out infinite;pointer-events:none;}
.cs-badge{position:relative;display:inline-flex;align-items:center;gap:7px;background:rgba(255,255,255,.16);
  border:1px solid rgba(255,255,255,.32);border-radius:999px;padding:6px 14px;font-size:.7rem;font-weight:800;
  letter-spacing:.13em;text-transform:uppercase;-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px);}
.cs-livedot{width:8px;height:8px;border-radius:50%;background:#00FFC1;animation:csDot 1.6s infinite;}
.cs-gif{width:152px;max-width:62%;border-radius:18px;margin:14px auto 6px;display:block;background:#fff;
  box-shadow:0 10px 26px rgba(0,0,0,.20);}
.cs-emoji{font-size:2.5rem;display:inline-block;margin:12px 0 2px;animation:csBounce 1.8s ease-in-out infinite;}
.cs-hero h2{font-size:1.7rem;font-weight:800;margin:4px 0 8px;letter-spacing:-.02em;
  background:linear-gradient(90deg,#ffffff,#00FFC1,#ffffff);background-size:200% auto;
  -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;
  animation:csGrad 4s linear infinite;}
.cs-hero p{color:rgba(255,255,255,.94)!important;margin:0 auto;font-size:.95rem;max-width:500px;
  line-height:1.55;font-weight:500;}
.cs-chips{display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin:16px auto 14px;max-width:520px;}
.cs-chip{display:inline-flex;align-items:center;gap:6px;background:rgba(255,255,255,.17);
  border:1px solid rgba(255,255,255,.3);border-radius:999px;padding:6px 13px;font-size:.8rem;font-weight:600;
  color:#fff!important;-webkit-backdrop-filter:blur(4px);backdrop-filter:blur(4px);}
.cs-track{position:relative;height:8px;border-radius:999px;background:rgba(255,255,255,.22);
  overflow:hidden;max-width:420px;margin:4px auto 4px;}
.cs-track>i{position:absolute;top:0;height:100%;width:45%;border-radius:999px;
  background:linear-gradient(90deg,transparent,#00FFC1,#fff,#00FFC1,transparent);animation:csBar 1.5s ease-in-out infinite;}
.cs-status{color:rgba(255,255,255,.9)!important;font-size:.78rem;font-weight:700;margin-top:9px;letter-spacing:.02em;}
.cs-cap{color:#6B7480;font-size:.8rem;margin:14px 2px 8px;text-align:center;font-weight:500;}
.cs-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(128px,1fr));gap:12px;}
.cs-tile{position:relative;border:1px solid #E7E3DC;border-radius:16px;padding:15px 14px 14px;background:#fff;overflow:hidden;}
.cs-tile:before{content:'';position:absolute;top:0;left:0;right:0;height:3px;
  background:linear-gradient(90deg,#5367FC,#00FFC1);}
.cs-sk{height:11px;border-radius:6px;margin:8px 0;
  background:linear-gradient(90deg,#ECEFF3 25%,#F6F8FA 37%,#ECEFF3 63%);
  background-size:420px 100%;animation:csShimmer 1.4s linear infinite;}
.cs-sk.w40{width:42%}.cs-sk.w70{width:72%}.cs-sk.big{height:26px;width:60%}
</style>
"""
    gif = _baby_gif_uri()
    visual = (f'<img class="cs-gif" src="{gif}" alt="guess who is coming"/>' if gif
              else f'<div class="cs-emoji">{emoji}</div>')
    chips = "".join(f'<span class="cs-chip">{p}</span>' for p in points)
    tile = ('<div class="cs-tile"><div class="cs-sk w40"></div><div class="cs-sk big"></div>'
            '<div class="cs-sk w70"></div></div>')
    body = f"""
<div class="cs-hero">
  <span class="cs-blob b1"></span><span class="cs-blob b2"></span>
  <span class="cs-spark" style="top:18px;left:28px;">✨</span>
  <span class="cs-spark" style="top:44px;right:40px;animation-delay:.6s;">⭐</span>
  <span class="cs-spark" style="bottom:62px;left:46px;animation-delay:1.1s;">✨</span>
  <span class="cs-spark" style="bottom:30px;right:58px;animation-delay:1.6s;">💫</span>
  <span class="cs-badge"><span class="cs-livedot"></span> Dropping soon</span>
  {visual}
  <h2>{title}</h2>
  <p>{subtitle}</p>
  <div class="cs-chips">{chips}</div>
  <div class="cs-track"><i></i></div>
  <div class="cs-status">{status}</div>
</div>
<div class="cs-cap">sneak peek — this is the glow-up your live numbers are getting ✨</div>
<div class="cs-tiles">{tile*tiles}</div>
"""
    st.markdown(css + body, unsafe_allow_html=True)


def mis_dashboard(user):
    # MIS backend isn't connected yet — show a fun "coming soon" instead of empty KPIs.
    _mis_coming_soon(
        "Cooking up your dashboard",
        "Your daily target-vs-achievement numbers are syncing up. Sit tight — this glow-up "
        "is almost done. 🔥",
        ["🎯 Target vs achieved", "⚡ Daily run-rate", "📈 KPI trends", "🧩 Source-wise"],
        tiles=2, emoji="📊", status="syncing the data sauce…")
    return []


def _mis_dashboard_live(user):
    uk = user["user_key"]
    cards = scorecards(uk)

    st.markdown("### 📊 MIS Dashboard")
    brief = storage.get_mis_brief(uk, TODAY_STR)
    src = f"MIS — synced {brief['date']}" if brief else "MIS snapshot (placeholder)"
    row = st.columns([4, 1])
    row[0].caption(f"Target vs achievement · as of {TODAY.strftime('%d %b %Y')} · _source: {src}_")
    if row[1].button("↻ Reload", key="mis_reload_today", help="Re-pull from the MIS link",
                     use_container_width=True):
        _mis_quick_reload(uk, user)

    if not cards:
        st.info("No KPIs yet — Reload MIS above, or sync the file in Monthly.")
        return cards

    # Compact KPI tiles (two per row fits the half-width column).
    for i in range(0, len(cards), 2):
        cols = st.columns(2)
        for col, s in zip(cols, cards[i:i + 2]):
            with col:
                with st.container(border=True):
                    st.markdown(f"**{status_color(s['status'])} {s['kpi_name']}**")
                    st.markdown(
                        f"<span style='font-size:1.4rem;font-weight:600'>{s['achieved_mtd']:,.0f}</span>"
                        f"<span style='color:#6B7480'> / {s['monthly_target']:,.0f} {s['target_unit']}</span>",
                        unsafe_allow_html=True)
                    st.caption(f"{s['achievement_pct']}% vs {s['expected_pct']}% exp · "
                               f"gap {s['gap']:,.0f} · need {s['required_run_rate']:,.0f}/day")

    # The headered MIS table (placeholder — the agreed contract shape).
    st.markdown("##### MIS table")
    table = [{
        "KPI": s["kpi_name"],
        "Target": f"{s['monthly_target']:,.0f}",
        "Achieved": f"{s['achieved_mtd']:,.0f}",
        "Gap": f"{s['gap']:,.0f}",
        "Req/day": f"{s['required_run_rate']:,.0f}",
        "Status": s["status"],
    } for s in cards]
    st.dataframe(table, use_container_width=True, hide_index=True)

    return cards


# ================================================================ Plan + tasks (RIGHT half)

def render_daily_targets(user):
    """The 4 target boxes at the top. Returns the list of set goal headings.

    Start empty; user fills heading (<=2 words) + number via Edit. At least one
    set target unlocks dictation and task-adding (the gate).
    """
    uk = user["user_key"]
    goals = storage.get_day_goals(uk, TODAY_STR)
    headings = [g["heading"] for g in goals if g["heading"]]

    head = st.columns([4, 1])
    with head[0]:
        st.markdown("##### 🎯 Today's targets")
    with head[1]:
        if st.button("Edit", key="edit_goals", use_container_width=True):
            st.session_state.editing_goals = not st.session_state.get("editing_goals", False)

    if st.session_state.get("editing_goals"):
        with st.form("goals_form"):
            st.caption("Heading: max 2 words. A target needs a number to count.")
            new_goals = []
            cols = st.columns(4)
            for i in range(4):
                with cols[i]:
                    h = st.text_input(f"Box {i+1}", value=goals[i]["heading"], key=f"gh_{i}",
                                      placeholder="e.g. Revenue")
                    n = st.text_input("Number", value=str(goals[i]["target_number"]),
                                      key=f"gn_{i}", placeholder="83K", label_visibility="collapsed")
                    new_goals.append({"slot": i + 1, "heading": h, "target_number": n})
            if st.form_submit_button("Save targets", type="primary", use_container_width=True):
                storage.save_day_goals(uk, TODAY_STR, new_goals)
                st.session_state.editing_goals = False
                st.rerun()
    else:
        cols = st.columns(4)
        for i, g in enumerate(goals):
            with cols[i]:
                with st.container(border=True):
                    if g["heading"]:
                        st.markdown(f"**{g['heading']}**")
                        st.markdown(f"<span style='font-size:1.3rem;font-weight:600'>{g['target_number']}</span>",
                                    unsafe_allow_html=True)
                    else:
                        st.markdown("<span style='color:#6B7480'>— empty —</span>", unsafe_allow_html=True)
                        st.caption("tap Edit")
    return headings


def plan_and_tasks(user, cards):
    uk = user["user_key"]
    st.markdown("### 🗂️ Today's targets & tasks")
    st.caption(f"{user['name']} · {user['role'].replace('_',' ').title()} · {TODAY.strftime('%d %b %Y')}")

    # Carry unfinished tasks + prepare any due reminder messages, once per session.
    if not st.session_state.get("carried_today"):
        storage.carry_forward(uk, TODAY_STR)
        storage.run_due_message_schedules(uk, TODAY_STR)
        st.session_state.carried_today = True

    headings = render_daily_targets(user)
    st.divider()

    # ---- the gate: no target -> no planning ----
    if not headings:
        st.info("🔒 Set at least one of today's targets to start planning. "
                "Tap **Edit** above, write a 2-word heading and a number.")
        return

    # ---- dictate / type to create tasks ----
    st.markdown("##### Today I want to do…")
    try:
        from streamlit_mic_recorder import mic_recorder
        rec = mic_recorder(start_prompt="🎙️ Dictate", stop_prompt="⏹️ Stop",
                           key="mic", format="wav")
        if rec and rec.get("bytes"):
            with st.spinner("Transcribing…"):
                transcribed = ai.transcribe(rec["bytes"])
                if transcribed:
                    # set state BEFORE the widget is instantiated (allowed)
                    st.session_state.plan_input = transcribed
    except Exception:
        st.caption("🎙️ (Install streamlit-mic-recorder for voice; type below for now.)")

    # one-shot clear requested by a previous run, done before the widget exists
    if st.session_state.pop("_clear_plan", False):
        st.session_state.plan_input = ""

    raw = st.text_area("Plan (one thought per line)", key="plan_input",
                       height=100, label_visibility="collapsed",
                       placeholder="Call funded-not-traded clients\nFollow up top 5 partners")
    if st.button("Generate tasks", type="primary", use_container_width=True):
        with st.spinner("Structuring against your targets…"):
            kpis = set(committed_kpis(uk)) | set(headings)
            acts = committed_activities(uk)
            role_prompt = storage.read_role_prompt(user["role"], uk)
            proposed = ai.generate_tasks(raw, user["role"], kpis, cards, TODAY_STR, role_prompt)
            proposed = [nudge.classify_task(t, kpis, acts) for t in proposed]
            mis_ctx = _mis_cue_context(uk, user, cards)
            for t in proposed:
                t["plan_date"] = TODAY_STR
                if not t.get("day_goal"):
                    hit = nudge.goal_match(
                        f"{t.get('title','')} {t.get('linked_kpi','')}", headings)
                    if hit:
                        t["day_goal"] = hit
                # companion cue — lead with a proven rule for this topic if one exists
                topic = storage._topic_of(t.get("day_goal", ""), t.get("category", ""))
                rule = storage.best_rule(uk, topic)
                t["coach_cue"] = ai.companion_cue(
                    t.get("title", ""), t.get("day_goal", ""), role_prompt,
                    mis_ctx, rule["rule_text"] if rule else "")
            storage.add_tasks(uk, proposed)   # autosave — no Save button
            st.session_state._clear_plan = True
            st.rerun()

    # ---- add a task manually (no AI) ----
    with st.expander("➕ Add a task manually"):
        with st.form("manual_task", clear_on_submit=True):
            mt_title = st.text_input("Task", placeholder="e.g. Call 20 funded-not-traded clients")
            mc = st.columns([2, 1, 1, 1])
            goal_opts = ["—"] + headings
            mt_goal = mc[0].selectbox("Goal it serves", goal_opts,
                                      help="Link it to one of today's targets, or leave —")
            mt_hz = mc[1].selectbox("Horizon", ["Today", "Build"],
                                    help="Delivers today, or builds toward a near-future goal")
            mt_pri = mc[2].selectbox("Priority", ["P1", "P2", "P3", "P4", "P5"], index=1)
            mt_time = mc[3].time_input("⏰ Remind at", value=None, step=300,
                                       help="Optional — buzzes you at this time until you update it")
            if st.form_submit_button("Add task", type="primary"):
                if not mt_title.strip():
                    st.warning("Give the task a title.")
                else:
                    task = {"title": mt_title.strip(), "plan_date": TODAY_STR,
                            "day_goal": "" if mt_goal == "—" else mt_goal,
                            "horizon": mt_hz, "priority": mt_pri, "source": "manual",
                            "due_time": mt_time.strftime("%H:%M") if mt_time else "",
                            "goal_aligned": "Yes" if mt_goal != "—" else "No"}
                    # optional companion cue if a proven rule exists for this goal
                    topic = storage._topic_of(task["day_goal"], "")
                    rule = storage.best_rule(uk, topic)
                    if rule:
                        task["coach_cue"] = f"Last time this worked for you: {rule['rule_text']}"
                    storage.add_tasks(uk, [task])
                    st.success("Task added.")
                    st.rerun()

    # ---- coach nudge: any target with no task pointing at it? (normalized) ----
    tasks = storage.get_tasks(uk, TODAY_STR)
    if not tasks.empty:
        served = {nudge.goal_served(g, headings) for g in tasks["day_goal"].tolist()}
        served.discard(None)
        unserved = [h for h in headings if h not in served]
        if unserved:
            st.warning(f"🧠 **Coach:** nothing today points at: **{', '.join(unserved)}**. "
                       "Add a task, or move it to tomorrow?")

    st.divider()

    if tasks.empty:
        st.caption("No tasks yet — dictate or type above, then Generate.")
    else:
        open_t = tasks[~tasks["status"].isin(["Done", "Dropped"])]
        done_t = tasks[tasks["status"] == "Done"]

        role_prompt = storage.read_role_prompt(user["role"], uk)
        st.markdown(f"##### Open · {len(open_t)}")
        for _, t in open_t.iterrows():
            _task_card(uk, t, headings, role_prompt)

        if not done_t.empty:
            with st.expander(f"Done · {len(done_t)}"):
                for _, t in done_t.iterrows():
                    c = st.columns([6, 1])
                    c[0].markdown(f"~~{t['title']}~~")
                    if c[1].button("🗑", key=f"del_{t['task_id']}"):
                        storage.update_task(uk, t["task_id"], status="Dropped"); st.rerun()


def _close_my_day(uk, user, tasks):
    """End-of-day ritual: update today's numbers, check in on each cued task, then the
    final actions (download DSR + back up) at the bottom. Open/close is controlled by the
    caller (a footer button), so task actions never force it open."""
    # ---- today's numbers — based on the targets the USER SAVED FOR TODAY ----
    st.markdown("##### Today's numbers")
    st.caption("Enter what you achieved against the targets you set for today.")
    day_goals = storage.get_day_goals(uk, TODAY_STR)
    saved_goals = [g for g in day_goals if str(g.get("heading", "") or "").strip()]
    existing = storage.get_monthly_progress(uk, month=MONTH)
    today_prog = existing[existing["date"] == TODAY_STR] if not existing.empty else existing
    prior = {r["kpi_name"]: r["achieved"] for _, r in today_prog.iterrows()} if not today_prog.empty else {}
    if saved_goals:
        with st.form("close_numbers"):
            entries = {}
            for i, g in enumerate(saved_goals):
                heading = g["heading"]
                tgt = g.get("target_number", "")
                c = st.columns([3, 2, 2])
                c[0].markdown(heading)
                c[1].caption(f"Target: {tgt or '—'}")
                pv = float(prior.get(heading, 0) or 0)
                entries[heading] = (c[2].number_input("Achieved", value=pv, step=1.0,
                                                      key=f"close_ach_{i}",
                                                      label_visibility="collapsed"), tgt)
            # action button at the bottom of the block
            if st.form_submit_button("Save today's numbers", type="primary"):
                for heading, (ach, tgt) in entries.items():
                    storage.record_monthly_progress(
                        uk, TODAY_STR, MONTH, heading, str(tgt or ""),
                        str(int(ach) if ach == int(ach) else ach))
                st.success("Numbers saved for today.")
                st.rerun()
    else:
        st.info("You haven't set today's targets yet — set them in the daily goal boxes on "
                "Today, and you can log your achievement against them here.")

    st.divider()
    # ---- the cue check-ins ----
    st.markdown("##### How did my suggestions go?")
    st.caption("Quick check-in, like a friend would. Wins get saved so I lead with "
               "them next time.")
    cued = tasks[tasks["coach_cue"].astype(str).str.len() > 0]
    reviewed = storage.get_outcomes(uk, TODAY_STR)
    done_ids = set(reviewed["task_id"]) if not reviewed.empty else set()
    pending = [t for _, t in cued.iterrows() if t["task_id"] not in done_ids]
    if not pending:
        st.success("All checked in for today. 🙌")
    else:
        for t in pending:
            with st.container(border=True):
                st.markdown(f"**{t['title']}**")
                st.caption(f"💬 {t['coach_cue']}")
                tried = st.radio("Did you try what I suggested?", ["—", "Yes", "No"],
                                 horizontal=True, key=f"tr_{t['task_id']}")
                result = ""
                if tried == "Yes":
                    result = st.radio("How did it go?", ["success", "partial", "failure"],
                                      horizontal=True, key=f"rs_{t['task_id']}")
                # action button at the bottom of each question block
                if st.button("Save check-in", key=f"sv_{t['task_id']}",
                             use_container_width=True):
                    if tried == "—":
                        st.warning("Pick Yes or No first."); st.stop()
                    topic = storage._topic_of(t["day_goal"], t.get("category", ""))
                    storage.add_outcome(uk, TODAY_STR, t["task_id"], t["title"], topic,
                                        t["coach_cue"], tried, result)
                    if tried == "Yes" and result == "success":
                        rule = ai.distill_rule(t["coach_cue"], t["title"], t["day_goal"])
                        storage.promote_rule(uk, user["role"], topic, rule)
                        r = storage.best_rule(uk, topic)
                        badge = "tried & tested ✅" if r and r["status"] == "tested" else "saved as a candidate"
                        st.success(f"Nice — saved that one ({badge}): \"{rule}\"")
                    else:
                        st.info("Logged. We'll learn from it.")
                    st.rerun()

    # ---- final actions: DSR (download + silent daily save) + backup ----
    st.divider()
    st.markdown("##### Finish")
    dsr_bytes = dsr.build_docx(user, TODAY_STR, MONTH)

    # save today's DSR silently (text → cloud-synced store + a local Word archive). Once
    # per session per day; no message — the user just sees the download button.
    if st.session_state.get("dsr_saved_date") != TODAY_STR:
        try:
            storage.save_dsr(uk, TODAY_STR, dsr.docx_to_text(dsr_bytes))
            # local Word archive in the user's reports folder
            import paths, os as _os
            rep_dir = paths.user_reports_dir(uk)
            _os.makedirs(rep_dir, exist_ok=True)
            with open(_os.path.join(rep_dir, f"DSR_{TODAY_STR}.docx"), "wb") as _fh:
                _fh.write(dsr_bytes)
            storage.sync_to_sheets(uk)   # silent, incremental push of the changed DSR store
        except Exception:
            pass
        st.session_state["dsr_saved_date"] = TODAY_STR

    fa = st.columns(2)
    fa[0].download_button("⬇️ Download day report (Word)",
                          data=dsr_bytes,
                          file_name=f"DSR_{user['user_key']}_{TODAY_STR}.docx",
                          mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                          type="primary", use_container_width=True)
    if fa[1].button("☁️ Back up to Google Sheets", key="closeday_backup",
                    use_container_width=True):
        with st.spinner("Backing up…"):
            pushed, _, err = storage.sync_to_sheets(uk, force=True)
        import time as _t
        st.session_state["last_backup_ts"] = _t.time()
        (st.success if not err else st.warning)(
            f"Backed up {pushed} file(s)." if not err else f"Backup issue: {err}")

    # Close the day = download the report (above) + mark it closed, so tomorrow doesn't block.
    if storage.is_day_closed(uk, TODAY_STR):
        st.success("✅ Today is closed. Report saved & downloadable above.")
    else:
        st.caption("Download the report above, then close the day to wrap up.")
        if st.button("🌙 Close the day", type="primary", key="close_today_btn",
                     use_container_width=True):
            storage.mark_day_closed(uk, TODAY_STR)
            st.session_state["closeday_open"] = False
            st.rerun()
    st.caption("The day report covers targets vs achievement, tasks & cues, meetings, "
               "reminders, monthly standing, and what's working. Saved automatically.")


def _meeting_form(user):
    """Log a meeting: type -> type-specific identity -> dictate outcome -> AI rewrite
    -> save -> propose a scheduled follow-up if a next date was found."""
    uk = user["user_key"]
    type_label = {"new_partner": "New partner", "existing_partner": "Existing partner",
                  "client": "Client", "internal": "Internal team"}
    mtype = st.selectbox("Meeting with", list(type_label.keys()),
                         format_func=lambda k: type_label[k], key="mtg_type")
    mcol = st.columns(2)
    pname = mcol[0].text_input("Partner / person name", key="mtg_name",
                               placeholder="e.g. Rajesh Kumar")
    id_label = storage.IDENTITY_LABELS[mtype][1]
    identity = mcol[1].text_input(id_label, key="mtg_identity",
                                  placeholder={"new_partner": "98xxxxxxxx",
                                               "existing_partner": "PTR123",
                                               "client": "CLI456",
                                               "internal": "name"}.get(mtype, ""))
    # mic dictation for the outcome (same flow as the Today plan box)
    try:
        from streamlit_mic_recorder import mic_recorder
        rec = mic_recorder(start_prompt="🎙️ Dictate outcome", stop_prompt="⏹️ Stop",
                           key="mtg_mic", format="wav")
        if rec and rec.get("bytes"):
            with st.spinner("Transcribing…"):
                txt = ai.transcribe(rec["bytes"])
                if txt:
                    st.session_state.mtg_raw = txt
    except Exception:
        st.caption("🎙️ (Install streamlit-mic-recorder for voice; type below.)")
    raw = st.text_area("Outcome (dictate or type what happened)", key="mtg_raw",
                       height=90, placeholder="Met them, showed the platform, "
                       "they liked it but want to think — call back Thursday.")
    if st.button("Save meeting", type="primary", key="mtg_save"):
        itype, ival, warn = storage.normalize_identity(mtype, identity)
        if not ival:
            st.error(f"Please enter the {id_label.lower()}.")
            return
        if warn:
            st.warning(warn)
        with st.spinner("Writing up the meeting…"):
            role_prompt = storage.read_role_prompt(user["role"], uk)
            d = ai.rewrite_meeting(raw, mtype, ival, role_prompt, TODAY_STR)
            # link to the partner identity; upsert to Directory when a phone is present
            p_mobile = ival if itype == "mobile" else ""
            p_code = ival if itype in ("partner_code", "client_code") else ""
            if p_mobile or p_code or pname:
                _pid, pident = storage.upsert_partner(
                    uk, name=pname, mobile=p_mobile, code=p_code) \
                    if (p_mobile or p_code) else (None, storage._partner_identity("", "", pname))
            else:
                pident = ""
            mid = storage.save_meeting(uk, {
                "date": TODAY_STR, "meeting_type": mtype,
                "partner_name": pname,
                "identity_type": itype, "identity_value": ival,
                "partner_identity": pident,
                "discussed": d.get("discussed", ""), "outcome": d.get("outcome", ""),
                "objections": d.get("objections", ""),
                "pipeline_stage": d.get("pipeline_stage", ""),
                "next_action": d.get("next_action", ""), "next_date": d.get("next_date", ""),
                "ai_written": d.get("ai_written", ""), "raw_dictation": raw,
            })
        st.session_state.last_meeting = {"mid": mid, "ival": ival,
                                         "next_action": d.get("next_action", ""),
                                         "next_date": d.get("next_date", "")}
        st.success("Meeting saved to your Daily Log.")
        st.rerun()

    # propose a follow-up if the last saved meeting had a next date
    lm = st.session_state.get("last_meeting")
    if lm and lm.get("next_date"):
        st.info(f"📅 Next action: **{lm['next_action'] or 'follow up'}** "
                f"on **{lm['next_date']}** with {lm['ival']}.")
        c1, c2 = st.columns(2)
        if c1.button("Schedule follow-up", type="primary", key="mtg_sched"):
            storage.schedule_followup(uk, lm["next_date"], lm["ival"], lm["mid"],
                                      title=f"Follow up: {lm['ival']} — {lm['next_action']}")
            st.session_state.last_meeting = None
            st.success(f"Scheduled — it'll appear in your tasks on {lm['next_date']}.")
            st.rerun()
        if c2.button("Skip", key="mtg_skip"):
            st.session_state.last_meeting = None
            st.rerun()


def _mis_context(cards):
    """Short text of which KPIs are behind, for grounding the companion cue."""
    bits = []
    for s in (cards or []):
        if s.get("status") in ("Behind", "Critical"):
            bits.append(f"{s.get('kpi_name')} is {s.get('status')} "
                        f"({s.get('achievement_pct')}% done, need {s.get('required_run_rate')}/day)")
    return "; ".join(bits) or "on track across KPIs"


def _mis_cue_context(uk, user, cards):
    """Prefer the once-a-day grounded MIS brief (KB) for cue context; fall back to the
    live scorecard summary."""
    brief = storage.get_mis_brief(uk, TODAY_STR)
    if brief and (brief.get("brief") or "").strip():
        return brief["brief"]
    return _mis_context(cards)


def _step_changed(uk, task_id, i, key):
    """Checkbox on_change: write this step's value, then sync task status —
    task auto-completes ONLY when every step is checked, never on one step."""
    storage.set_step_done(uk, task_id, i, st.session_state.get(key, False))
    storage.sync_task_from_steps(uk, task_id)


def _task_card(uk, t, headings, role_prompt=""):
    """One open/carried task as a COLLAPSIBLE card. The label summarises it so the
    collapsed state is still informative; expanding reveals goal, steps, actions."""
    carried = bool(t["carried_from"])
    steps = storage.get_task_steps(uk, t["task_id"])

    # build an informative expander label
    bits = [("↪ " if carried else "") + t["title"]]
    if steps:
        done_n = sum(1 for s in steps if s["done"])
        bits.append(f"{done_n}/{len(steps)} steps")
    if t["day_goal"]:
        bits.append(f"🔗 {t['day_goal']}")
    elif not (t["source"] == "follow_up"):
        bits.append("⚠️ no goal")
    if t["source"] == "follow_up" and t["followup_for"]:
        bits.append(f"📞 {t['followup_for']}")
    label = "  ·  ".join(bits)

    # gist line — a short, always-visible summary of what this task is about
    cue = ""
    try:
        cue = (t["coach_cue"] or "").strip()
    except Exception:
        cue = ""
    if t["source"] == "follow_up" and t["followup_for"]:
        ff = f"Follow up with {t['followup_for']}"
        gist = f"{ff} — {cue}" if cue else ff
    else:
        gist = cue
    if gist:
        st.caption(f"💡 {gist[:110]}")

    with st.expander(label, expanded=False):
        # goal dropdown — populated from today's targets + explicit no-goal
        choices = headings + ["— no goal (just needed) —"]
        cur = t["day_goal"] if t["day_goal"] in headings else choices[-1]
        pick = st.selectbox("Goal this serves", choices, index=choices.index(cur),
                            key=f"goal_{t['task_id']}", label_visibility="collapsed")
        new_goal = "" if pick.startswith("— no goal") else pick
        if new_goal != t["day_goal"]:
            storage.update_task(uk, t["task_id"], day_goal=new_goal)

        tags = []
        tags.append("⚡ Today" if t["horizon"] != "Build" else "🌱 Build")
        tags.append("🔗 " + new_goal if new_goal else "⚠️ No goal")
        if t["source"] == "follow_up" and t["followup_for"]:
            tags.append(f"📞 follow-up · {t['followup_for']}")
        if carried:
            tags.append(f"↪ carried from {t['carried_from']}")
        st.caption(" · ".join(tags))
        if not new_goal:
            st.caption("⚠️ Kept because you chose it — won't move today's numbers.")

        # companion cue — the saved "how to do this well" nudge
        cue = t["coach_cue"]
        cc = st.columns([6, 1])
        if cue:
            cc[0].markdown(f"💬 _{cue}_")
        else:
            cc[0].caption("💬 no cue yet")
        if cc[1].button("🔄", key=f"cue_{t['task_id']}", help="Rethink this cue"):
            with st.spinner("Thinking…"):
                topic = storage._topic_of(new_goal, t.get("category", ""))
                rule = storage.best_rule(uk, topic)
                new_cue = ai.companion_cue(t["title"], new_goal, role_prompt,
                                           _mis_cue_context(uk, user, None),
                                           rule["rule_text"] if rule else "")
                storage.update_task(uk, t["task_id"], coach_cue=new_cue)
            st.rerun()

        # ---- due time (reminder) ----
        import datetime as _dt
        dc = st.columns([2, 3])
        cur_time = (t.get("due_time") or "").strip()
        default_t = None
        if cur_time:
            for f in ("%H:%M", "%H:%M:%S"):
                try:
                    default_t = _dt.datetime.strptime(cur_time, f).time(); break
                except Exception:
                    pass
        set_time = dc[0].time_input("⏰ Remind at", value=default_t,
                                    key=f"due_{t['task_id']}", step=300)
        new_time = set_time.strftime("%H:%M") if set_time else ""
        if new_time != cur_time:
            storage.update_task(uk, t["task_id"], due_time=new_time)
        if new_time:
            dc[1].caption(f"Buzzer will remind you at {new_time} and re-nag every 5 min "
                          "until you post an update below.")

        # ---- Task update row (the act-to-stop signal) ----
        ups = storage.get_task_updates(uk, t["task_id"])
        if not ups.empty:
            with st.popover(f"📝 Updates ({len(ups)})"):
                for _, up in ups.iterrows():
                    st.caption(f"**{up['created_at'][11:16]}** — {up['remark']}")
        urow = st.columns([5, 1])
        rmk = urow[0].text_input("Task update", key=f"upd_{t['task_id']}",
                                 placeholder="Type a quick remark to log progress / stop the buzzer",
                                 label_visibility="collapsed")
        if urow[1].button("Update", key=f"updbtn_{t['task_id']}", use_container_width=True):
            if rmk.strip():
                storage.add_task_update(uk, t["task_id"], rmk.strip())
                st.session_state.pop(f"upd_{t['task_id']}", None)
                st.toast("Update logged — buzzer silenced for this task.")
                st.rerun()
            else:
                st.caption("Type a remark first.")

        # collaborators from the Directory (team contacts) + share the plan on WhatsApp
        directory = storage.get_partners(uk)
        team = directory[directory["contact_type"] == "team"] if not directory.empty else directory
        if not team.empty:
            names = list(team["name"])
            current = storage.get_task_collaborators(uk, t["task_id"])
            picked = st.multiselect("Collaborators", names,
                                    default=[n for n in current if n in names],
                                    key=f"collab_{t['task_id']}",
                                    placeholder="Add teammates (from your Directory)")
            if set(picked) != set(current):
                storage.set_task_collaborators(uk, t["task_id"], picked)
            if picked:
                with st.popover("📲 Share on WhatsApp"):
                    mk = f"share_{t['task_id']}"
                    if st.button("Write message", key=f"wmsg_{t['task_id']}"):
                        st.session_state[mk] = ai.share_plan_message(
                            t["title"], ", ".join(picked), t["day_goal"], t["coach_cue"],
                            storage.get_user(uk).get("name", "I"))
                    msg = st.session_state.get(mk, "")
                    msg = st.text_area("Message", value=msg, key=f"wtext_{t['task_id']}",
                                       height=90)
                    for n in picked:
                        mob = storage.partner_mobile(uk, n)
                        if mob:
                            st.link_button(f"Send to {n}", storage.wa_link(mob, msg),
                                           use_container_width=True)
                        else:
                            st.caption(f"{n}: no mobile on file")
        else:
            st.caption("Add teammates in Records → Directory (type: team) to collaborate.")

        # steps (if broken down) — checkbox uses on_change callback (no value=, no
        # manual rerun) so one click = one toggle, reflected immediately.
        if steps:
            done_n = sum(1 for s in steps if s["done"])
            st.caption(f"Steps · {done_n}/{len(steps)} done"
                       + (" · all done → task complete" if done_n == len(steps) else ""))
            for i, s in enumerate(steps):
                ck = f"step_{t['task_id']}_{i}"
                if ck not in st.session_state:
                    st.session_state[ck] = bool(s["done"])
                st.checkbox(s["text"], key=ck,
                            on_change=_step_changed, args=(uk, t["task_id"], i, ck))
            # edit steps by AI prompt
            with st.popover("✏️ Edit steps with AI"):
                instr = st.text_input("How should the steps change?",
                                      key=f"si_{t['task_id']}",
                                      placeholder="e.g. add a prep-call step, split the last one")
                if st.button("Rewrite steps", key=f"sr_{t['task_id']}"):
                    cur_texts = [s["text"] for s in steps]
                    done_map = {s["text"]: s["done"] for s in steps}
                    new_texts = ai.edit_steps(t["title"], cur_texts, instr, t["day_goal"], role_prompt)
                    new_steps = [{"text": x, "done": done_map.get(x, False)} for x in new_texts]
                    storage.set_task_steps(uk, t["task_id"], new_steps)
                    # remember the user-curated steps for similar future tasks
                    topic = storage._topic_of(t["day_goal"], t.get("category", ""))
                    storage.save_step_template(uk, topic, t["title"], new_texts)
                    for i in range(len(steps)):
                        st.session_state.pop(f"step_{t['task_id']}_{i}", None)
                    storage.sync_task_from_steps(uk, t["task_id"])
                    st.rerun()

        if steps:
            # Task with steps completes ONLY by ticking all steps.
            b = st.columns(2)
            if b[0].button("✏️ Edit", key=f"edit_{t['task_id']}", use_container_width=True):
                st.session_state[f"editing_{t['task_id']}"] = True
            if b[1].button("🗑 Delete", key=f"deltask_{t['task_id']}", use_container_width=True):
                storage.update_task(uk, t["task_id"], status="Dropped"); st.rerun()
        else:
            b = st.columns(4)
            if b[0].button("✅ Done", key=f"done_{t['task_id']}", use_container_width=True):
                storage.update_task(uk, t["task_id"], status="Done"); st.rerun()
            if b[1].button("✏️ Edit", key=f"edit_{t['task_id']}", use_container_width=True):
                st.session_state[f"editing_{t['task_id']}"] = True
            if b[2].button("🪜 Steps", key=f"steps_{t['task_id']}", use_container_width=True):
                topic = storage._topic_of(t["day_goal"], t.get("category", ""))
                past, matched = storage.find_step_template(uk, topic, t["title"])
                with st.spinner("Breaking into steps…" + (" (using your past steps)" if past else "")):
                    s = ai.break_into_steps(t["title"], t["day_goal"], role_prompt, past_steps=past)
                    storage.set_task_steps(uk, t["task_id"], s)
                    storage.log_task_event(uk, t["task_id"], t["title"], "steps_added",
                                           t["day_goal"], detail=f"{len(s)} steps"
                                           + (f" (from '{matched}')" if past else ""))
                st.rerun()
            if b[3].button("🗑", key=f"del_{t['task_id']}", use_container_width=True):
                storage.update_task(uk, t["task_id"], status="Dropped"); st.rerun()

            # let the user choose his own steps instead of AI
            with st.popover("✍️ Write my own steps"):
                own = st.text_area("One step per line", key=f"own_{t['task_id']}", height=90,
                                   placeholder="Call the partner\nShow platform demo\nBook next meeting")
                if st.button("Set steps", key=f"setown_{t['task_id']}"):
                    lines = [ln.strip() for ln in own.splitlines() if ln.strip()]
                    if lines:
                        storage.set_task_steps(uk, t["task_id"], lines)
                        # remember the user's own steps for similar future tasks
                        topic = storage._topic_of(t["day_goal"], t.get("category", ""))
                        storage.save_step_template(uk, topic, t["title"], lines)
                        storage.log_task_event(uk, t["task_id"], t["title"], "steps_added",
                                               t["day_goal"], detail=f"{len(lines)} own steps")
                        st.rerun()

        if st.session_state.get(f"editing_{t['task_id']}"):
            nt = st.text_input("Title", value=t["title"], key=f"t_{t['task_id']}")
            if st.button("Save", key=f"save_{t['task_id']}"):
                storage.update_task(uk, t["task_id"], title=nt)
                st.session_state[f"editing_{t['task_id']}"] = False
                st.rerun()




# ================================================================ tab views

def _buzzer(uk, user):
    """Auto-refreshing reminder. Finds due tasks (act-to-stop) and pops a banner + plays
    a video buzzer, re-nagging every 5 min until the user posts a Task update."""
    import datetime as _dt
    # auto-refresh every 60s so the clock is re-checked without a manual reload
    # (kept at 60s, not less, to limit Google Sheets read volume / avoid 429 quota)
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=60000, key="buzz_tick")
    except Exception:
        st.markdown("<meta http-equiv='refresh' content='60'>", unsafe_allow_html=True)

    due = storage.due_buzzing_tasks(uk, TODAY_STR)
    if not due:
        return
    for t in due:
        storage.mark_buzzed(uk, t["task_id"])   # stamp so the 5-min re-nag clock starts
    names = ", ".join(t["title"] for t in due[:3])
    more = f"  ·  +{len(due)-3} more" if len(due) > 3 else ""

    # Prominent FLASHING banner — a CSS animation so it grabs attention on every device
    # without depending on a media file (mobile/Cloud block autoplay audio anyway).
    st.markdown(f"""
    <style>
    @keyframes pmdBuzz {{
      0%,100%{{ background:#D9544D; box-shadow:0 0 0 0 rgba(217,84,77,.55); }}
      50%{{ background:#A8352E; box-shadow:0 0 0 12px rgba(217,84,77,0); }}
    }}
    .pmd-buzzer{{
      animation:pmdBuzz 1s ease-in-out infinite;
      color:#fff !important; border-radius:12px; padding:14px 18px; margin:4px 0 14px;
      font-weight:700; font-size:1.02rem; border:2px solid rgba(255,255,255,.35);
    }}
    .pmd-buzzer .sub{{ font-weight:500; font-size:.85rem; opacity:.96; display:block;
      margin-top:5px; color:#fff !important; }}
    </style>
    <div class="pmd-buzzer">🔔 Reminder — action needed now: {names}{more}
      <span class="sub">Open the task below and post an update to stop the buzzer.</span>
    </div>
    """, unsafe_allow_html=True)

    # Video buzzer — plays with SOUND (autoplay + unmuted). Note: browsers block UNMUTED
    # autoplay until the user has interacted with the page, so sound is reliable after any
    # click in the session; on a brand-new page load the browser may mute the first play.
    # The flashing banner above is the always-reliable visual cue.
    import os as _os
    vid = _os.path.join(_os.path.dirname(__file__), "assets", "buzzer.mp4")
    if not _os.path.exists(vid):   # fallback to a workspace copy if present
        import paths
        vid = _os.path.join(paths.base_dir(), "_common", "buzzer.mp4")
    if _os.path.exists(vid):
        try:
            st.video(vid, autoplay=True, muted=False, loop=True)
        except TypeError:
            try:
                st.video(vid, autoplay=True, muted=False)
            except TypeError:
                st.video(vid)   # older Streamlit without autoplay/muted args
    else:
        st.markdown(
            "<audio autoplay><source src='https://actions.google.com/sounds/v1/alarms/"
            "beep_short.ogg' type='audio/ogg'></audio>", unsafe_allow_html=True)


def _maybe_nudge_popup(user):
    """Show ONE nudge popup per session, capped at 4 per day. Driven by the person's role +
    accepted learnings (both injected into the AI call)."""
    uk = user["user_key"]
    if st.session_state.get("nudge_popped_session"):
        return
    if storage.get_popup_count(uk, TODAY_STR) >= 4:        # daily cap
        return
    try:
        tasks = storage.get_tasks(uk, TODAY_STR)
        open_titles, goals = [], []
        if not tasks.empty:
            open_t = tasks[~tasks["status"].isin(["Done", "Dropped"])]
            open_titles = list(open_t["title"])[:12]
            goals = [g for g in tasks["day_goal"].tolist() if g][:8]
        msg = ai.daily_nudge(open_titles, goals)
    except Exception:
        msg = ""
    st.session_state["nudge_popped_session"] = True       # one per session regardless
    if msg:
        try:
            st.toast(msg, icon="💡")
        except Exception:
            st.info(f"💡 {msg}")
        storage.bump_popup_count(uk, TODAY_STR)


def today_view(user):
    _buzzer(user["user_key"], user)
    _maybe_nudge_popup(user)
    _mis_alert_banner(user["user_key"], user)
    left, right = st.columns([1, 1], gap="large")
    with left:
        cards = mis_dashboard(user)
    with right:
        plan_and_tasks(user, cards)

    # Close My Day — a FOOTER you open with a button (so closing tasks never pops it open).
    #  Shows weekdays after 4 PM, or in the morning if the last working day wasn't closed.
    uk = user["user_key"]
    if _should_show_close_my_day(uk):
        st.divider()
        if not st.session_state.get("closeday_open"):
            st.markdown('<div class="closeday-bar">🌙 Close My Day'
                        '<span class="sub">Wrap up — today\'s numbers, cue check-ins, DSR & backup</span>'
                        '</div>', unsafe_allow_html=True)
            if st.button("🌙 Open Close My Day", use_container_width=True, key="open_closeday"):
                st.session_state["closeday_open"] = True
                st.rerun()
        else:
            mid = st.columns([1, 6, 1])[1]
            with mid:
                if st.button("✖ Collapse", key="hide_closeday"):
                    st.session_state["closeday_open"] = False
                    st.rerun()
                _close_my_day(uk, user, storage.get_tasks(uk, TODAY_STR))


def _ensure_mis_brief(uk, user, force=False):
    """Generate today's MIS brief once per day (or on force). Stores a snapshot for
    slip-detection and the grounded brief as KB for the nudge. Returns the brief dict."""
    existing = storage.get_mis_brief(uk, TODAY_STR)
    if existing and not force:
        return existing
    targets = storage.get_targets(uk, MONTH)
    # auto-pull from the configured MIS link if we have no targets yet (first run of the day)
    if targets.empty:
        import mis_sync
        link = mis_sync.mis_url()
        if link and not st.session_state.get("mis_autopull_tried"):
            st.session_state["mis_autopull_tried"] = True
            try:
                data = mis_sync.fetch_excel(link)
                summary = mis_sync.parse_summary(data, uk, user.get("name", ""))
                if summary and summary["kpis"]:
                    month = summary["month"] or MONTH
                    storage.set_targets_from_mis(uk, month, user.get("role", ""), summary["kpis"])
                    mis_sync.save_fingerprint(uk, summary)
                    targets = storage.get_targets(uk, MONTH)
            except Exception:
                pass
    if targets.empty:
        return None
    situation = nudge.kpi_situation(targets, TODAY.year, TODAY.month)
    prev_snap = storage.get_mis_snapshot(uk, _prev_working_day(TODAY).isoformat())
    slipped = nudge.newly_slipped(situation, prev_snap)
    behind = nudge.behind_kpis(situation)
    brief = ai.mis_brief(situation, behind, slipped)
    storage.save_mis_snapshot(uk, TODAY_STR, situation)
    storage.save_mis_brief(uk, TODAY_STR, brief, behind, slipped)
    return storage.get_mis_brief(uk, TODAY_STR)


def _mis_alert_banner(uk, user):
    """Show the daily MIS brief as an alert at the top of Today."""
    brief = _ensure_mis_brief(uk, user)
    if not brief or not (brief.get("brief") or "").strip():
        return
    behind = brief.get("behind_csv", "")
    slipped = brief.get("slipped_csv", "")
    tone = "error" if behind else ("warning" if slipped else "info")
    icon = "🔴" if behind else ("🟠" if slipped else "🟢")
    head = "MIS alert" if behind else ("Heads-up" if slipped else "MIS — on pace")
    box = {"error": st.error, "warning": st.warning, "info": st.info}[tone]
    box(f"{icon} **{head}** · {brief['brief']}")
    if slipped:
        st.caption(f"Newly slipped since yesterday: {slipped}")


def _prev_working_day(d):
    """The previous working day before d (only Sunday is treated as non-working)."""
    from datetime import timedelta
    p = d - timedelta(days=1)
    while p.weekday() == 6:        # Sun=6 only
        p -= timedelta(days=1)
    return p


def _should_show_close_my_day(uk):
    now = datetime.now()
    if now.weekday() == 6:                     # Sunday → never
        return False
    if now.hour >= 16:                         # after 4 PM → end-of-day wrap
        return True
    # earlier in the day → only if the previous working day wasn't closed
    prev = _prev_working_day(now.date()).isoformat()
    return not storage.is_day_closed(uk, prev)


def communicate_view(user):
    uk = user["user_key"]
    st.markdown("### 📣 Communicate")
    st.caption("Left: compose a message (AI reads your image). Right: pick contacts and send "
               "via WhatsApp Web. WhatsApp links carry text only — attach the media in the one tap.")

    left, right = st.columns([1, 1], gap="large")

    # ---------------------------------------------------- LEFT: compose
    with left:
        st.markdown("#### Compose")
        media = st.file_uploader("Image or video (optional)",
                                 type=["png", "jpg", "jpeg", "mp4", "mov"], key="comm_media")
        kind = "image"; img_bytes = None
        if media is not None:
            if media.type.startswith("image"):
                st.image(media, width=260); img_bytes = media.getvalue(); kind = "image"
            else:
                st.video(media); kind = "video"

        intent = st.text_input("What's the message about?",
                               placeholder="Morning market update / new product / festive wish")
        audience = st.radio("Audience", ["Partners / clients", "My team"], horizontal=True,
                            key="comm_audience",
                            help="‘My team’ switches to a motivating sales-leader tone")
        aud = "team" if audience == "My team" else "partner"
        if st.button("✍️ Write the message", type="primary"):
            with st.spinner("Writing…"):
                generated = ai.broadcast_message(intent, img_bytes, kind, audience=aud)
            st.session_state["comm_text"] = generated   # write into the widget's own key
            err = ai.last_broadcast_error()
            if err and not generated.strip():
                st.warning(f"Couldn't generate a message: {err} — type one below.")
            elif err:
                st.caption(f"(note: {err} — used a fallback; edit below.)")

        st.text_area("Message", key="comm_text", height=150,
                     placeholder="Your message appears here after you click Write — or type your own.")

    # ---------------------------------------------------- RIGHT: contacts + send
    with right:
        st.markdown("#### Contacts")
        directory = storage.get_partners(uk)
        if directory is None or directory.empty:
            st.info("Add contacts in Records → Directory (partner or team).")
            return
        # who to message — partners and/or team
        whom = st.multiselect("Include", ["partner", "team"], default=["partner", "team"],
                              key="comm_whom")
        contacts = directory[directory["contact_type"].isin(whom)] if whom else directory.iloc[0:0]
        if contacts.empty:
            st.info("No contacts of the selected type(s) in your Directory.")
            return

        personalise = st.checkbox("Personalise with each contact's name", value=True,
                                  key="comm_personalise")
        inc_meeting = st.radio("Include last meeting details?", ["No", "Yes"],
                               horizontal=True, key="comm_inc_meeting",
                               help="Adds a follow-up line from your last meeting with each contact") == "Yes"

        ctop = st.columns([3, 1])
        ctop[0].caption(f"{len(contacts)} synced from Directory")
        if ctop[1].button("All", help="Select all"):
            for _, p in contacts.iterrows():
                st.session_state[f"pick_{p['partner_id']}"] = True
            st.rerun()

        selected = []
        for _, p in contacts.iterrows():
            pid = p["partner_id"]
            row = st.columns([0.5, 3.2, 0.7])
            checked = row[0].checkbox("", value=st.session_state.get(f"pick_{pid}", True),
                                      key=f"pick_{pid}", label_visibility="collapsed")
            tag = "👤" if p["contact_type"] == "team" else "🤝"
            row[1].markdown(f"{tag} **{p['name']}**  \n{p['mobile'] or '—'}")
            if row[2].button("🗑", key=f"rmp_{pid}", help="Remove contact"):
                storage.remove_partner(uk, pid)
                st.session_state.pop(f"pick_{pid}", None)
                st.rerun()
            if checked and p["mobile"]:
                selected.append((p["name"], p["mobile"]))

        st.divider()
        base_msg = st.session_state.get("comm_text", "")
        st.markdown(f"**Send to {len(selected)} selected**")
        if not base_msg.strip():
            st.caption("Write a message on the left first.")
            return

        def _msg_for(name, mob):
            if not personalise:
                return base_msg
            lm = storage.last_meeting_for(uk, name, mob) if inc_meeting else None
            sal = storage.partner_salutation(uk, name, mob) if aud == "partner" else ""
            return storage.personalise(base_msg, name, lm, salutation=sal)

        if personalise:
            note = "Each message is greeted by name"
            note += "; a short follow-up line from your last meeting is added." if inc_meeting \
                else "."
            st.caption(note)

        # Manual click-to-send only. (Auto-send via WhatsApp Web can't run on the hosted
        # app — it needs a logged-in browser on the same machine — so it's removed.)
        if media is not None:
            st.warning("**Links can't carry media** — WhatsApp links are text-only. The text "
                       "will pre-fill; attach the file yourself after WhatsApp opens.")
        else:
            st.caption("Each opens WhatsApp with the text pre-filled — tap send.")
        for name, mob in selected:
            st.link_button(f"Send to {name}", storage.wa_link(mob, _msg_for(name, mob)),
                           use_container_width=True)


def settings_view(user):
    st.markdown("### ⚙️ Settings")

    # ---- storage / backup (local-first; Sheets is a periodic backup mirror) ----
    st.markdown("#### Storage & backup")
    st.caption("Your data is saved **locally on this machine** (instant, always available). "
               "It's backed up to Google Sheets automatically every ~30 minutes while the "
               "app is open, and when you Close My Day.")
    uk = user["user_key"]
    try:
        import gsheets
        backup_on = gsheets.enabled()
    except Exception:
        backup_on = False

    info = st.session_state.get("last_backup_info")
    last_ts = st.session_state.get("last_backup_ts")
    if last_ts:
        import datetime as _dt
        when = _dt.datetime.fromtimestamp(last_ts).strftime("%I:%M %p")
        st.caption(f"Last backup: {when}" + (f" · {info}" if info else ""))
    else:
        st.caption("No backup yet this session.")

    bc = st.columns(2)
    if bc[0].button("☁️ Back up now", use_container_width=True, disabled=not backup_on):
        with st.spinner("Backing up to Google Sheets…"):
            pushed, skipped, err = storage.sync_to_sheets(uk, force=True)
        import time as _t
        st.session_state["last_backup_ts"] = _t.time()
        if err:
            st.warning(f"Backed up {pushed}, with issues: {err}")
        else:
            st.success(f"Backed up {pushed} file(s).")
    with bc[1].popover("⬇️ Restore from backup", use_container_width=True):
        st.caption("Pull your data **down** from Google Sheets into this machine — use only "
                   "on a new/wiped computer. This overwrites local files.")
        if st.button("Yes, restore from backup", key="do_restore", disabled=not backup_on):
            with st.spinner("Restoring…"):
                restored, err = storage.restore_from_sheets(uk)
            (st.success if not err else st.warning)(
                f"Restored {restored} file(s). Reload the app." if not err
                else f"Restored {restored}, with issues: {err}")
    if not backup_on:
        st.caption("⚠️ Google Sheets backup isn't configured (add SHEET_ID + credentials in "
                   "secrets). Your data is still saved locally.")

    st.divider()
    # ---- account ----
    st.markdown("#### Account")
    st.markdown(f"Signed in as **{user.get('name', user['user_key'])}** "
                f"· role: {user.get('role','').replace('_',' ').title() or '—'}")
    st.caption(f"Knowledge/data scope: your own workspace ({user['user_key']}).")

    st.divider()
    # ---- AI status ----
    st.markdown("#### AI coach")
    try:
        import ai
        if ai.have_key():
            st.success("AI is active — cues, task generation, and message writing are AI-powered.")
        else:
            st.warning("No AI key found — the app runs with simpler rule-based fallbacks. "
                       "Add OPENAI_API_KEY or ANTHROPIC_API_KEY to enable full coaching.")

        # ---- token usage + spend ----
        st.markdown("#### AI usage & spend")
        u = ai.usage_summary()
        def _tok(n):
            return f"{n/1000:.1f}K" if n < 1_000_000 else f"{n/1_000_000:.2f}M"
        c = st.columns(3)
        for col, label, key in zip(c, ["Today", "This month", "All time"],
                                   ["today", "month", "total"]):
            b = u[key]
            tot = b["in"] + b["out"]
            col.metric(f"{label} · spend", f"${b['cost']:.3f}",
                       f"{_tok(tot)} tokens · {b['calls']} calls")
        st.caption(f"Tokens this month — in: {_tok(u['month']['in'])}, "
                   f"out: {_tok(u['month']['out'])}. Cost is an estimate from public "
                   "per-token rates and may differ from your provider invoice.")
    except Exception:
        pass


def _monthly_scorecard(uk, targets):
    """Visual Target vs Achievement scorecard — one card per KPI with a progress bar."""
    st.markdown("#### 📊 Scorecard — Target vs Achievement")
    row = st.columns([4, 1])
    row[0].caption("Target vs achievement for this month")
    if row[1].button("↻ Reload", key="mis_reload_monthly", help="Re-pull from the MIS link",
                     use_container_width=True):
        _mis_quick_reload(uk, st.session_state.user)
    rows = list(targets.iterrows())[:4]
    cols = st.columns(len(rows))
    for col, (idx, r) in zip(cols, rows):
        s = nudge.score_kpi(r["monthly_target"], r["achieved_mtd"], TODAY.year, TODAY.month)
        tgt = float(r["monthly_target"] or 0)
        ach = float(r["achieved_mtd"] or 0)
        pct = min(int(s["achievement_pct"]), 100)
        color = {"On pace": "#2E9E6B", "On track": "#2E9E6B", "Ahead": "#2E9E6B",
                 "Behind": "#E2A13B", "Critical": "#D9544D"}.get(s["status"], "#5C6B7A")
        with col:
            st.markdown(f"""
<div style="background:#fff;border:1px solid #E7E3DC;border-radius:14px;
     box-shadow:0 1px 2px rgba(27,39,51,.04),0 6px 20px rgba(27,39,51,.06);padding:14px 16px;">
  <div style="font-weight:700;font-size:.92rem;color:#1B2733;">{r['kpi_name']}</div>
  <div style="margin:6px 0 2px;">
    <span style="font-size:1.6rem;font-weight:800;letter-spacing:-.02em;">{ach:,.0f}</span>
    <span style="color:#6B7480;font-size:.9rem;"> / {tgt:,.0f}</span>
  </div>
  <div style="background:#EEE9E1;border-radius:999px;height:8px;overflow:hidden;margin:8px 0 6px;">
    <div style="width:{pct}%;height:100%;background:{color};border-radius:999px;"></div>
  </div>
  <div style="display:flex;justify-content:space-between;font-size:.74rem;color:#5C6B7A;">
    <span>{s['achievement_pct']}%</span>
    <span style="color:{color};font-weight:600;">{s['status']}</span>
  </div>
  <div style="font-size:.72rem;color:#6B7480;margin-top:4px;">
    gap {s['gap']:,.0f} · need {s['required_run_rate']:,.0f}/day
  </div>
</div>""", unsafe_allow_html=True)


def _mis_manual_map(uk, user, data, preset_sheet=None):
    """Fallback: let the user point at the KPI/Target/Achieved columns by hand."""
    import io, pandas as pd
    xls = pd.ExcelFile(io.BytesIO(data))
    if not preset_sheet or preset_sheet not in xls.sheet_names:
        st.info("No data found for your profile yet.")
        return
    sheet = preset_sheet
    df = xls.parse(sheet).fillna("")
    if df.empty:
        st.caption("That sheet is empty."); return
    cols = list(df.columns)
    c = st.columns(3)
    kc = c[0].selectbox("KPI column", cols, key="mm_kpi")
    tc = c[1].selectbox("Target column", cols, key="mm_tgt")
    ac = c[2].selectbox("Achieved column", cols, key="mm_ach")
    if st.button("Apply mapping", key="mm_apply"):
        kpis = []
        for _, r in df.iterrows():
            name = str(r[kc]).strip()
            if not name:
                continue
            try:
                t = float(str(r[tc]).replace(",", "").replace("₹", "").strip())
                a = float(str(r[ac]).replace(",", "").replace("₹", "").strip())
            except Exception:
                continue
            kpis.append({"name": name, "target": t, "achieved": a})
        if kpis:
            storage.set_targets_from_mis(uk, MONTH, user.get("role", ""), kpis)
            import mis_sync
            mis_sync.save_fingerprint(uk, {"kpis": kpis, "month": MONTH})
            st.session_state.pop("mis_data", None)
            st.success(f"Applied {len(kpis)} KPI(s).")
            st.rerun()
        else:
            st.error("No numeric KPI rows found with those columns.")


def _mis_quick_reload(uk, user):
    """Re-pull the MIS from the configured source (secrets MIS_SHARE_URL), apply it, and
    refresh the daily brief. Used by the reload buttons on Today + Monthly."""
    import mis_sync
    url = (mis_sync.mis_url() or "").strip()
    if not url:
        st.warning("Automatic MIS fetch isn't configured. Use Monthly → Sync to upload the file.")
        return
    try:
        with st.spinner("Reloading from MIS…"):
            data = mis_sync.fetch_excel(url)
            summary = mis_sync.parse_summary(data, uk, user.get("name", ""))
        if not summary or not summary.get("kpis"):
            st.error(summary.get("error") if isinstance(summary, dict) and summary.get("error")
                     else "Couldn't read a KPI / Target / Achieved block from the MIS.")
            return
        month = summary["month"] or MONTH
        storage.set_targets_from_mis(uk, month, user.get("role", ""), summary["kpis"])
        mis_sync.save_fingerprint(uk, summary)
        _ensure_mis_brief(uk, user, force=True)
        st.success(f"Reloaded {len(summary['kpis'])} KPI(s) from your tab "
                   f"‘{summary.get('tab','')}’ for {month}.")
        st.rerun()
    except Exception as e:
        st.error(f"Reload failed: {_safe_err(e)}")


def _safe_err(e):
    """Error text with any URLs removed, so the MIS source link never leaks to the UI."""
    import re
    return re.sub(r"https?://\S+", "[link hidden]", str(e))


def _mis_sync_panel(uk, user):
    import mis_sync
    import pandas as pd
    st.markdown("#### 🔄 Sync achievement from MIS")
    st.caption("Pull the latest numbers from your MIS, or upload the file. You'll see "
               "exactly what was read before it's applied.")

    has_link = bool((mis_sync.mis_url() or "").strip())
    up = st.file_uploader("Upload the MIS .xlsx", type=["xlsx"], key="mis_up")

    bcol = st.columns(2)
    fetch = bcol[0].button("↻ Fetch latest from MIS", type="primary", disabled=not has_link,
                           help=None if has_link else "MIS source isn't configured yet.")
    read_upload = bcol[1].button("Read uploaded file", disabled=up is None)

    if fetch:
        try:
            with st.spinner("Downloading…"):
                st.session_state["mis_data"] = mis_sync.fetch_excel(mis_sync.mis_url())
            st.session_state.pop("mis_pick_sheet", None)
        except Exception as e:
            st.error(f"Couldn’t fetch the file: {_safe_err(e)}")
    elif read_upload and up is not None:
        st.session_state["mis_data"] = up.getvalue()
        st.session_state.pop("mis_pick_sheet", None)

    if not has_link:
        st.caption("ℹ️ Automatic MIS fetch isn't set up — upload the file above, or ask your "
                   "admin to configure it.")

    data = st.session_state.get("mis_data")
    if not data:
        return

    sheets = mis_sync.list_sheets(data)
    if not sheets:
        st.error("This doesn't look like a readable Excel workbook.")
        return

    # Read ONLY the logged-in user's own tab. Never list the workbook's tabs or offer a
    # picker — other users' tabs live in the same file and must stay private.
    sheet = mis_sync._match_tab(sheets, uk, user.get("name", ""))
    if not sheet:
        st.info("No data found for your profile yet.")
        return

    parsed = mis_sync.parse_sheet(data, sheet)
    if not parsed.get("kpis"):
        st.error("Couldn't read a KPI / Target / Achieved block from your MIS.")
        st.caption("Map the columns yourself:")
        _mis_manual_map(uk, user, data, sheet)
        return

    # render a preview of what was read (no tab name shown — it's the username)
    month = parsed["month"] or MONTH
    st.markdown(f"**Preview — your MIS · {month}**")
    prev_df = pd.DataFrame([{
        "KPI": k["name"], "Target": f"{k['target']:,.0f}",
        "Achieved": f"{k['achieved']:,.0f}",
        "%": f"{(k['achieved']/k['target']*100 if k['target'] else 0):.0f}%"
    } for k in parsed["kpis"]])
    st.dataframe(prev_df, use_container_width=True, hide_index=True)

    warns = mis_sync.sanity_check(parsed, mis_sync.load_fingerprint(uk))
    if warns:
        st.warning("Heads-up before applying:")
        for w in warns:
            st.markdown(f"- {w}")

    if st.button("✅ Apply to my scorecard", type="primary", key="mis_apply"):
        storage.set_targets_from_mis(uk, month, user.get("role", ""), parsed["kpis"])
        mis_sync.save_fingerprint(uk, parsed)
        _ensure_mis_brief(uk, user, force=True)
        st.session_state.pop("mis_data", None)
        st.success(f"Applied {len(parsed['kpis'])} KPI(s) for {month}.")
        st.rerun()


def monthly_view(user):
    # MIS backend isn't connected yet — show "coming soon" instead of empty targets/achievement.
    st.markdown("### 🧭 Monthly — Targets & Achievement")
    _mis_coming_soon(
        "Big numbers, loading…",
        "Month-to-date targets, achievement & trends are brewing. Hang tight — this one's "
        "dropping soon. ✨",
        ["🎯 MTD vs target", "⚡ Run-rate", "📊 Progress bars", "📈 MoM trend"],
        tiles=4, emoji="🚀", status="brewing your monthly stats…")
    return


def _monthly_view_live(user):
    uk = user["user_key"]
    st.markdown("### 🧭 Monthly — Targets & Achievement")
    targets = storage.get_targets(uk, MONTH)
    if targets.empty:
        st.info("No targets yet — sync your MIS below to load them, or set them manually.")
        _mis_sync_panel(uk, user)
        return

    _monthly_scorecard(uk, targets)
    _mis_sync_panel(uk, user)
    st.divider()

    rows = list(targets.iterrows())[:4]   # up to 4 KPI blocks, left to right

    # ---- 4 target blocks across the top (user fills the monthly target) ----
    st.markdown("#### Set this month's targets")
    cols = st.columns(len(rows))
    for col, (idx, r) in zip(cols, rows):
        with col:
            with st.container(border=True):
                s = nudge.score_kpi(r["monthly_target"], r["achieved_mtd"], TODAY.year, TODAY.month)
                st.markdown(f"**{r['kpi_name']}**")
                nt = st.number_input("Target", value=float(r["monthly_target"] or 0),
                                     step=1.0, key=f"tgt_{idx}", label_visibility="collapsed")
                st.caption(f"{status_color(s['status'])} {s['achievement_pct']}% · "
                           f"need {s['required_run_rate']:,.0f}/day")
                if nt != float(r["monthly_target"] or 0):
                    t2 = targets.copy()
                    t2.loc[idx, "monthly_target"] = str(int(nt) if nt == int(nt) else nt)
                    t2["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    storage.save_targets(uk, t2)
                    st.rerun()

    st.divider()

    # ---- daily target vs achievement (one row saved per day) ----
    st.markdown("#### Daily target vs achievement")
    st.caption("Target comes from your daily goal sheet (the boxes above today's tasks). "
               "Achievement is entered manually for now — later pushed from the MIS Excel. "
               "Saved as a new row per day.")
    day_goals = storage.get_day_goals(uk, TODAY_STR)
    with st.form("record_today"):
        st.markdown(f"**Record for {TODAY_STR}**")
        ach_inputs = {}
        for idx, r in rows:
            kpi = r["kpi_name"]
            planned = ""
            for g in day_goals:
                if g["heading"] and nudge.goal_served(g["heading"], [kpi]):
                    planned = g["target_number"]; break
            c = st.columns([3, 2, 2])
            c[0].markdown(kpi)
            c[1].caption(f"Daily target: {planned or '—'}")
            ach_inputs[kpi] = (c[2].number_input("Achieved", value=0.0, step=1.0,
                                                 key=f"acht_{idx}", label_visibility="collapsed"),
                               planned)
        if st.form_submit_button("Save today's row", type="primary"):
            for kpi, (ach, planned) in ach_inputs.items():
                storage.record_monthly_progress(
                    uk, TODAY_STR, MONTH, kpi, str(planned or ""),
                    str(int(ach) if ach == int(ach) else ach))
            st.success("Saved.")
            st.rerun()

    # history table (all KPIs, date-wise)
    prog = storage.get_monthly_progress(uk, month=MONTH)
    if not prog.empty:
        tbl = prog[["date", "kpi_name", "planned", "achieved"]].copy()
        tbl["gap"] = (pd.to_numeric(tbl["achieved"], errors="coerce").fillna(0)
                      - pd.to_numeric(tbl["planned"], errors="coerce").fillna(0))
        tbl.columns = ["Date", "KPI", "Target", "Achievement", "Gap"]
        st.dataframe(tbl, use_container_width=True, hide_index=True)


def daily_log_view(user):
    st.markdown("### 📒 Daily log")
    # radio (not st.tabs) — a custom component like the mic recorder fails to mount
    # inside a hidden tab panel, so we render one view at a time.
    view = st.radio("view", ["Log a meeting", "Meetings"], horizontal=True,
                    label_visibility="collapsed", key="dl_view")
    if view == "Log a meeting":
        _meeting_form(user)
    else:
        mtg = storage.get_meetings(user["user_key"])
        if mtg.empty:
            st.info("No meetings logged yet.")
        else:
            tl = {"new_partner": "New partner", "existing_partner": "Existing partner",
                  "client": "Client", "internal": "Internal"}
            for _, m in mtg.iterrows():
                name = (m.get("partner_name") or "").strip() or m.get("identity_value") or "—"
                summary = (m["ai_written"] or m.get("raw_dictation") or "").strip().replace("\n", " ")
                gist = (summary[:90] + "…") if len(summary) > 90 else summary
                label = (f"👤 {name} · {tl.get(m['meeting_type'], m['meeting_type'])} · {m['date']}"
                         + (f" — {gist}" if gist else ""))
                with st.expander(label):
                    if name and name != "—":
                        st.markdown(f"**{name}**" + (f"  ·  {m['identity_value']}"
                                    if m.get("identity_value") and m["identity_value"] != name else ""))
                    st.markdown(m["ai_written"].replace("\n", "  \n") if m["ai_written"] else "_(no summary)_")
                    if m["next_date"]:
                        booked = "✅ scheduled" if m["followup_task_id"] else "⚠️ not scheduled"
                        st.caption(f"Next: {m['next_action']} on {m['next_date']} · {booked}")
                    if m["raw_dictation"]:
                        st.caption(f"🎙️ Original: {m['raw_dictation']}")


def records_view(user):
    uk = user["user_key"]
    st.markdown("### 🗃️ Records")
    tabs = st.tabs(["Directory", "Reminders", "Due today"])
    with tabs[0]:
        _directory_section(user)
    with tabs[1]:
        _reminders_section(user)
    with tabs[2]:
        ob = storage.get_outbox(uk, TODAY_STR)
        st.caption("Messages your reminders prepared for today — your send checklist. "
                   "(Sending isn't automated.)")
        due = ob[ob["status"] == "due"] if not ob.empty else ob
        if ob.empty or due.empty:
            st.info("Nothing due today.")
        else:
            for _, m in due.iterrows():
                with st.container(border=True):
                    st.markdown(f"**{m['recipient_name']}** · {m['recipient_mobile']}")
                    st.caption(m["message"])
                    c = st.columns(2)
                    if c[0].button("Mark sent", key=f"sent_{m['msg_id']}", use_container_width=True):
                        storage.mark_message(uk, m["msg_id"], "done"); st.rerun()
                    if c[1].button("Skip", key=f"skip_{m['msg_id']}", use_container_width=True):
                        storage.mark_message(uk, m["msg_id"], "skipped"); st.rerun()


def _directory_section(user):
    uk = user["user_key"]
    with st.form("add_contact"):
        st.caption("Add a contact — partner or team member. Used as reminder recipients.")
        c = st.columns([3, 3, 2])
        name = c[0].text_input("Name")
        mobile = c[1].text_input("Mobile")
        ctype = c[2].selectbox("Type", ["partner", "team"])
        c2 = st.columns([2, 3, 3])
        sal = c2[0].selectbox("Address as", ["—", "Sir", "Mam"],
                              help="Used to greet partners as ‘Name Sir’ / ‘Name Mam’")
        role = c2[1].text_input("Role / dept (optional)")
        code = c2[2].text_input("Code (optional)")
        if st.form_submit_button("Add to directory", type="primary"):
            if name.strip():
                storage.add_partner(uk, name, mobile, code, "partner_code",
                                    contact_type=ctype, role=role,
                                    salutation="" if sal == "—" else sal)
                st.rerun()
            else:
                st.error("Name is required.")

    # ---- bulk upload directory ----
    with st.expander("⬆️ Upload directory (Excel)"):
        st.caption("Columns recognised: Name, Mobile/Phone, Code (and optional Role). "
                   "Matched by phone → code → name, so re-uploading updates existing contacts.")
        up = st.file_uploader("Directory .xlsx", type=["xlsx"], key="dir_upload")
        uctype = st.radio("Import as", ["partner", "team"], horizontal=True, key="dir_uptype")
        if up is not None and st.button("Import contacts", key="dir_import_btn"):
            try:
                done, skipped = storage.import_directory_excel(uk, up, contact_type=uctype)
                st.success(f"Imported/updated {done} contact(s)"
                           + (f", skipped {skipped} empty row(s)." if skipped else "."))
                st.rerun()
            except Exception as e:
                st.error(f"Couldn't import: {e}")

    contacts = storage.get_partners(uk)
    if contacts.empty:
        st.info("Directory is empty."); return

    def _chip_color(text):
        import hashlib
        # darker palette so the saturated colour is readable as small chip text on its
        # light tint (bright colours like orange/teal fail contrast otherwise)
        palette = ["#3B4FE0", "#047857", "#A0381E", "#0768B3", "#5B4BD6",
                   "#00695C", "#C2185B", "#8A5200", "#0A6B57"]
        if not text:
            return "#5C6B7A"
        h = int(hashlib.md5(str(text).encode()).hexdigest(), 16)
        return palette[h % len(palette)]

    for ctype, label in [("partner", "Partners"), ("team", "Team")]:
        grp = contacts[contacts["contact_type"] == ctype]
        if grp.empty:
            continue
        st.markdown(f"##### {label}")
        for _, p in grp.iterrows():
            c = st.columns([3, 3, 2, 1])
            sal = str(p.get("salutation", "") or "")
            disp = f"{p['name']} {sal}".strip() if sal else p["name"]
            c[0].markdown(f"**{disp}**" + (f"  \n📞 {p['mobile']}" if p["mobile"] else ""))
            role = str(p.get("role", "") or "")
            if role:
                col = _chip_color(role)
                c[1].markdown(
                    f"<span style='background:{col}22;color:{col};padding:2px 10px;"
                    f"border-radius:12px;font-size:0.8rem;font-weight:600;'>{role}</span>",
                    unsafe_allow_html=True)
            badge = []
            if sal:
                badge.append(f"🪪 {sal}")
            if p["code"]:
                badge.append(f"🔖 {p['code']}")
            if badge:
                c[2].caption(" · ".join(badge))
            if c[3].button("🗑", key=f"delp_{p['partner_id']}"):
                storage.remove_partner(uk, p["partner_id"]); st.rerun()


def _reminders_section(user):
    uk = user["user_key"]
    partners = storage.get_partners(uk)
    if partners.empty:
        st.info("Add partners first (Partners tab) — they're the recipients."); return
    pmap = {f"{r['name']} ({r['mobile']})": r["partner_id"] for _, r in partners.iterrows()}

    with st.form("add_schedule"):
        st.caption("Recurring reminder — prepares a message for each chosen partner on schedule.")
        msg = st.text_area("Message", height=80,
                           placeholder="Good morning! Sharing today's market update…")
        picks = st.multiselect("Send to", list(pmap.keys()))
        c = st.columns(3)
        rec = c[0].selectbox("Repeat", ["daily", "weekly", "once"])
        rtime = c[1].text_input("Time", value="09:00")
        weekday = c[2].selectbox("Weekday (if weekly)",
                                 ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        rundate = st.text_input("Date (YYYY-MM-DD, if once)", value=TODAY_STR)
        if st.form_submit_button("Save reminder", type="primary"):
            if not msg.strip() or not picks:
                st.error("Need a message and at least one recipient.")
            else:
                wd = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"].index(weekday)
                storage.add_message_schedule(
                    uk, msg, [pmap[p] for p in picks], recurrence=rec,
                    run_time=rtime, run_date=rundate if rec == "once" else "",
                    weekday=wd if rec == "weekly" else "")
                st.success("Reminder saved.")
                st.rerun()

    sched = storage.get_message_schedules(uk)
    if not sched.empty:
        st.markdown("##### Active reminders")
        for _, s in sched.iterrows():
            with st.container(border=True):
                try:
                    n = len(__import__("json").loads(s["recipients"] or "[]"))
                except Exception:
                    n = 0
                on = str(s["active"]) == "Yes"
                st.markdown(f"{'🟢' if on else '⚪'} **{s['label']}** · {s['recurrence']} "
                            f"@ {s['run_time']} · {n} recipient(s)")
                c = st.columns(2)
                if c[0].button("On/off", key=f"tog_{s['schedule_id']}", use_container_width=True):
                    storage.set_schedule_active(uk, s["schedule_id"], not on); st.rerun()
                if c[1].button("Delete", key=f"dels_{s['schedule_id']}", use_container_width=True):
                    storage.delete_message_schedule(uk, s["schedule_id"]); st.rerun()


def history_view(user):
    uk = user["user_key"]
    st.markdown("### 🗒️ Task log")
    st.caption("Every task event — newest first. Reopen a closed task to bring it back to Open.")

    # reopen control for done/dropped tasks
    closed = storage.get_tasks(uk)
    closed = closed[closed["status"].isin(["Done", "Dropped"])] if not closed.empty else closed
    if not closed.empty:
        with st.expander("↩️ Reopen a task"):
            opts = {f"{r['title']} ({r['status']}, {r['plan_date']})": r["task_id"]
                    for _, r in closed.iterrows()}
            pick = st.selectbox("Closed tasks", list(opts.keys()), key="reopen_pick")
            if st.button("Reopen", key="reopen_btn"):
                storage.reopen_task(uk, opts[pick])
                st.success("Reopened — find it in Today.")
                st.rerun()

    log = storage.get_task_log(uk)
    if log.empty:
        st.info("No history yet."); return
    icon = {"created": "➕", "done": "✅", "carried": "↪", "deleted": "🗑",
            "steps_added": "🪜", "reopened": "↩️"}
    show = log[["ts", "event", "title", "day_goal", "detail"]].copy()
    show["event"] = show["event"].map(lambda e: f"{icon.get(e,'•')} {e}")
    st.dataframe(show, use_container_width=True, hide_index=True)


def learning_view(user):
    uk = user["user_key"]
    role = user.get("role", "")
    st.markdown("### 🧠 Learning")
    st.caption("Dictate your day. The companion mines it for learnings, checks them against "
               "what you already believe, and asks you before accepting anything that conflicts.")

    # radio (not tabs) so the mic component mounts visibly
    sub = st.radio("section", ["Dictate log", "Review learnings", "What I've learned"],
                   horizontal=True, label_visibility="collapsed", key="learn_sub")

    if sub == "Dictate log":
        _learn_dictate(uk, role)
    elif sub == "Review learnings":
        _learn_review(uk, role)
    else:
        _learn_accepted(uk)


def _learn_dictate(uk, role):
    st.markdown("#### Dictate today's log")
    # clear request from a previous save must happen BEFORE the widget is created
    if st.session_state.pop("_clear_log_text", False):
        st.session_state["log_text"] = ""
    # mic dictation (component must NOT be inside tabs/expander)
    try:
        from streamlit_mic_recorder import mic_recorder
        rec = mic_recorder(start_prompt="🎙️ Dictate log", stop_prompt="⏹️ Stop",
                           key="log_mic", format="webm")
        if rec and rec.get("bytes"):
            with st.spinner("Transcribing…"):
                txt = ai.transcribe(rec["bytes"])
            if txt:
                st.session_state["log_text"] = (st.session_state.get("log_text", "") + " " + txt).strip()
    except Exception:
        st.caption("🎙️ (Install streamlit-mic-recorder for voice; type below.)")

    st.text_area("Today's log", key="log_text", height=160,
                 placeholder="What happened today — what you tried, what worked, what didn't, "
                             "what you noticed about partners, the market, your approach…")

    # ---- attach a partner (pick existing or add new) ----
    st.caption("Tag a partner (optional) — links this log to them and adds them to your Directory.")
    parts = storage.get_partners(uk)
    existing = list(parts["name"]) if not parts.empty else []
    pc = st.columns([3, 2, 2])
    pick = pc[0].selectbox("Partner", ["— none —", "➕ New partner"] + existing,
                           key="log_partner_pick")
    p_name, p_mobile, p_code = "", "", ""
    if pick == "➕ New partner":
        p_name = pc[0].text_input("Name", key="log_p_name")
        p_mobile = pc[1].text_input("Phone", key="log_p_mobile",
                                    placeholder="10-digit (adds to Directory)")
        p_code = pc[2].text_input("Code (optional)", key="log_p_code")
    elif pick not in ("— none —",):
        p_name = pick
        row = parts[parts["name"] == pick]
        if not row.empty:
            p_mobile = row.iloc[0]["mobile"]; p_code = row.iloc[0]["code"]
            pc[1].caption(f"📞 {p_mobile or '—'}")
            pc[2].caption(f"🔖 {p_code or '—'}")

    if st.button("💾 Save log", type="primary"):
        txt = st.session_state.get("log_text", "").strip()
        if not txt:
            st.warning("Nothing to save yet.")
        elif pick == "➕ New partner" and (not p_name.strip() or not p_mobile.strip()):
            st.error("For a new partner, both Name and Phone are required.")
        else:
            storage.save_daily_log(uk, TODAY_STR, txt, partner_name=p_name,
                                   partner_mobile=p_mobile, partner_code=p_code)
            st.session_state["_clear_log_text"] = True
            # scan the log for a future commitment with a date ("next Thursday" etc.)
            sugg = ai.detect_followup_from_log(txt, TODAY_STR)
            if sugg.get("has_followup"):
                if not sugg.get("who") and p_name:
                    sugg["who"] = p_name
                st.session_state["log_followup_suggestion"] = sugg
            if p_mobile:
                st.success(f"Saved and linked to {p_name} (added/updated in Directory).")
            elif p_name:
                st.success(f"Saved and tagged to {p_name}. Add a phone to save them in Directory.")
            else:
                st.success("Saved. Go to ‘Review learnings’ to mine it.")
            st.rerun()

    # Follow-up the log mentioned for a future day — offer to schedule it
    sugg = st.session_state.get("log_followup_suggestion")
    if sugg:
        from datetime import datetime as _dt
        nice = _dt.strptime(sugg["date"], "%Y-%m-%d").strftime("%A, %d %b")
        who = sugg.get("who") or ""
        what = sugg.get("what") or "Follow up"
        with st.container(border=True):
            st.markdown(f"📅 **Looks like a follow-up for {nice}**")
            st.caption((f"With **{who}** — " if who else "") + what)
            cc = st.columns([2, 1])
            if cc[0].button(f"➕ Add follow-up on {nice}", type="primary", key="add_log_followup"):
                title = what if what else (f"Follow up: {who}" if who else "Follow up")
                storage.schedule_followup(uk, sugg["date"], who or what, "", title=title)
                st.session_state.pop("log_followup_suggestion", None)
                st.success(f"Scheduled — it'll appear in your tasks on {sugg['date']}.")
                st.rerun()
            if cc[1].button("Dismiss", key="dismiss_log_followup"):
                st.session_state.pop("log_followup_suggestion", None)
                st.rerun()

    logs = storage.get_daily_logs(uk)
    if not logs.empty:
        st.divider()
        st.markdown("##### Past logs")
        for _, lg in logs.head(10).iterrows():
            tag = f" · 👤 {lg['partner_name']}" if lg.get("partner_name") else ""
            with st.expander(f"{lg['date']}{tag}"):
                st.write(lg["transcript"])


def _learn_review(uk, role):
    st.markdown("#### Review learnings")
    logs = storage.get_daily_logs(uk)
    if logs.empty:
        st.info("Dictate a log first, then mine it for learnings.")
        return

    # pick a log to mine
    opts = {f"{r['date']} — {r['transcript'][:40]}…": r["log_id"] for _, r in logs.iterrows()}
    pick = st.selectbox("Mine which log?", list(opts.keys()))
    if st.button("🔎 Find learnings in this log"):
        log = storage.get_daily_log(uk, opts[pick])
        with st.spinner("Reading your log…"):
            role_prompt = storage.read_role_prompt(role, uk)
            found = ai.extract_learnings(log["transcript"], role_prompt)
            # contradiction gate vs already-accepted learnings
            accepted = storage.get_learnings(uk, status="accepted")
            existing_texts = list(accepted["text"]) if not accepted.empty else []
            existing_ids = list(accepted["learning_id"]) if not accepted.empty else []
            cand_texts = [f["text"] for f in found]
            conflicts = ai.find_contradictions(cand_texts, existing_texts)
            conflict_map = {c["candidate"]: c for c in conflicts}
            for i, f in enumerate(found):
                cw = ""
                if i in conflict_map:
                    cw = existing_ids[conflict_map[i]["existing"]]
                storage.add_learning(uk, log["date"], log["log_id"], f["topic"], f["text"],
                                     status="pending", conflict_with=cw,
                                     note=conflict_map.get(i, {}).get("reason", ""))
        st.success(f"Found {len(found)} candidate learning(s). Review below.")
        st.rerun()

    # pending tray with the contradiction gate
    pending = storage.get_learnings(uk, status="pending")
    if pending.empty:
        st.caption("No pending learnings. Mine a log above.")
        return
    st.divider()
    st.markdown(f"##### Pending ({len(pending)}) — accept what's right")
    for _, lr in pending.iterrows():
        with st.container(border=True):
            st.markdown(f"💡 **{lr['text']}**")
            st.caption(f"topic: {lr['topic']} · from {lr['date']}")
            if lr["conflict_with"]:
                old = storage.get_learnings(uk)
                oldrow = old[old["learning_id"] == lr["conflict_with"]]
                old_text = oldrow.iloc[0]["text"] if not oldrow.empty else "(an earlier learning)"
                st.warning(f"⚠️ This conflicts with something you already accepted:\n\n"
                           f"**Existing:** {old_text}"
                           + (f"\n\n_{lr['note']}_" if lr["note"] else ""))
                st.caption("Which holds?")
                b = st.columns(3)
                if b[0].button("Keep existing", key=f"keepold_{lr['learning_id']}"):
                    storage.update_learning(uk, lr["learning_id"], status="rejected",
                                            decided_at=storage._now()); st.rerun()
                if b[1].button("Accept new (replace)", key=f"replace_{lr['learning_id']}"):
                    storage.accept_learning(uk, lr["learning_id"], role,
                                            supersede_id=lr["conflict_with"]); st.rerun()
                if b[2].button("Keep both", key=f"both_{lr['learning_id']}"):
                    storage.accept_learning(uk, lr["learning_id"], role); st.rerun()
            else:
                b = st.columns(2)
                if b[0].button("✅ Accept", key=f"acc_{lr['learning_id']}"):
                    storage.accept_learning(uk, lr["learning_id"], role); st.rerun()
                if b[1].button("✕ Reject", key=f"rej_{lr['learning_id']}"):
                    storage.update_learning(uk, lr["learning_id"], status="rejected",
                                            decided_at=storage._now()); st.rerun()


def _learn_accepted(uk):
    st.markdown("#### What I've learned")
    acc = storage.get_learnings(uk, status="accepted")
    if acc.empty:
        st.info("Nothing accepted yet. Mine a log and accept learnings in ‘Review learnings’.")
        return
    st.caption("These guide your nudges. The companion leads with them next time.")
    for topic, grp in acc.groupby("topic"):
        st.markdown(f"##### {topic}")
        for _, lr in grp.iterrows():
            st.markdown(f"- {lr['text']}  \n  <span style='color:#6B7480;font-size:.8rem'>"
                        f"since {lr['date']}</span>", unsafe_allow_html=True)
    sup = storage.get_learnings(uk, status="superseded")
    if not sup.empty:
        with st.expander(f"Superseded ({len(sup)})"):
            for _, lr in sup.iterrows():
                st.caption(f"~~{lr['text']}~~ (replaced)")


def team_view(user):
    st.markdown("### 👥 Team")
    st.caption("Team members and partners. Import from Excel or add manually.")

    up = st.file_uploader("Team Excel (.xlsx) — columns: Name, Mobile, Department",
                          type=["xlsx"], key="team_xlsx")
    if up is not None:
        itype = st.selectbox("Import as", ["team", "partner"], key="team_imp_type")
        if st.button("Import file", type="primary", key="team_import_btn"):
            try:
                added, updated = storage.import_team_excel(up, itype)
                st.success(f"Imported — {added} added, {updated} updated.")
                st.rerun()
            except Exception as e:
                st.error(f"Couldn't import: {e}")

    with st.expander("➕ Add a member manually"):
        with st.form("add_member"):
            c = st.columns([3, 3, 2])
            name = c[0].text_input("Name")
            mobile = c[1].text_input("Mobile")
            mtype = c[2].selectbox("Type", ["team", "partner"])
            dept = st.text_input("Department (optional)")
            if st.form_submit_button("Add member", type="primary"):
                if name.strip():
                    storage.add_team_member(name, mobile, mtype, dept)
                    st.rerun()
                else:
                    st.error("Name is required.")

    roster = storage.get_team_roster()
    if roster.empty:
        st.info("No members yet."); return
    st.markdown(f"**{len(roster)} member(s)**")
    for mtype, label in [("team", "Team members"), ("partner", "Partners")]:
        grp = roster[roster["member_type"] == mtype]
        if grp.empty:
            continue
        st.markdown(f"##### {label}")
        for _, m in grp.iterrows():
            c = st.columns([4, 3, 2, 1])
            c[0].markdown(f"**{m['name']}**")
            c[1].caption(m["mobile"] or "—")
            c[2].caption(m["department"] or "—")
            if c[3].button("Remove", key=f"rmtm_{m['member_id']}"):
                storage.remove_team_member(m["member_id"]); st.rerun()


# ================================================================ shell + header nav

def header_nav(is_lead):
    """Single-row header tabs across the full content width."""
    tabs = ["Today", "Daily log", "Records", "Communicate", "Monthly", "Learning", "History", "Settings"]
    icons = ["columns-gap", "notebook", "address-book", "send", "compass", "lightbulb", "history", "gear"]
    if HAVE_OPTION_MENU:
        return option_menu(
            None, tabs, icons=icons, orientation="horizontal", default_index=0,
            styles=style.NAV_STYLES)
    return st.radio("Navigate", tabs, horizontal=True, label_visibility="collapsed")


def _maybe_backup(uk):
    """Push local files to the Google Sheets backup every ~30 min while the app is open.
    Only relevant on a LOCAL machine — on Streamlit Cloud writes already go straight to
    Sheets, so there's nothing to push. Runs quietly; never blocks on an error."""
    if not storage.local_first():
        return   # on Cloud: data is already in Sheets
    import time
    last = st.session_state.get("last_backup_ts", 0)
    if time.time() - last >= 1800:   # 30 minutes
        try:
            pushed, _, err = storage.sync_to_sheets(uk)
            st.session_state["last_backup_ts"] = time.time()
            st.session_state["last_backup_info"] = (
                f"backed up {pushed} file(s)" if not err else f"backup issue: {err}")
        except Exception as e:
            st.session_state["last_backup_ts"] = time.time()
            st.session_state["last_backup_info"] = f"backup failed: {e}"


def _force_close_previous_day(user, prev_date):
    """Blocking screen shown when the previous working day wasn't closed. The user must
    close it (download its report) before they can use anything else."""
    uk = user["user_key"]
    nice = datetime.strptime(prev_date, "%Y-%m-%d").strftime("%A, %d %b")
    st.markdown(f"""
    <div style="border-radius:16px;padding:22px 20px;margin:8px 0 14px;
      background:linear-gradient(135deg,#2D4A5E,#5367FC);color:#fff;text-align:center;">
      <div style="font-size:1.6rem;">🌙</div>
      <div style="font-size:1.3rem;font-weight:800;margin:6px 0 4px;">Close your last day first</div>
      <div style="opacity:.92;font-size:.95rem;max-width:460px;margin:0 auto;">
        You didn't close <b>{nice}</b>. Wrap it up and download the report to continue — this
        keeps your record complete. The rest of the app unlocks once it's done.</div>
    </div>
    """, unsafe_allow_html=True)

    month = prev_date[:7]
    try:
        dsr_bytes = dsr.build_docx(user, prev_date, month)
    except Exception as e:
        dsr_bytes = None
        st.error(f"Couldn't build the report: {e}")

    if dsr_bytes:
        # save the report (text → cloud, plus local Word archive) once
        if st.session_state.get("forceclose_saved") != prev_date:
            try:
                storage.save_dsr(uk, prev_date, dsr.docx_to_text(dsr_bytes))
                import paths, os as _os
                rep = paths.user_reports_dir(uk); _os.makedirs(rep, exist_ok=True)
                with open(_os.path.join(rep, f"DSR_{prev_date}.docx"), "wb") as fh:
                    fh.write(dsr_bytes)
            except Exception:
                pass
            st.session_state["forceclose_saved"] = prev_date

        st.download_button("⬇️ Download the day's report", data=dsr_bytes,
                           file_name=f"DSR_{uk}_{prev_date}.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           use_container_width=True)
        st.caption("Download the report, then close the day below.")
        if st.button(f"✅ Close {nice} & continue", type="primary", use_container_width=True):
            storage.mark_day_closed(uk, prev_date)
            st.session_state.pop("forceclose_saved", None)
            st.rerun()
    else:
        # report couldn't build — still let them close so they aren't permanently locked out
        if st.button(f"✅ Mark {nice} closed & continue", type="primary", use_container_width=True):
            storage.mark_day_closed(uk, prev_date)
            st.rerun()


def main():
    style.inject()
    if "user" not in st.session_state:
        login_view()
        return
    user = st.session_state.user
    is_lead = user.get("login_role") == "lead" or user.get("role") == "lead"

    # Inject THIS user's role objective into every AI call this request — so the daily
    # quote, task suggestions, nudges, KRA reminders, message-writing, and summaries are
    # all shaped by the role's goal. (Must run before the quote is generated below.)
    ai.set_role_brief(storage.read_role_prompt(user.get("role", ""), user["user_key"]))

    # Inject the person's ACCEPTED learnings into every AI call too — so nudges, next-actions,
    # cues, the quote and messages reflect what they've learned and how they like to work.
    try:
        acc = storage.get_learnings(user["user_key"], status="accepted")
        if not acc.empty:
            lines = []
            for _, lr in acc.iterrows():
                tp = (lr.get("topic") or "").strip()
                lines.append(f"- ({tp}) {lr['text']}" if tp else f"- {lr['text']}")
            ai.set_learnings_brief("\n".join(lines[:40]))
        else:
            ai.set_learnings_brief("")
    except Exception:
        ai.set_learnings_brief("")

    # periodic backup: push local files to Google Sheets every ~30 min while the app is open
    _maybe_backup(user["user_key"])

    # daily quote — generated once per day, cached in session
    qkey = f"quote_{TODAY_STR}"
    if qkey not in st.session_state:
        st.session_state[qkey] = ai.daily_quote(seed=TODAY_STR)
    quote, qtag = st.session_state[qkey]

    st.markdown(
        f'<div class="pmd-hero">'
        f'<div class="brand"><span class="dot">🌅</span> Plan My Day</div>'
        f'<div class="tagline">Your daily execution coach — turning intent into the number.</div>'
        f'<div class="quote">“{quote}”<span class="by">{qtag}</span></div>'
        f'</div>', unsafe_allow_html=True)

    # who / role + logout
    top = st.columns([5, 1])
    with top[0]:
        role_disp = user.get("role", "").replace("_", " ").title()
        st.caption(f"**{user['name']}** · {role_disp}"
                   + (" · 🤖" if ai.have_key() else " · ⚙️ no AI key"))
    with top[1]:
        if st.button("Log out", use_container_width=True):
            del st.session_state.user
            st.rerun()

    # GATE: if the previous working day wasn't closed, force-close it first — block every
    # other screen until it's done. (Only Sunday is a non-working day.)
    uk = user["user_key"]
    if datetime.now().weekday() != 6:
        prev = _prev_working_day(date.today()).isoformat()
        if not storage.is_day_closed(uk, prev):
            _force_close_previous_day(user, prev)
            return

    # Row 2: full-width nav (single clean row)
    choice = header_nav(is_lead)
    st.divider()

    {"Today": today_view, "Daily log": daily_log_view, "Records": records_view,
     "Communicate": communicate_view, "Monthly": monthly_view, "Learning": learning_view,
     "History": history_view, "Settings": settings_view}.get(choice, today_view)(user)


if __name__ == "__main__":
    main()
