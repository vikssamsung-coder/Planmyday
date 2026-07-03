"""build_team_guide.py — generates the Plan My Day team manual as a designed PDF:
tutorial + ready reckoner + FAQ + troubleshooting. Run: python3 build_team_guide.py
Writes PlanMyDay_Team_Guide.pdf next to this script.
"""

import io
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                HRFlowable, ListFlowable, ListItem, KeepTogether, PageBreak)

INK   = colors.HexColor("#1B2733")
SOFT  = colors.HexColor("#5C6B7A")
SLATE = colors.HexColor("#2D4A5E")
SLATE_TXT = colors.HexColor("#CFE0EA")
AMBER = colors.HexColor("#E8833A")
GOOD  = colors.HexColor("#227C4E")
GOODBG = colors.HexColor("#E6F4EC")
WARN  = colors.HexColor("#B9770E")
WARNBG = colors.HexColor("#FBEFD6")
BAD   = colors.HexColor("#C0392B")
BADBG = colors.HexColor("#FCE4E2")
LINE  = colors.HexColor("#E7E3DC")
CARD  = colors.HexColor("#FBFAF8")
HEADBG = colors.HexColor("#F1ECE4")

S = {}
def _mk():
    S["h1"]   = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=15, textColor=colors.white, leading=18)
    S["h2"]   = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13, textColor=SLATE, leading=16, spaceBefore=10, spaceAfter=3)
    S["h3"]   = ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=11, textColor=INK, leading=14, spaceBefore=6, spaceAfter=2)
    S["body"] = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, textColor=INK, leading=14, spaceAfter=4)
    S["li"]   = ParagraphStyle("li", fontName="Helvetica", fontSize=9.5, textColor=INK, leading=13.5)
    S["cap"]  = ParagraphStyle("cap", fontName="Helvetica", fontSize=8.5, textColor=SOFT, leading=11)
    S["cell"] = ParagraphStyle("cell", fontName="Helvetica", fontSize=9, textColor=INK, leading=12)
    S["cellb"]= ParagraphStyle("cellb", fontName="Helvetica-Bold", fontSize=9, textColor=INK, leading=12)
    S["cellh"]= ParagraphStyle("cellh", fontName="Helvetica-Bold", fontSize=9, textColor=colors.white, leading=12)
    S["q"]    = ParagraphStyle("q", fontName="Helvetica-Bold", fontSize=10, textColor=SLATE, leading=13, spaceBefore=7)
    S["a"]    = ParagraphStyle("a", fontName="Helvetica", fontSize=9.5, textColor=INK, leading=14, spaceAfter=2)
    S["title"]= ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=26, textColor=colors.white, leading=30)
    S["subt"] = ParagraphStyle("subt", fontName="Helvetica", fontSize=12, textColor=SLATE_TXT, leading=17)
_mk()

USABLE = A4[0] - 2 * 16 * mm


def h1(text):
    band = Table([[Paragraph(text, S["h1"])]], colWidths=[USABLE])
    band.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SLATE),
                              ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                              ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                              ("ROUNDEDCORNERS", [6, 6, 6, 6])]))
    return [Spacer(1, 8), band, Spacer(1, 6)]


def p(text): return Paragraph(text, S["body"])
def h2(text): return [Paragraph(text, S["h2"])]
def h3(text): return Paragraph(text, S["h3"])
def cap(text): return Paragraph(text, S["cap"])


def bullets(items):
    return [ListFlowable([ListItem(Paragraph(t, S["li"]), leftIndent=10, value="•") for t in items],
                         bulletType="bullet", start="•", leftIndent=8, spaceAfter=4)]


def callout(kind, text):
    bg, fg, tag = {"tip": (GOODBG, GOOD, "TIP"), "warn": (WARNBG, WARN, "HEADS-UP"),
                   "bad": (BADBG, BAD, "IMPORTANT"), "gate": (colors.HexColor("#EAF0F4"), SLATE, "THE GATE")}[kind]
    para = Paragraph(f"<b><font color='{fg.hexval()[2:] and '#'+fg.hexval()[4:]}'>{tag} · </font></b>{text}", S["body"])
    para = Paragraph(f"<b>{tag} · </b>{text}", ParagraphStyle("co", parent=S["body"], textColor=fg))
    t = Table([[para]], colWidths=[USABLE])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("BOX", (0, 0), (-1, -1), 0.5, fg),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                           ("ROUNDEDCORNERS", [6, 6, 6, 6])]))
    return [Spacer(1, 2), t, Spacer(1, 4)]


