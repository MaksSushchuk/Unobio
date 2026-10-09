"""Report JSON (+ evidence bundle) -> Markdown, for download. Deterministic, no LLM.

Layout follows the PDF: verdict, capital, one section per analyst with its claims and numbered source
references, risks, unknowns, diligence questions, the verdict rule log, then the numbered source list.
Sources are the primary records behind each claim (app/sources.py), numbered in order of first citation.
"""
from __future__ import annotations

from typing import Any

from evidence_bundle import EvidenceBundle

from .sources import expand_refs, source_entry

VERDICT = {"invest": "Invest", "conditional": "Conditional", "do_not_invest": "Do not invest"}
REQUIRES = {"proprietary_data": "company data", "patient_data": "patient-level data", "kol": "expert interviews",
            "experiment": "experiment", "cmc_ip": "manufacturing / IP diligence"}


def _usd(x: float) -> str:
    return f"{'-' if x < 0 else ''}${abs(x) / 1e6:,.1f}M"


def _md(text: str) -> str:
    """Keep table cells and list items on one line."""
    return " ".join(str(text or "").split()).replace("|", "\\|")


def render_markdown(report: dict[str, Any], bundle: EvidenceBundle) -> str:
    by_id = bundle.by_id()
    numbers: dict[str, int] = {}

    def cite(refs: list[dict[str, str]]) -> str:
        expanded = expand_refs(((r["evidence_id"], r["stance"]) for r in refs), by_id, keep_derived=False)
        tags = []
        for eid, stance in expanded:
            if eid not in by_id:
                continue
            n = numbers.setdefault(eid, len(numbers) + 1)
            tags.append(f"[{n}]" + ("↯" if stance == "contradicts" else ""))
        return " ".join(tags)

    inp = report["input"]
    d = report.get("details") or {}
    lines = [f"# {inp['mechanism']} · {inp['indication']}", ""]
    meta = [f"Run `{report['id']}`", report["created_at"][:19].replace("T", " ") + " UTC"]
    if d.get("evidence_cutoff"):
        meta.append(f"evidence as of {d['evidence_cutoff']}")
    lines += [" · ".join(meta), ""]
    if report.get("is_mock"):
        lines += ["> **Mock data** — fixture content or offline stand-in analysts; sources not verified.", ""]
    extras = [f"**{k.title()}:** {_md(', '.join(v) if isinstance(v, list) else v)}"
              for k, v in inp.items() if k not in ("indication", "mechanism") and v]
    if extras:
        lines += ["  \n".join(extras), ""]

    lines += ["## Recommendation", "",
              f"**{VERDICT.get(report['recommendation'], report['recommendation'])}** — "
              f"confidence {report['confidence']:.0%}", "", _md(report["summary"]), ""]
    if d.get("lens_scores"):
        lines += ["Analyst scores (0–5): " + ", ".join(f"{k} {v}" for k, v in d["lens_scores"].items()
                                                       if v is not None), ""]

    c = report["capital"]
    lines += ["## Capital to milestone", "", f"**{_md(c['milestone'])}** · {c['months_low']}–{c['months_high']} months",
              "", "| Low | Base | High |", "|---|---|---|",
              f"| {_usd(c['usd_low'])} | {_usd(c['usd_base'])} | {_usd(c['usd_high'])} |", ""]
    lines += [f"- {_md(a)}" for a in c["assumptions"]] + [""]
    if d.get("rnpv"):
        r = d["rnpv"]
        lines += [f"Risk-adjusted NPV: {_usd(r['usd_low'])} / {_usd(r['usd_base'])} / {_usd(r['usd_high'])} "
                  f"(probability of approval {r['probability_of_success']:.1%})", ""]

    for s in report["sections"]:
        lines += [f"## {s['title']}", ""]
        if s.get("summary"):
            lines += [f"_{_md(s['summary'])}_", ""]
        for cl in s["claims"]:
            kind = "Source fact" if cl["kind"] == "source_fact" else "Inference"
            refs = cite(cl["evidence"])
            lines.append(f"- {_md(cl['text'])} — *{kind}, confidence {cl['confidence']:.0%}*"
                         + (f" {refs}" if refs else ""))
        lines.append("")

    if report["risks"]:
        lines += ["## Key risks", ""]
        lines += [f"- **[{r['severity']}] {_md(r['title'])}** — {_md(r['description'])}" for r in report["risks"]]
        lines.append("")
    if report["unknowns"]:
        lines += ["## Critical unknowns", ""] + [f"- {_md(u)}" for u in report["unknowns"]] + [""]
    if report["diligence_questions"]:
        lines += ["## Diligence questions", ""]
        lines += [f"{i}. {_md(q['question'])} — {_md(q['rationale'])} *(needs {REQUIRES.get(q['requires'], q['requires'])})*"
                  for i, q in enumerate(report["diligence_questions"], 1)]
        lines.append("")
    if d.get("rule_trace"):
        lines += ["## How the verdict was reached", "", "```text", *d["rule_trace"], "```", ""]

    lines += ["## Sources", "", "↯ = cited as evidence against the claim.", ""]
    for eid, n in sorted(numbers.items(), key=lambda kv: kv[1]):
        src = source_entry(by_id[eid])
        meta = " · ".join(x for x in (src["database"], src["identifier"], src["year"]) if x)
        lines.append(f"{n}. {_md(src['title'])} — {meta}. <{src['url']}>" if src["url"] else
                     f"{n}. {_md(src['title'])} — {meta}.")
    if not numbers:
        lines.append("_No evidence cited._")
    lines.append("")
    return "\n".join(lines)
