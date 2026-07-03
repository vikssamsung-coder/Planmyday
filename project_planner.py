"""project_planner.py — a saved, colour-coded Project Planner for Plan My Day.

A main-task / sub-task tracker (Owner, Priority, Status, dates, % complete, Notes) with
computed Days-Left and Health. Overdue work turns red so nothing quietly slips. Four tabs:
How to Use · Dashboard · Campaigns (the editable plan) · Calendar (agenda by urgency).

Persistence goes through storage.get_project_tasks / save_project_tasks (per-user), so it
survives Streamlit Cloud restarts exactly like the rest of the app. Days-Left and Health are
computed at render time from end_date/status/percent — never stored — so they're always live.

Design principle (shared with the rest of the app): every project should ladder up to a goal.
A campaign that can't name the goal it serves is the thing to question, not schedule.
"""

from datetime import datetime, date

import pandas as pd
import streamlit as st

import storage
import schemas

# ---- colours (Sunrise) --------------------------------------------------------------
HEALTH_COLORS = {
    "Overdue":  ("#FCE4E2", "#C0392B"),   # (bg, text)
    "Due Soon": ("#FBEFD6", "#B9770E"),
    "On Track": ("#E6F4EC", "#227C4E"),
    "Done":     ("#EAECEF", "#7A8794"),
    "No date":  ("#F2F0EB", "#8A8478"),
}
PRIORITY_COLORS = {"P0": "#D9544D", "P1": "#E8833A", "P2": "#2D4A5E", "P3": "#8A94A0"}
STATUS_ORDER = ["Not Started", "In Progress", "Blocked", "Done"]


