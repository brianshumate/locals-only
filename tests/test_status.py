"""`eval status` read-only introspection."""

from eval_pipeline import status as st


def _settings_for(db_path):
    from eval_pipeline.config import load_settings
    s = load_settings()
    # db_path resolves as PROJECT_ROOT / database; an absolute path overrides.
    s.database = str(db_path)
    return s


def test_fmt_dur():
    assert st._fmt_dur(5) == "5s"
    assert st._fmt_dur(75) == "1m 15s"
    assert st._fmt_dur(3720) == "1h 2m"


def test_status_report_smoke(seeded_db, monkeypatch):
    """Runs read-only against a seeded db, reports doc count and ETA."""
    db, doc_a, _ = seeded_db
    # A config judge id so the per-judge/throughput section has real data.
    db.upsert_judge("judge-gemma-4-26b", "google/gemma-4-26b-a4b-qat")
    db.record_judgment(doc_a, "judge-gemma-4-26b", "style-guide", "1", 8.0,
                       0.9, [], {"score": 8.0}, 60.0)

    monkeypatch.setattr(st, "_server_reachable", lambda url: True)
    report = st.status_report(settings=_settings_for(db.path))

    assert "Eval status" in report
    assert "Documents: 2" in report
    assert "Model server" in report


def test_status_report_missing_db(tmp_path):
    report = st.status_report(settings=_settings_for(tmp_path / "nope.sqlite"))
    assert "nothing has run yet" in report
