"""PDF rendering (reportlab). A4, restrained styling that follows the web UI.

Every piece of text goes through ``esc`` before it reaches a Paragraph.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    CondPageBreak,
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.fonts import addMapping

from .agent import ListItem, Narrative, Report
from .config import PANEL_TITLES, WriterConfig
from .schema import Claim, Section

# Palette (Tailwind slate + status colours, as in the UI).
INK = colors.HexColor("#0F172A")
TEXT = colors.HexColor("#334155")
MUTED = colors.HexColor("#64748B")
FAINT = colors.HexColor("#94A3B8")
BORDER = colors.HexColor("#E2E8F0")
SURFACE = colors.HexColor("#F8FAFC")
TRACK = colors.HexColor("#E2E8F0")
BAR = colors.HexColor("#1E293B")
VERDICT_COLOURS = {
    "invest": (colors.HexColor("#15803D"), colors.HexColor("#F0FDF4"), colors.HexColor("#BBF7D0")),
    "do not invest": (colors.HexColor("#DC2626"), colors.HexColor("#FEF2F2"), colors.HexColor("#FECACA")),
}
AMBER = (colors.HexColor("#B45309"), colors.HexColor("#FFFBEB"), colors.HexColor("#FDE68A"))
NEUTRAL = (MUTED, SURFACE, BORDER)

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
FRAME_PAD = 6  # SimpleDocTemplate frame padding on each side
CONTENT_W = PAGE_W - 2 * MARGIN - 2 * FRAME_PAD

FONT = "DejaVuSans"
FONT_B = "DejaVuSans-Bold"

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def esc(text: Any) -> str:
    """Escape arbitrary text for reportlab's Paragraph mini-markup."""
    return escape(_CTRL.sub("", "" if text is None else str(text)))


def register_fonts(cfg: WriterConfig) -> None:
    if FONT in pdfmetrics.getRegisteredFontNames():
        return
    pdfmetrics.registerFont(TTFont(FONT, str(cfg.font_regular)))
    pdfmetrics.registerFont(TTFont(FONT_B, str(cfg.font_bold)))
    # No oblique face is bundled; italics fall back to the upright face.
    addMapping(FONT, 0, 0, FONT)
    addMapping(FONT, 1, 0, FONT_B)
    addMapping(FONT, 0, 1, FONT)
    addMapping(FONT, 1, 1, FONT_B)


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName=FONT, fontSize=9.5, leading=13.5, textColor=TEXT)
    s = {
        "base": base,
        "h1": ParagraphStyle("h1", parent=base, fontName=FONT_B, fontSize=19, leading=24, textColor=INK, spaceAfter=4),
        "h2": ParagraphStyle("h2", parent=base, fontName=FONT_B, fontSize=13.5, leading=18, textColor=INK, spaceBefore=6, spaceAfter=2),
        "h3": ParagraphStyle("h3", parent=base, fontName=FONT_B, fontSize=11, leading=15, textColor=INK, spaceBefore=6, spaceAfter=3),
        "label": ParagraphStyle("label", parent=base, fontName=FONT_B, fontSize=7.5, leading=10, textColor=MUTED),
        "meta": ParagraphStyle("meta", parent=base, fontSize=8, leading=11, textColor=MUTED),
        "small": ParagraphStyle("small", parent=base, fontSize=8, leading=11, textColor=MUTED),
        "small_r": ParagraphStyle("small_r", parent=base, fontSize=8, leading=11, textColor=MUTED, alignment=TA_RIGHT),
        "claim": ParagraphStyle("claim", parent=base, fontSize=9.5, leading=13.5, textColor=INK),
        "chip": ParagraphStyle("chip", parent=base, fontSize=7.5, leading=10, textColor=TEXT, alignment=TA_RIGHT),
        "takeaway": ParagraphStyle("takeaway", parent=base, fontSize=10, leading=14.5, textColor=TEXT),
        "missing": ParagraphStyle("missing", parent=base, fontSize=8.5, leading=12, textColor=FAINT),
        "bullet": ParagraphStyle("bullet", parent=base, leftIndent=12, bulletIndent=2),
        "source": ParagraphStyle("source", parent=base, fontSize=8.5, leading=12, leftIndent=22, firstLineIndent=-22),
        "toc": ParagraphStyle("toc", parent=base, fontSize=9, leading=12),
        "toc_r": ParagraphStyle("toc_r", parent=base, fontSize=9, leading=12, textColor=FAINT, alignment=TA_RIGHT),
        "big": ParagraphStyle("big", parent=base, fontName=FONT_B, fontSize=10.5, leading=14, textColor=INK),
    }
    return s