def _parse_date(s):
    if isinstance(s, (datetime, date)):
        return s.date() if isinstance(s, datetime) else s
    s = str(s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%b-%y", "%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _pctf(v):
    try:
        return max(0, min(100, int(float(v or 0))))
    except (TypeError, ValueError):
        return 0


def _days_left(end, today):
    return (end - today).days if end else None


def _health(status, end, percent, today):
    if str(status or "").strip().lower() == "done" or _pctf(percent) >= 100:
        return "Done"
    if end is None:
        return "No date"
    if end < today:
        return "Overdue"
    return "Due Soon" if (end - today).days <= 3 else "On Track"


def _enrich(df, today):
    """Add parsed dates, days_left and health columns for rendering."""
    if df is None or df.empty:
        return pd.DataFrame(columns=list(schemas.PROJECT_TASKS) + ["_end", "_start", "_dl", "_health"])
    d = df.copy()
    d["_start"] = d["start_date"].map(_parse_date)
    d["_end"] = d["end_date"].map(_parse_date)
    d["_dl"] = d["_end"].map(lambda e: _days_left(e, today))
    d["_health"] = [_health(r["status"], r["_end"], r["percent"], today) for _, r in d.iterrows()]
    return d


def _badge(text, bg, fg):
    return (f"<span style='background:{bg};color:{fg};border-radius:999px;padding:2px 9px;"
            f"font-size:11.5px;font-weight:600;white-space:nowrap;'>{text}</span>")


# ======================================================================= TAB: How to use
def _how_to_use():
    st.markdown("""
Use this planner to run your **campaigns and initiatives** as main tasks broken into
**sub-tasks** — each with an owner, priority, dates and progress. The plan **saves
automatically** to your workspace, so it's here when you come back.

**The columns**
- **Main Task** — the campaign/initiative (e.g. *Q3 Brand Refresh*).
- **Sub-Task** — one concrete piece of it, owned by one person.
- **Owner · Priority · Status** — who, how urgent (P0 highest), and where it stands.
- **Start / End** — the window. **Days Left** and **Health** are computed for you.
- **% Complete · Notes / Dependencies** — progress and what it's waiting on.

**Health colours** (set automatically from the End date + status)
""")
    cols = st.columns(4)
    for c, (label, (bg, fg)) in zip(cols, list(HEALTH_COLORS.items())[:4]):
        c.markdown(
            f"<div style='background:{bg};color:{fg};border-radius:10px;padding:8px 10px;"
            f"text-align:center;font-weight:700;font-size:13px;'>{label}</div>",
            unsafe_allow_html=True)
    st.markdown("""
- **🔴 Overdue** — the End date has passed and it isn't Done. It turns **red** everywhere.
- **🟠 Due Soon** — due within 3 days.  **🟢 On Track** — has room.  **⚪ Done** — finished.

**How to edit** — open **Campaigns → ✏️ Edit plan**, change cells directly (add a row at the
bottom, delete with the checkbox), then **💾 Save plan**.

**One rule worth keeping:** every campaign should serve a goal. If a project can't name the
goal it moves, question whether it belongs on the plan at all — busy is not the same as progress.
""")


# ======================================================================= TAB: Dashboard
def _metric_card(col, label, value, tone="ink"):
    palette = {"ink": ("#FFFFFF", "#1B2733", "#5C6B7A"),
               "bad": ("#FCE4E2", "#C0392B", "#C0392B"),
               "warn": ("#FBEFD6", "#B9770E", "#B9770E"),
               "good": ("#E6F4EC", "#227C4E", "#227C4E")}
    bg, num, lab = palette.get(tone, palette["ink"])
    col.markdown(
        f"<div style='background:{bg};border:0.5px solid #E7E3DC;border-radius:12px;"
        f"padding:12px 14px;'>"
        f"<div style='font-size:12px;color:{lab};'>{label}</div>"
        f"<div style='font-size:26px;font-weight:800;color:{num};line-height:1.1;'>{value}</div>"
        f"</div>", unsafe_allow_html=True)


def _dashboard(uk, df, today):
    d = _enrich(df, today)
    if d.empty:
        st.info("No campaigns yet. Add your plan in the **Campaigns** tab.")
        return
    total = len(d)
    projects = d["project"].replace("", pd.NA).dropna().nunique()
    overdue = int((d["_health"] == "Overdue").sum())
    due_soon = int((d["_health"] == "Due Soon").sum())
    done = int((d["_health"] == "Done").sum())
    avg_pct = round(d["percent"].map(_pctf).mean()) if total else 0

    r1 = st.columns(4)
    _metric_card(r1[0], "Sub-tasks", total)
    _metric_card(r1[1], "Campaigns", projects)
    _metric_card(r1[2], "Overdue", overdue, "bad" if overdue else "good")
    _metric_card(r1[3], "Due soon (≤3d)", due_soon, "warn" if due_soon else "ink")
    r2 = st.columns(4)
    _metric_card(r2[0], "Done", done, "good")
    _metric_card(r2[1], "In progress", int((d["status"] == "In Progress").sum()))
    _metric_card(r2[2], "Not started", int((d["status"] == "Not Started").sum()))
    _metric_card(r2[3], "Avg complete", f"{avg_pct}%")

    if overdue:
        st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)
        st.markdown(f"<div style='background:#FCE4E2;color:#C0392B;border-radius:10px;"
                    f"padding:9px 12px;font-weight:600;font-size:13px;'>🔴 {overdue} sub-task(s) "
                    f"overdue — clear these before adding anything new.</div>",
                    unsafe_allow_html=True)
        od = d[d["_health"] == "Overdue"].sort_values("_end")
        for _, r in od.iterrows():
            dl = r["_dl"]
            st.markdown(
                f"<div style='padding:6px 2px;font-size:13px;color:#1B2733;'>"
                f"<b>{r['project']}</b> · {r['subtask']} — {r['owner'] or '—'} · "
                f"<span style='color:#C0392B;font-weight:600;'>{abs(dl)}d overdue</span></div>",
                unsafe_allow_html=True)

    # ---- by owner ----
    st.markdown("##### By owner")
    grp = d.groupby(d["owner"].replace("", "—"))
    rows = []
    for owner, g in grp:
        rows.append({
            "Owner": owner,
            "Tasks": len(g),
            "Overdue": int((g["_health"] == "Overdue").sum()),
            "Due soon": int((g["_health"] == "Due Soon").sum()),
            "Done": int((g["_health"] == "Done").sum()),
            "Avg %": round(g["percent"].map(_pctf).mean()),
        })
    st.dataframe(pd.DataFrame(rows).sort_values("Overdue", ascending=False),
                 use_container_width=True, hide_index=True)


