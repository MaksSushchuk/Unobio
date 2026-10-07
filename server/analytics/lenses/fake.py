"""Offline stand-in for the lens LLMs (LLM_PROVIDER=fake).

NOT analysis: it reads the prompt, finds the evidence ids and KILL SIGNAL lines,
and produces a schema-valid answer that cites them. It exists so the whole
pipeline (orchestrator, verdict, writer, UI) can run end-to-end without a model.
"""
from __future__ import annotations

import re
from typing import Any

from ..llm import FakeLLM, LLMRequest

_ID = re.compile(r"^\[(E\d+|A\d+)\]", re.M)
_KILL = re.compile(r"^\[(E\d+)\] CONFLICT \([^)]*KILL SIGNAL", re.M)
_NOT_SHOWN = re.compile(r"## NOT SHOWN.*", re.S)


def _ids(prompt: str) -> tuple[list[str], list[str]]:
    shown = _NOT_SHOWN.sub("", prompt)
    return _ID.findall(shown), _KILL.findall(shown)


def _base(lens: str, prompt: str) -> dict[str, Any]:
    ids, kills = _ids(prompt)
    first = ids[:3] or ["E1"]
    claims = [{"text": f"Kill-signal conflict {k} shows a same-target program already failed in this indication.",
               "kind": "source_fact", "confidence": 0.9, "evidence": [{"id": k, "stance": "supports"}]} for k in kills]
    claims.append({"text": f"[fake {lens}] Summary claim based on the highest-priority evidence in context.",
                   "kind": "inference", "confidence": 0.5, "evidence": [{"id": i, "stance": "supports"} for i in first]})
    if len(claims) < 2:
        claims.append({"text": f"[fake {lens}] Second claim to satisfy the minimum claim count.",
                       "kind": "inference", "confidence": 0.4, "evidence": [{"id": first[0], "stance": "supports"}]})
    score = 0 if kills else 3
    return {
        "score": score,
        "score_rationale": f"[fake] score {score}: " + ("kill signals present " + ", ".join(f"[{k}]" for k in kills) if kills
                                                         else "no kill signals in context"),
        "claims": claims[:8],
        "counter_evidence": "[fake] counter-evidence not assessed by the offline stand-in.",
        "unknowns": [{"question": "Is the efficacious exposure achievable safely in humans?", "requires": "experiment",
                      "why": "[fake] needs asset-level PK/PD data"}],
        "need_detail": [],
    }


def _science(req: LLMRequest) -> dict[str, Any]:
    prompt = req.messages[1].content
    ans = _base("science", prompt)
    ids, kills = _ids(prompt)
    steps = ["molecular_effect", "human_exposure", "target_engagement", "biological_response", "patient_benefit"]
    ans["causality"] = "contradicted" if kills else "correlative"
    ans["translation_chain"] = [
        {"step": s, "status": ("contradicted" if kills and s == "patient_benefit" else "weak"),
         "evidence": (kills[:1] if kills and s == "patient_benefit" else [])}
        for s in steps
    ]
    return ans


def _clinical(req: LLMRequest) -> dict[str, Any]:
    ans = _base("clinical", req.messages[1].content)
    ans["development_plan"] = {
        "population": "[fake] moderate-to-severe patients refractory to standard of care",
        "primary_endpoint": "[fake] clinical remission at week 12",
        "comparator": "placebo on background therapy", "biomarker_strategy": "[fake] target-engagement biomarker",
        "phase_sequence": "Ph1b -> Ph2 PoC -> Ph3", "analogues": "[fake] see cited same-target trials",
    }
    return ans


def _market(req: LLMRequest) -> dict[str, Any]:
    ans = _base("market", req.messages[1].content)
    ans["market"] = {k: f"[fake] {k.replace('_', ' ')}" for k in
                     ("standard_of_care", "unmet_need", "addressable_patients", "pricing_analogues",
                      "differentiation_required", "scientific_vs_commercial")}
    return ans


def _investment(req: LLMRequest) -> dict[str, Any]:
    ans = _base("investment", req.messages[1].content)
    ans["params"] = {
        "current_stage": "preclinical", "next_milestone": "Phase 2 proof-of-concept readout",
        "trials_to_milestone": [
            {"phase": "PHASE1", "patients_low": 40, "patients_high": 80, "months_low": 9, "months_high": 15},
            {"phase": "PHASE2", "patients_low": 100, "patients_high": 200, "months_low": 18, "months_high": 30},
        ],
        "peak_sales_usd_low": 2e8, "peak_sales_usd_base": 6e8, "peak_sales_usd_high": 1.5e9,
        "exit_options": "[fake] licensing after Ph2 PoC",
    }
    return ans


def fake_lens_llm() -> FakeLLM:
    return FakeLLM(script={"lens:science": _science, "lens:clinical": _clinical,
                           "lens:market": _market, "lens:investment": _investment},
                   default={"note": "no fake script for this agent"})
