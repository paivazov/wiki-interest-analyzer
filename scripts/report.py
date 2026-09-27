"""One-page PDF report (reportlab). Layout: question -> answer -> chart -> table ->
"what this does and doesn't tell you" -> source/method line.

Every sentence comes from i18n templates filled by code. The only free text is the
optional --note (max 600 chars), printed under its own "Analyst note" heading.
The whole page is wrapped in a shrink-to-fit frame, so it is always exactly one page.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepInFrame, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import analyze as an
import charts
import i18n

INK, INK_2, RULE, HEAD_BG = colors.HexColor("#0b0b0b"), colors.HexColor("#52514e"), colors.HexColor("#e4e3df"), \
    colors.HexColor("#f4f3f0")
MAX_LIMITS = 7


def _fonts():
    d = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    if "WIA" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("WIA", str(d / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("WIA-Bold", str(d / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily("WIA", normal="WIA", bold="WIA-Bold", italic="WIA", boldItalic="WIA-Bold")


def _styles():
    base = dict(fontName="WIA", textColor=INK, alignment=TA_LEFT)
    return {
        "title": ParagraphStyle("title", fontName="WIA-Bold", fontSize=14, leading=17, textColor=INK),
        "meta": ParagraphStyle("meta", fontSize=7.5, leading=9.5, textColor=INK_2, **{k: v for k, v in base.items()
                                                                                    if k != "textColor"}),
        "h": ParagraphStyle("h", fontName="WIA-Bold", fontSize=10, leading=13, spaceBefore=5, spaceAfter=2,
                            textColor=INK),
        "body": ParagraphStyle("body", fontSize=9, leading=12, **base),
        "cell": ParagraphStyle("cell", fontSize=7.5, leading=9, **base),
        "cellh": ParagraphStyle("cellh", fontName="WIA-Bold", fontSize=7.2, leading=8.6, textColor=INK),
        "bullet": ParagraphStyle("bullet", fontSize=8, leading=10.2, leftIndent=9, bulletIndent=0, **base),
        "foot": ParagraphStyle("foot", fontSize=6.8, leading=8.4, textColor=INK_2, fontName="WIA"),
    }


def _table(a: dict, st) -> Table:
    rl, res, spec = a["rl"], a["results"], a["spec"]
    rank = {e["lang"]: e.get("rank") for e in a.get("ranking", [])}
    cols = ["r.col_lang", "r.col_level", "r.col_views", "r.col_yoy", "r.col_slope", "r.col_trend", "r.col_conf"]
    if a.get("ranking"):
        cols.append("r.col_rank")
    head = [Paragraph(escape(i18n.t(c, rl)).replace("\n", "<br/>"), st["cellh"]) for c in cols]
    rows = [head]
    for l in spec["langs"]:
        r = res[l]
        name = f"{i18n.lang_name(l, rl)} ({l})"
        if r["status"] != "ok":
            row = [name, i18n.t("no_article", rl)] + [""] * (len(cols) - 2)
        else:
            ci = r.get("slope_ci_pct") or [None, None]
            slope = (f"{i18n.pct(r.get('slope_pct_per_year'))} ({i18n.pct(ci[0])}…{i18n.pct(ci[1])})"
                     if r.get("slope_pct_per_year") is not None else "—")
            row = [name, f"{r['level_per_million']:g}", f"{r['avg_monthly_views']:,}".replace(",", " "),
                   i18n.pct(r.get("yoy_pct")) if r.get("yoy_pct") is not None else "—", slope,
                   i18n.t(f"trend_short.{r['trend']}", rl), i18n.t(f"conf.{r['confidence']}", rl)]
            if a.get("ranking"):
                row.append(str(rank.get(l) or "—"))
        rows.append([Paragraph(escape(str(c)), st["cell"]) for c in row])
    widths = [30, 20, 21, 16, 38, 20, 19, 12][:len(cols)]
    total = sum(widths)
    avail = A4[0] - 28 * mm
    t = Table(rows, colWidths=[w / total * avail for w in widths], repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), HEAD_BG), ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 2.5),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]
    for i, l in enumerate(spec["langs"], start=1):
        if res[l]["status"] != "ok":
            style.append(("SPAN", (1, i), (-1, i)))
    t.setStyle(TableStyle(style))
    return t


def render(a: dict, out: Path, note: str | None = None) -> dict:
    _fonts()
    st = _styles()
    rl, spec = a["rl"], a["spec"]
    topic = an.topic_name(spec, rl)
    title = spec.get("question") or i18n.t("r.title_default", rl, topic=topic, langs=", ".join(spec["langs"]))
    months = a["months"]
    members = [(c.get("labels") or {}).get(rl) or c.get("label") or c["qid"] for c in spec.get("cluster", [])]
    topic_line = escape(topic) + (f" + {escape(', '.join(members[1:]))}" if len(members) > 1 else "")
    meta = (f"{topic_line} · Wikidata {spec.get('topic_qid')} · {months[0]} – {months[-1]} · "
            f"{', '.join(spec['langs'])} · {a['generated']}")

    story = [Paragraph(escape(title), st["title"]), Spacer(1, 2), Paragraph(meta, st["meta"]),
             Paragraph(escape(i18n.t("r.answer", rl)), st["h"]), Paragraph(escape(an.answer_text(a)), st["body"]),
             Spacer(1, 4), Paragraph(escape(i18n.t("r.chart_title", rl)), st["meta"])]
    tmp = Path(tempfile.mkdtemp(prefix="wia-")) / "chart.png"
    charts.render(a, "share", tmp, size=(7.4, 3.5), dpi=220)
    width = A4[0] - 28 * mm
    story += [Image(str(tmp), width=width, height=width * 3.5 / 7.4), Spacer(1, 3), _table(a, st)]
    if a.get("ranking"):
        story += [Spacer(1, 2), Paragraph(escape(i18n.t("r.rank_note", rl)), st["meta"])]
    if note:
        story += [Paragraph(escape(i18n.t("r.note", rl)), st["h"]), Paragraph(escape(note.strip()[:600]), st["body"])]
    cav = an.all_caveats(a)
    limits = [c for c in cav if c["severity"] != "info"][:MAX_LIMITS - 1]
    limits += [c for c in cav if c["code"] == "curiosity_not_demand"]
    if len(limits) < MAX_LIMITS:
        limits += [c for c in cav if c["severity"] == "info" and c["code"] != "curiosity_not_demand"][
            :MAX_LIMITS - len(limits)]
    story.append(Paragraph(escape(i18n.t("r.limits", rl)), st["h"]))
    story += [Paragraph(escape(c["text"]), st["bullet"], bulletText="•") for c in limits]
    n_articles = max((len(spec.get("articles", {}).get(l, [])) for l in spec["langs"]), default=0)
    foot = i18n.t("r.source", rl, agent=spec.get("agent", "user"), access=spec.get("access", "all-access"),
                  start=months[0], end=months[-1], n=len(months), qid=spec.get("topic_qid"), n_articles=n_articles,
                  version=a["version"], date=a["generated"])
    story += [Spacer(1, 5), Paragraph(escape(foot), st["foot"])]

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm,
                            bottomMargin=12 * mm, title=title, author="wiki-interest-analyzer")
    frame_h = A4[1] - 24 * mm - 1
    doc.build([KeepInFrame(width, frame_h, story, mode="shrink")])
    try:
        os.remove(tmp)
        os.rmdir(tmp.parent)
    except OSError:
        pass
    return {"limitations": len(limits), "report_lang": rl}
