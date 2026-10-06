"""Check that every number in model-written text occurs in the input.

Numbers are extracted the same way from the input JSON (all numeric values and
every number inside string values) and from the model's text. A number in the
text is accepted when its value, or its value scaled by a suffix such as
"M"/"million" or "%", matches a value derived from the input.

Values derived from the input that the model is shown (evidence counts per
claim, confidences as percentages) are also accepted.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_SCALE = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mm": 1e6, "mn": 1e6, "million": 1e6, "millions": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9, "billions": 1e9,
}

# A number, optionally with thousands separators and decimals, then an
# optional magnitude suffix. Digits glued to letters (IL23R, R381Q) are still
# extracted, identically on both sides.
_NUM = re.compile(
    r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?"
    r"(?:\s?(k|thousand|mm|mn|m|million|millions|bn|b|billion|billions)\b)?(%)?",
    re.IGNORECASE,
)


def _key(v: float) -> float:
    return round(v, 6)


def extract_numbers(text: str) -> list[tuple[str, set[float]]]:
    """Return (literal, candidate values) for every number in ``text``."""
    found = []
    for m in _NUM.finditer(text or ""):
        whole = m.group(1).replace(",", "")
        frac = m.group(2) or ""
        value = float(whole + frac)
        cands = {_key(value)}
        suffix = (m.group(3) or "").lower()
        if suffix:
            cands.add(_key(value * _SCALE[suffix]))
        found.append((m.group(0).strip(), cands))
    return found


def _walk(obj: Any) -> Iterable[Any]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)
    else:
        yield obj


def allowed_values(data: Any, extra: Iterable[float] = ()) -> set[float]:
    """All numeric values that appear in the input, in several equivalent forms."""
    values: set[float] = set()

    def add(v: float) -> None:
        values.add(_key(v))
        # Fractions are shown as percentages; percentages may be cited as fractions.
        if 0 <= v <= 1:
            values.add(_key(v * 100))
            values.add(_key(round(v * 100)))
        # Large amounts may be cited in millions / billions / thousands.
        for scale in (1e3, 1e6, 1e9):
            if abs(v) >= scale:
                values.add(_key(v / scale))

    for leaf in _walk(data):
        if isinstance(leaf, bool) or leaf is None:
            continue
        if isinstance(leaf, (int, float)):
            add(float(leaf))
        elif isinstance(leaf, str):
            for _, cands in extract_numbers(leaf):
                for c in cands:
                    add(c)
    for v in extra:
        add(float(v))
    return values


def unverified_numbers(text: str, allowed: set[float]) -> list[str]:
    """Literals in ``text`` whose value is not among ``allowed``."""
    bad = []
    for literal, cands in extract_numbers(text):
        if not cands & allowed:
            bad.append(literal)
    return bad
