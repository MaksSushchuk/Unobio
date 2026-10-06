"""Render a sample report from the fixture without the real model.

    python scripts/render_sample.py [--live] [-o sample_report.pdf]

Without --live, canned model replies (restricted to facts in the fixture) are
used so the layout can be checked offline. With --live, the configured model
endpoint (WRITER_LLM_URL / WRITER_LLM_MODEL) is called.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from writer_agent import WriterAgent, WriterConfig  # noqa: E402

CANNED_SECTIONS = {
    "Target biology & human genetics": {
        "takeaway": "Genetics implicate the IL-23/Th17 axis, but IL-23-independent IL-17A protects the intestinal epithelial barrier, so blocking IL-17A is not equivalent to blocking the pathway.",
        "synthesis": "Human genetic support is strong at the level of IL23R and other Th17-pathway loci, and IL-17A is elevated in inflamed mucosa. However, the same section reports that IL-17A from gut γδ T cells maintains barrier integrity and that its neutralization worsens permeability in mouse colitis. Elevated expression therefore does not establish IL-17A as a pathogenic driver. The Open Targets association is moderate and driven by literature and expression rather than genetics.",
    },
    "Direct evidence: IL-17 blockade in Crohn's disease": {
        "takeaway": "Both randomized trials of IL-17 pathway blockade in Crohn's disease failed, with signals of disease worsening.",
        "synthesis": "The secukinumab trial in 59 patients did not meet its primary endpoint and was stopped for futility, with more adverse events and disease worsening than placebo. The brodalumab trial was terminated early for disproportionate worsening. By contrast, IL-23 p19 inhibitors succeeded in phase 3, which shows the pathway can be targeted upstream of IL-17 but does not support IL-17 blockade itself.",
    },
    "Safety": {
        "takeaway": "Class labels warn about new-onset or exacerbated IBD, and the asset has no data in patients with active intestinal inflammation.",
        "synthesis": "The label warning is direct regulatory evidence against use in this indication. The asset's own Phase 1 data come from healthy volunteers only and cite no source, so the safety profile in Crohn's disease is unknown.",
    },
    "Market & positioning": {
        "takeaway": "The market is served by several biologic classes, and an IL-17 agent would need clear differentiation to compete with IL-23 p19 inhibitors.",
        "synthesis": "Evidence here is descriptive and mostly analyst inference; no sizing, pricing or share data are provided in the input.",
    },
    "Skeptic review": {
        "takeaway": "The Skeptic finds that genetic support for the axis does not transfer to IL-17A blockade, given negative direct randomized evidence.",
        "synthesis": "The Skeptic's challenges target the genetic and expression claims and are backed by the two failed trials and the barrier biology. It also notes that no mechanism has been presented to avoid barrier disruption, which remains an open gap rather than a sourced finding.",
    },
}

CANNED_FINAL = {
    "rationale": "Genetics implicate the IL-23/Th17 axis, but direct interventional evidence contradicts IL-17 blockade as a Crohn's therapy. Randomized trials of secukinumab (anti-IL-17A) and brodalumab (anti-IL-17RA) in Crohn's disease showed no efficacy and higher rates of disease worsening than placebo, and IL-17 inhibitor labels now warn about new-onset or exacerbated IBD. Mechanistic work indicates IL-17A protects the intestinal epithelial barrier, which explains the divergence from IL-23 inhibition. Absent a differentiated mechanism, the thesis is not investable.",
    "risks": [
        {"text": "Disease exacerbation in Crohn's patients, consistent with both failed trials and the class label warning.", "claim_ids": ["cli-2", "cli-3", "saf-1"]},
        {"text": "Epithelial barrier disruption from IL-17A neutralization.", "claim_ids": ["bio-4"]},
        {"text": "Competitive disadvantage against IL-23 p19 inhibitors with positive phase 3 data.", "claim_ids": ["cli-4", "com-2"]},
        {"text": "Biomarker strategy based on mucosal IL17A expression is not validated.", "claim_ids": ["skp-2"]},
    ],
    "critical_unknowns": [
        {"text": "Whether the asset differs mechanistically from secukinumab and brodalumab in a way that spares the barrier.", "claim_ids": ["skp-3"]},
        {"text": "Safety of the asset in patients with active intestinal inflammation.", "claim_ids": ["saf-2"]},
        {"text": "Whether any patient subgroup benefits from IL-17 blockade.", "claim_ids": []},
        {"text": "Market size and pricing for the indication; not provided in the input.", "claim_ids": []},
    ],
    "diligence_questions": [
        {"text": "What preclinical data show the asset does not worsen barrier permeability?", "claim_ids": ["bio-4"]},
        {"text": "How does the company interpret the secukinumab and brodalumab results?", "claim_ids": ["cli-1", "cli-3"]},
        {"text": "Has a regulator commented on the class IBD warning for this indication?", "claim_ids": ["saf-1"]},
        {"text": "What evidence supports mucosal IL17A expression as a selection biomarker?", "claim_ids": ["bio-3"]},
        {"text": "What differentiation versus IL-23 p19 inhibitors is claimed, and on what data?", "claim_ids": ["com-2"]},
    ],
}


class CannedClient:
    def chat(self, messages):
        user = messages[1]["content"]
        if '"rationale"' in user:
            return json.dumps(CANNED_FINAL)
        for title, reply in CANNED_SECTIONS.items():
            if f'"title": {json.dumps(title, ensure_ascii=False)}' in user:
                return "```json\n" + json.dumps(reply, ensure_ascii=False) + "\n```"
        return "{}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(ROOT / "fixtures" / "il17_crohns.json"))
    ap.add_argument("-o", "--output", default=str(ROOT / "sample_report.pdf"))
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    agent = WriterAgent(WriterConfig(), client=None if args.live else CannedClient())
    result = agent.run(args.input, args.output)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 1 if result.status == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