# ======================================================================= TAB: Campaigns
def _render_board(df, today):
    d = _enrich(df, today)
    if d.empty:
        st.info("No campaigns yet — add your first one in **✏️ Edit plan** below.")
        return
    try:
        d = d.sort_values(["project", "sort_order"], key=lambda s: pd.to_numeric(s, errors="ignore"))
    except Exception:
        pass

    hd = ("padding:8px 10px;font-size:11.5px;font-weight:700;color:#5C6B7A;"
          "text-align:left;border-bottom:1px solid #E7E3DC;")
    html = ['<div style="overflow-x:auto;"><table style="border-collapse:collapse;width:100%;'
            'font-family:Inter,system-ui,sans-serif;font-size:13px;">']
    heads = ["Sub-Task", "Owner", "Priority", "Status", "Start", "End", "%", "Days Left", "Health", "Notes / Dependencies"]
    html.append("<tr>" + "".join(f'<td style="{hd}">{h}</td>' for h in heads) + "</tr>")

    for project in list(dict.fromkeys(d["project"].tolist())):
        g = d[d["project"] == project]
        avg = round(g["percent"].map(_pctf).mean()) if len(g) else 0
        od = int((g["_health"] == "Overdue").sum())
        roll = (f" · <span style='color:#C0392B;'>{od} overdue</span>" if od else "")
        html.append(
            f'<tr><td colspan="10" style="padding:12px 10px 6px;font-size:14px;font-weight:800;'
            f'color:#1B2733;">🗂️ {project or "—"} '
            f'<span style="font-weight:500;color:#5C6B7A;font-size:12px;">· {len(g)} sub-tasks · '
            f'{avg}% avg{roll}</span></td></tr>')
        for _, r in g.iterrows():
            hl = r["_health"]
            hbg, hfg = HEALTH_COLORS.get(hl, HEALTH_COLORS["No date"])
            overdue = hl == "Overdue"
            row_bg = "#FDECEA" if overdue else ("#FBFAF8" if hl == "Done" else "#FFFFFF")
            base = f"padding:7px 10px;border-bottom:1px solid #F1ECE4;vertical-align:middle;background:{row_bg};"
            dl = r["_dl"]
            dl_txt = "—" if dl is None else (f"{dl}d" if dl >= 0 else f"{abs(dl)}d late")
            dl_style = "color:#C0392B;font-weight:700;" if (dl is not None and dl < 0 and hl != "Done") else "color:#5C6B7A;"
            pc = _pctf(r["percent"])
            pcol = "#227C4E" if pc >= 100 else ("#E8833A" if pc >= 40 else "#8A94A0")
            pri = str(r["priority"] or "").strip().upper()
            pri_c = PRIORITY_COLORS.get(pri, "#8A94A0")
            def _dfmt(dt):
                return dt.strftime("%d-%b-%y") if dt else "—"
            title_style = "text-decoration:line-through;color:#9AA6B2;" if hl == "Done" else "color:#1B2733;font-weight:600;"
            html.append("<tr>")
            html.append(f'<td style="{base}{title_style}">{r["subtask"] or "—"}</td>')
            html.append(f'<td style="{base}">{r["owner"] or "—"}</td>')
            html.append(f'<td style="{base}">{_badge(pri or "—", "#F3EEE7", pri_c)}</td>')
            html.append(f'<td style="{base}color:#3B4A57;">{r["status"] or "—"}</td>')
            html.append(f'<td style="{base}color:#5C6B7A;white-space:nowrap;">{_dfmt(r["_start"])}</td>')
            end_style = "color:#C0392B;font-weight:700;" if overdue else "color:#3B4A57;"
            html.append(f'<td style="{base}{end_style}white-space:nowrap;">{_dfmt(r["_end"])}</td>')
            html.append(f'<td style="{base}"><span style="color:{pcol};font-weight:700;">{pc}%</span></td>')
            html.append(f'<td style="{base}{dl_style}white-space:nowrap;">{dl_txt}</td>')
            html.append(f'<td style="{base}">{_badge(hl, hbg, hfg)}</td>')
            html.append(f'<td style="{base}color:#5C6B7A;">{r["notes"] or ""}</td>')
            html.append("</tr>")
    html.append("</table></div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def _example_rows(uk):
    ex = [
        ("Q3 Brand Refresh", "Finalise logo & brand guidelines", "Vikrant", "P1", "In Progress", "2026-07-01", "2026-07-18", "40", "Sign-off pending"),
        ("Q3 Brand Refresh", "Update website visual assets", "Tressy", "P1", "Not Started", "2026-07-18", "2026-08-05", "0", "After guidelines lock"),
        ("Q3 Brand Refresh", "Roll out social templates", "Amish", "P2", "Not Started", "2026-08-05", "2026-08-15", "0", ""),
        ("SEO 90-Day Plan", "Fix P0 title tags & meta", "Tressy", "P0", "In Progress", "2026-06-15", "2026-07-10", "50", "Stock profile pages first"),
        ("SEO 90-Day Plan", "Build content clusters", "Tressy", "P1", "Not Started", "2026-07-10", "2026-09-15", "0", ""),
        ("Festive Campaign Prep", "Segment list from Sarthi", "Kapil", "P2", "Not Started", "2026-07-20", "2026-07-28", "0", "NT + active base"),
        ("Festive Campaign Prep", "WhatsApp creative & approval", "Nishi", "P2", "Not Started", "2026-07-28", "2026-08-10", "0", "SEBI check"),
    ]
    for i, e in enumerate(ex):
        storage.add_project_task(uk, {
            "project": e[0], "subtask": e[1], "owner": e[2], "priority": e[3], "status": e[4],
            "start_date": e[5], "end_date": e[6], "percent": e[7], "notes": e[8], "sort_order": str(i)})


def _campaigns(uk, df, today):
    _render_board(df, today)

    if df.empty:
        if st.button("➕ Load the example plan", key="proj_seed", type="primary"):
            _example_rows(uk)
            st.rerun()

    with st.expander("✏️ Edit plan (add rows at the bottom · tick to delete · then Save)",
                     expanded=df.empty):
        edit_cols = ["row_id", "project", "subtask", "owner", "priority", "status",
                     "start_date", "end_date", "percent", "notes"]
        if df.empty:
            ed = pd.DataFrame(columns=edit_cols)
        else:
            ed = df.reindex(columns=edit_cols).copy()
        ed["start_date"] = ed["start_date"].map(_parse_date)
        ed["end_date"] = ed["end_date"].map(_parse_date)
        ed["percent"] = ed["percent"].map(_pctf)

        edited = st.data_editor(
            ed, num_rows="dynamic", use_container_width=True, key="proj_editor",
            column_config={
                "row_id": None,
                "project": st.column_config.TextColumn("Main Task", width="medium"),
                "subtask": st.column_config.TextColumn("Sub-Task", width="large"),
                "owner": st.column_config.TextColumn("Owner"),
                "priority": st.column_config.SelectboxColumn("Priority", options=["P0", "P1", "P2", "P3"]),
                "status": st.column_config.SelectboxColumn("Status", options=STATUS_ORDER),
                "start_date": st.column_config.DateColumn("Start", format="DD-MMM-YY"),
                "end_date": st.column_config.DateColumn("End", format="DD-MMM-YY"),
                "percent": st.column_config.NumberColumn("% Complete", min_value=0, max_value=100,
                                                         step=5, format="%d%%"),
                "notes": st.column_config.TextColumn("Notes / Dependencies", width="large"),
            })

        if st.button("💾 Save plan", type="primary", key="proj_save"):
            import uuid as _uuid

            def _ds(v):
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    return ""
                try:
                    return v.strftime("%Y-%m-%d")
                except AttributeError:
                    return str(v)

            rows, seen = [], set()
            for i, r in edited.reset_index(drop=True).iterrows():
                proj = str(r.get("project") or "").strip()
                sub = str(r.get("subtask") or "").strip()
                if not proj and not sub:
                    continue
                rid = str(r.get("row_id") or "").strip()
                if not rid or rid.lower() == "nan" or rid in seen:
                    rid = "pt_" + _uuid.uuid4().hex[:10]
                seen.add(rid)
                rows.append({
                    "row_id": rid, "user_key": uk, "project": proj, "subtask": sub,
                    "owner": str(r.get("owner") or "").strip(),
                    "priority": str(r.get("priority") or "").strip(),
                    "status": str(r.get("status") or "").strip(),
                    "start_date": _ds(r.get("start_date")),
                    "end_date": _ds(r.get("end_date")),
                    "percent": str(_pctf(r.get("percent"))),
                    "notes": str(r.get("notes") or "").strip(),
                    "sort_order": str(i),
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
            ndf = (pd.DataFrame(rows, columns=schemas.PROJECT_TASKS) if rows
                   else pd.DataFrame(columns=schemas.PROJECT_TASKS))
            storage.save_project_tasks(uk, ndf)
            st.success(f"Saved {len(rows)} sub-task(s).")
            st.rerun()


# ======================================================================= TAB: Calendar
def _calendar(uk, df, today):
    d = _enrich(df, today)
    if d.empty:
        st.info("No dated tasks yet.")
        return
    d = d[d["_end"].notna()].copy()
    if d.empty:
        st.info("No tasks have an End date yet — add dates in the Campaigns tab.")
        return
    d = d.sort_values("_end")

    buckets = [
        ("🔴 Overdue", d[(d["_health"] == "Overdue")], "#C0392B"),
        ("🟠 Due this week", d[(d["_dl"].between(0, 7)) & (d["_health"] != "Overdue") & (d["_health"] != "Done")], "#B9770E"),
        ("🟢 Later", d[(d["_dl"] > 7) & (d["_health"] != "Done")], "#227C4E"),
        ("⚪ Done", d[d["_health"] == "Done"], "#7A8794"),
    ]
    for label, g, colr in buckets:
        if g.empty:
            continue
        st.markdown(f"<div style='margin-top:10px;font-weight:800;color:{colr};'>{label} "
                    f"<span style='color:#8A94A0;font-weight:500;'>· {len(g)}</span></div>",
                    unsafe_allow_html=True)
        for _, r in g.iterrows():
            dl = r["_dl"]
            when = r["_end"].strftime("%a %d-%b-%y")
            dl_txt = (f"{abs(dl)}d late" if dl < 0 else ("today" if dl == 0 else f"in {dl}d"))
            st.markdown(
                f"<div style='display:flex;justify-content:space-between;gap:10px;padding:6px 10px;"
                f"border-left:3px solid {colr};background:#FFFFFF;border:0.5px solid #E7E3DC;"
                f"border-radius:8px;margin:4px 0;font-size:13px;'>"
                f"<span><b>{r['project']}</b> · {r['subtask']} "
                f"<span style='color:#8A94A0;'>· {r['owner'] or '—'} · {_pctf(r['percent'])}%</span></span>"
                f"<span style='white-space:nowrap;color:{colr};font-weight:600;'>{when} · {dl_txt}</span>"
                f"</div>", unsafe_allow_html=True)


# ======================================================================= entry
def project_view(user):
    uk = user["user_key"]
    today = date.today()
    st.markdown("### 🗂️ Project Planner")
    st.caption("Campaigns → sub-tasks, with owners, dates and health. Overdue work turns red. "
               "Saves automatically.")
    df = storage.get_project_tasks(uk)
    t_help, t_dash, t_camp, t_cal = st.tabs(["📖 How to Use", "📊 Dashboard",
                                             "🗂️ Campaigns", "📅 Calendar"])
    with t_help:
        _how_to_use()
    with t_dash:
        _dashboard(uk, df, today)
    with t_camp:
        _campaigns(uk, df, today)
    with t_cal:
        _calendar(uk, df, today)