# ---------------------------------------------------------------- flowables


class Bar(Flowable):
    """A rounded progress bar: confidence or similar 0..1 values."""

    def __init__(self, width: float, value: float | None, colour=BAR, height: float = 4):
        super().__init__()
        self.width, self.height, self.value, self.colour = width, height, value, colour

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        c, r = self.canv, self.height / 2
        c.setFillColor(TRACK)
        c.roundRect(0, 0, self.width, self.height, r, stroke=0, fill=1)
        if self.value is not None and self.value > 0:
            c.setFillColor(self.colour)
            c.roundRect(0, 0, max(self.height, self.width * min(self.value, 1)), self.height, r, stroke=0, fill=1)


class RangeBar(Flowable):
    """Low–high track with a marker at the base case."""

    def __init__(self, width: float, low: float, base: float | None, high: float):
        super().__init__()
        self.width, self.height = width, 12
        self.low, self.base, self.high = low, base, high

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        c, y = self.canv, 4
        c.setFillColor(TRACK)
        c.roundRect(0, y, self.width, 4, 2, stroke=0, fill=1)
        if self.base is not None and self.high > self.low:
            x = (self.base - self.low) / (self.high - self.low) * self.width
            x = min(max(x, 1.5), self.width - 1.5)
            c.setFillColor(BAR)
            c.roundRect(x - 1.5, 0, 3, 12, 1.5, stroke=0, fill=1)


def card(flowables: list, *, bg=colors.white, border=BORDER, pad: float = 10, width: float = CONTENT_W) -> Table:
    t = Table([[flowables]], colWidths=[width])
    t.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.75, border),
                ("BACKGROUND", (0, 0), (-1, -1), bg),
                ("ROUNDEDCORNERS", [6, 6, 6, 6]),
                ("LEFTPADDING", (0, 0), (-1, -1), pad),
                ("RIGHTPADDING", (0, 0), (-1, -1), pad),
                ("TOPPADDING", (0, 0), (-1, -1), pad * 0.8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), pad * 0.8),
            ]
        )
    )
    return t


def grid(rows, widths, extra=()) -> Table:
    t = Table(rows, colWidths=widths)
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                *extra,
            ]
        )
    )
    return t


# ---------------------------------------------------------------- formatting


def pct(v: float | None) -> str:
    return "n/a" if v is None else f"{round(v * 100)}%"


_CURRENCY = {"USD": "$", "EUR": "€", "GBP": "£"}


def money(v: float | None, currency: str) -> str:
    """Compact but exact: 2,500,000 -> $2.5M; 1,234,567 stays in full."""
    if v is None:
        return "n/a"
    sym = _CURRENCY.get(currency.upper(), "")
    prefix = sym if sym else f"{currency} "
    for scale, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= scale:
            scaled = v / scale
            if abs(round(scaled, 2) - scaled) < 1e-9:
                return f"{prefix}{scaled:,.2f}".rstrip("0").rstrip(".") + suffix
    return f"{prefix}{v:,.2f}".rstrip("0").rstrip(".")


def fmt_timestamp(ts: str) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    out = dt.strftime("%d.%m.%Y, %H:%M:%S")
    if dt.tzinfo is not None:
        out += " " + (dt.tzname() or "")
    return out.strip()


