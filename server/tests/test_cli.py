from researcher.schema import Bundle, Subject
from researcher import pipeline
from researcher.__main__ import main


def test_cli_writes_valid_bundle(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(
        pipeline, "resolve", lambda inp, plan, statuses, **kw: Subject(indication=inp.indication, mechanism=inp.mechanism)
    )
    monkeypatch.setattr(pipeline, "default_connectors", lambda http: [])
    assert main(["Crohn's disease", "IL-17 inhibition", "--no-llm"]) == 0
    path = capsys.readouterr().out.strip()
    bundle = Bundle.model_validate_json(open(path).read())
    assert bundle.subject.indication == "Crohn's disease"
    assert bundle.evidence == []
    assert bundle.plan is not None and bundle.llm_calls == []
    assert path.endswith(f"{bundle.run_id}/researcher_bundle.json")
