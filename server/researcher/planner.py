"""Search planner: turn (indication, mechanism) into search terms.

The plan is a SUGGESTION. Every target symbol and drug name must be verified
against Open Targets / ChEMBL in resolve.py before it is trusted.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from researcher.schema import Action, Modality, ResearchInput, SearchPlan
from researcher.llm import LLM, LlmError

# Loose HGNC symbol shape: uppercase letters/digits, optional hyphen/dot groups (e.g. HLA-DRB1).
_SYMBOL_RE = re.compile(r"^[A-Z0-9]+(?:[-.][A-Z0-9]+)*$")

# Action words stripped from the mechanism in the no-LLM fallback, mapped to a normalized action.
_ACTION_WORDS: list[tuple[str, Action | None]] = [
    (r"inhibition|inhibitors?|inhibiting", "inhibition"),
    (r"antagonism|antagonists?", "inhibition"),
    (r"agonism|agonists?", "activation"),
    (r"blockade|blockers?|blocking", "inhibition"),
    (r"degraders?|degradation", "degradation"),
    (r"modulation|modulators?", "modulation"),
    (r"activation|activators?", "activation"),
    (r"targeting|targeted|directed", None),
    (r"antibody|antibodies|mab|monoclonal", None),
    (r"car[- ]?t|cell therapy", None),
    (r"therapy|treatment|siRNA|antisense|vaccine", None),
]

# Modality words in the mechanism (or ResearchInput.modality) for the no-LLM fallback, most specific first.
_MODALITY_WORDS: list[tuple[str, Modality]] = [
    (r"car[- ]?t|car[- ]?nk|cell therap\w*|t[- ]?cell therap\w*|tcr[- ]?t", "cell_therapy"),
    (r"antibody[- ]drug conjugates?|adcs?", "adc"),
    (r"bispecifics?|bi[- ]?specific|bites?|t[- ]?cell engagers?", "bispecific"),
    (r"gene therap\w*|aav|gene editing|crispr", "gene_therapy"),
    (r"sirna|antisense|asos?|oligonucleotides?|rnai", "oligonucleotide"),
    (r"small[- ]molecules?|oral inhibitors?", "small_molecule"),
    (r"antibod\w*|mabs?|monoclonals?|nanobod\w*", "antibody"),
]


class _LlmPlanBase(BaseModel):
    action: Action | None = Field(
        description="Effect of the mechanism on its targets. inhibition: inhibitors, antagonists, "
        "blocking or neutralizing antibodies. activation: agonists, activators. modulation: allosteric "
        "or mixed modulators. degradation: degraders, siRNA/antisense knockdown. other: anything else "
        "(e.g. cell therapy, vaccines). null if unclear."
    )
    modality: Modality = Field(
        description="Therapeutic modality of the mechanism. Take it from the mechanism text or the given Modality "
        "(CAR-T -> cell_therapy, BiTE / T-cell engager -> bispecific, antibody-drug conjugate -> adc, siRNA / "
        "antisense -> oligonucleotide, AAV / gene therapy -> gene_therapy, 'X inhibitor' pills -> small_molecule). "
        "If the text names no modality, use the modality of the drugs you list in drug_names when they all share "
        "one; otherwise unspecified."
    )
    disease_terms: list[str] = Field(
        description="Standard names and common synonyms of the indication, for searching registries and literature."
    )
    drug_names: list[str] = Field(
        description="Names (INN or code names) of drugs that act through this mechanism. Empty if unsure."
    )
    extra_literature_terms: list[str] = Field(
        description="A few additional search phrases for the mechanism in this disease (pathway or cytokine names)."
    )


class _LlmPlanWithTargets(_LlmPlanBase):
    target_symbols: list[str] = Field(
        description="Official HGNC gene symbols of the human molecular targets of the mechanism, "
        "e.g. PCSK9, IL17A. Gene symbols only, not protein names or free text. Empty if unsure."
    )


_PROMPT = """You are helping plan a biomedical evidence search.

Indication: {indication}
Mechanism: {mechanism}
{extra}
{target_instruction}
Rules:
- action must be exactly one of: inhibition, activation, modulation, degradation, other (or null if unclear).
- modality must be exactly one of: small_molecule, antibody, bispecific, adc, cell_therapy, gene_therapy,
  oligonucleotide, other, unspecified. A CAR-T is cell_therapy, not antibody, even though its receptor is
  antibody-derived.
