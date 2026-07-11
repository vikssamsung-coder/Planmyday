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
import schemas
import nudge
import ai
import retrieval
import classify
import report
import project_planner
import workspace as ws
import style
import dsr
try:
    import reports_engine            # Desktop-only module; may be absent on Cloud
except Exception:
    reports_engine = None
try:
    import dump_sender               # Desktop-only "Update the Dump" (Outlook email)
except Exception:
    dump_sender = None
try:
    import mindmap                   # Mind Map (Level 1 auto-layout)
except Exception:
    mindmap = None

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

# Users allowed to see the PARTNER-ACQUISITION surfaces: the New Partner meeting logging
# (the "Daily log" tab), the Partner section of the Directory, and Communicate. Everyone
# else does not see these. Edit this set to grant/revoke access.
# (To gate by ROLE instead of by username, change _partner_features_allowed below to check
#  user.get("role") in {"partner_acquisition", "digital_partner_acquisition"}.)
PARTNER_FEATURE_USERS = {"rinku", "ketki"}


def _partner_features_allowed(user):
    return (user or {}).get("user_key", "") in PARTNER_FEATURE_USERS


def _is_admin(user):
    """Admin = a dedicated login whose role is ADMIN. Sees only the CMS/admin module."""
    return str((user or {}).get("role", "")).upper() == "ADMIN"
MONTH = TODAY.strftime("%Y-%m")


# ---- fragment / scoped-rerun shims -------------------------------------------------
# @st.fragment (Streamlit >= 1.37) lets a piece of the page rerun in ISOLATION, so a task
# card edit or a buzzer tick reruns only that block instead of re-running all of main()
# (role brief, quote, backups, every card...). On older Streamlit these degrade to the
# previous whole-app behaviour — correct, just not as fast.
try:
    _fragment = st.fragment                      # 1.37+
except AttributeError:
    try:
        _fragment = st.experimental_fragment     # 1.33–1.36
    except AttributeError:
        def _fragment(func=None, **_kw):          # very old: no-op decorator
            if func is None:
                return lambda f: f
            return func


def _rerun(scope="app"):
    """st.rerun with an optional scope ('fragment' reruns just the current fragment).
    Falls back to a full rerun on Streamlit versions without the scope kwarg."""
    try:
        st.rerun(scope=scope)
    except TypeError:
        st.rerun()

# First launch: build the workspace on disk (folders + Excel + md + role prompts),
# seed demo data if empty. Idempotent — runs once per session, creates only what's missing.
if "workspace_ready" not in st.session_state:
    try:
        st.session_state.ws_report = ws.ensure_workspace()
    except Exception as e:
        st.session_state.ws_report = {"base_dir": "?", "created": [], "error": str(e)}
    st.session_state.workspace_ready = True

# Ensure the Postgres schema exists (idempotent CREATE TABLE IF NOT EXISTS only — never
# ALTERs an existing table). Runs once per session when Neon is configured, so brand-new
# tables (e.g. content, ai_usage) appear without anyone hand-running SQL. Best-effort: if
# the DB role lacks CREATE rights it's skipped quietly and the app still runs.
if "schema_ready" not in st.session_state:
    try:
        if storage._use_pg():
            import db as _db
            _db.init_schema()
    except Exception:
        pass
    st.session_state.schema_ready = True


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
                storage.record_login(user["user_key"])   # track daily sign-in for Admin panel
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


def _daily_achievement(user):
    """Below the targets: enter what was ACHIEVED against each of today's targets, saved
    together with one Update button. Fill this before you close the day — it flows straight
    into the Progress Brief's 'targets vs achievement' table."""
    uk = user["user_key"]
    goals = [g for g in storage.get_day_goals(uk, TODAY_STR) if g["heading"]]
    if not goals:
        return
    with st.container(border=True):
        st.markdown("##### 📊 Today's achievement")
        st.caption("Update what you achieved against each target — do this before you close the day.")
        cols = st.columns(len(goals))
        for i, g in enumerate(goals):
            with cols[i]:
                st.markdown(
                    f"**{g['heading']}**  \n"
                    f"<span style='font-size:12px;color:#5C6B7A;'>target: "
                    f"{g['target_number'] or '—'}</span>", unsafe_allow_html=True)
                st.text_input("Achieved", value=g.get("achieved", "") or "",
                              key=f"ach_{g['slot']}", placeholder="e.g. 62K",
                              label_visibility="collapsed")
        if st.button("💾 Update achievement", key="ach_update", type="primary",
                     use_container_width=True):
            by_slot = {g["slot"]: st.session_state.get(f"ach_{g['slot']}", "") for g in goals}
            storage.set_day_achievements(uk, TODAY_STR, by_slot)
            st.success("Achievement updated — it'll show in your Progress Brief.")
            st.rerun()


def _apply_merge_plan(uk, mr, decisions):
    """Apply the user-reviewed merge plan: skip duplicates, group/attach into headers with
    subtasks (steps), add the rest. Unchecked suggestions fall back to adding individually."""
    import json as _json
    proposed = mr["proposed"]
    plan = mr["plan"]
    to_add, used = [], set()
    for idx, a in enumerate(plan):
        act = a.get("action")
        new_idx = [i for i in a.get("new", []) if 0 <= i < len(proposed)]
        on = decisions.get(idx, True)
        if act == "skip" and on:
            used.update(new_idx)                       # drop duplicates
            continue
        if act == "group" and on and new_idx:
            base = dict(proposed[new_idx[0]])
            base["title"] = a.get("header") or base.get("title", "")
            subs = a.get("subtasks") or [proposed[i]["title"] for i in new_idx]
            base["steps_json"] = _json.dumps([{"text": str(s), "done": False} for s in subs])
            to_add.append(base)
            used.update(new_idx)
            continue
        if act == "attach" and on and new_idx:
            eid = a.get("existing_id")
            subs = a.get("subtasks") or [proposed[i]["title"] for i in new_idx]
            if eid:
                try:
                    storage.update_task(uk, eid, title=a.get("header", ""),
                                        steps_json=_json.dumps([{"text": str(s), "done": False}
                                                                for s in subs]))
                except Exception:
                    pass
            used.update(new_idx)
            continue
        # add — or an unchecked skip/group/attach falls through to individual adds
        for i in new_idx:
            if i not in used:
                to_add.append(proposed[i]); used.add(i)
    # safety: anything the plan didn't mention gets added
    for i, t in enumerate(proposed):
        if i not in used:
            to_add.append(t)
    if to_add:
        storage.add_tasks(uk, to_add, dedupe=True)


def _render_merge_review(uk):
    """If a dictation produced possible duplicates/groupings, show a review panel. Returns
    True if the panel is showing (caller should pause the normal add flow)."""
    mr = st.session_state.get("merge_review")
    if not mr:
        return False
    proposed, plan = mr["proposed"], mr["plan"]
    st.markdown("#### 📋 Review before adding")
    st.caption("I spotted some duplicates or tasks that can be grouped. Untick anything you'd "
               "rather keep separate, then confirm.")
    decisions = {}
    for idx, a in enumerate(plan):
        act = a.get("action")
        titles = [proposed[i]["title"] for i in a.get("new", []) if i < len(proposed)]
        if act == "skip":
            decisions[idx] = st.checkbox(
                f"⏭️ Skip **{titles[0] if titles else '?'}** — looks like a duplicate of "
                f"\"{a.get('duplicate_of', 'an existing task')}\"", value=True, key=f"mr_{idx}")
        elif act == "group":
            subs = a.get("subtasks") or titles
            decisions[idx] = st.checkbox(
                f"🗂️ Group {' + '.join(titles)} under **{a.get('header', 'Group')}** "
                f"— subtasks: {', '.join(subs)}", value=True, key=f"mr_{idx}")
        elif act == "attach":
            subs = a.get("subtasks") or titles
            decisions[idx] = st.checkbox(
                f"🔗 Combine **{titles[0] if titles else '?'}** with your existing task under "
                f"**{a.get('header', '')}** — subtasks: {', '.join(subs)}", value=True, key=f"mr_{idx}")
        else:
            st.markdown(f"➕ Add: {', '.join(titles)}")
            decisions[idx] = True
    c1, c2 = st.columns(2)
    if c1.button("✅ Confirm", type="primary", key="mr_confirm", use_container_width=True):
        _apply_merge_plan(uk, mr, decisions)
        st.session_state.pop("merge_review", None)
        st.rerun()
    if c2.button("Add all as-is", key="mr_addall", use_container_width=True):
        storage.add_tasks(uk, mr["proposed"], dedupe=True)
        st.session_state.pop("merge_review", None)
        st.rerun()
    st.divider()
    return True


