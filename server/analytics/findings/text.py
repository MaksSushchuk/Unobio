"""Tiny text helpers for deterministic de-duplication."""
from __future__ import annotations

import hashlib
import re

_STOP = {
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is", "are", "be", "this", "that", "it", "its",
    "with", "by", "as", "at", "from", "does", "do", "what", "which", "how", "whether", "can", "will", "would",
}


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 2}


def similar(a: str, b: str, threshold: float = 0.6) -> bool:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return a.strip().lower() == b.strip().lower()
    return len(ta & tb) / len(ta | tb) >= threshold


def short_hash(*parts: str, prefix: str = "") -> str:
    return prefix + hashlib.sha1("|".join(parts).encode()).hexdigest()[:8]
