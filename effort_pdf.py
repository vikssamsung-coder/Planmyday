"""effort_pdf.py — a polished, downloadable PDF of the "Where My Energy Goes" effort matrix.

Pure reportlab (platypus). Self-contained: no Streamlit / app imports, so it can be unit-tested
and reused. Call `build_effort_pdf(...)` with the exact tuple that `classify.build_matrix`
returns and it hands back PDF bytes.

Design language mirrors the app's "Sunrise" system: slate header, warm-amber heat cells,
cream canvas, Helvetica (the reportlab built-in that reads closest to Inter).
"""

import io
from datetime import datetime

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                HRFlowable)

# ---- Sunrise palette ----------------------------------------------------------------
INK       = colors.HexColor("#1B2733")
INK_SOFT  = colors.HexColor("#5C6B7A")
SLATE     = colors.HexColor("#2D4A5E")
SLATE_D   = colors.HexColor("#22394A")
SLATE_TXT = colors.HexColor("#CFE0EA")
AMBER     = colors.HexColor("#E8833A")
GOOD      = colors.HexColor("#2E9E6B")
BAD       = colors.HexColor("#D9544D")
CREAM     = colors.HexColor("#F6F4F0")
LINE      = colors.HexColor("#E7E3DC")
TOTAL_BG  = colors.HexColor("#F1ECE4")
WHITE     = colors.white


def _heat(count, maxv):
    """Warm-amber heat shade for a cell count (matches the in-app heatmap)."""
    if count <= 0 or maxv <= 0:
        return colors.HexColor("#FCFAF7"), colors.HexColor("#CBC3B8")
    inten = (count / maxv) ** 0.62
    stops = [(0.0, (251, 244, 236)), (0.5, (244, 198, 144)),
             (0.8, (232, 131, 58)), (1.0, (199, 95, 35))]
    rgb = stops[-1][1]
    for i in range(len(stops) - 1):
        a, b = stops[i], stops[i + 1]
        if inten <= b[0]:
            f = (inten - a[0]) / ((b[0] - a[0]) or 1)
            rgb = tuple(round(a[1][k] + f * (b[1][k] - a[1][k])) for k in range(3))
            break
    bg = colors.Color(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)
    txt = WHITE if inten > 0.55 else INK
    return bg, txt


def _styles():
    return {
        "h1":    ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=17,
                                textColor=WHITE, leading=20),
        "sub":   ParagraphStyle("sub", fontName="Helvetica", fontSize=9.5,
                                textColor=SLATE_TXT, leading=13),
        "kpi_l": ParagraphStyle("kpi_l", fontName="Helvetica", fontSize=8,
                                textColor=INK_SOFT, leading=10, alignment=TA_LEFT),
        "kpi_n": ParagraphStyle("kpi_n", fontName="Helvetica-Bold", fontSize=17,
                                textColor=SLATE, leading=19, alignment=TA_LEFT),
        "kpi_s": ParagraphStyle("kpi_s", fontName="Helvetica", fontSize=7.5,
                                textColor=INK_SOFT, leading=9, alignment=TA_LEFT),
        "colh":  ParagraphStyle("colh", fontName="Helvetica-Bold", fontSize=8.5,
                                textColor=WHITE, leading=10, alignment=TA_CENTER),
        "rowh":  ParagraphStyle("rowh", fontName="Helvetica-Bold", fontSize=9,
                                textColor=INK, leading=11, alignment=TA_LEFT),
        "tot":   ParagraphStyle("tot", fontName="Helvetica-Bold", fontSize=10.5,
                                textColor=SLATE, leading=11, alignment=TA_CENTER),
        "totp":  ParagraphStyle("totp", fontName="Helvetica", fontSize=7,
                                textColor=INK_SOFT, leading=8, alignment=TA_CENTER),
        "grand": ParagraphStyle("grand", fontName="Helvetica-Bold", fontSize=15,
                                textColor=WHITE, leading=16, alignment=TA_CENTER),
        "grands": ParagraphStyle("grands", fontName="Helvetica", fontSize=7,
                                 textColor=SLATE_TXT, leading=8, alignment=TA_CENTER),
        "note":  ParagraphStyle("note", fontName="Helvetica", fontSize=9,
                                textColor=INK_SOFT, leading=13),
        "note_b": ParagraphStyle("note_b", fontName="Helvetica-Bold", fontSize=9.5,
                                 textColor=BAD, leading=13),
        "cap":   ParagraphStyle("cap", fontName="Helvetica", fontSize=7.5,
                                textColor=INK_SOFT, leading=9),
    }