def plan_and_tasks(user, cards):
    uk = user["user_key"]
    st.markdown("### 🗂️ Today's targets & tasks")
    st.caption(f"{user['name']} · {user['role'].replace('_',' ').title()} · {TODAY.strftime('%d %b %Y')}")

    # Carry unfinished tasks + prepare any due reminder messages, once per session.
    if not st.session_state.get("carried_today"):
        storage.carry_forward(uk, TODAY_STR)
        storage.run_due_message_schedules(uk, TODAY_STR)
        st.session_state.carried_today = True

    # If a dictation produced possible duplicates/groupings, review them first.
    if _render_merge_review(uk):
        return

    headings = render_daily_targets(user)

    # enter what was achieved against today's targets (fill before closing the day)
    _daily_achievement(user)

    # live admin banners (announcements) surface here
    _today_banners(user)

    # ---- action bar: Close your Day + Share Progress Brief (distinct slate colour) ----
    st.markdown("""<style>
    .st-key-bar_close_day button, .st-key-bar_share_brief button {
      background:#2D4A5E !important; color:#fff !important; border:1px solid #2D4A5E !important;
      font-weight:500 !important; }
    .st-key-bar_close_day button:hover, .st-key-bar_share_brief button:hover {
      background:#24414F !important; border-color:#24414F !important; color:#fff !important; }
    </style>""", unsafe_allow_html=True)
    _bar = st.columns(2)
    if _bar[0].button("🌙 Close your Day", key="bar_close_day", use_container_width=True):
        st.session_state["closeday_open"] = True
        st.rerun()
    if _bar[1].button("📤 Share Progress Brief", key="bar_share_brief", use_container_width=True):
        with st.spinner("Building your Progress Brief…"):
            try:
                st.session_state["pb_bytes"] = dsr.build_docx(user, TODAY_STR, MONTH)
                st.session_state["pb_date"] = TODAY_STR
                st.session_state.pop("pb_err", None)
            except Exception:
                st.session_state["pb_bytes"] = None
                st.session_state["pb_err"] = True
    if st.session_state.get("pb_bytes") is not None and st.session_state.get("pb_date") == TODAY_STR:
        st.download_button("⬇️ Download the Progress Brief (Word)",
                           data=st.session_state["pb_bytes"],
                           file_name=f"ProgressBrief_{uk}_{TODAY_STR}.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           key="bar_pb_dl", use_container_width=True)
    elif st.session_state.get("pb_err"):
        st.caption("Couldn't build the Progress Brief just now — try again in a moment.")

    st.divider()

    # ---- the gate: no target -> no planning ----
    if not headings:
        st.info("🔒 Set at least one of today's targets to start planning. "
                "Tap **Edit** above, write a 2-word heading and a number.")
        return

    # ---- dictate / type to create tasks ----
    st.markdown("##### Today I want to do…")
    _t = _dictate_text("plan", "🎙️ Dictate")
    if _t:
        # set state BEFORE the widget is instantiated (allowed)
        st.session_state.plan_input = _t

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
            for t in proposed:
                t["plan_date"] = TODAY_STR
                if not t.get("day_goal"):
                    hit = nudge.goal_match(
                        f"{t.get('title','')} {t.get('linked_kpi','')}", headings)
                    if hit:
                        t["day_goal"] = hit
                # NOTE: the companion cue is NOT generated here anymore. Generating a cue per
                # task meant N sequential AI calls before a single task appeared (the "hang").
                # Tasks now show instantly; a cue is one tap away on each card (⚙️ Options → 🔄).
            # semantic dedup / grouping — compare new tasks against existing open tasks
            allt = storage.get_tasks(uk)
            existing_open = ([{"id": r["task_id"], "title": r["title"]}
                              for _, r in allt.iterrows()
                              if str(r.get("status", "")).strip() == "Open"]
                             if not allt.empty else [])
            plan = ai.detect_merges([t["title"] for t in proposed], existing_open)
            nontrivial = any(a.get("action") in ("skip", "group", "attach") for a in plan)
            if nontrivial:
                st.session_state["merge_review"] = {"proposed": proposed, "plan": plan}
            else:
                storage.add_tasks(uk, proposed, dedupe=True)
            st.session_state._clear_plan = True
            st.rerun()

    # ---- add a task manually (no AI) ----
    with st.expander("➕ Add a task manually"):
        with st.form("manual_task", clear_on_submit=True):
            mt_title = st.text_input("Task", placeholder="e.g. Call 20 funded-not-traded clients")
            mc = st.columns([2, 1, 1, 1])
            gk_opts = _goal_kra_options(uk, headings)
            gk_labels = ["—"] + [o[0] for o in gk_opts]
            gk_by_label = {o[0]: o for o in gk_opts}
            mt_goal = mc[0].selectbox("Goal / KRA it serves", gk_labels,
                                      help="Link it to one of today's targets, a KRA, "
                                           "Self-Improvement, or leave —")
            mt_hz = mc[1].selectbox("Horizon", ["Today", "Build"],
                                    help="Delivers today, or builds toward a near-future goal")
            mt_pri = mc[2].selectbox("Priority", ["P1", "P2", "P3", "P4", "P5"], index=1)
            mt_time = mc[3].time_input("⏰ Remind at", value=None, step=300,
                                       help="Optional — buzzes you at this time until you update it")
            if st.form_submit_button("Add task", type="primary"):
                if not mt_title.strip():
                    st.warning("Give the task a title.")
                elif storage.open_task_exists(uk, mt_title.strip()):
                    st.warning("You already have an open task with this title — not adding a duplicate.")
                else:
                    # route the choice to the right field
                    day_goal, linked_kpi, kra_res = "", "", ""
                    if mt_goal != "—":
                        kind, value = gk_by_label[mt_goal][1], gk_by_label[mt_goal][2]
                        if kind == "goal":
                            day_goal = value
                        elif kind == "kra":
                            linked_kpi = value; kra_res = value      # explicit KRA pick
                        elif kind == "learning":
                            kra_res = "Self-Improvement"
                    task = {"title": mt_title.strip(), "plan_date": TODAY_STR,
                            "day_goal": day_goal, "linked_kpi": linked_kpi,
                            "kra_resolved": kra_res,
                            "horizon": mt_hz, "priority": mt_pri, "source": "manual",
                            "due_time": mt_time.strftime("%H:%M") if mt_time else "",
                            "goal_aligned": "No" if mt_goal == "—" else "Yes"}
                    # optional companion cue if a proven rule exists for this goal
                    topic = storage._topic_of(task["day_goal"], "")
                    rule = storage.best_rule(uk, topic)
                    if rule:
                        task["coach_cue"] = f"Last time this worked for you: {rule['rule_text']}"
                    storage.add_tasks(uk, [task])
                    st.success("Task added.")
                    st.rerun()

    # ---- read every task ONCE, derive both the scheduled-ahead list and today's tasks ----
    allt = storage.get_tasks(uk)
    tasks = allt[allt["plan_date"] == TODAY_STR] if not allt.empty else allt

    # ---- scheduled ahead (reminders): future-dated tasks that auto-appear on their day ----
    if not allt.empty:
        ahead = allt[(allt["status"] == "Open") & (allt["plan_date"] > TODAY_STR)]
        if not ahead.empty:
            with st.expander(f"🔔 Scheduled ahead ({len(ahead)})", expanded=False):
                st.caption("Tasks you've scheduled for a future day. Each appears in Today "
                           "automatically on its date — nothing to do now.")
                ah = ahead[["plan_date", "title", "due_time"]].copy().sort_values("plan_date")
                import datetime as _d2
                def _nice(s):
                    try:
                        return _d2.datetime.strptime(str(s), "%Y-%m-%d").strftime("%a %d %b %Y")
                    except Exception:
                        return str(s)
                ah["plan_date"] = ah["plan_date"].map(_nice)
                ah["due_time"] = ah["due_time"].map(lambda t: f"⏰ {t}" if str(t).strip() else "")
                ah.columns = ["Scheduled for", "Task", "Reminder"]
                st.dataframe(ah, use_container_width=True, hide_index=True)

    # ---- coach nudge: any target with no task pointing at it? (normalized) ----
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
        _oh = st.columns([5, 2])
        _oh[0].markdown(f"##### Open · {len(open_t)}")
        if _oh[1].button("⛶ Full screen", key="open_quadrants", use_container_width=True):
            st.session_state["tasks_expanded"] = True
            st.rerun()
        _inject_rail_css()
        st.caption("Numbered by time — set a ⏰ on the rail and press Update; the list re-orders automatically.")
        open_sorted = _sort_open_by_time(open_t)
        for _n, (_, t) in enumerate(open_sorted.iterrows(), start=1):
            _task_card(uk, t["task_id"], headings, role_prompt, seq=_n, first=(_n == 1))

        if not done_t.empty:
            with st.expander(f"Done · {len(done_t)}"):
                for _, t in done_t.iterrows():
                    c = st.columns([6, 1])
                    c[0].markdown(f"~~{t['title']}~~")
                    if c[1].button("🗑", key=f"del_{t['task_id']}"):
                        storage.update_task(uk, t["task_id"], status="Dropped"); st.rerun()


def _close_my_day(uk, user, tasks):
    """End-of-day ritual: check in on each cued task, then commit the close (one button).
    Today's targets vs achievement is entered in the "📊 Today's achievement" card under
    the targets (it feeds the Progress Brief) and is NOT duplicated here. The brief is
    saved silently on close; download it from "📤 Share Progress Brief" under the targets."""
    # Today's targets vs achievement is entered in the "📊 Today's achievement" card
    # directly under the targets (day_goals → flows straight into the Progress Brief);
    # it is intentionally NOT duplicated here. Nudge if any target is still blank.
    day_goals = storage.get_day_goals(uk, TODAY_STR)
    saved_goals = [g for g in day_goals if str(g.get("heading", "") or "").strip()]
    missing = [g["heading"] for g in saved_goals if not str(g.get("achieved", "") or "").strip()]
    if saved_goals and missing:
        which = ", ".join(missing) if len(missing) <= 3 else f"{len(missing)} targets"
        st.info(f"📊 Log what you achieved against **{which}** in the *Today's achievement* "
                "card under your targets — that's what the Progress Brief reports.")

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

    # ---- commit the close. The Progress Brief is built + saved silently here; download
    #      it from the "📤 Share Progress Brief" button under your targets. ----
    st.divider()
    try:
        dsr_bytes = dsr.build_docx(user, TODAY_STR, MONTH)
    except Exception:
        dsr_bytes = None

    # save today's DSR silently (text → cloud-synced store + a local Word archive),
    # once per session per day.
    if dsr_bytes is not None and st.session_state.get("dsr_saved_date") != TODAY_STR:
        try:
            storage.save_dsr(uk, TODAY_STR, dsr.docx_to_text(dsr_bytes))
            import paths, os as _os
            rep_dir = paths.user_reports_dir(uk)
            _os.makedirs(rep_dir, exist_ok=True)
            with open(_os.path.join(rep_dir, f"ProgressBrief_{TODAY_STR}.docx"), "wb") as _fh:
                _fh.write(dsr_bytes)
            storage.sync_to_sheets(uk)   # silent, incremental push of the changed DSR store
        except Exception:
            pass
        st.session_state["dsr_saved_date"] = TODAY_STR

    if storage.is_day_closed(uk, TODAY_STR):
        st.success("✅ Today is closed. Grab the Word file from **📤 Share Progress Brief** "
                   "under your targets.")
    else:
        if style.busy_button("🌙 Close the day", key="close_today_btn",
                             working="Closing…", type="primary",
                             use_container_width=True):
            with st.spinner("Wrapping up your day…"):
                storage.mark_day_closed(uk, TODAY_STR)
                # classify today's unassigned tasks into KRAs (one batched AI call)
                _resolve_kras_with_ai(uk, storage.get_tasks(uk, TODAY_STR))
            st.toast("Day closed ✅", icon="🌙")
            st.session_state["closeday_open"] = False
            st.rerun()


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
    # mic dictation for the outcome — native audio_input (reliable on Cloud)
    _t = _dictate_text("mtg", "🎙️ Dictate outcome")
    if _t:
        st.session_state.mtg_raw = _t
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


def _mis_cue_context(uk, cards):
    """Prefer the once-a-day grounded MIS brief (KB) for cue context; fall back to the
    live scorecard summary."""
    brief = storage.get_mis_brief(uk, TODAY_STR)
    if brief and (brief.get("brief") or "").strip():
        return brief["brief"]
    return _mis_context(cards)


def _steps_of(t):
    """Parse a task row's steps_json (already in the row — no extra DB read)."""
    import json as _j
    raw = (t.get("steps_json") if hasattr(t, "get") else t["steps_json"]) or ""
    try:
        return _j.loads(raw) if raw else []
    except Exception:
        return []


def _collab_of(t):
    """Parse a task row's collaborators (already in the row — no extra DB read)."""
    import json as _j
    raw = (t.get("collaborators") if hasattr(t, "get") else t["collaborators"]) or ""
    try:
        return _j.loads(raw) if raw else []
    except Exception:
        return []


def _has_time(v):
    v = str(v or "").strip()
    return len(v) == 5 and v[2] == ":"


def _sort_open_by_time(df):
    """Open tasks ordered for planning: timed tasks chronologically, untimed last
    (stable, so untimed keep their existing order)."""
    if df is None or df.empty:
        return df
    d = df.copy()
    d["_tk"] = d["due_time"].map(lambda v: str(v).strip() if _has_time(v) else "99:99")
    d = d.sort_values("_tk", kind="stable").drop(columns=["_tk"])
    return d


def _inject_rail_css():
    st.markdown("""<style>
    .pmd-rail{display:flex;flex-direction:column;align-items:center;gap:2px;padding-top:4px}
    .pmd-rail-node{width:28px;height:28px;border-radius:50%;background:#2D4A5E;color:#fff;
      display:flex;align-items:center;justify-content:center;font-weight:600;font-size:.9rem;
      border:2px solid #22394A}
    .pmd-rail-time{font-size:.72rem;color:#2D4A5E;font-weight:600;line-height:1.1}
    .pmd-rail-time.none{color:#9AA6B2;font-weight:500}
    </style>""", unsafe_allow_html=True)


def _task_rail(uk, t, idx, first=False):
    """Numbered time-rail node to the LEFT of a task card: order number + scheduled time,
    plus a ⏰ setter. Selecting Hr/Min does nothing on its own; the Update button writes the
    time AND triggers a full rerun so the whole list re-sorts by time (no separate button)."""
    tid = t["task_id"]
    due = (t.get("due_time") or "").strip()
    timed = _has_time(due)
    time_html = (f"<div class='pmd-rail-time'>{due}</div>" if timed
                 else "<div class='pmd-rail-time none'>—</div>")
    st.markdown(f"<div class='pmd-rail'><div class='pmd-rail-node'>{idx}</div>{time_html}</div>",
                unsafe_allow_html=True)
    with st.popover("⏰", use_container_width=True):
        st.caption("Pick a time, then Update — the list re-orders by time.")
        hours = ["—"] + [f"{h:02d}" for h in range(24)]
        base_min = list(range(0, 60, 5))
        cur_h, cur_m = "", ""
        if timed:
            cur_h, cur_m = due[:2], due[3:5]
        if cur_m.isdigit() and int(cur_m) not in base_min:
            base_min = sorted(set(base_min) | {int(cur_m)})
        mins = [f"{m:02d}" for m in base_min]
        rc = st.columns(2)
        hh = rc[0].selectbox("Hr", hours, index=hours.index(cur_h) if cur_h in hours else 0,
                             key=f"rh_{tid}")
        mm = rc[1].selectbox("Min", mins, index=mins.index(cur_m) if cur_m in mins else 0,
                             key=f"rm_{tid}")
        new_time = "" if hh == "—" else f"{hh}:{mm}"
        if st.button("Update", key=f"rtset_{tid}", type="primary", use_container_width=True):
            storage.update_task(uk, tid, due_time=new_time, last_buzz_at="")
            st.rerun()   # full rerun -> the open list auto-sorts by the new time


@_fragment
def _task_card(uk, task_id, headings, role_prompt="", seq=None, first=False):
    """One open/carried task as a COLLAPSIBLE fragment card.

    The card re-reads its own task (cache-backed) and reruns in ISOLATION (scope='fragment')
    for in-card edits; set-changing actions (Done, Delete, save-completes-the-task, moved to
    another day) promote to a full app rerun so the Open list and buzzer refresh.

    Open/closed state is held in session (NOT st.expander) so ticking a step doesn't collapse
    the card. Step ticks are held in session and persisted only when 'Save progress' is pressed
    — no write and no completion happen on each individual tick.
    """
    t = storage.get_task(uk, task_id)
    if t is None:
        return
    status = str(t.get("status", "") or "").strip()
    if status in ("Done", "Dropped"):
        _rerun("app")
        return

    carried = bool(t.get("carried_from"))
    steps = _steps_of(t)
    cur_goal = t.get("day_goal", "") or ""

    _rc = st.columns([1, 7], gap="small")
    with _rc[0]:
        _task_rail(uk, t, seq or 1, first=first)
    with _rc[1]:

        # ---- collapsed header label ----
        bits = [("↪ " if carried else "") + t["title"]]
        if steps:
            done_n = sum(1 for s in steps if s.get("done"))
            bits.append(f"{done_n}/{len(steps)} steps")
        if cur_goal:
            bits.append(f"🔗 {cur_goal}")
        elif t.get("source") != "follow_up":
            bits.append("⚠️ no goal")
        if t.get("source") == "follow_up" and t.get("followup_for"):
            bits.append(f"📞 {t['followup_for']}")
        label = "  ·  ".join(bits)

        # ---- coaching gist (rendered UNDER the header so the card lines up with the rail #) ----
        cue = (t.get("coach_cue") or "").strip()
        if t.get("source") == "follow_up" and t.get("followup_for"):
            ff = f"Follow up with {t['followup_for']}"
            gist = f"{ff} — {cue}" if cue else ff
        else:
            gist = cue

        # ---- session-backed collapse (survives step-tick reruns) ----
        open_key = f"open_{task_id}"
        is_open = st.session_state.get(open_key, False)
        if st.button(("▾ " if is_open else "▸ ") + label, key=f"tgl_{task_id}",
                     use_container_width=True):
            st.session_state[open_key] = not is_open
            _rerun("fragment")
        if gist and not is_open:
            st.caption(f"💡 {gist[:110]}")
        if not is_open:
            return

        with st.container(border=True):
            tags = ["⚡ Today" if t.get("horizon") != "Build" else "🌱 Build"]
            tags.append("🔗 " + cur_goal if cur_goal else "⚠️ No goal")
            if t.get("source") == "follow_up" and t.get("followup_for"):
                tags.append(f"📞 follow-up · {t['followup_for']}")
            if carried:
                tags.append(f"↪ carried from {t['carried_from']}")
            st.caption(" · ".join(tags))
            if not cur_goal:
                st.caption("⚠️ Kept because you chose it — won't move today's numbers.")

            # ===================== PRIMARY: daily-driver actions =====================
            # (1) log progress / stop the buzzer
            urow = st.columns([5, 1])
            rmk = urow[0].text_input("Task update", key=f"upd_{task_id}",
                                     placeholder="Quick remark — logs progress & stops the buzzer",
                                     label_visibility="collapsed")
            if urow[1].button("Log", key=f"updbtn_{task_id}", use_container_width=True):
                if rmk.strip():
                    storage.add_task_update(uk, task_id, rmk.strip())
                    st.session_state.pop(f"upd_{task_id}", None)
                    st.toast("Update logged — buzzer silenced for this task.")
                    _rerun("app")
                else:
                    st.caption("Type a remark first.")

            # (2) steps — tick freely (session only), persist once on Save
            if steps:
                live_done = 0
                for i, s in enumerate(steps):
                    ck = f"step_{task_id}_{i}"
                    if ck not in st.session_state:
                        st.session_state[ck] = bool(s.get("done"))
                    live_done += 1 if st.session_state[ck] else 0
                st.caption(f"Steps · {live_done}/{len(steps)} ticked · tick freely, then Save")
                for i, s in enumerate(steps):
                    st.checkbox(s["text"], key=f"step_{task_id}_{i}")   # no on_change -> no per-tick save
                dirty = any(bool(st.session_state.get(f"step_{task_id}_{i}")) != bool(steps[i].get("done"))
                            for i in range(len(steps)))
                save_lbl = "💾 Save progress" + (" •" if dirty else "")
                if st.button(save_lbl, key=f"savesteps_{task_id}", use_container_width=True,
                             disabled=not dirty):
                    new_steps = [{"text": s["text"],
                                  "done": bool(st.session_state.get(f"step_{task_id}_{i}"))}
                                 for i, s in enumerate(steps)]
                    storage.set_task_steps(uk, task_id, new_steps)
                    new_status = storage.sync_task_from_steps(uk, task_id)
                    if new_status == "Done":
                        for i in range(len(steps)):
                            st.session_state.pop(f"step_{task_id}_{i}", None)
                        st.toast("All steps done — task completed.")
                        _rerun("app")
                    else:
                        st.toast("Progress saved.")
                        _rerun("fragment")

            # (3) primary buttons for a task with no steps yet
            if not steps:
                b = st.columns(2)
                if b[0].button("✅ Done", key=f"done_{task_id}", use_container_width=True):
                    storage.update_task(uk, task_id, status="Done"); _rerun("app")
                if b[1].button("🪜 Break into steps", key=f"steps_{task_id}", use_container_width=True):
                    topic = storage._topic_of(cur_goal, t.get("category", ""))
                    past, matched = storage.find_step_template(uk, topic, t["title"])
                    with st.spinner("Breaking into steps…" + (" (using your past steps)" if past else "")):
                        s = ai.break_into_steps(t["title"], cur_goal, role_prompt, past_steps=past)
                        storage.set_task_steps(uk, task_id, s)
                        storage.log_task_event(uk, task_id, t["title"], "steps_added", cur_goal,
                                               detail=f"{len(s)} steps" + (f" (from '{matched}')" if past else ""))
                    _rerun("fragment")
            else:
                # task HAS steps — still give a direct way to complete the whole task
                # (ticking every step + Save also completes it, but this never hides the Done).
                if st.button("✅ Mark done", key=f"done_{task_id}", use_container_width=True):
                    storage.update_task(uk, task_id, status="Done"); _rerun("app")

            # ===================== SECONDARY: everything else, one place =====================
            with st.popover("⚙️ Options", use_container_width=True):
                # goal this serves
                choices = headings + ["— no goal (just needed) —"]
                cur_choice = cur_goal if cur_goal in headings else choices[-1]
                pick = st.selectbox("Goal this serves", choices,
                                    index=choices.index(cur_choice), key=f"goal_{task_id}")
                picked_goal = "" if pick.startswith("— no goal") else pick
                if picked_goal != cur_goal:
                    storage.update_task(uk, task_id, day_goal=picked_goal)
                    _rerun("fragment")

                # coaching cue (on demand)
                cc = st.columns([6, 1])
                cc[0].markdown(f"💬 _{cue}_" if cue else "💬 _no cue yet_")
                if cc[1].button("🔄", key=f"cue_{task_id}", help="Get / rethink a coaching tip"):
                    with st.spinner("Thinking…"):
                        topic = storage._topic_of(cur_goal, t.get("category", ""))
                        rule = storage.best_rule(uk, topic)
                        rel = _task_relevant_context(uk, t["title"], cur_goal)
                        new_cue = ai.companion_cue(t["title"], cur_goal, role_prompt,
                                                   _mis_cue_context(uk, None),
                                                   rule["rule_text"] if rule else "",
                                                   relevant_context=rel)
                        storage.update_task(uk, task_id, coach_cue=new_cue)
                    _rerun("fragment")

                # move to another day (time-of-day now lives on the rail)
                import datetime as _dt
                cur_date = (t.get("plan_date") or TODAY_STR).strip()
                try:
                    default_d = _dt.datetime.strptime(cur_date, "%Y-%m-%d").date()
                except Exception:
                    default_d = TODAY
                new_date = st.date_input("📅 Move to another day", value=default_d, min_value=TODAY,
                                         key=f"date_{task_id}", format="DD/MM/YYYY")
                new_date_str = new_date.strftime("%Y-%m-%d")
                if new_date_str != cur_date:
                    storage.update_task(uk, task_id, plan_date=new_date_str, last_buzz_at="")
                    if new_date_str != TODAY_STR:
                        st.toast(f"📅 Postponed to {new_date.strftime('%d %b %Y')}")
                        _rerun("app")     # leaves today -> refresh the list
                    else:
                        _rerun("fragment")

                # past updates
                ups = storage.get_task_updates(uk, task_id)
                if not ups.empty:
                    with st.popover(f"📝 Updates ({len(ups)})"):
                        for _, up in ups.iterrows():
                            st.caption(f"**{up['created_at'][11:16]}** — {up['remark']}")

                # collaborators + WhatsApp share
                directory = storage.get_partners(uk)
                team = directory[directory["contact_type"] == "team"] if not directory.empty else directory
                if not team.empty:
                    names = list(team["name"])
                    current = _collab_of(t)
                    picked = st.multiselect("Collaborators", names,
                                            default=[n for n in current if n in names],
                                            key=f"collab_{task_id}",
                                            placeholder="Add teammates (from your Directory)")
                    if set(picked) != set(current):
                        storage.set_task_collaborators(uk, task_id, picked)
                    if picked:
                        with st.popover("📲 Share on WhatsApp"):
                            mk = f"share_{task_id}"
                            if st.button("Write message", key=f"wmsg_{task_id}"):
                                st.session_state[mk] = ai.share_plan_message(
                                    t["title"], ", ".join(picked), cur_goal, cue,
                                    storage.get_user(uk).get("name", "I"))
                            msg = st.session_state.get(mk, "")
                            msg = st.text_area("Message", value=msg, key=f"wtext_{task_id}", height=90)
                            for n in picked:
                                mob = storage.partner_mobile(uk, n)
                                if mob:
                                    st.link_button(f"Send to {n}", storage.wa_link(mob, msg),
                                                   use_container_width=True)
                                else:
                                    st.caption(f"{n}: no mobile on file")
                else:
                    st.caption("Add teammates in Records → Directory (type: team) to collaborate.")

                # edit steps (AI) / write my own
                if steps:
                    with st.popover("✏️ Edit steps with AI"):
                        instr = st.text_input("How should the steps change?", key=f"si_{task_id}",
                                              placeholder="e.g. add a prep-call step, split the last one")
                        if st.button("Rewrite steps", key=f"sr_{task_id}"):
                            cur_texts = [s["text"] for s in steps]
                            done_map = {s["text"]: s.get("done") for s in steps}
                            new_texts = ai.edit_steps(t["title"], cur_texts, instr, cur_goal, role_prompt)
                            new_steps = [{"text": x, "done": done_map.get(x, False)} for x in new_texts]
                            storage.set_task_steps(uk, task_id, new_steps)
                            topic = storage._topic_of(cur_goal, t.get("category", ""))
                            storage.save_step_template(uk, topic, t["title"], new_texts)
                            for i in range(len(steps)):
                                st.session_state.pop(f"step_{task_id}_{i}", None)
                            storage.sync_task_from_steps(uk, task_id)
                            _rerun("fragment")
                else:
                    with st.popover("✍️ Write my own steps"):
                        own = st.text_area("One step per line", key=f"own_{task_id}", height=90,
                                           placeholder="Call the partner\nShow platform demo\nBook next meeting")
                        if st.button("Set steps", key=f"setown_{task_id}"):
                            lines = [ln.strip() for ln in own.splitlines() if ln.strip()]
                            if lines:
                                storage.set_task_steps(uk, task_id, lines)
                                topic = storage._topic_of(cur_goal, t.get("category", ""))
                                storage.save_step_template(uk, topic, t["title"], lines)
                                storage.log_task_event(uk, task_id, t["title"], "steps_added", cur_goal,
                                                       detail=f"{len(lines)} own steps")
                                _rerun("fragment")

                # rename + delete
                st.divider()
                nt = st.text_input("Rename task", value=t["title"], key=f"t_{task_id}")
                rb = st.columns(2)
                if rb[0].button("💾 Save name", key=f"save_{task_id}", use_container_width=True):
                    if nt.strip():
                        storage.update_task(uk, task_id, title=nt.strip()); _rerun("fragment")
                if rb[1].button("🗑 Delete task", key=f"del_{task_id}", use_container_width=True):
                    storage.update_task(uk, task_id, status="Dropped"); _rerun("app")


def _buzzer(uk, user):
    """Auto-refreshing reminder. Finds due tasks (act-to-stop) and pops a banner + plays
    an alarm, re-nagging every 5 min until the user posts a Task update. Runs on EVERY page
    (called from main), and scans ALL dates so a reminder scheduled for any day — including
    one missed while the app was closed — is never silently dropped.

    Auto-refresh is activated only when there's a pending timed task (already due, or due
    later today), so idle pages with no reminders don't refresh and can't interrupt a
    long-running AI action."""
    import datetime as _dt
    now = _dt.datetime.now()

    # keep the page checking only while there's something to watch (due or upcoming-today)
    pending = storage.pending_buzzer_tasks(uk, now)
    watch_today = any(
        (_d := storage._due_dt(t)) and _d.date() == now.date() for t in pending
    ) or any(
        (_d := storage._due_dt(t)) and _d <= now for t in pending
    )
    if watch_today:
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=60000, key="buzz_tick")
        except Exception:
            st.markdown("<meta http-equiv='refresh' content='60'>", unsafe_allow_html=True)

    due = storage.due_buzzing_tasks(uk)
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

    # Alarm — show the buzzer VIDEO (muted, so browsers allow autoplay) AND play its sound
    # via st.audio. The previous code used st.audio ALONE on a video file, which plays only the
    # file's audio track and never renders the picture — that's exactly why you heard beeps but
    # saw no video. A MUTED video autoplays reliably in every browser and inside the Streamlit
    # Cloud iframe; the sound is best-effort (browsers permit unmuted autoplay once the user has
    # interacted with the page, which they have by the time a reminder fires).
    import os as _os
    snd = _os.path.join(_os.path.dirname(__file__), "assets", "buzzer.mp4")
    if not _os.path.exists(snd):
        import paths
        snd = _os.path.join(paths.base_dir(), "_common", "buzzer.mp4")
    if _os.path.exists(snd):
        # 1) the PICTURE — muted video autoplays everywhere (fall back through param sets for
        #    older Streamlit that lacks muted/loop/autoplay).
        _shown = False
        for _kw in ({"autoplay": True, "loop": True, "muted": True},
                    {"autoplay": True, "loop": True},
                    {"autoplay": True}):
            try:
                st.video(snd, **_kw); _shown = True; break
            except TypeError:
                continue
        if not _shown:
            st.video(snd)
        # 2) the SOUND — best-effort unmuted autoplay of the same clip
        try:
            st.audio(snd, autoplay=True, loop=True)
        except TypeError:
            try:
                st.audio(snd, autoplay=True)
            except TypeError:
                st.audio(snd)
    else:
        st.markdown(
            "<audio autoplay loop><source src='https://actions.google.com/sounds/v1/alarms/"
            "beep_short.ogg' type='audio/ogg'></audio>", unsafe_allow_html=True)