def table(headers, rows, widths, head_bg=SLATE):
    data = [[Paragraph(h, S["cellh"]) for h in headers]]
    for r in rows:
        data.append([Paragraph(str(c), S["cell"]) for c in r])
    t = Table(data, colWidths=widths, repeatRows=1)
    st = [("BACKGROUND", (0, 0), (-1, 0), head_bg),
          ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
          ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
          ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
          ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CARD])]
    t.setStyle(TableStyle(st))
    return t


def swatch_table(rows):
    """rows: (label, bg_hex, fg_hex, meaning)"""
    data = []
    for label, bg, fg, meaning in rows:
        chip = Table([[Paragraph(f"<b>{label}</b>", ParagraphStyle('c', fontName='Helvetica-Bold',
                     fontSize=9, textColor=colors.HexColor(fg), alignment=1))]], colWidths=[26 * mm])
        chip.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)),
                                  ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                                  ("ROUNDEDCORNERS", [10, 10, 10, 10])]))
        data.append([chip, Paragraph(meaning, S["cell"])])
    t = Table(data, colWidths=[30 * mm, USABLE - 30 * mm])
    t.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE)]))
    return t


def faq(q, a):
    return KeepTogether([Paragraph(q, S["q"]), Paragraph(a, S["a"])])


def build():
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm,
                            title="Plan My Day — Team Guide", author="Plan My Day")
    st = []

    # ---------- cover ----------
    cover = Table([[Paragraph("Plan My Day", S["title"])],
                   [Paragraph("Team Guide &amp; Ready Reckoner", S["subt"])],
                   [Spacer(1, 6)],
                   [Paragraph("Tutorial · Cheat sheets · FAQ · Troubleshooting", S["subt"])]],
                  colWidths=[USABLE])
    cover.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SLATE),
                               ("LEFTPADDING", (0, 0), (-1, -1), 22), ("RIGHTPADDING", (0, 0), (-1, -1), 22),
                               ("TOPPADDING", (0, 0), (0, 0), 26), ("BOTTOMPADDING", (0, -1), (0, -1), 26),
                               ("ROUNDEDCORNERS", [10, 10, 10, 10])]))
    st += [Spacer(1, 6), cover, Spacer(1, 10)]
    st += callout("gate", "Busy is not progress. Every task on your day must move you toward a goal — "
                          "by <b>delivering an outcome today</b> or <b>building what pays off tomorrow</b>. "
                          "If a task serves neither, it doesn't belong on today's plan.")
    st += [Spacer(1, 4), cap(f"Generated {datetime.now().strftime('%d %b %Y')} · for the Bigul growth team")]

    # ---------- 1. the idea ----------
    st += h1("1 · The one idea")
    st += [p("Plan My Day is not a to-do list. It is a discipline: the day's actions are chosen on "
             "purpose to move you toward your goals. The whole app is built around a single test.")]
    st += callout("gate", "<b>Does doing this move me toward a goal — either by achieving something today, "
                          "or by preparing what builds tomorrow?</b>  Yes → it stays (tagged <b>Today</b> or "
                          "<b>Build</b>). No → park it, defer it, delegate it, or drop it — and say why.")
    st += [p("Two kinds of good work: <b>Today</b> tasks produce a result now; <b>Build</b> tasks lay "
             "groundwork that compounds (prep, setup, learning, relationships, systems). A healthy day has "
             "both — never all delivery and no building, never all building and no delivery.")]

    # ---------- 2. the daily loop ----------
    st += h1("2 · The daily loop")
    st += [p("Every day runs the same five steps. The app locks the steps in order so the plan stays "
             "goal-driven.")]
    st += [table(["Step", "Where", "What you do"],
                 [["1. Set targets", "Today · top", "Name today's 1–4 goals. Nothing plans until a target exists."],
                  ["2. Plan tasks", "Today", "Dictate or type; the app drafts goal-tagged tasks. Add manual ones."],
                  ["3. Work tasks", "Today · task cards", "Log progress, tick steps, mark done. Reminders keep time."],
                  ["4. Close the day", "Today · Close your Day", "Grade the day; the Progress Brief is generated."],
                  ["5. Review", "Monthly · Effort · Projects", "See targets vs actuals, where effort went, project health."]],
                 [30 * mm, 34 * mm, USABLE - 64 * mm])]

    st += h2("2.1  Set today's targets — the Gate")
    st += [p("At the top of <b>Today</b> you set up to four target boxes (a short heading + a number). "
             "This is the Gate: until at least one target exists, dictation and task-adding stay locked. "
             "That is deliberate — you plan toward goals, you don't collect tasks at random.")]
    st += h2("2.2  Plan your tasks")
    st += bullets([
        "<b>Dictate or type</b> what you want to do, then <b>Generate tasks</b> — the app drafts tasks and "
        "tags each with the goal it serves and a <b>Today</b> or <b>Build</b> badge.",
        "<b>Add a task manually</b> from the box below Generate.",
        "A task that links to no goal shows a <b>⚠ no goal</b> flag — that's your cue to link it, defer it, or drop it.",
    ])
    st += h2("2.3  Work a task — the task card")
    st += [p("Each open task is a card. Collapsed, it shows the title, its goal + horizon chips, and a one-line "
             "coaching hint. Open it for the daily actions:")]
    st += bullets([
        "<b>Log an update</b> — type a quick remark and press Log. This records progress <i>and</i> silences the "
        "task's reminder (see the buzzer).",
        "<b>Steps</b> — tick sub-steps freely; nothing saves on each tick. Press <b>Save progress</b> when done. "
        "Ticking every step and saving completes the task.",
        "<b>Done / Break into steps</b> — mark a simple task done, or let the app split it into steps.",
        "<b>⚙ Options</b> — change the goal it serves, get a coaching tip, move it to another day, add "
        "collaborators / share on WhatsApp, rename, or delete.",
    ])
    st += h2("2.4  The time rail — plan your day by the clock")
    st += bullets([
        "Open tasks are numbered down a rail on the left, in time order.",
        "Tap the <b>⏰</b> on a task's rail, pick an hour and minute, and press <b>Update</b>. The task takes that "
        "time and the whole list re-sorts into your agenda.",
        "Changing one task's time refreshes only that card — the list re-orders when you press Update.",
    ])
    st += h2("2.5  Reminders — the buzzer")
    st += [p("Give a task a time and it becomes a reminder. At that time the app flashes a banner and plays the "
             "buzzer clip. If you don't act, it nags again every 5 minutes.")]
    st += callout("tip", "There is <b>no snooze</b>. The <i>only</i> way to stop a buzzer is to <b>log an update</b> "
                          "on that task — because acting on it, not dismissing it, is the point.")
    st += h2("2.6  Close your day &amp; the Progress Brief")
    st += [p("<b>Close your Day</b> captures what got done, what's pending, and what worked, then generates a "
             "<b>Progress Brief</b> (a Word document) you can share. Closing a day also stops its reminders. "
             "If you didn't close yesterday, the app asks you to close it first.")]

    st.append(PageBreak())

    # ---------- 3. the other screens ----------
    st += h1("3 · The other screens")
    st += [table(["Screen", "What it's for"],
                 [["Monthly", "Each KPI's target vs achieved-to-date and the required pace. Achievements come from the MIS sync."],
                  ["Effort", "\u201cWhere My Energy Goes\u201d — a heat grid of your tasks by KRA and effort type. Download it as a designed PDF."],
                  ["Projects", "The Project Planner: campaigns broken into sub-tasks with owners, dates, health and a calendar. See §4."],
                  ["Updates", "Announcements, banners, contests and videos published by the admin."],
                  ["Records / Communicate / Daily log", "Partner directory, WhatsApp schedules and meeting logs (shown only to partner-acquisition owners)."],
                  ["Settings", "Your AI usage and spend, role brief and backup controls."]],
                 [34 * mm, USABLE - 34 * mm])]

    # ---------- 4. project planner ----------
    st += h1("4 · The Project Planner")
    st += [p("Run your campaigns and initiatives as <b>main tasks broken into sub-tasks</b> — each with an owner, "
             "priority, dates and progress. It <b>saves automatically</b>, so it's there when you return. Four tabs:")]
    st += bullets([
        "<b>How to Use</b> — a built-in primer on the columns and the health colours.",
        "<b>Dashboard</b> — totals, overdue count, and a by-owner breakdown.",
        "<b>Campaigns</b> — the colour-coded board plus an <b>✏ Edit plan</b> grid (add rows, tick to delete, then "
        "<b>💾 Save plan</b>). Empty on first open — press <b>Load the example plan</b> to see it in action.",
        "<b>Calendar</b> — an agenda by urgency: overdue, due this week, later, done.",
    ])
    st += [h3("Health is automatic — and overdue turns red")]
    st += [swatch_table([
        ("Overdue", "#FCE4E2", "#C0392B", "End date has passed and it isn't Done. The row, End date and Days-Left all turn red."),
        ("Due Soon", "#FBEFD6", "#B9770E", "Due within 3 days."),
        ("On Track", "#E6F4EC", "#227C4E", "Has room before the End date."),
        ("Done", "#EAECEF", "#7A8794", "Finished (100% or status Done). Shown muted."),
    ])]
    st += callout("tip", "You never set health by hand — it's computed from the End date and status every time the "
                          "board loads, so it's always current.")

    st.append(PageBreak())

    # ---------- 5. ready reckoner ----------
    st += h1("5 · Ready reckoner (cheat sheets)")
    st += [h2("Horizons — how a task earns its place")]
    st += [swatch_table([
        ("Today", "#FBEFD6", "#B9770E", "Delivers a result now — the outcome happens today."),
        ("Build", "#EAF3DE", "#3B6D11", "Prepares tomorrow — prep, setup, learning, relationships, systems."),
        ("⚠ no goal", "#F3F1EC", "#8A8478", "Links to no goal. Link it, defer it, or drop it — it fails the Gate."),
    ])]
    st += [h2("Priorities")]
    st += [table(["Code", "Meaning"],
                 [["P0", "Drop everything — urgent and blocking."],
                  ["P1", "Important this cycle; do soon."],
                  ["P2", "Normal priority."],
                  ["P3", "Nice to have / whenever there's room."]],
                 [22 * mm, USABLE - 22 * mm])]
    st += [h2("Statuses")]
    st += [table(["Status", "Meaning"],
                 [["Not Started", "Planned, not begun."],
                  ["In Progress", "Actively being worked."],
                  ["Blocked", "Waiting on something (note the dependency)."],
                  ["Done", "Complete."]],
                 [30 * mm, USABLE - 30 * mm])]
    st += [h2("Buzzer rules")]
    st += bullets([
        "A task buzzes at its set time, then re-nags every <b>5 minutes</b>.",
        "It buzzes across all your tabs — being on another screen won't make you miss it.",
        "<b>Logging an update</b> on the task is the only thing that stops it. No snooze, no dismiss.",
        "A closed day never buzzes; a task moved to a future day won't buzz until that day.",
    ])
    st += [h2("Where things are saved")]
    st += bullets([
        "Your day plan, tasks, targets, effort and project plan are saved to the cloud database automatically.",
        "The Project Planner saves when you press <b>Save plan</b>; the day plan saves as you work.",
        "Nothing important lives only on your screen — close the tab and it's still there next time.",
    ])

    st.append(PageBreak())

    # ---------- 6. FAQ ----------
    st += h1("6 · FAQ")
    st += [faq("Why can't I add a task or dictate?",
               "You haven't set a target yet. That's the Gate — set at least one target box at the top of Today, "
               "and planning unlocks.")]
    st += [faq("What's the difference between a Today task and a Build task?",
               "Today delivers a result now; Build prepares something that pays off later. Both are valid — a good "
               "day has some of each. If you can't say which one a task is, question whether it belongs.")]
    st += [faq("How do I stop a task from buzzing?",
               "Log an update on it. Acting on the task is the only exit — there is no snooze by design.")]
    st += [faq("My task disappeared — where did it go?",
               "It was either marked Done, deleted, or moved to a future date (Options → move to another day). "
               "Future-dated tasks reappear on their day; a \u201cscheduled ahead\u201d list shows them meanwhile.")]
    st += [faq("I changed a task's time but the list didn't reorder.",
               "Open the ⏰ on the rail, set the time, and press <b>Update</b> — that's what applies the time and "
               "re-sorts the day. Just picking hour/minute doesn't apply until you press Update.")]
    st += [faq("Does my Project Planner save?",
               "Yes — but only when you press <b>Save plan</b> after editing. The colour board above the editor "
               "always shows the last saved state.")]
    st += [faq("Why is a project row red?",
               "It's overdue: the End date has passed and it isn't Done. Update the date, mark progress, or close it out.")]
    st += [faq("How do I download my effort report?",
               "Go to Effort and press <b>⬇ Download effort report (PDF)</b>. It uses the date range you've selected.")]
    st += [faq("Can my teammate and I share one project plan?",
               "Today each person's plan is their own. Owners on sub-tasks are just labels — assign anyone. A shared "
               "team plan can be added if the team wants it.")]
    st += [faq("Who can see the Updates / banners?",
               "Everyone sees Updates the admin publishes. Some tabs (Daily log, Communicate, Admin) are role-gated "
               "and only appear for the people who need them.")]
    st += [faq("The app feels slow for a moment after I click.",
               "It's reading from the cloud database. It's normal on the first load of a screen; subsequent actions "
               "are quick.")]

    st.append(PageBreak())

    # ---------- 7. troubleshooting ----------
    st += h1("7 · Troubleshooting")
    st += [table(["Symptom", "Likely cause", "What to do"],
                 [["Buzzer only beeps — no video shows",
                   "The buzzer video isn't on the deployed app.",
                   "Tell the admin to make sure buzzer.mp4 is committed and deployed. Until then you'll hear the fallback tone."],
                  ["Buzzer shows but plays no sound",
                   "Browsers block sound until you interact with the page.",
                   "Click anywhere in the app once; sound then plays. The flashing banner is always the reliable cue."],
                  ["\u201cSet a target first\u201d — can't plan",
                   "No target set (the Gate).",
                   "Add a target box at the top of Today."],
                  ["A task won't stop nagging",
                   "You haven't acted on it.",
                   "Log an update on that task. That is the only way to stop it."],
                  ["My project edits vanished",
                   "You didn't save.",
                   "In Projects → Campaigns, press 💾 Save plan after editing the grid."],
                  ["Changed a time, list didn't reorder",
                   "Time wasn't applied.",
                   "Press Update inside the ⏰ popover — that applies the time and re-sorts."],
                  ["Locked out until I close yesterday",
                   "Previous working day left open.",
                   "Close the previous day (the app takes you there); then today opens."],
                  ["Effort/plan says \u201cPDF unavailable\u201d",
                   "The PDF library isn't deployed.",
                   "Admin: ensure reportlab is in requirements and redeploy."],
                  ["Monthly numbers look stale",
                   "MIS not synced yet.",
                   "Achievements update when the admin runs the MIS push. Ask the admin."],
                  ["I can't see Admin / partner tabs",
                   "Those tabs are role-gated.",
                   "That's expected — they only show for the roles that use them."]],
                 [42 * mm, 45 * mm, USABLE - 87 * mm])]

    st += [Spacer(1, 8)]
    st += callout("gate", "When in doubt, come back to the Gate: name the goal each task serves and the horizon it "
                          "serves it on. If you can't, it doesn't belong on today — and saying so is the most useful "
                          "thing this tool does.")

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5); canvas.setFillColor(SOFT)
        canvas.drawString(16 * mm, 9 * mm, "Plan My Day · Team Guide")
        canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, f"Page {d.page}")
        canvas.setStrokeColor(LINE); canvas.line(16 * mm, 11.5 * mm, A4[0] - 16 * mm, 11.5 * mm)
        canvas.restoreState()

    flat = []
    for _x in st:
        (flat.extend if isinstance(_x, list) else flat.append)(_x)
    doc.build(flat, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


if __name__ == "__main__":
    pdf = build()
    with open("PlanMyDay_Team_Guide.pdf", "wb") as f:
        f.write(pdf)
    print("wrote PlanMyDay_Team_Guide.pdf", len(pdf), "bytes")