def verdict_palette(verdict: str | None):
    if not verdict:
        return NEUTRAL
    return VERDICT_COLOURS.get(verdict.strip().lower(), AMBER)


# ---------------------------------------------------------------- source numbering


class SourceIndex:
    """Numbers sources by first citation; deduplicates across the whole report."""

    def __init__(self, report: Report):
        self.sources = report.input.sources
        self.by_key: dict[str, int] = {}
        self.entries: list = []
        for section in report.input.sections:
            for claim in section.claims:
                self.refs_for_claim(claim)

    def number(self, source_id: str) -> int | None:
        src = self.sources.get(source_id)
        if src is None:
            return None
        key = src.dedup_key()
        if key not in self.by_key:
            self.entries.append(src)
            self.by_key[key] = len(self.entries)
        return self.by_key[key]

    def refs_for_claim(self, claim: Claim) -> list[int]:
        nums = [self.number(e.source_id) for e in claim.evidence]
        return sorted({n for n in nums if n is not None})


def refs_markup(nums: list[int]) -> str:
    return " ".join(f"[{n}]" for n in nums)


# ---------------------------------------------------------------- builder


class Builder:
    def __init__(self, report: Report):
        self.r = report
        self.wi = report.input
        self.s = _styles()
        self.src = SourceIndex(report)
        self.claims_by_id = {c.id: c for c in self.wi.all_claims()}

    def title(self) -> str:
        return f"{self.wi.thesis.mechanism} · {self.wi.thesis.indication}"

    # -- header -----------------------------------------------------------
    def header(self) -> list:
        s, wi, out = self.s, self.wi, []
        if wi.run.is_mock:
            kinds = [k for k, on in (("simulated run", wi.run.simulated), ("fixture data", wi.run.fixture_data)) if on]
            out += [
                card(
                    [Paragraph(f"<b>⚠ Mock data</b> – sources not verified ({esc(', '.join(kinds))})", ParagraphStyle("b", parent=s["small"], textColor=AMBER[0]))],
                    bg=AMBER[1], border=AMBER[2], pad=6, width=CONTENT_W,
                ),
                Spacer(1, 8),
            ]
        out.append(Paragraph(esc(self.title()), s["h1"]))
        t = wi.thesis
        meta = [(k, v) for k, v in (("Modality", t.modality), ("Stage", t.stage), ("Biomarkers", ", ".join(t.biomarkers)), ("Route", t.route)) if v]
        if meta:
            out.append(Paragraph("   ·   ".join(f"<font color='#94A3B8'>{k}</font> {esc(v)}" for k, v in meta), s["meta"]))
        run_line = " · ".join(p for p in (wi.run.id, fmt_timestamp(wi.run.timestamp)) if p)
        if run_line:
            out.append(Paragraph(esc(run_line), s["small"]))
        out.append(Spacer(1, 10))
        return out

    # -- recommendation ---------------------------------------------------
    def recommendation(self) -> list:
        s, r = self.s, self.r
        fg, bg, bd = verdict_palette(r.verdict)
        mark = {"invest": "✓", "do not invest": "✕"}.get((r.verdict or "").lower(), "●")
        pill_text = f"{mark}  {r.verdict}" if r.verdict else "Verdict not available"
        inner_w = CONTENT_W - 20
        conf_w = 200
        pill_style = ParagraphStyle("p", parent=s["base"], fontName=FONT_B, fontSize=11, leading=14, textColor=fg)
        pill_w = min(inner_w - conf_w - 30, pdfmetrics.stringWidth(pill_text, FONT_B, 11) + 18)
        pill = Table([[Paragraph(esc(pill_text), pill_style)]], colWidths=[pill_w])
        pill.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), bg), ("BOX", (0, 0), (-1, -1), 0.75, bd),
            ("ROUNDEDCORNERS", [4, 4, 4, 4]),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        conf = [
            grid([[Paragraph("Confidence", s["small"]), Paragraph(f"<b>{pct(r.confidence)}</b>", ParagraphStyle("c", parent=s["small_r"], textColor=INK))]], [conf_w / 2, conf_w / 2]),
            Spacer(1, 3),
            Bar(conf_w, r.confidence, fg if r.verdict else MUTED, height=5),
        ]
        top = grid([[pill, conf]], [inner_w - conf_w - 10, conf_w + 10], [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (0, 0), "LEFT")])
        body = [Paragraph("RECOMMENDATION", s["label"]), Spacer(1, 6), top, Spacer(1, 8)]
        if r.rationale:
            body.append(Paragraph(esc(r.rationale), s["base"]))
        else:
            body.append(Paragraph("Rationale unavailable: the final synthesis could not be generated. See Report QA notes.", s["missing"]))
        src_note = {
            "upstream": "Verdict and confidence as supplied by the upstream agents"
            + ("; rationale written by the Writer from the input evidence." if r.rationale else "."),
            "model": "Verdict written by the Writer model (no upstream verdict supplied); confidence as supplied upstream.",
            "none": "No verdict was supplied upstream or produced by the Writer.",
        }[r.verdict_source]
        body += [Spacer(1, 6), Paragraph(esc(src_note), s["small"])]
        return [card(body), Spacer(1, 10)]

    # -- capital ----------------------------------------------------------
    def capital(self) -> list:
        s, c = self.s, self.wi.capital
        if c is None:
            return [card([Paragraph("CAPITAL TO MILESTONE", s["label"]), Spacer(1, 4), Paragraph("Not provided in the input.", s["missing"])]), Spacer(1, 10)]
        inner_w = CONTENT_W - 20
        body = [Paragraph("CAPITAL TO MILESTONE", s["label"]), Spacer(1, 6)]
        if c.milestone:
            body.append(Paragraph(esc(c.milestone), s["big"]))
        if c.duration:
            body.append(Paragraph(esc(c.duration), s["small"]))
        body.append(Spacer(1, 8))
        if c.low is not None and c.high is not None:
            body.append(RangeBar(inner_w, c.low, c.base, c.high))
            body.append(Spacer(1, 4))
        cols = [
            [Paragraph("LOW", s["label"]), Paragraph(esc(money(c.low, c.currency)), s["base"])],
            [Paragraph("BASE", s["label"]), Paragraph(f"<b>{esc(money(c.base, c.currency))}</b>", ParagraphStyle("bb", parent=s["base"], textColor=INK))],
            [Paragraph("HIGH", s["label"]), Paragraph(esc(money(c.high, c.currency)), s["base"])],
        ]
        body.append(grid([cols], [inner_w / 3] * 3))
        if c.assumptions:
            body += [Spacer(1, 8), Paragraph(f"{len(c.assumptions)} assumptions", s["label"]), Spacer(1, 2)]
            body += [Paragraph(esc(a), s["bullet"], bulletText="•") for a in c.assumptions]
        return [card(body), Spacer(1, 12)]

    # -- contents ---------------------------------------------------------
    def contents(self) -> list:
        s = self.s
        rows = [[Paragraph(esc(sec.title), s["toc"]), Paragraph(str(len(sec.claims)), s["toc_r"])] for sec in self.wi.sections]
        rows += [
            [Paragraph("Risks", s["toc"]), Paragraph(str(len(self.r.risks)), s["toc_r"])],
            [Paragraph("Critical unknowns", s["toc"]), Paragraph(str(len(self.r.unknowns)), s["toc_r"])],
            [Paragraph("Diligence questions", s["toc"]), Paragraph(str(len(self.r.questions)), s["toc_r"])],
        ]
        if self.wi.panels:
            rows.append([Paragraph("Appendix – data panels", s["toc"]), Paragraph(str(len(self.wi.panels)), s["toc_r"])])
        rows.append([Paragraph("Sources", s["toc"]), Paragraph(str(len(self.src.entries)), s["toc_r"])])
        t = grid(rows, [CONTENT_W - 40, 40], [("LINEBELOW", (0, 0), (-1, -2), 0.5, BORDER), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)])
        return [Paragraph("CONTENTS", s["label"]), Spacer(1, 4), t]

    # -- claim card ---------------------------------------------------------
    def claim_card(self, claim: Claim) -> Any:
        s = self.s
        inner_w = CONTENT_W - 20
        chip_w = 90
        text = Paragraph(esc(claim.text), s["claim"])
        chip = Paragraph(esc(claim.claim_type.replace("_", " ").capitalize()) if claim.claim_type else "", s["chip"])
        top = grid([[text, chip]], [inner_w - chip_w, chip_w], [("RIGHTPADDING", (0, 0), (0, 0), 8)])
        conf_w = 150
        conf = [
            grid([[Paragraph("Confidence", s["small"]), Paragraph(f"<b>{pct(claim.confidence)}</b>", ParagraphStyle("cc", parent=s["small_r"], textColor=INK))]], [conf_w / 2, conf_w / 2]),
            Spacer(1, 2),
            Bar(conf_w, claim.confidence),
        ]
        up = "#15803D" if claim.supporting else "#CBD5E1"
        down = "#DC2626" if claim.contradicting else "#CBD5E1"
        counts = Paragraph(
            f"<font color='{up}'>▲</font> {claim.supporting} supporting    <font color='{down}'>▼</font> {claim.contradicting} contradicting",
            s["small"],
        )
        refs = self.src.refs_for_claim(claim)
        ref_p = Paragraph(("Sources " + refs_markup(refs)) if refs else "No sources cited", s["small_r"])
        rest = inner_w - conf_w - 16
        bottom = grid([[conf, counts, ref_p]], [conf_w + 16, rest * 0.55, rest * 0.45], [("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("LEFTPADDING", (1, 0), (1, 0), 0)])
        if len(claim.text) > 2500:  # cannot fit a single unsplittable card on a page safely
            return [Paragraph(esc(claim.text), s["claim"]), Spacer(1, 3), bottom, Spacer(1, 8)]
        return [card([top, Spacer(1, 6), bottom], pad=9), Spacer(1, 6)]

    # -- sections -----------------------------------------------------------
    def section(self, sec: Section) -> list:
        s = self.s
        narrative: Narrative | None = self.r.narratives.get(sec.key)
        sub = f"{'Skeptic' if sec.is_skeptic else 'Analyst'}: {sec.key} · {len(sec.claims)} claim{'s' if len(sec.claims) != 1 else ''}"
        head = [Paragraph(esc(sec.title), s["h2"]), Paragraph(esc(sub), s["small"]), Spacer(1, 6)]
        out: list = [CondPageBreak(60 * mm)]
        if not sec.claims:
            out += head + [Paragraph(f"No data received from this agent (status: {esc(sec.status)}).", s["missing"]), Spacer(1, 10)]
            return out
        if narrative is None:
            head.append(Paragraph("Narrative unavailable: the model call failed for this section. Claims are shown as received.", s["missing"]))
            head.append(Spacer(1, 6))
        else:
            if narrative.takeaway:
                q = grid([[Paragraph(esc(narrative.takeaway), s["takeaway"])]], [CONTENT_W], [("LINEBEFORE", (0, 0), (0, 0), 2, BORDER), ("LEFTPADDING", (0, 0), (-1, -1), 10)])
                head += [q, Spacer(1, 6)]
            if narrative.synthesis:
                head += [Paragraph(esc(narrative.synthesis), s["base"]), Spacer(1, 8)]
        cards = []
        for claim in sec.claims:
            cards += self.claim_card(claim)
        # Keep the heading with the first claim.
        out.append(KeepTogether(head + cards[:2]))  # heading + first card (+ its spacer)
        out += cards[2:]
        if sec.follow_up_queries:
            out += [Paragraph("Follow-up searches requested", s["h3"])]
            out += [Paragraph(esc(q), s["bullet"], bulletText="•") for q in sec.follow_up_queries]
        out.append(Spacer(1, 8))
        return out

    def item_list(self, title: str, items: list[ListItem]) -> list:
        s = self.s
        out: list = [CondPageBreak(40 * mm), Paragraph(esc(title), s["h2"]), Spacer(1, 4)]
        if not items:
            msg = "None identified." if self.r.final_ok else "Unavailable: the final synthesis could not be generated."
            return out + [Paragraph(msg, s["missing"]), Spacer(1, 8)]
        for i, it in enumerate(items, 1):
            refs: set[int] = set()
            for cid in it.claim_ids:
                if cid in self.claims_by_id:
                    refs.update(self.src.refs_for_claim(self.claims_by_id[cid]))
            tail = f" <font color='#94A3B8' size='7.5'>{refs_markup(sorted(refs))}</font>" if refs else ""
            out.append(Paragraph(esc(it.text) + tail, s["bullet"], bulletText=f"{i}."))
            out.append(Spacer(1, 3))
        return out + [Spacer(1, 6)]

    # -- panels -------------------------------------------------------------
    def _value(self, value: Any, depth: int = 0) -> list:
        s = self.s
        style = ParagraphStyle(f"pv{depth}", parent=s["base"], leftIndent=12 * (depth + 1), bulletIndent=12 * depth + 2)
        if value is None or value == "" or value == [] or value == {}:
            return []
        if isinstance(value, (str, int, float, bool)):
            return [Paragraph(esc(value), ParagraphStyle(f"pt{depth}", parent=s["base"], leftIndent=12 * depth))]
        if isinstance(value, list):
            out = []
            for v in value:
                if isinstance(v, dict) and all(isinstance(x, (str, int, float, bool)) or x is None for x in v.values()):
                    line = " · ".join(
                        f"<b>{esc(str(k).replace('_', ' ').capitalize())}</b>: {esc('' if x is None else x)}" for k, x in v.items()
                    )
                    out.append(Paragraph(line, style, bulletText="•"))
                elif isinstance(v, (dict, list)):
                    if depth >= 2:
                        out.append(Paragraph(esc(json.dumps(v, ensure_ascii=False)), style, bulletText="•"))
                    else:
                        out += [Spacer(1, 2)] + self._value(v, depth + 1)
                else:
                    out.append(Paragraph(esc(v), style, bulletText="•"))
            return out
        if isinstance(value, dict):
            out = []
            for k, v in value.items():
                label = f"<b>{esc(str(k).replace('_', ' ').capitalize())}</b>"
                if isinstance(v, (str, int, float, bool)) or v is None:
                    out.append(Paragraph(f"{label}: {esc('' if v is None else v)}", ParagraphStyle(f"pk{depth}", parent=s["base"], leftIndent=12 * depth)))
                elif depth >= 2:
                    out.append(Paragraph(f"{label}: {esc(json.dumps(v, ensure_ascii=False))}", ParagraphStyle(f"pk{depth}", parent=s["base"], leftIndent=12 * depth)))
                else:
                    out.append(Paragraph(label, ParagraphStyle(f"pk{depth}", parent=s["base"], leftIndent=12 * depth)))
                    out += self._value(v, depth + 1)
            return out
        return [Paragraph(esc(value), s["base"])]

    def panels(self) -> list:
        s, panels = self.s, self.wi.panels
        if not panels:
            return []
        out: list = [PageBreak(), Paragraph("Appendix – data panels", s["h2"]),
                     Paragraph("Panel content is reproduced as received from upstream; it is not rewritten by the model.", s["small"]), Spacer(1, 6)]
        keys = [k for k in PANEL_TITLES if k in panels] + [k for k in panels if k not in PANEL_TITLES]
        empty = []
        for key in keys:
            body = self._value(panels[key])
            if not body:
                empty.append(PANEL_TITLES.get(key, key))
                continue
            out += [CondPageBreak(30 * mm), Paragraph(esc(PANEL_TITLES.get(key, key)), s["h3"]), Paragraph(esc(key), s["small"]), Spacer(1, 3)]
            out += body + [Spacer(1, 6)]
        if empty:
            out.append(Paragraph("Panels without data: " + esc(", ".join(empty)), s["missing"]))
        return out

    # -- sources / QA ---------------------------------------------------------
    def sources(self) -> list:
        s = self.s
        out: list = [CondPageBreak(40 * mm), Paragraph("Sources", s["h2"]), Spacer(1, 4)]
        if not self.src.entries:
            return out + [Paragraph("No sources cited.", s["missing"])]
        for i, src in enumerate(self.src.entries, 1):
            parts = [f"[{i}]&nbsp;&nbsp;"]
            parts.append(f"<b>{esc(src.title or src.identifier or src.url or src.id)}</b>")
            meta = " · ".join(esc(p) for p in (src.authors, src.database, src.identifier, src.year) if p)
            if meta:
                parts.append(f"<br/><font color='#64748B'>{meta}</font>")
            if src.url:
                parts.append(f"<br/><font color='#64748B'>{esc(src.url)}</font>")
            out += [Paragraph("".join(parts), s["source"]), Spacer(1, 3)]
        return out

    def qa(self) -> list:
        s = self.s
        if not self.r.warnings:
            return []
        out: list = [CondPageBreak(30 * mm), Spacer(1, 6), Paragraph("Report QA notes", s["h2"]),
                     Paragraph("Automatic checks raised by the Writer. Review before relying on this report.", s["small"]), Spacer(1, 4)]
        out += [Paragraph(esc(w), ParagraphStyle("qa", parent=s["bullet"], fontSize=8.5, leading=12), bulletText="•") for w in self.r.warnings]
        return out

    def story(self) -> list:
        out = self.header() + self.recommendation() + self.capital() + self.contents()
        out.append(PageBreak())
        for sec in self.wi.sections:
            out += self.section(sec)
        out += self.item_list("Risks", self.r.risks)
        out += self.item_list("Critical unknowns", self.r.unknowns)
        out += self.item_list("Diligence questions", self.r.questions)
        out += self.panels()
        out += self.sources()
        out += self.qa()
        return out


# ---------------------------------------------------------------- canvas / entry


def _canvas_class(title: str, sink: dict):
    class NumberedCanvas(rl_canvas.Canvas):
        """Two-pass canvas so the footer can say 'Page x of y'."""

        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._pages: list[dict] = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            sink["pages"] = total
            for state in self._pages:
                self.__dict__.update(state)
                self._footer(total)
                super().showPage()
            super().save()

        def _footer(self, total: int):
            y = 11 * mm
            self.setStrokeColor(BORDER)
            self.setLineWidth(0.5)
            self.line(MARGIN, y + 9, PAGE_W - MARGIN, y + 9)
            self.setFont(FONT, 7.5)
            self.setFillColor(MUTED)
            label = f"{title} — Underwriting report"
            while pdfmetrics.stringWidth(label, FONT, 7.5) > CONTENT_W - 80 and len(label) > 4:
                label = label[:-2]
            self.drawString(MARGIN, y, label)
            self.drawRightString(PAGE_W - MARGIN, y, f"Page {self._pageNumber} of {total}")

    return NumberedCanvas


def render_pdf(report: Report, path: Path, cfg: WriterConfig) -> int:
    """Render ``report`` to ``path``; returns the page count."""
    register_fonts(cfg)
    b = Builder(report)
    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN + 6 * mm,
        title=f"{b.title()} — Underwriting report", author="Writer agent",
    )
    sink: dict = {}
    doc.build(b.story(), canvasmaker=_canvas_class(b.title(), sink))
    return int(sink.get("pages", 0))