def _personalisation_brief(user):
    """The always-on personalisation text. Two layers:
      - <= RAW_INLINE_MAX accepted learnings: return them raw (all fit, zero loss).
      - more than that: return a DISTILLED brief covering ALL of them, regenerated from the
        full raw set when it has grown by >= REGEN_GROWTH since the brief was last built
        (or when no brief exists yet). Regeneration is the only AI call here and is rare.
    """
    RAW_INLINE_MAX = 12      # below this, just inline the raw learnings
    REGEN_GROWTH = 12        # regenerate the distilled brief after this many new learnings
    uk = user["user_key"]
    acc = storage.get_learnings(uk, status="accepted")
    n = 0 if acc is None or acc.empty else len(acc)
    if n == 0:
        return ""

    def _raw_lines(df):
        lines = []
        for _, lr in df.iterrows():
            tp = (lr.get("topic") or "").strip()
            lines.append(f"- ({tp}) {lr['text']}" if tp else f"- {lr['text']}")
        return "\n".join(lines)

    if n <= RAW_INLINE_MAX:
        return _raw_lines(acc)

    # many learnings -> use the distilled brief, regenerating when it's grown enough
    digest = None
    try:
        digest = storage.get_learnings_digest(uk)
    except Exception:
        digest = None
    prev_count = int(digest["source_count"]) if (digest and str(digest.get("source_count","")).isdigit()) else -1
    need_regen = (digest is None) or (n - prev_count >= REGEN_GROWTH) or (n < prev_count)

    if need_regen:
        try:
            role_prompt = storage.read_role_prompt(user.get("role", ""), uk)
        except Exception:
            role_prompt = ""
        raw = [{"text": lr.get("text", ""), "topic": lr.get("topic", "")}
               for _, lr in acc.iterrows()]
        brief = ai.distill_learnings(raw, role_prompt=role_prompt)
        if brief:
            try:
                storage.save_learnings_digest(uk, brief, n)
            except Exception:
                pass
            return brief
        # distillation produced nothing — fall back to a recent slice rather than nothing
        return _raw_lines(acc.tail(RAW_INLINE_MAX))

    return digest.get("brief") or _raw_lines(acc.tail(RAW_INLINE_MAX))


