"""End-to-end app: fixture -> analytics (fake model) -> report.json, via the pipeline and the HTTP API."""
import asyncio
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

import app.pipeline as pipeline
import app.server as server
import evidence_bundle.paths as paths
from app.pipeline import RunRequest, run_pipeline

# Fields of `Report` in web/src/types.ts
REPORT_KEYS = {"id", "created_at", "is_mock", "previous_run_id", "input", "recommendation", "confidence", "summary",
               "sections", "risks", "unknowns", "diligence_questions", "capital", "evidence", "traces"}


@pytest.fixture(autouse=True)
def tmp_runs(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    for mod in (paths, pipeline, server):
        monkeypatch.setattr(mod, "RUNS_DIR", runs)
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("LLM_CACHE", "0")
    return runs


def _run(**kw):
    req = RunRequest(indication="Crohn's disease", mechanism="IL-17 inhibition", source="fixture", **kw)
    return asyncio.run(run_pipeline(req))


def test_demo_flip_and_previous_run_link(tmp_runs):
    early = _run(evidence_cutoff=date(2011, 6, 30))
    now = _run()
    assert early.report["recommendation"] == "conditional"
    assert now.report["recommendation"] == "do_not_invest"
    assert now.report["previous_run_id"] == early.run_id  # drives the UI "What changed" tab
    for name in ("researcher_bundle.json", "evidence_bundle.json", "analysis_result.json", "report.json", "trace.jsonl"):
        assert (now.dir / name).exists(), name


def test_report_matches_web_contract():
    r = _run().report
    assert REPORT_KEYS <= set(r)
    assert r["recommendation"] in {"invest", "conditional", "do_not_invest"}
    ids = {e["id"] for e in r["evidence"]}
    cited = {ref["evidence_id"] for s in r["sections"] for c in s["claims"] for ref in c["evidence"]}
    assert cited and cited <= ids, "every cited evidence id must be in report.evidence (traceability)"
    assert all(e["id"] not in e.get("related_evidence_ids", []) for e in r["evidence"])
    assert all("[E" not in c["text"] for s in r["sections"] for c in s["claims"])
    json.dumps(r)


def test_pick_fixture_by_input_and_cutoff():
    assert pipeline.pick_fixture(RunRequest("Crohn's disease", "IL-17 inhibition", evidence_cutoff=date(2011, 1, 1))
                                 ).name == "il17_crohn_2011.json"
    assert pipeline.pick_fixture(RunRequest("Hypercholesterolemia", "PCSK9 inhibition")).name.startswith("pcsk9")
    with pytest.raises(ValueError):
        pipeline.pick_fixture(RunRequest("zzz", "qqq"))


def test_http_api_run_events_report():
    with TestClient(server.app) as client:
        assert client.get("/api/health").json()["ok"]
        rid = client.post("/api/runs", json={"input": {"indication": "Crohn's disease", "mechanism": "IL-17 inhibition"},
                                              "options": {"source": "fixture"}}).json()["run_id"]
        with client.stream("GET", f"/api/runs/{rid}/events") as resp:
            body = "".join(resp.iter_text())
        assert "event: end" in body and '"step": "pipeline", "status": "done"' in body
        report = client.get(f"/api/runs/{rid}/report").json()
        assert report["id"] == rid and report["recommendation"] == "do_not_invest"
        assert client.get("/api/runs").json()[0]["id"] == rid
        info = client.get(f"/api/runs/{rid}").json()  # the home screen reopens a run from this
        assert info["done"] and info["input"]["indication"] == "Crohn's disease"
        assert info["result"]["recommendation"] == "do_not_invest" and info["options"]["source"] == "fixture"
        assert client.get("/api/runs/..%2F..%2Fetc").status_code == 404
        assert client.get("/api/runs/does-not-exist/report").status_code == 404


# ---------------------------------------------------------------- downloads, sources, web contract

from pathlib import Path  # noqa: E402

from app.report_schema import validate_report  # noqa: E402

WEB_FIXTURES = Path(__file__).resolve().parents[2] / "web" / "src" / "fixtures"


def test_report_validates_against_web_types_mirror():
    validate_report(_run().report)
    for f in sorted(WEB_FIXTURES.glob("*.json")):  # the frontend's own fixtures follow the same contract
        validate_report(json.loads(f.read_text(encoding="utf-8")))


def test_markdown_cites_primary_studies():
    out = _run()
    md = (out.dir / "report.md").read_text(encoding="utf-8")
    assert "## Sources" in md and "ClinicalTrials.gov · NCT" in md
    assert "Derived (cross-source check)" not in md  # derived items are expanded to the trials behind them
    assert "[1]" in md.split("## Sources")[0]  # claims carry numbered references


def test_claim_evidence_includes_records_behind_derived_items():
    r = _run().report
    by_id = {e["id"]: e for e in r["evidence"]}
    refs = [ref["evidence_id"] for s in r["sections"] for c in s["claims"] for ref in c["evidence"]]
    assert any(by_id[i]["source"] == "clinicaltrials" for i in refs)


def test_download_endpoints():
    with TestClient(server.app) as client:
        rid = client.post("/api/runs", json={"input": {"indication": "Crohn's disease", "mechanism": "IL-17 inhibition"},
                                              "options": {"source": "fixture", "pdf": False}}).json()["run_id"]
        with client.stream("GET", f"/api/runs/{rid}/events") as resp:
            "".join(resp.iter_text())
        md = client.get(f"/api/runs/{rid}/report.md")
        assert md.status_code == 200 and md.text.startswith("# IL-17 inhibition")
        z = client.get(f"/api/runs/{rid}/files.zip")
        assert z.status_code == 200 and z.content[:2] == b"PK"
        import io
        import zipfile
        names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
        assert f"{rid}/report.json" in names and f"{rid}/report.md" in names
        pytest.importorskip("writer_agent")
        pdf = client.get(f"/api/runs/{rid}/report.pdf")  # run had no PDF: generated on request
        assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
        assert client.get("/api/runs/nope/report.md").status_code == 404


def test_researcher_bridge_sends_schema_and_retries_invalid_json():
    """Gemini in object JSON mode echoed the input instead of the plan: the schema is in the prompt and one
    corrective retry follows an invalid answer."""
    import asyncio
    import threading

    from pydantic import BaseModel

    from analytics.llm import LLMResponse
    from app.llm_bridge import ResearcherLLM

    class Plan(BaseModel):
        disease_terms: list[str]

    class Stub:
        model = "stub"

        def __init__(self):
            self.requests = []

        async def complete(self, request):
            self.requests.append(request)
            text = '{"indication": "Psoriasis"}' if len(self.requests) == 1 else '{"disease_terms": ["psoriasis"]}'
            return LLMResponse(text=text, model="stub", input_tokens=10, output_tokens=5)

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        stub = Stub()
        bridge = ResearcherLLM(stub, loop, "stub")
        out = bridge.generate_json("Plan the search.", Plan, purpose="planner")
    finally:
        loop.call_soon_threadsafe(loop.stop)
    assert out.disease_terms == ["psoriasis"]
    assert '"disease_terms"' in stub.requests[0].messages[0].content  # schema text in the prompt
    assert len(stub.requests) == 2 and "invalid" in stub.requests[1].messages[-1].content
    assert bridge.calls[-1].ok and bridge.calls[-1].attempts == 2


def test_run_request_normalizes_typographic_characters():
    from app.pipeline import RunRequest
    r = RunRequest(indication="Alzheimer’s  disease", mechanism="BACE1–inhibition", source="fixture",
                   biomarkers=["CSF p‑tau", " "])
    assert r.indication == "Alzheimer's disease" and r.mechanism == "BACE1-inhibition"
    assert r.biomarkers == ["CSF p-tau"]