def _pct(n, grand):
    return f"{round(n * 100 / grand)}%" if grand else "0%"


def _header_band(usable_w, meta, S):
    title = Paragraph("Where My Energy Goes", S["h1"])
    who = meta.get("name", "")
    role = str(meta.get("role", "") or "").replace("_", " ").title()
    rng = f"{meta.get('date_from','')}  \u2192  {meta.get('date_to','')}"
    sub = Paragraph(
        f"{who}{' &nbsp;\u00b7&nbsp; ' + role if role else ''} &nbsp;\u00b7&nbsp; "
        f"effort report &nbsp;\u00b7&nbsp; {rng}", S["sub"])
    band = Table([[title], [sub]], colWidths=[usable_w])
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SLATE),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("TOPPADDING", (0, 0), (0, 0), 14),
        ("BOTTOMPADDING", (0, 0), (0, 0), 2),
        ("TOPPADDING", (0, 1), (0, 1), 0),
        ("BOTTOMPADDING", (0, 1), (0, 1), 10),
        ("ROUNDEDCORNERS", [8, 8, 8, 8]),
    ]))
    return band


def _summary_cards(usable_w, cards, S):
    """cards: list of (label, big, small) tuples."""
    cells = []
    for label, big, small in cards:
        inner = Table([[Paragraph(label, S["kpi_l"])],
                       [Paragraph(str(big), S["kpi_n"])],
                       [Paragraph(small, S["kpi_s"])]])
        inner.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (0, 0), 10),
            ("BOTTOMPADDING", (0, -1), (0, -1), 10),
            ("TOPPADDING", (0, 1), (0, 2), 1),
            ("BOTTOMPADDING", (0, 0), (0, 1), 1),
            ("BACKGROUND", (0, 0), (-1, -1), WHITE),
            ("BOX", (0, 0), (-1, -1), 0.5, LINE),
            ("ROUNDEDCORNERS", [8, 8, 8, 8]),
        ]))
        cells.append(inner)
    n = len(cells)
    gap = 8
    col_w = (usable_w - gap * (n - 1)) / n
    grid = Table([cells], colWidths=[col_w] * n)
    grid.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), gap),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return grid


def _matrix_table(usable_w, rows, cols, counts, row_tot, col_tot, grand, best_col, S):
    maxv = max((counts[r][c] for r in rows for c in cols), default=0)
    max_rt = max(row_tot.values(), default=0) or 1

    ncol = len(cols)
    first_w = 42 * mm
    total_w = 24 * mm
    kra_w = (usable_w - first_w - total_w) / max(ncol, 1)   # always fits the page width
    col_widths = [first_w] + [kra_w] * ncol + [total_w]

    # header row
    header = [Paragraph("Effort \u2193 &nbsp;/&nbsp; KRA \u2192", S["colh"])]
    header += [Paragraph(str(c), S["colh"]) for c in cols]
    header += [Paragraph("TOTAL", S["colh"])]
    data = [header]

    for r in rows:
        line = [Paragraph(r, S["rowh"])]
        for c in cols:
            v = counts[r][c]
            line.append(str(v) if v > 0 else "\u00b7")
        rt = row_tot[r]
        line.append(Paragraph(f"{rt}<br/><font size=7 color='#5C6B7A'>{_pct(rt, grand)}</font>",
                              S["tot"]))
        data.append(line)

    # total row
    trow = [Paragraph("TOTAL", S["rowh"])]
    for c in cols:
        ct = col_tot[c]
        trow.append(Paragraph(f"{ct}<br/><font size=7 color='#5C6B7A'>{_pct(ct, grand)}</font>",
                              S["tot"]))
    trow.append(Paragraph(f"{grand}<br/><font size=7 color='#CFE0EA'>tasks</font>", S["grand"]))
    data.append(trow)

    tbl = Table(data, colWidths=col_widths, repeatRows=1)

    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        # header
        ("BACKGROUND", (0, 0), (-1, 0), SLATE),
        ("LINEBELOW", (0, 0), (-1, 0), 0, WHITE),
        # first (label) column
        ("BACKGROUND", (0, 1), (0, -1), TOTAL_BG),
        ("TEXTCOLOR", (0, 1), (0, -1), INK),
        # tile separation: thin white grid so heat cells read as tiles
        ("INNERGRID", (0, 0), (-1, -1), 1.2, CREAM),
        ("BOX", (0, 0), (-1, -1), 0, CREAM),
        # right TOTAL column + bottom TOTAL row
        ("BACKGROUND", (-1, 1), (-1, -2), TOTAL_BG),
        ("BACKGROUND", (0, -1), (-2, -1), TOTAL_BG),
        ("BACKGROUND", (-1, -1), (-1, -1), SLATE),
    ]

    # per-cell heat shading + text colour
    for ri, r in enumerate(rows, start=1):
        for ci, c in enumerate(cols, start=1):
            v = counts[r][c]
            bg, txt = _heat(v, maxv)
            style.append(("BACKGROUND", (ci, ri), (ci, ri), bg))
            style.append(("TEXTCOLOR", (ci, ri), (ci, ri), txt))
            if v > 0:
                style.append(("FONT", (ci, ri), (ci, ri), "Helvetica-Bold", 12))
    # accent the widest-spread KRA header
    if best_col in cols:
        bi = cols.index(best_col) + 1
        style.append(("LINEBELOW", (bi, 0), (bi, 0), 2.2, GOOD))

    tbl.setStyle(TableStyle(style))
    return tbl