def _task_relevant_context(uk, task_title, day_goal=""):
    """Build a SHORT, relevant context block for a task — the few learnings/meetings/logs
    that relate to it — selected in Python (no AI). Stores are read once per session and
    cached, so this is effectively free to call per task. Empty string if nothing fits."""
    if not (task_title or day_goal):
        return ""
    cache = st.session_state.setdefault("_retrieval_cache", {})
    # cache the source frames for a few seconds so generating cues for a whole plan
    # doesn't re-read storage once per task
    import time as _t
    stamp = cache.get("_stamp", 0)
    if _t.time() - stamp > 20:
        try:
            cache["learnings"] = storage.get_learnings(uk, status="accepted")
        except Exception:
            cache["learnings"] = None
        try:
            cache["meetings"] = storage.get_meetings(uk)
        except Exception:
            cache["meetings"] = None
        try:
            cache["logs"] = storage.get_daily_logs(uk) if hasattr(storage, "get_daily_logs") else None
        except Exception:
            cache["logs"] = None
        cache["_stamp"] = _t.time()
    try:
        return retrieval.context_for_task(
            task_title, day_goal,
            learnings_df=cache.get("learnings"),
            meetings_df=cache.get("meetings"),
            logs_df=cache.get("logs"))
    except Exception:
        return ""


def _maybe_nudge_popup(user):
    """Show ONE nudge popup per session, capped at 4 per day. Instant (no AI call on the
    open path): surfaces a goal-drift warning, else one of the person's accepted learnings."""
    uk = user["user_key"]
    if st.session_state.get("nudge_popped_session"):
        return
    st.session_state["nudge_popped_session"] = True      # one per session, set up front
    if storage.get_popup_count(uk, TODAY_STR) >= 4:       # daily cap
        return
    msg = ""
    try:
        tasks = storage.get_tasks(uk, TODAY_STR)
        if not tasks.empty:
            open_t = tasks[~tasks["status"].isin(["Done", "Dropped"])]
            if not open_t.empty:
                unlinked = open_t[(open_t["day_goal"].fillna("") == "")
                                  & (open_t["source"] != "follow_up")]
                if len(unlinked) >= 2:
                    msg = (f"{len(unlinked)} of {len(open_t)} tasks today aren't tied to a "
                           f"goal — sure they belong on today?")
        if not msg:
            acc = storage.get_learnings(uk, status="accepted")
            if not acc.empty:
                # surface the learning most RELEVANT to today's open tasks (Python, no AI);
                # fall back to a recent one if nothing matches.
                focus = ""
                try:
                    ot = storage.get_tasks(uk, TODAY_STR)
                    if not ot.empty:
                        ot = ot[~ot["status"].isin(["Done", "Dropped"])]
                        focus = " ".join(ot["title"].fillna("").tolist()[:6]
                                         + ot["day_goal"].fillna("").tolist()[:6])
                except Exception:
                    focus = ""
                picked = retrieval.relevant_learnings(acc, focus, k=1) if focus else []
                text = picked[0]["text"] if picked else str(acc.sample(1).iloc[0]["text"])
                msg = "Remember: " + text
    except Exception:
        msg = ""
    if msg:
        try:
            st.toast(msg, icon="💡")
        except Exception:
            st.info(f"💡 {msg}")
        storage.bump_popup_count(uk, TODAY_STR)


def _quadrant_view(user):
    """Full-width 'expand' view: today's open tasks grouped into target quadrants. Each daily
    target is a cell holding the tasks that serve it; tasks serving no target land in an
    'Unaligned' cell — the Gate made visual."""
    uk = user["user_key"]
    headings = storage.day_goal_headings(uk, TODAY_STR)
    tasks = storage.get_tasks(uk, TODAY_STR)
    open_t = tasks[~tasks["status"].isin(["Done", "Dropped"])] if not tasks.empty else tasks

    top = st.columns([6, 1])
    top[0].markdown("### 🎯 Today's tasks — by target")
    if top[1].button("✖ Minimize", key="min_quadrants", use_container_width=True):
        st.session_state["tasks_expanded"] = False
        st.rerun()
    st.caption("Each task sits under the target it moves. Tasks that serve none are flagged — "
               "move, defer, or drop them.")

    # group open tasks by the target they serve
    groups = {h: [] for h in headings}
    unaligned = []
    if open_t is not None and not open_t.empty:
        for _, t in open_t.iterrows():
            dg = str(t.get("day_goal", "") or "")
            matched = next((h for h in headings if nudge.goal_served(dg, [h])), None)
            (groups[matched] if matched else unaligned).append(t)

    cells = list(headings)
    if unaligned:
        cells.append("__unaligned__")
    if not cells:
        st.info("No targets set today — set targets on the normal view to organise by goal.")
        return

    def _q_task(t, muted=False):
        mark = "◦" if muted else "•"
        meta = f"  ·  ⏰ {t['due_time']}" if str(t.get("due_time", "") or "").strip() else ""
        line = st.columns([6, 1])
        line[0].markdown(f"{mark} {t['title']}{meta}")
        if line[1].button("✓", key=f"qd_{t['task_id']}", help="Mark done"):
            storage.update_task(uk, t["task_id"], status="Done")
            st.rerun()
        with line[0].popover("update / details"):
            cue = str(t.get("coach_cue", "") or "")
            if cue:
                st.caption(f"💬 {cue}")
            rmk = st.text_input("Post an update", key=f"qu_{t['task_id']}",
                                placeholder="Quick remark to log progress / stop the buzzer")
            if st.button("Save update", key=f"qus_{t['task_id']}") and rmk.strip():
                storage.add_task_update(uk, t["task_id"], rmk.strip())
                st.toast("Update logged.")
                st.rerun()

    # two quadrants per row
    for i in range(0, len(cells), 2):
        row = st.columns(2, gap="medium")
        for j, cell in enumerate(cells[i:i + 2]):
            with row[j]:
                with st.container(border=True):
                    if cell == "__unaligned__":
                        st.markdown("⚠️ **Unaligned** · serves no target")
                        for t in unaligned:
                            _q_task(t, muted=True)
                        st.caption("These don't ladder up to a target.")
                    else:
                        n = len(groups[cell])
                        st.markdown(f"🎯 **{cell}** · {n} task{'s' if n != 1 else ''}")
                        if n == 0:
                            st.caption("No tasks yet for this target.")
                        for t in groups[cell]:
                            _q_task(t)


def today_view(user):
    uk = user["user_key"]
    # full-width expand view (target quadrants) replaces the page when toggled on
    if st.session_state.get("tasks_expanded"):
        _quadrant_view(user)
        return
    _maybe_nudge_popup(user)
    _mis_alert_banner(user["user_key"], user)
    left, right = st.columns([1, 1], gap="large")
    with left:
        cards = mis_dashboard(user)
    with right:
        plan_and_tasks(user, cards)

    # Close My Day — a FOOTER you open with a button (so closing tasks never pops it open).
    #  Shows weekdays after 4 PM, in the morning if the last working day wasn't closed, OR
    #  whenever the "Close your Day" bar set the flag.
    # Close My Day ritual — opened ONLY by the "🌙 Close your Day" button under the targets
    # (single entry point; the old footer "Open Close My Day" bar has been removed).
    if st.session_state.get("closeday_open"):
        st.divider()
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
    if not _partner_features_allowed(user):
        st.info("This section isn't part of your workspace.")
        return
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

    # ---- storage ----
    st.markdown("#### Storage")
    st.caption("Your data is saved automatically to the app's secure cloud database — "
               "nothing to back up by hand.")
    uk = user["user_key"]

    st.divider()
    # ---- account ----
    st.markdown("#### Account")
    st.markdown(f"Signed in as **{user.get('name', user['user_key'])}** "
                f"· role: {user.get('role','').replace('_',' ').title() or '—'}")
    st.caption(f"Knowledge/data scope: your own workspace ({user['user_key']}).")

    if not storage._on_cloud_host():
        st.divider()
        # ---- app updates (Desktop only) ----
        try:
            import updater
            updater.render_update_section(user)
        except Exception as _e:
            st.caption("Update tool unavailable: %s" % _e)

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

        # ---- token usage + spend (per user, persisted, cumulative) ----
        st.markdown("#### AI usage & spend")
        u = storage.ai_usage_summary(uk)
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
        days = storage.ai_usage_by_day(uk, limit=14)
        if days:
            with st.expander("Day-wise usage (last 14 days)"):
                tb = pd.DataFrame([{"Date": d["day"], "Calls": d["calls"],
                                    "Tokens": d["tokens"], "Cost ($)": round(d["cost"], 4)}
                                   for d in days])
                st.dataframe(tb, use_container_width=True, hide_index=True)
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


def _goal_kra_options(uk, headings):
    """One deduped list of everything a task can be mapped to: today's targets + the user's
    KRAs + Self-Improvement. Returns list of (label, kind, value); kind in goal/kra/learning.
    Exact (case/space-insensitive) overlaps between a target and a KRA are shown once."""
    def _n(s):
        return " ".join(str(s or "").lower().split())
    opts, seen = [], set()
    for h in headings:                       # today's targets first
        if h and _n(h) not in seen:
            opts.append((h, "goal", h)); seen.add(_n(h))
    for k in _effort_kpi_names(uk):          # KRAs not already shown as a target
        if k and _n(k) not in seen:
            opts.append((f"{k}  ·  KRA", "kra", k)); seen.add(_n(k))
    opts.append(("📘 Self-Improvement / Learning", "learning", "Self-Improvement"))
    return opts


def _voice_vocab():
    """Team member names fed to the transcriber so they're spelled right, not guessed."""
    try:
        df = storage.get_team_roster()
        if df.empty:
            return ""
        names = [str(n).strip() for n in df["name"].tolist() if str(n).strip()]
        return ", ".join(names[:40])
    except Exception:
        return ""


def _working(label="Working…"):
    """Context manager showing a small green spinning-clock 'in progress' indicator while a
    slow step (transcribe, AI, send) runs. Clears itself when the block finishes."""
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        ph = st.empty()
        ph.markdown(
            "<div style='display:flex;align-items:center;gap:9px;padding:4px 0;'>"
            "<span class='pmd-ring'></span>"
            f"<span style='color:#1B7F4B;font-weight:600;font-size:0.9rem;'>{label}</span>"
            "</div>"
            "<style>@keyframes pmdspin{to{transform:rotate(360deg)}}"
            ".pmd-ring{width:16px;height:16px;border:2.5px solid #B7E4C7;"
            "border-top-color:#1B7F4B;border-radius:50%;display:inline-block;"
            "animation:pmdspin .8s linear infinite;}</style>",
            unsafe_allow_html=True)
        try:
            yield
        finally:
            ph.empty()

    return _cm()


