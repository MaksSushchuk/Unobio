$context

## Your task — $title

Answer these questions using ONLY the evidence above:
$questions

Then return the JSON object:
- `score` (0–5) using the rubric from your instructions, and `score_rationale` that cites ids like [E3].
- 2–8 `claims`, each citing 1–6 evidence ids with a `stance` (supports / contradicts the claim).
  Every evidence marked **KILL SIGNAL** above must be cited by at least one claim.
- `counter_evidence`: the strongest evidence against your conclusion and why it does or does not change the score.
- `unknowns`: what cannot be answered from public evidence, with the kind of data it `requires`.
- `need_detail`: ids listed as titles only or under "NOT SHOWN" that you need in full to answer (max 8), otherwise [].
- the lens-specific fields required by the answer format.