def build_effort_pdf(meta, rows, cols, counts, row_tot, col_tot, grand,
                     learning_kra="Self-Improvement", unassigned_kra="Unassigned"):
    """Return PDF bytes for the effort matrix.

    meta   : dict(name, role, date_from, date_to)
    rows,cols,counts,row_tot,col_tot,grand : exactly classify.build_matrix(...) output.
    """
    S = _styles()
    buf = io.BytesIO()
    page = landscape(A4)
    m = 12 * mm
    usable_w = page[0] - 2 * m

    doc = SimpleDocTemplate(buf, pagesize=page,
                            leftMargin=m, rightMargin=m, topMargin=m, bottomMargin=14 * mm,
                            title="Effort Report", author="Plan My Day")

    # summary signals (tie back to goal-alignment: Unassigned = tasks not laddering to a goal)
    busiest = max(rows, key=lambda r: row_tot[r]) if grand else "\u2014"
    served = [c for c in cols if c not in (unassigned_kra,)]
    top_kra = max(served, key=lambda c: col_tot.get(c, 0)) if served and grand else "\u2014"
    unassigned = col_tot.get(unassigned_kra, 0)
    cards = [
        ("Tasks in range", grand, "total effort logged"),
        ("Busiest effort type", busiest, f"{row_tot.get(busiest,0)} tasks \u00b7 {_pct(row_tot.get(busiest,0), grand)}"),
        ("Most-served KRA", top_kra, f"{col_tot.get(top_kra,0)} tasks \u00b7 {_pct(col_tot.get(top_kra,0), grand)}"),
        ("Not laddering to a goal", unassigned,
         "unassigned \u2014 review or drop" if unassigned else "every task serves a KRA \u2713"),
    ]

    best_col = max(cols, key=lambda c: sum(1 for r in rows if counts[r][c] > 0)) if cols else None

    story = [
        _header_band(usable_w, meta, S),
        Spacer(1, 8),
        _summary_cards(usable_w, cards, S),
        Spacer(1, 10),
        _matrix_table(usable_w, rows, cols, counts, row_tot, col_tot, grand, best_col, S),
        Spacer(1, 8),
        HRFlowable(width="100%", thickness=0.5, color=LINE, spaceBefore=0, spaceAfter=8),
        Paragraph(
            "Read a <b>column \u2193</b> for the kinds of effort a KRA receives \u00b7 read a "
            "<b>row \u2192</b> for where each effort type is spent \u00b7 darker cell = more tasks "
            "\u00b7 <font color='#2E9E6B'>\u25cf</font> = KRA with the widest effort spread.",
            S["note"]),
    ]
    if unassigned:
        story += [Spacer(1, 6), Paragraph(
            f"\u26a0 {unassigned} task(s) in this range don\u2019t ladder up to any goal. "
            "Assign them a KRA, defer, or drop \u2014 busy is not the same as progress.",
            S["note_b"])]

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(INK_SOFT)
        stamp = datetime.now().strftime("%d %b %Y, %H:%M")
        canvas.drawString(m, 9 * mm, f"Plan My Day \u00b7 generated {stamp}")
        canvas.drawRightString(page[0] - m, 9 * mm, f"Page {_doc.page}")
        canvas.setStrokeColor(LINE)
        canvas.line(m, 11.5 * mm, page[0] - m, 11.5 * mm)
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