def _dictate_bytes(key, label):
    """Capture mic audio robustly across environments.

    Prefers Streamlit's NATIVE st.audio_input, which renders reliably on Streamlit
    Cloud — the third-party streamlit-mic-recorder is a custom component that mounts
    inside an iframe and frequently fails to appear on Cloud. Falls back to
    mic_recorder only on older Streamlit that lacks audio_input.

    Returns raw audio bytes for a NEW recording, else None (native audio_input
    re-returns the same clip on every rerun, so we transcribe each clip only once).
    """
    audio_bytes = None
    if hasattr(st, "audio_input"):
        if not st.session_state.get("_dictate_css"):
            st.markdown(
                "<style>"
                "[data-testid='stAudioInput']{max-width:340px;}"
                "[data-testid='stAudioInput'] label{font-size:0.85rem;font-weight:600;"
                "color:#2D4A5E;}"
                "</style>", unsafe_allow_html=True)
            st.session_state["_dictate_css"] = True
        clip = st.audio_input(label, key=f"ai_{key}")
        if clip is not None:
            audio_bytes = clip.getvalue()
    else:
        try:
            from streamlit_mic_recorder import mic_recorder
            rec = mic_recorder(start_prompt=label, stop_prompt="⏹️ Stop",
                               key=f"mr_{key}", format="wav")
            if rec and rec.get("bytes"):
                audio_bytes = rec["bytes"]
        except Exception:
            st.caption("🎙️ (Voice needs Streamlit ≥1.35 or streamlit-mic-recorder; type below.)")
            return None
    if not audio_bytes:
        return None
    import hashlib
    sig = hashlib.md5(audio_bytes).hexdigest()
    sigk = f"_dictate_sig_{key}"
    if st.session_state.get(sigk) == sig:
        return None                       # this clip was already transcribed
    st.session_state[sigk] = sig
    return audio_bytes


def _dictate_text(key, label):
    """Capture + transcribe in one call. Returns transcribed text, or '' if nothing new."""
    b = _dictate_bytes(key, label)
    if not b:
        return ""
    with _working("Transcribing…"):
        return ai.transcribe(b, vocab=_voice_vocab()) or ""


def _resolve_kras_with_ai(uk, tasks_df):
    """Run AI KRA classification on the UNASSIGNED tasks in tasks_df and store the result
    on each task (kra_resolved). Returns how many were newly assigned. Never raises — if AI
    is unavailable or fails, returns 0 and leaves tasks unassigned."""
    try:
        kpis = _effort_kpi_names(uk)
        pending = classify.unassigned_tasks(tasks_df, kpis)
        if not pending:
            return 0
        assigned = ai.classify_kras_ai(pending, kpis)
        n = 0
        for tid, kra in assigned.items():
            try:
                storage.update_task(uk, tid, kra_resolved=kra)
                n += 1
            except Exception:
                pass
        return n
    except Exception:
        return 0


def _effort_kra_columns(uk):
    """The Effort-matrix KRA columns — a single, fully-editable list owned by the user. Seeds
    from the monthly-target KPI names the first time (so existing columns aren't lost); after
    the user saves, their list is the source of truth. Self-Improvement / Unassigned are added
    by build_matrix."""
    saved = storage.get_effort_kras(uk)
    if saved:
        return saved
    names = []
    try:
        df = storage._read(storage._targets_path(uk), schemas.MONTHLY_TARGETS)
        if not df.empty:
            for n in df["kpi_name"].tolist():
                n = str(n).strip()
                if n and n not in names:
                    names.append(n)
    except Exception:
        pass
    return names


def _effort_kpi_names(uk):
    """KRA columns for the matrix (thin wrapper so callers keep working)."""
    return _effort_kra_columns(uk)


def _effort_cell_color(count, maxv):
    """Warm-amber heatmap shade for a count (Sunrise palette)."""
    if count <= 0 or maxv <= 0:
        return "#FCFAF7", "#CBC3B8"   # bg, text(dot)
    import math
    inten = (count / maxv) ** 0.62
    # interpolate cream -> amber -> deep amber
    stops = [(0.0, (251, 244, 236)), (0.5, (244, 198, 144)), (0.8, (232, 131, 58)), (1.0, (199, 95, 35))]
    for i in range(len(stops) - 1):
        a, b = stops[i], stops[i + 1]
        if inten <= b[0]:
            f = (inten - a[0]) / (b[0] - a[0] or 1)
            rgb = tuple(round(a[1][k] + f * (b[1][k] - a[1][k])) for k in range(3))
            break
    else:
        rgb = stops[-1][1]
    bg = "#%02X%02X%02X" % rgb
    txt = "#FFFFFF" if inten > 0.55 else "#1B2733"
    return bg, txt


def effort_view(user):
    uk = user["user_key"]
    st.markdown("### ⚡ Where My Energy Goes")
    st.caption("Effort type × KRA — how many tasks of each kind serve each goal, for the period you pick.")

    # ---- date filter: quick chips + custom from/to ----
    from datetime import timedelta
    today = TODAY
    # Pickers own the range (keyed widgets). Chips write to the widget keys and rerun, so the
    # pickers are the single source of truth. The old code kept a separate range and re-read
    # the pickers into it — but a keyed date_input ignores value= once it exists, so its stale
    # value overwrote whatever a chip set. That's why Today/All looked dead.
    if "eff_from" not in st.session_state:
        st.session_state.eff_from = today.replace(day=1)   # month-to-date default
        st.session_state.eff_to = today

    chips = st.columns([1, 1, 1, 1, 3])
    if chips[0].button("Today", key="eff_today", use_container_width=True):
        st.session_state.eff_from, st.session_state.eff_to = today, today
        st.rerun()
    if chips[1].button("This Week", key="eff_week", use_container_width=True):
        st.session_state.eff_from = today - timedelta(days=today.weekday())
        st.session_state.eff_to = today
        st.rerun()
    if chips[2].button("This Month", key="eff_month", use_container_width=True):
        st.session_state.eff_from, st.session_state.eff_to = today.replace(day=1), today
        st.rerun()
    if chips[3].button("All", key="eff_all", use_container_width=True):
        st.session_state.eff_from, st.session_state.eff_to = date(2020, 1, 1), today
        st.rerun()

    cc = st.columns([1, 1, 3])
    d_from = cc[0].date_input("From", key="eff_from")
    d_to = cc[1].date_input("To", key="eff_to")

    # ---- gather tasks in range ----
    fs, ts = d_from.strftime("%Y-%m-%d"), d_to.strftime("%Y-%m-%d")
    tasks = storage.get_tasks_between(uk, fs, ts) if hasattr(storage, "get_tasks_between") else None
    if tasks is None:
        # fallback: read the store and filter by plan_date
        df = storage._read(storage._tasks_path(uk), schemas.TASKS)
        tasks = df[(df["plan_date"] >= fs) & (df["plan_date"] <= ts)] if not df.empty else df

    kpis = _effort_kpi_names(uk)
    rows, cols, counts, row_tot, col_tot, grand = classify.build_matrix(tasks, kpis)

    if grand == 0:
        st.info(f"No tasks between {fs} and {ts}. Pick a wider range, or add some tasks.")
        return

    maxv = max((counts[r][c] for r in rows for c in cols), default=0)
    best_col = max(cols, key=lambda c: sum(1 for r in rows if counts[r][c] > 0))  # widest spread

    # ---- render the matrix as an HTML heatmap ----
    def pct(n):
        return f"{round(n * 100 / grand)}%" if grand else "0%"

    th = ("padding:10px 8px;font-size:12.5px;font-weight:700;color:#1B2733;"
          "text-align:center;line-height:1.15;")
    html = ['<div style="overflow-x:auto;"><table style="border-collapse:separate;'
            'border-spacing:6px;width:100%;font-family:Inter,system-ui,sans-serif;">']
    # header row
    html.append('<tr><td style="width:150px;"></td>')
    for c in cols:
        accent = ("border-bottom:3px solid #2E9E6B;" if c == best_col else "")
        html.append(f'<td style="{th}{accent}">{c}</td>')
    html.append('<td style="' + th + 'color:#2D4A5E;">TOTAL</td></tr>')
    # body
    for r in rows:
        html.append('<tr>')
        html.append(f'<td style="padding:8px 10px;font-size:13px;font-weight:600;'
                    f'color:#1B2733;text-align:right;white-space:nowrap;">{r}</td>')
        for c in cols:
            v = counts[r][c]
            bg, txt = _effort_cell_color(v, maxv)
            cell = str(v) if v > 0 else "·"
            fw = "700" if v > 0 else "400"
            html.append(f'<td style="background:{bg};color:{txt};border-radius:10px;'
                        f'text-align:center;font-size:15px;font-weight:{fw};height:48px;'
                        f'min-width:62px;vertical-align:middle;">{cell}</td>')
        # row total
        rt = row_tot[r]
        bar = int(48 * (rt / (max(row_tot.values()) or 1)))
        html.append(f'<td style="background:#F1ECE4;border-radius:10px;text-align:center;'
                    f'vertical-align:middle;min-width:62px;">'
                    f'<div style="font-size:15px;font-weight:700;color:#2D4A5E;">{rt}</div>'
                    f'<div style="font-size:10px;color:#5C6B7A;">{pct(rt)}</div></td>')
        html.append('</tr>')
    # total row
    html.append('<tr><td style="padding:8px 10px;font-size:13px;font-weight:700;'
                'color:#2D4A5E;text-align:right;">TOTAL</td>')
    for c in cols:
        ct = col_tot[c]
        html.append(f'<td style="background:#F1ECE4;border-radius:10px;text-align:center;'
                    f'vertical-align:middle;">'
                    f'<div style="font-size:15px;font-weight:700;color:#2D4A5E;">{ct}</div>'
                    f'<div style="font-size:10px;color:#5C6B7A;">{pct(ct)}</div></td>')
    html.append(f'<td style="background:#2D4A5E;border-radius:10px;text-align:center;'
                f'vertical-align:middle;">'
                f'<div style="font-size:18px;font-weight:800;color:#fff;">{grand}</div>'
                f'<div style="font-size:9.5px;color:#CFE0EA;">tasks</div></td></tr>')
    html.append('</table></div>')
    st.markdown("".join(html), unsafe_allow_html=True)

    st.markdown('<div style="margin-top:14px;font-size:12px;color:#5C6B7A;">'
                'Read a <b>column ↓</b> to see what kinds of effort a KRA gets · '
                'read a <b>row →</b> to see where each effort type is spent · '
                'cell shade = number of tasks · '
                '<span style="color:#2E9E6B;">● widest effort spread</span></div>',
                unsafe_allow_html=True)

    # ---- downloadable, designed PDF of this matrix ----
    try:
        import effort_pdf
        meta = {"name": user.get("name", uk), "role": user.get("role", ""),
                "date_from": fs, "date_to": ts}
        pdf_bytes = effort_pdf.build_effort_pdf(
            meta, rows, cols, counts, row_tot, col_tot, grand,
            learning_kra=classify.LEARNING_KRA, unassigned_kra=classify.UNASSIGNED)
        st.download_button("⬇️ Download effort report (PDF)", data=pdf_bytes,
                           file_name=f"EffortReport_{uk}_{fs}_to_{ts}.pdf",
                           mime="application/pdf", use_container_width=True,
                           key="effort_pdf_dl")
    except Exception as e:
        st.caption(f"PDF export unavailable ({_safe_err(e)}) — add `reportlab` to requirements.txt.")

    # ---- edit the KRA columns (all editable) ----
    with st.expander("⚙️ Edit KRA columns"):
        st.caption("These are the goal areas shown as columns. Edit the list — one KRA per "
                   "line. (Self-Improvement and Unassigned are always shown automatically.)")
        current = [c for c in cols if c not in (classify.LEARNING_KRA, classify.UNASSIGNED)]
        txt = st.text_area("KRAs (one per line)", value="\n".join(current),
                           height=160, key="kra_edit_box",
                           placeholder="Revenue\nNew Partner Acquisition\nTeam Mentoring")
        ec = st.columns([1, 1, 2])
        if ec[0].button("Save KRAs", type="primary", key="kra_save_btn"):
            skip = {classify.LEARNING_KRA.lower(), classify.UNASSIGNED.lower()}
            new_names = [ln.strip() for ln in txt.splitlines()
                         if ln.strip() and ln.strip().lower() not in skip]
            storage.save_effort_kras(uk, new_names)
            st.success("KRA columns updated.")
            st.rerun()
        if ec[1].button("Reset to my targets", key="kra_reset_btn",
                        help="Clear your custom list and reseed from your MIS target KPIs"):
            storage.save_effort_kras(uk, [])
            st.success("Reset to your target KPIs.")
            st.rerun()

    # ---- unassigned + manual/AI resolution ----
    pending = classify.unassigned_tasks(tasks, kpis)
    if pending:
        st.divider()
        sc = st.columns([3, 1])
        sc[0].caption(f"📌 {len(pending)} task(s) in this range have no KRA yet. "
                      "They're auto-classified when you Close My Day. To classify older "
                      "tasks too, set the range to **All** and tap Sync now — or assign "
                      "them by hand on the History page.")
        if sc[1].button("✨ Sync now", key="eff_sync", use_container_width=True,
                        help="Run AI to assign KRAs to the unassigned tasks in this range"):
            with st.spinner("Classifying…"):
                n = _resolve_kras_with_ai(uk, tasks)
            if n:
                st.success(f"Assigned {n} task(s).")
                st.rerun()
            else:
                st.info("Nothing could be auto-assigned — try assigning them on History.")


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
    if not _partner_features_allowed(user):
        st.info("This section isn't part of your workspace.")
        return
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
    partner_ok = _partner_features_allowed(user)
    type_opts = ["partner", "team"] if partner_ok else ["team"]
    with st.form("add_contact"):
        st.caption("Add a contact — partner or team member. Used as reminder recipients."
                   if partner_ok else "Add a team member. Used for task collaborators & reminders.")
        c = st.columns([3, 3, 2])
        name = c[0].text_input("Name")
        mobile = c[1].text_input("Mobile")
        ctype = c[2].selectbox("Type", type_opts)
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
        uctype = (st.radio("Import as", ["partner", "team"], horizontal=True, key="dir_uptype")
                  if partner_ok else "team")
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

    groups = [("partner", "Partners"), ("team", "Team")] if partner_ok else [("team", "Team")]
    for ctype, label in groups:
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

    # ---- assign KRAs to tasks that have none (feeds the Effort matrix) ----
    all_tasks = storage.get_tasks(uk)
    kpis = _effort_kpi_names(uk)
    pend = classify.unassigned_tasks(all_tasks, kpis) if not all_tasks.empty else []
    if pend:
        kra_opts = list(kpis) + [classify.LEARNING_KRA]
        with st.expander(f"🏷️ Assign a KRA — {len(pend)} task(s) need one", expanded=False):
            st.caption("These tasks aren't linked to any goal yet, so they show as "
                       "“Unassigned” in Where My Energy Goes. Pick the KRA each one served, "
                       "then press Save all — they save together in one go.")
            # show the most recent ones first, cap the list so it stays manageable
            pend_sorted = sorted(pend, key=lambda t: str(t.get("plan_date", "")), reverse=True)[:25]
            for t in pend_sorted:
                tid = t["task_id"]
                cc = st.columns([4, 3])
                cc[0].markdown(f"**{t.get('title','(untitled)')}**  \n"
                               f"<span style='color:#5C6B7A;font-size:12px;'>{t.get('plan_date','')}</span>",
                               unsafe_allow_html=True)
                cc[1].selectbox("KRA", ["— pick —"] + kra_opts + ["Not goal-related"],
                                key=f"kra_pick_{tid}", label_visibility="collapsed")
            if st.button("💾 Save all", type="primary", key="kra_save_all"):
                mapping = {}
                for t in pend_sorted:
                    tid = t["task_id"]
                    choice = st.session_state.get(f"kra_pick_{tid}", "— pick —")
                    if choice and choice != "— pick —":
                        # "Not goal-related" -> Unassigned so it stops re-appearing
                        mapping[tid] = "Unassigned" if choice == "Not goal-related" else choice
                if mapping:
                    n = storage.set_kras_bulk(uk, mapping)
                    st.success(f"Saved {n} — Where My Energy Goes will reflect it.")
                    st.rerun()
                else:
                    st.warning("Pick a KRA for at least one task first.")

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

    # ---- tasks by status: Pending / Completed ----
    st.markdown("##### Tasks")
    if "hist_filter" not in st.session_state:
        st.session_state.hist_filter = "Pending"
    fc = st.columns([1, 1, 1, 3])
    if fc[0].button("⏳ Pending", key="hf_pend", use_container_width=True):
        st.session_state.hist_filter = "Pending"
    if fc[1].button("✅ Completed", key="hf_done", use_container_width=True):
        st.session_state.hist_filter = "Completed"
    if fc[2].button("All", key="hf_all", use_container_width=True):
        st.session_state.hist_filter = "All"
    flt = st.session_state.hist_filter
    allt = storage.get_tasks(uk)
    if allt.empty:
        st.caption("No tasks yet.")
    else:
        if flt == "Pending":
            view = allt[allt["status"] == "Open"]
        elif flt == "Completed":
            view = allt[allt["status"] == "Done"]
        else:
            view = allt
        if view.empty:
            st.caption(f"No {flt.lower()} tasks.")
        else:
            tb = view[["plan_date", "title", "status", "day_goal"]].copy()
            tb = tb.sort_values("plan_date", ascending=False)
            tb.columns = ["Date", "Task", "Status", "Goal / KRA"]
            st.dataframe(tb, use_container_width=True, hide_index=True)
            st.caption(f"{len(view)} {flt.lower()} task(s)")
    st.divider()

    st.markdown("##### Activity log")
    log = storage.get_task_log(uk)
    if log.empty:
        st.info("No activity yet."); return
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
    # mic dictation — native audio_input (reliable on Cloud)
    _t = _dictate_text("log", "🎙️ Dictate log")
    if _t:
        st.session_state["log_text"] = (st.session_state.get("log_text", "") + " " + _t).strip()

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