- Do not guess. If you are not confident about an item, leave that list empty.
- Only list drugs you know act through this mechanism; generic names or company codes only.
- Keep each list short (at most 8 items) and avoid duplicates.
"""

_TARGET_INSTRUCTION = (
    "Return the human molecular targets as official HGNC gene symbols (e.g. IL17A, not "
    '"interleukin-17" or "IL-17"). If the mechanism names a family, list the family members '
    "that are actually drugged by this mechanism. An empty list is better than a guess.\n"
)


def plan(inp: ResearchInput, llm: LLM | None = None) -> SearchPlan:
    given_targets = _clean_symbols(inp.target_symbols or [])[0] or None
    if llm is None:
        return _fallback(inp, given_targets, "no LLM configured")

    schema: type[_LlmPlanBase] = _LlmPlanBase if given_targets is not None else _LlmPlanWithTargets
    try:
        out = llm.generate_json(_prompt(inp, ask_targets=given_targets is None), schema, purpose="planner")
    except LlmError as e:
        return _fallback(inp, given_targets, f"LLM error: {e}")

    notes = ["source: llm"]
    if given_targets is not None:
        targets = given_targets
        notes.append("target_symbols taken from input")
    else:
        targets, rejected = _clean_symbols(getattr(out, "target_symbols", []))
        if rejected:
            notes.append(f"dropped non-symbol targets: {rejected}")
        if not targets:
            notes.append("LLM returned no target symbols")

    return SearchPlan(
        target_symbols=targets,
        action=out.action,
        modality=out.modality,
        disease_terms=_dedupe([inp.indication, *out.disease_terms]),
        drug_names=_dedupe(out.drug_names),
        extra_literature_terms=_dedupe(out.extra_literature_terms),
        notes=notes + ["unverified: targets and drugs must be checked in resolve.py"],
    )


def _prompt(inp: ResearchInput, ask_targets: bool) -> str:
    extra = "\n".join(
        f"{label}: {value}"
        for label, value in [
            ("Modality", inp.modality),
            ("Route", inp.route),
            ("Biomarkers", ", ".join(inp.biomarkers) if inp.biomarkers else None),
        ]
        if value
    )
    return _PROMPT.format(
        indication=inp.indication,
        mechanism=inp.mechanism,
        extra=extra,
        target_instruction=_TARGET_INSTRUCTION if ask_targets else "",
    )


def _fallback(inp: ResearchInput, given_targets: list[str] | None, reason: str) -> SearchPlan:
    term, action = strip_action_words(inp.mechanism)
    notes = [f"source: fallback ({reason})"]
    if given_targets is not None:
        notes.append("target_symbols taken from input")
    if term:
        notes.append(f"mechanism search term: {term!r}")
    return SearchPlan(
        target_symbols=given_targets or [],
        action=action,
        modality=modality_from_text(inp.modality, inp.mechanism),
        disease_terms=[inp.indication],
        drug_names=[],
        extra_literature_terms=[term] if term else [],
        notes=notes,
    )


def strip_action_words(mechanism: str) -> tuple[str, Action | None]:
    """'IL-17 inhibition' -> ('IL-17', 'inhibition'); 'anti-CD19 CAR-T' -> ('CD19', None)."""
    text = re.sub(r"\banti[- ]", " ", mechanism, flags=re.I)
    action = None
    for pattern, normalized in _ACTION_WORDS:
        text, n = re.subn(rf"\b(?:{pattern})\b", " ", text, flags=re.I)
        if n and action is None and normalized:
            action = normalized
    return " ".join(text.split()).strip(" -,/"), action


def modality_from_text(*texts: str | None) -> Modality:
    """First modality named in `texts` (ResearchInput.modality first, then the mechanism); 'unspecified' if none."""
    for text in texts:
        for pattern, modality in _MODALITY_WORDS:
            if text and re.search(rf"\b(?:{pattern})\b", text, flags=re.I):
                return modality
    return "unspecified"


def _clean_symbols(symbols: list[str]) -> tuple[list[str], list[str]]:
    ok, rejected = [], []
    for s in symbols:
        s = s.strip().upper()
        if _SYMBOL_RE.match(s):
            ok.append(s)
        elif s:
            rejected.append(s)
    return _dedupe(ok), rejected


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        item = item.strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out