def header_nav(is_lead, partner_ok=True, is_admin=False):
    """Grouped two-row nav (Option A): a small row of GROUPS that never wraps, and a
    second row of the selected group's sub-tabs. Returns the selected leaf tab name, so
    routing in main() is unchanged. Admin logins see only the Admin module."""
    if is_admin:
        if HAVE_OPTION_MENU:
            return option_menu(None, ["Admin"], icons=["shield-lock"],
                               orientation="horizontal", default_index=0,
                               styles=style.NAV_STYLES)
        return "Admin"

    desktop_extra = []
    if reports_engine is not None and not storage._on_cloud_host():
        desktop_extra.append(("Reports", "bar-chart"))
    if dump_sender is not None:
        desktop_extra.append(("Sarthi", "send"))     # both platforms; Send Dumps is desktop-gated inside

    mind_ok = mindmap is not None and not storage._on_cloud_host()

    # (group label, group icon, [(tab, icon), ...]) — tabs filtered by permission/availability
    groups = [
        ("Plan", "columns-gap",
            [("Today", "columns-gap")]
            + ([("Daily log", "notebook")] if partner_ok else [])
            + [("Updates", "megaphone")]),
        ("Work", "kanban",
            [("Records", "address-book")]
            + ([("Communicate", "send")] if partner_ok else [])
            + [("Projects", "kanban")]
            + ([("Mind Map", "sitemap")] if mind_ok else [])),
        ("Track", "compass",
            [("Monthly", "compass"), ("Effort", "grid-3x3-gap-fill"),
             ("History", "clock-history"), ("Learning", "lightbulb")]),
        ("Data", "bar-chart", desktop_extra),
        ("Settings", "gear", [("Settings", "gear")]),
    ]
    groups = [(g, ic, tabs) for (g, ic, tabs) in groups if tabs]   # drop empty groups (e.g. Data on Cloud)

    if not HAVE_OPTION_MENU:
        flat = [t for _, _, tabs in groups for t, _ in tabs]
        return st.radio("Navigate", flat, horizontal=True, label_visibility="collapsed")

    group_labels = [g for g, _, _ in groups]
    group_icons = [ic for _, ic, _ in groups]
    cur_group = st.session_state.get("nav_group", group_labels[0])
    if cur_group not in group_labels:
        cur_group = group_labels[0]
    sel_group = option_menu(
        None, group_labels, icons=group_icons, orientation="horizontal",
        default_index=group_labels.index(cur_group), key="nav_group_menu",
        styles=style.NAV_STYLES)
    st.session_state["nav_group"] = sel_group

    sub = dict((g, tabs) for g, _, tabs in groups)[sel_group]
    sub_labels = [t for t, _ in sub]
    sub_icons = [ic for _, ic in sub]
    if len(sub_labels) == 1:
        return sub_labels[0]                      # single-item group (e.g. Settings) — no sub-row
    key_sub = "nav_sub_" + sel_group
    cur_sub = st.session_state.get(key_sub, sub_labels[0])
    if cur_sub not in sub_labels:
        cur_sub = sub_labels[0]
    sel_sub = option_menu(
        None, sub_labels, icons=sub_icons, orientation="horizontal",
        default_index=sub_labels.index(cur_sub), key="menu_" + key_sub,
        styles=style.NAV_SUB_STYLES)
    st.session_state[key_sub] = sel_sub
    return sel_sub


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


def _had_activity(uk, d):
    """Did the user actually use the app on date d? (Any tasks planned, or a daily log.)
    If not, there's nothing to close — so the close-day gate must not block on it."""
    try:
        t = storage.get_tasks(uk, d)
        if not t.empty:
            return True
        logs = storage.get_daily_logs(uk)
        if not logs.empty and (logs["date"] == d).any():
            return True
    except Exception:
        return False
    return False


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
        You didn't close <b>{nice}</b>. Wrap it up and download the Progress Brief to continue — this
        keeps your record complete. The rest of the app unlocks once it's done.</div>
    </div>
    """, unsafe_allow_html=True)

    month = prev_date[:7]
    # Build the report ONCE and cache it — never rebuild on every rerun (that was very slow).
    ck = f"forceclose_dsr_{prev_date}"
    if ck not in st.session_state:
        try:
            st.session_state[ck] = dsr.build_docx(user, prev_date, month)
        except Exception as e:
            st.session_state[ck] = None
            st.error(f"Couldn't build the Progress Brief: {e}")
    dsr_bytes = st.session_state[ck]

    if dsr_bytes:
        # save the report (text → cloud, plus local Word archive) once
        if st.session_state.get("forceclose_saved") != prev_date:
            try:
                storage.save_dsr(uk, prev_date, dsr.docx_to_text(dsr_bytes))
                import paths, os as _os
                rep = paths.user_reports_dir(uk); _os.makedirs(rep, exist_ok=True)
                with open(_os.path.join(rep, f"ProgressBrief_{prev_date}.docx"), "wb") as fh:
                    fh.write(dsr_bytes)
            except Exception:
                pass
            st.session_state["forceclose_saved"] = prev_date

        st.download_button("⬇️ Download the Progress Brief", data=dsr_bytes,
                           file_name=f"ProgressBrief_{uk}_{prev_date}.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           use_container_width=True)
        st.caption("Download the Progress Brief, then close the day below.")
        if st.button(f"✅ Close {nice} & continue", type="primary", use_container_width=True):
            storage.mark_day_closed(uk, prev_date)
            _resolve_kras_with_ai(uk, storage.get_tasks(uk, prev_date))
            st.session_state.pop("forceclose_saved", None)
            st.session_state.pop(ck, None)
            st.rerun()
    else:
        # report couldn't build — still let them close so they aren't permanently locked out
        if st.button(f"✅ Mark {nice} closed & continue", type="primary", use_container_width=True):
            storage.mark_day_closed(uk, prev_date)
            _resolve_kras_with_ai(uk, storage.get_tasks(uk, prev_date))
            st.rerun()


def _render_media(row):
    """Render a content item's media: an uploaded image/MP4 (stored as a data URI),
    a YouTube iframe, an MP4 player, or an image URL."""
    url = str(row.get("media_url", "") or "").strip()
    kind = str(row.get("media_kind", "") or "").strip() or storage._detect_media_kind(url)
    if not url or kind == "none":
        return
    # uploaded file, stored inline as a base64 data URI
    if url.startswith("data:"):
        import base64 as _b64
        try:
            head, b64 = url.split(",", 1)
            data = _b64.b64decode(b64)
        except Exception:
            return
        if head.startswith("data:video") or kind == "mp4":
            try:
                st.video(data)
            except Exception:
                pass
        else:
            st.image(data, use_container_width=True)
        return
    if kind == "youtube":
        vid = ""
        if "youtu.be/" in url:
            vid = url.split("youtu.be/")[1].split("?")[0].split("&")[0]
        elif "watch?v=" in url:
            vid = url.split("watch?v=")[1].split("&")[0]
        elif "/embed/" in url:
            vid = url.split("/embed/")[1].split("?")[0]
        if vid:
            st.markdown(
                f'<div style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;'
                f'border-radius:10px;"><iframe src="https://www.youtube.com/embed/{vid}" '
                f'style="position:absolute;top:0;left:0;width:100%;height:100%;border:0;" '
                f'allowfullscreen></iframe></div>', unsafe_allow_html=True)
        else:
            st.video(url)
    elif kind == "mp4":
        try:
            st.video(url)
        except Exception:
            st.markdown(f"[▶ Watch video]({url})")
    elif kind == "image":
        st.image(url, use_container_width=True)


def _content_card(row):
    """Render one piece of content as a card (used in the Updates tab)."""
    icon = {"banner": "📢", "video": "🎬", "contest": "🏆",
            "result": "🥇", "update": "📣"}.get(row.get("type", "update"), "📣")
    with st.container(border=True):
        title = str(row.get("title", "") or "").strip()
        if title:
            st.markdown(f"#### {icon} {title}")
        _render_media(row)
        body = str(row.get("body", "") or "").strip()
        if body:
            st.markdown(body)
        when = str(row.get("created_at", "") or "")[:10]
        st.caption(f"{row.get('type','update').title()} · {when}")


def _today_banners(user):
    """Top-priority live banners surfaced on the Today page so announcements are seen."""
    rows = storage.live_content_for(user["user_key"], types=["banner"])
    for row in rows[:2]:
        with st.container(border=True):
            t = str(row.get("title", "") or "").strip()
            b = str(row.get("body", "") or "").strip()
            st.markdown(f"📢 **{t}**" if t else "📢")
            _render_media(row)
            if b:
                st.markdown(b)


def updates_view(user):
    st.markdown("### 📣 Updates")
    st.caption("Announcements, contests, results and posts from the team.")
    rows = storage.live_content_for(user["user_key"])
    if not rows:
        st.info("No updates right now. Check back later.")
        return
    for row in rows:
        _content_card(row)


# ---------------------------------------------------------------- Admin / CMS module

_CONTENT_TYPES = ["banner", "video", "contest", "result", "update"]


def _admin_users_panel():
    st.markdown("#### Users & logins")
    st.caption("Create or edit team logins. The **role** decides which Role prompt the "
               "person gets (it must match a role_prompts/<role>.md file). Passwords are "
               "stored hashed. Saves to the shared users table (Neon when configured).")

    df = storage.get_users()
    if df.empty:
        st.info("No users yet — add one below.")
    else:
        hc = st.columns([3, 3, 2, 2])
        for col, h in zip(hc, ["Name / username", "Role", "Status", ""]):
            col.markdown(f"<span style='font-size:12px;font-weight:700;color:#5C6B7A;'>{h}</span>",
                         unsafe_allow_html=True)
        for _, u in df.sort_values("user_key").iterrows():
            uk = str(u["user_key"])
            active = str(u.get("active", "Yes")).strip().lower() in ("yes", "true", "1", "y", "")
            rc = st.columns([3, 3, 2, 2])
            rc[0].markdown(f"**{u.get('name') or uk}**  \n`{uk}`")
            rc[1].markdown((u.get("role", "") or "—").replace("_", " "))
            rc[2].markdown("🟢 active" if active else "🔴 inactive")
            if rc[3].button("Deactivate" if active else "Activate", key=f"ua_{uk}"):
                storage.set_user_active(uk, "No" if active else "Yes")
                st.rerun()

    st.divider()
    st.markdown("##### Add or edit a login")
    st.caption("To edit someone, enter their existing username. Leave the password blank "
               "to keep the current one; type a new one to reset it.")
    c = st.columns(2)
    uk_in = c[0].text_input("Username (user_key)", key="au_uk", placeholder="e.g. rinku")
    name_in = c[1].text_input("Display name", key="au_name")
    email_in = st.text_input("Email (optional — used for MIS report replies)", key="au_email",
                             placeholder="name@bigul.co")
    # Role options are read live from the repo's role_prompts/ files, so any <role>.md you
    # upload appears here automatically — plus ADMIN and a type-your-own option.
    role_list = storage.available_roles()
    OTHER = "➕ Other (type a name)…"
    c2 = st.columns(2)
    role_sel = c2[0].selectbox("Role", role_list + ["ADMIN", OTHER], key="au_role")
    dept_in = c2[1].text_input("Department (optional)", key="au_dept")
    if role_sel == OTHER:
        role_in = st.text_input(
            "Role name — must exactly match a role_prompts/<name>.md in the repo",
            key="au_role_custom").strip()
    else:
        role_in = role_sel
    pw_in = st.text_input("Password (blank = keep existing when editing)",
                          type="password", key="au_pw")
    if st.button("Save login", type="primary", key="au_save"):
        if not (uk_in or "").strip():
            st.error("Username is required.")
        elif not (role_in or "").strip():
            st.error("Pick a role, or type one that matches a role_prompts/<name>.md file.")
        else:
            try:
                action, saved = storage.upsert_user(
                    uk_in, name_in, role_in, password=(pw_in or None),
                    department=dept_in, email=(email_in.strip() or None),
                    login_role="admin" if role_in == "ADMIN" else "member")
                verified = storage.verify_user_in_db(saved)
                if verified is True:
                    st.success(f"Login '{saved}' {action} and confirmed in Neon ✅")
                    st.rerun()
                elif verified is False:
                    st.error(
                        f"'{saved}' was {action} in the app but is NOT readable back from "
                        f"Neon — the write did not persist to the database. "
                        f"(Neon backend active: {storage._use_pg()}; "
                        f"users routed to Neon: {storage._pg_for(storage._users_path())}; "
                        f"on cloud host: {storage._on_cloud_host()})")
                else:
                    st.warning(
                        f"Login '{saved}' {action}, but Neon is NOT configured — it was "
                        "saved to LOCAL files, not the shared database. Set NEON_DATABASE_URL.")
            except Exception as e:
                st.error(f"Couldn't save: {e}")

    st.divider()
    with st.expander("⚙️ Database schema (Neon)"):
        try:
            import db as _dbmod
            d = _dbmod.diagnostics()
            good = d["psycopg_imported"] and d["url_present"] and d["enabled"]
            line = (f"psycopg imported: **{d['psycopg_imported']}** · "
                    f"NEON_DATABASE_URL present: **{d['url_present']}** · "
                    f"Neon active: **{d['enabled']}**")
            (st.success if good else st.error)(line)
            if not good:
                st.caption("If Neon active is False, saves go to LOCAL files, not the shared "
                           "database. psycopg False → the driver didn't install (check logs). "
                           "URL present False → NEON_DATABASE_URL isn't readable (must be a "
                           "top-level key in Secrets, not nested). Reboot the app after fixing.")
        except Exception as _e:
            st.caption(f"diagnostics unavailable: {_e}")
        st.caption("Create any missing tables in the shared Neon database (e.g. login_log). "
                   "Idempotent and safe to run anytime — it only adds what's missing and "
                   "never touches existing data. Both the cloud and desktop apps use this "
                   "same database, so running it once here updates it for everyone.")
        if st.button("Update database schema now", key="db_init_btn"):
            with st.spinner("Updating Neon schema…"):
                ok, msg = storage.ensure_db_schema()
            (st.success if ok else st.error)(msg)


def _admin_registries_panel():
    st.markdown("#### Dump types & MIS types")
    st.caption("These lists drive the Sarthi screen (Send Dump / Request MIS) on every "
               "machine. Add or edit here — the sender and receiver both read from Neon, so "
               "a new type appears everywhere. A brand-new type still needs a matching "
               "handler on the Sarthi (receiver) side to be processed.")

    reg = st.radio("Registry", ["Dump types", "MIS types"], horizontal=True, key="reg_which")
    is_dump = (reg == "Dump types")

    rows = storage.get_dump_types(active_only=False) if is_dump \
        else storage.get_mis_types(active_only=False)
    if rows:
        hc = st.columns([3, 3, 2, 2])
        for col, h in zip(hc, ["Name / key", "Handler", "Active", ""]):
            col.markdown(f"<span style='font-size:12px;font-weight:700;color:#5C6B7A;'>{h}</span>",
                         unsafe_allow_html=True)
        for r in rows:
            k = str(r.get("key", ""))
            act = str(r.get("active", "Yes")).strip().lower() in ("yes", "true", "1", "y", "")
            rc = st.columns([3, 3, 2, 2])
            rc[0].markdown(f"**{r.get('name') or k}**  \n`{k}`")
            rc[1].markdown(str(r.get("handler", "") or "—"))
            rc[2].markdown("🟢" if act else "🔴")
            if rc[3].button("Disable" if act else "Enable", key=f"reg_tog_{reg}_{k}"):
                new = "No" if act else "Yes"
                if is_dump:
                    storage.upsert_dump_type(k, r.get("name"), r.get("max_files", 1),
                                             r.get("handler"), new, r.get("sort_order", 100))
                else:
                    storage.upsert_mis_type(k, r.get("name"), r.get("params_hint", ""),
                                            r.get("handler"), new, r.get("sort_order", 100))
                st.rerun()

    st.divider()
    st.markdown("##### Add or edit")
    c = st.columns(2)
    key_in = c[0].text_input("Key (routing id, lowercase)", key="reg_key",
                             placeholder="e.g. algo_leads")
    name_in = c[1].text_input("Display name", key="reg_name")
    c2 = st.columns(2)
    handler_in = c2[0].text_input("Handler (receiver pipeline)", key="reg_handler",
                                  placeholder="defaults to key")
    sort_in = c2[1].number_input("Sort order", 1, 999, 100, 10, key="reg_sort")
    if is_dump:
        maxf = st.number_input("Max files", 1, 5, 1, 1, key="reg_maxf")
    else:
        hint = st.text_input("Parameters hint (shown to the user)", key="reg_hint",
                             placeholder="e.g. date range, team")
    if st.button("Save", type="primary", key="reg_save"):
        if not key_in.strip():
            st.error("Key is required.")
        else:
            try:
                if is_dump:
                    act, k = storage.upsert_dump_type(key_in, name_in, int(maxf),
                                                      handler_in, "Yes", int(sort_in))
                else:
                    act, k = storage.upsert_mis_type(key_in, name_in, hint,
                                                     handler_in, "Yes", int(sort_in))
                st.success(f"{reg[:-1]} '{k}' {act}.")
                st.rerun()
            except Exception as e:
                st.error(f"Couldn't save: {e}")


def admin_view(user):
    if not _is_admin(user):
        st.error("Admins only."); return
    st.markdown("### 🛡️ Admin — content & MIS")
    (tab_pub, tab_manage, tab_mis, tab_users, tab_reg,
     tab_team, tab_analysis) = st.tabs(
        ["Publish", "Manage", "MIS push", "Users", "Registries",
         "Team status", "Analysis"])

    with tab_users:
        _admin_users_panel()

    with tab_reg:
        _admin_registries_panel()

    # ---- Publish ----
    with tab_pub:
        st.caption("Create a banner, video, contest, result, or general update.")
        ctype = st.selectbox("Type", _CONTENT_TYPES, key="adm_type")
        title = st.text_input("Title", key="adm_title")
        body = st.text_area("Body (supports markdown)", key="adm_body", height=120)
        st.markdown("**Media** (optional)")
        msrc = st.radio("Media source", ["None", "Paste a link", "Upload a file"],
                        horizontal=True, key="adm_msrc", label_visibility="collapsed")
        media_url = ""
        if msrc == "Paste a link":
            media_url = st.text_input("Link (YouTube, or a direct image / .mp4 URL)",
                                      key="adm_media",
                                      placeholder="https://youtu.be/…  ·  https://….png  ·  https://….mp4")
            if media_url.strip():
                st.caption(f"Detected media: **{storage._detect_media_kind(media_url)}**")
        elif msrc == "Upload a file":
            up = st.file_uploader("Upload an image or a short MP4",
                                  type=["png", "jpg", "jpeg", "gif", "webp",
                                        "mp4", "webm", "mov"], key="adm_upl")
            if up is not None:
                data = up.getvalue()
                mb = len(data) / (1024 * 1024)
                mime = up.type or ""
                is_video = mime.startswith("video") or up.name.lower().endswith(
                    (".mp4", ".webm", ".mov"))
                cap = 15 if is_video else 5
                if mb > cap:
                    st.warning(
                        f"That file is {mb:.1f} MB — please keep "
                        f"{'videos' if is_video else 'images'} under {cap} MB. "
                        + ("For a longer video, upload it to YouTube and paste the link "
                           "instead (that's the reliable way to host video)."
                           if is_video else ""))
                else:
                    import base64 as _b64
                    default_mime = "video/mp4" if is_video else "image/png"
                    b64 = _b64.b64encode(data).decode()
                    media_url = f"data:{mime or default_mime};base64,{b64}"
                    if is_video:
                        st.video(data)
                    else:
                        st.image(data, width=280)
                    st.caption(f"Ready to attach — {mb:.1f} MB "
                               f"{'video' if is_video else 'image'}.")

        # target
        users = storage.get_users()
        opts = ["all"] + ([str(u) for u in users["user_key"].tolist()] if not users.empty else [])
        labels = {"all": "Everyone"}
        if not users.empty:
            for _, u in users.iterrows():
                labels[u["user_key"]] = f"{u['name']} ({u['user_key']})"
        target = st.selectbox("Show to", opts, format_func=lambda x: labels.get(x, x), key="adm_target")

        c = st.columns(2)
        priority = c[0].number_input("Priority (higher shows first)", 0, 100, 0, key="adm_prio")
        import datetime as _dt
        sched = c[1].checkbox("Schedule / expire", key="adm_sched")
        publish_at = expires_at = ""
        if sched:
            sc = st.columns(2)
            pd = sc[0].date_input("Publish on", value=TODAY, key="adm_pubd")
            pt = sc[0].time_input("at", value=_dt.time(9, 0), key="adm_pubt", step=900)
            publish_at = _dt.datetime.combine(pd, pt).isoformat(timespec="minutes")
            use_exp = sc[1].checkbox("Set an expiry", key="adm_useexp")
            if use_exp:
                ed = sc[1].date_input("Expire on", value=TODAY + _dt.timedelta(days=7), key="adm_expd")
                et = sc[1].time_input("at ", value=_dt.time(23, 59), key="adm_expt", step=900)
                expires_at = _dt.datetime.combine(ed, et).isoformat(timespec="minutes")

        b = st.columns(2)
        if b[0].button("📤 Publish now", type="primary", key="adm_pub_now", use_container_width=True):
            if not title.strip() and not body.strip() and not media_url.strip():
                st.warning("Add a title, body, or media first.")
            else:
                storage.add_content({"type": ctype, "title": title.strip(), "body": body,
                                     "media_url": media_url.strip(), "target": target,
                                     "status": "published", "priority": priority,
                                     "publish_at": publish_at, "expires_at": expires_at,
                                     "created_by": user["user_key"]})
                st.success("Published.")
                st.rerun()
        if b[1].button("💾 Save as draft", key="adm_pub_draft", use_container_width=True):
            storage.add_content({"type": ctype, "title": title.strip(), "body": body,
                                 "media_url": media_url.strip(), "target": target,
                                 "status": "draft", "priority": priority,
                                 "publish_at": publish_at, "expires_at": expires_at,
                                 "created_by": user["user_key"]})
            st.success("Saved as draft.")
            st.rerun()

    # ---- Manage ----
    with tab_manage:
        df = storage.get_content()
        if df.empty:
            st.info("No content yet — create some on the Publish tab.")
        else:
            df = df.sort_values("created_at", ascending=False)
            st.caption(f"{len(df)} item(s).")
            for _, r in df.iterrows():
                row = r.to_dict()
                stat = row.get("status", "draft")
                badge = {"published": "🟢", "draft": "⚪", "archived": "🗄️"}.get(stat, "•")
                tgt = row.get("target", "all")
                with st.expander(f"{badge} {row.get('type','update').title()} · "
                                 f"{row.get('title','(untitled)') or '(untitled)'} · "
                                 f"{'Everyone' if tgt=='all' else tgt}"):
                    if row.get("body"):
                        st.markdown(row["body"])
                    if row.get("media_url"):
                        st.caption(f"Media: {row['media_kind']} · {row['media_url']}")
                    mc = st.columns(4)
                    cid = row["content_id"]
                    if stat != "published":
                        if mc[0].button("Publish", key=f"pub_{cid}"):
                            storage.update_content(cid, status="published"); st.rerun()
                    else:
                        if mc[0].button("Unpublish", key=f"unp_{cid}"):
                            storage.update_content(cid, status="draft"); st.rerun()
                    if mc[1].button("Archive", key=f"arc_{cid}"):
                        storage.update_content(cid, status="archived"); st.rerun()
                    if mc[2].button("🗑 Delete", key=f"del_{cid}"):
                        storage.delete_content(cid); st.rerun()

    # ---- MIS push ----
    with tab_mis:
        st.caption("Upload the monthly MIS sheet — achievements are pushed into each user's "
                   "targets. Same format the app's MIS sync expects (user / KPI / achieved).")
        import mis_sync
        up = st.file_uploader("MIS Excel (.xlsx)", type=["xlsx"], key="adm_mis_file")
        month = st.text_input("Month (YYYY-MM)", value=MONTH, key="adm_mis_month")
        if up is not None and st.button("Parse & preview", key="adm_mis_parse"):
            try:
                rows = mis_sync.parse(up.getvalue())
                st.session_state["adm_mis_rows"] = rows
                st.success(f"Parsed {len(rows)} row(s).")
            except Exception as e:
                st.error(f"Couldn't parse: {e}")
        rows = st.session_state.get("adm_mis_rows")
        if rows:
            import pandas as _pd
            st.dataframe(_pd.DataFrame(rows), use_container_width=True, hide_index=True)
            if st.button("✅ Push to targets", type="primary", key="adm_mis_apply"):
                applied, skipped, log = mis_sync.apply(rows, month.strip())
                st.success(f"Applied {applied}, skipped {skipped}.")
                with st.expander("Details"):
                    st.write("\n".join(log[:200]))
                st.session_state.pop("adm_mis_rows", None)

    # ---- Team status: everyone's day at a glance ----
    with tab_team:
        st.caption(f"Everyone's status for today · {TODAY_STR}. See who has logged in, who "
                   "has closed their day, and open anyone's Progress Brief.")
        users = storage.get_users()
        people = ([u for _, u in users.iterrows()
                   if str(u.get("role", "")).upper() != "ADMIN"]
                  if not users.empty else [])
        if not people:
            st.info("No users yet.")
        else:
            n = len(people)
            n_login = sum(1 for u in people if storage.logged_in_today(u["user_key"]))
            n_closed = sum(1 for u in people
                           if storage.is_day_closed(u["user_key"], TODAY_STR))
            m = st.columns(3)
            m[0].metric("Team", n)
            m[1].metric("Logged in today", f"{n_login}/{n}")
            m[2].metric("Closed the day", f"{n_closed}/{n}")
            st.divider()
            hc = st.columns([3, 2, 2, 3])
            for col, lbl in zip(hc, ["Member", "Logged in", "Closed day", "Progress Brief"]):
                col.markdown(f"<span style='font-size:12px;font-weight:700;color:#5C6B7A;'>"
                             f"{lbl}</span>", unsafe_allow_html=True)
            for u in sorted(people, key=lambda x: str(x.get("name", ""))):
                uk2 = str(u["user_key"])
                lt = storage.login_time_today(uk2)   # 'HH:MM' if logged in today, else ''
                cd = storage.is_day_closed(uk2, TODAY_STR)
                rc = st.columns([3, 2, 2, 3])
                rc[0].markdown(
                    f"**{u.get('name', uk2)}**  \n"
                    f"<span style='color:#5C6B7A;font-size:12px;'>"
                    f"{str(u.get('role','')).replace('_',' ').title()}</span>",
                    unsafe_allow_html=True)
                rc[1].markdown(f"🔓 {lt}" if lt else "🔒 Not yet")
                rc[2].markdown("🌙 Closed" if cd else "☀️ Open")
                if rc[3].button("📄 Build brief", key=f"tb_pb_{uk2}"):
                    try:
                        st.session_state[f"tb_pbdata_{uk2}"] = dsr.build_docx(
                            dict(u), TODAY_STR, MONTH)
                    except Exception as e:
                        st.session_state[f"tb_pbdata_{uk2}"] = None
                        st.warning(f"Couldn't build brief for {u.get('name', uk2)}: {e}")
                if st.session_state.get(f"tb_pbdata_{uk2}"):
                    rc[3].download_button(
                        "⬇ Download", data=st.session_state[f"tb_pbdata_{uk2}"],
                        file_name=f"ProgressBrief_{uk2}_{TODAY_STR}.docx",
                        mime=("application/vnd.openxmlformats-officedocument."
                              "wordprocessingml.document"),
                        key=f"tb_pbdl_{uk2}")

    # ---- Analysis: daily task / KPI / effort report across the team ----
    with tab_analysis:
        import admin_report
        import pandas as _pd
        st.caption("Daily task completion, goal alignment, and effort — per user, for one day.")
        rep_date = st.date_input("Date", value=TODAY, key="adm_rep_date", format="DD/MM/YYYY")
        rep_str = rep_date.strftime("%Y-%m-%d")
        is_today = rep_str == TODAY_STR

        users = storage.get_users()
        people = ([u for _, u in users.iterrows()
                   if str(u.get("role", "")).upper() != "ADMIN"]
                  if not users.empty else [])
        if not people:
            st.info("No users yet.")
        else:
            analyses = [admin_report.user_analysis(u["user_key"], dict(u), rep_str)
                        for u in people]

            # ---- team roll-up: is the team's effort pointed at goals? ----
            n = len(analyses)
            tot_tasks = sum(a["tasks_total"] for a in analyses)
            tot_done = sum(a["done"] for a in analyses)
            tot_aligned = sum(a["aligned"] for a in analyses)
            tot_counted = sum(a["aligned"] + a["unaligned"] for a in analyses)
            team_completion = round(100 * tot_done / tot_tasks) if tot_tasks else 0
            team_aligned = round(100 * tot_aligned / tot_counted) if tot_counted else 0
            n_closed = sum(1 for a in analyses if a["closed"])
            m = st.columns(4)
            m[0].metric("Team completion", f"{team_completion}%")
            m[1].metric("Tasks goal-aligned", f"{team_aligned}%")
            m[2].metric("Closed the day", f"{n_closed}/{n}")
            if is_today:
                n_login = sum(1 for u in people if storage.logged_in_today(u["user_key"]))
                m[3].metric("Logged in today", f"{n_login}/{n}")

            # ---- summary table (one row per user) ----
            summ = _pd.DataFrame([{
                "User": a["name"], "Tasks": a["tasks_total"], "Done": a["done"],
                "Open": a["open"], "Compl %": a["completion"],
                "Today": a["today_h"], "Build": a["build_h"],
                "Aligned": a["aligned"], "Unaligned": a["unaligned"],
                "Targets": f"{a['targets_done']}/{a['targets_set']}",
                "Top KRA": a["top_kra"] or "—",
                "Closed": "✓" if a["closed"] else "—",
            } for a in analyses])
            st.dataframe(summ, use_container_width=True, hide_index=True)

            try:
                xlsx = admin_report.build_xlsx(rep_str, analyses)
                st.download_button(
                    "⬇️ Download Excel report", data=xlsx,
                    file_name=f"TeamAnalysis_{rep_str}.xlsx",
                    mime=("application/vnd.openxmlformats-officedocument."
                          "spreadsheetml.sheet"),
                    type="primary", key="adm_rep_dl")
            except Exception as e:
                st.warning(f"Couldn't build the Excel: {e}")

            # ---- team progress synced back from machines (close-day pushes) ----
            with st.expander("📈 Team progress (synced from machines)"):
                st.caption("Planned vs achieved per KPI, pushed up when each person closes "
                           "their day. Populated only when Neon is configured.")
                tp = storage.get_team_progress(month=MONTH)
                if tp.empty:
                    st.info("No synced progress yet for this month.")
                else:
                    view = tp[["user_key", "date", "kpi_name", "planned", "achieved"]].copy()
                    view["planned_n"] = _pd.to_numeric(view["planned"], errors="coerce").fillna(0)
                    view["achieved_n"] = _pd.to_numeric(view["achieved"], errors="coerce").fillna(0)
                    view["gap"] = view["achieved_n"] - view["planned_n"]
                    roll = (view.groupby(["user_key", "kpi_name"], as_index=False)
                                 .agg(Planned=("planned_n", "sum"),
                                      Achieved=("achieved_n", "sum"),
                                      Gap=("gap", "sum")))
                    roll.columns = ["User", "KPI", "Planned", "Achieved", "Gap"]
                    st.dataframe(roll, use_container_width=True, hide_index=True)
                    st.caption("Latest sync per person: "
                               + ", ".join(sorted(
                                   f"{u} ({view[view['user_key']==u]['date'].max()})"
                                   for u in view["user_key"].unique())))

            # ---- per-user drill-down: targets, tasks, effort matrix ----
            st.markdown("##### Per-user detail")
            for a in analyses:
                with st.expander(f"{a['name']} · {a['done']}/{a['tasks_total']} done · "
                                 f"{a['aligned']} aligned / {a['unaligned']} unaligned"):
                    if a["goals"]:
                        st.caption("Targets vs achievement")
                        st.dataframe(_pd.DataFrame([{
                            "Target": g["heading"], "Aim": g.get("target_number", "") or "—",
                            "Achieved": g.get("achieved", "") or "—"} for g in a["goals"]]),
                            use_container_width=True, hide_index=True)
                    tdf = a["tasks"]
                    if tdf is not None and len(tdf):
                        st.caption("Tasks")
                        st.dataframe(_pd.DataFrame([{
                            "Task": t.get("title", ""), "Goal": t.get("day_goal", "") or "—",
                            "Horizon": t.get("horizon", "") or "Today",
                            "Status": t.get("status", "")} for _, t in tdf.iterrows()]),
                            use_container_width=True, hide_index=True)
                    else:
                        st.caption("No tasks this day.")
                    rows, cols, counts, row_tot, col_tot, grand = a["matrix"]
                    if grand:
                        st.caption("Effort matrix — task counts by KRA × type")
                        mat = _pd.DataFrame(
                            {c: {r: counts[r][c] for r in rows} for c in cols})
                        mat["TOTAL"] = [row_tot[r] for r in rows]
                        st.dataframe(mat, use_container_width=True)
                    else:
                        st.caption("No effort data this day.")


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

    # Personalisation brief (the always-on half of the two-layer design):
    #   * few learnings  -> inject them raw (they all fit; nothing to lose)
    #   * many learnings -> inject a DISTILLED brief that preserves the essence of ALL of
    #     them (so old preferences are never dropped by a recency cap). The brief is a
    #     derived view, regenerated from the FULL raw set when it has grown enough — never
    #     from a previous brief, so there's no compounding summary-of-summary loss.
    # Task-specific calls additionally get only the RELEVANT raw learnings via retrieval.py.
    try:
        ai.set_learnings_brief(_personalisation_brief(user))
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

    # GATE: if the previous working day had ACTIVITY but wasn't closed, force-close it first —
    # block every other screen until done. (Only Sunday is non-working. A day with no tasks or
    # log had nothing to close, so it never blocks — new users aren't locked out.)
    uk = user["user_key"]
    if datetime.now().weekday() != 6:
        prev = _prev_working_day(date.today()).isoformat()
        if (not storage.is_day_closed(uk, prev)) and _had_activity(uk, prev):
            _force_close_previous_day(user, prev)
            return

    # Row 2: full-width nav (single clean row)
    partner_ok = _partner_features_allowed(user)
    is_admin = _is_admin(user)
    choice = header_nav(is_lead, partner_ok, is_admin)
    st.divider()

    # Desktop: show a banner when a newer build is on GitHub (auto-hides once updated).
    if not storage._on_cloud_host():
        try:
            import updater
            updater.render_update_banner()
        except Exception:
            pass

    # Buzzer reminders — checked on EVERY page (not just Today) and across all dates, so a
    # scheduled reminder is never missed because the user was on another tab. Runs after the
    # force-close gate so it doesn't fight that flow.
    if not is_admin:
        _buzzer(uk, user)

    # Defensive: route gated views back to a safe default.
    if choice in ("Daily log", "Communicate") and not partner_ok:
        choice = "Today"
    if choice == "Admin" and not is_admin:
        choice = "Today"

    _routes = {"Today": today_view, "Daily log": daily_log_view, "Records": records_view,
     "Communicate": communicate_view, "Updates": updates_view, "Monthly": monthly_view,
     "Effort": effort_view, "Projects": project_planner.project_view,
     "Learning": learning_view, "History": history_view,
     "Settings": settings_view, "Admin": admin_view}
    if reports_engine is not None:
        _routes["Reports"] = reports_engine.reports_view
    if dump_sender is not None:
        _routes["Sarthi"] = dump_sender.sarthi_view
    if mindmap is not None:
        _routes["Mind Map"] = mindmap.mindmap_view
    _routes.get(choice, today_view)(user)


if __name__ == "__main__":
    main()
