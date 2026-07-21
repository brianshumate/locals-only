"""Report generation + calibration form parsing."""

from eval_pipeline.calibrate import DOC_ID_RE, FORM_TEMPLATE, SCORE_RE


def test_form_roundtrip_parsing():
    form = FORM_TEMPLATE.format(doc_id=7, prompt_id="p1", author_id="a",
                                path="x.md")
    filled = form.replace("- style-guide:", "- style-guide: 8").replace(
        "- factuality:", "- factuality: 6.5")
    assert int(DOC_ID_RE.search(filled).group(1)) == 7
    scores = dict(SCORE_RE.findall(filled))
    assert scores == {"style-guide": "8", "factuality": "6.5"}


def test_write_reports(seeded_db, tmp_path, monkeypatch):
    db, doc_a, doc_b = seeded_db
    db.record_judgment(doc_a, "judge-1", "style-guide", "1.0", 9.0, 0.9, [], {}, 1)
    db.record_judgment(doc_b, "judge-1", "style-guide", "1.0", 4.0, 0.9, [], {}, 1)
    db.record_det_result(doc_a, "vale", True, 10.0, [])
    db.record_comparison("p1", doc_a, doc_b, "judge-1", "pairwise-compare", "a", 0.9, False)
    db.record_comparison("p1", doc_a, doc_b, "judge-1", "pairwise-compare", "a", 0.9, True)
    db.close()

    from eval_pipeline.config import Settings
    from eval_pipeline import report as report_mod

    settings = Settings(database=str(db.path), runs_dir=str(tmp_path / "runs"),
                        reports_dir=str(tmp_path / "reports"))
    # settings paths are PROJECT_ROOT-relative; patch properties directly.
    monkeypatch.setattr(Settings, "db_path", property(lambda self: db.path))
    monkeypatch.setattr(Settings, "reports_path",
                        property(lambda self: tmp_path / "reports"))
    written = report_mod.write_reports(settings)
    assert len(written) == 6
    leaderboard = (tmp_path / "reports" / "leaderboard.html").read_text()
    assert "author-a" in leaderboard
    dashboard = (tmp_path / "reports" / "dashboard.html").read_text()
    # model names, ranking basis, and stat tiles present
    assert "model-a" in dashboard and "model-b" in dashboard
    assert "Bradley–Terry" in dashboard
    assert "documents generated" in dashboard
    # author-a (score 9) ranks above author-b (score 4)
    assert dashboard.index(">author-a<") < dashboard.index(">author-b<")


def test_dashboard_breaks_out_environments(seeded_db, tmp_path, monkeypatch):
    db, doc_a, doc_b = seeded_db
    env = db.upsert_environment({
        "hostname": "srv", "os": "Linux", "os_version": "6", "arch": "x86_64",
        "cpu": "x", "gpu": "RTX 3090", "backend": "llamacpp",
        "backend_version": "b1"})
    doc_c = db.register_document("p1", "author-a", "runs/p1/a2.md", "hash-c",
                                 gen_time_s=10.0, tokens=100,
                                 environment_id=env)
    db.record_judgment(doc_a, "judge-1", "style-guide", "1.0", 9.0, 0.9, [], {}, 1)
    db.record_judgment(doc_c, "judge-1", "style-guide", "1.0", 5.0, 0.9, [], {}, 1)
    db.close()

    from eval_pipeline.config import Settings
    from eval_pipeline import report as report_mod

    settings = Settings(database=str(db.path), runs_dir=str(tmp_path / "runs"),
                        reports_dir=str(tmp_path / "reports"))
    monkeypatch.setattr(Settings, "db_path", property(lambda self: db.path))
    monkeypatch.setattr(Settings, "reports_path",
                        property(lambda self: tmp_path / "reports"))
    report_mod.write_reports(settings)
    dashboard = (tmp_path / "reports" / "dashboard.html").read_text()
    # one section per environment plus a flagged pooled table
    assert "srv · Linux/x86_64 · llamacpp" in dashboard
    assert "unrecorded environment" in dashboard
    assert "All environments (pooled)" in dashboard
    # per-env judge means differ from the pooled mean (9 vs 5 vs 7)
    assert ">5.00<" in dashboard and ">9.00<" in dashboard and ">7.00<" in dashboard
    criteria = (tmp_path / "reports" / "criteria.html").read_text()
    assert "srv · Linux/x86_64 · llamacpp" in criteria


def _rows(*author_ids):
    return [{"author_id": a, "model": "m", "slot": "--s1", "v": 5.0,
             "tt": {"v": "5"}} for a in author_ids]


def test_label_gutter_grows_for_long_author_ids():
    """SVG neither measures nor reflows text, so the gutter is sized before
    rendering — a fixed width clipped long ids off the left of the viewBox."""
    from eval_pipeline.report import LABEL_W_MIN, _label_gutter

    short = _label_gutter(_rows("lfm2.5-8b", "gemma-4-26b"))
    long = _label_gutter(_rows("lfm2.5-8b", "nemotron-3-nano-4b"))
    assert short == LABEL_W_MIN  # short names sit at the floor
    assert long > short


def test_long_label_fits_inside_the_viewbox():
    """The whole point: the longest label must start at x >= 0."""
    from eval_pipeline.report import LABEL_PAD, _label_gutter, _text_w

    name = "nemotron-3-nano-4b"
    gutter = _label_gutter(_rows(name))
    # labels are right-anchored at gutter - LABEL_PAD and run leftwards
    assert gutter - LABEL_PAD - _text_w(name) >= 0


def test_gutter_is_clamped_and_overlong_labels_truncate():
    """Past the clamp the gutter stops eating plot width; the name truncates
    but stays reachable as a <title>, so identity is never color/ellipsis
    alone."""
    from eval_pipeline.report import LABEL_W_MAX, _bar_chart, _label_gutter

    name = "an-extremely-long-author-identifier-well-past-the-clamp"
    assert _label_gutter(_rows(name)) == LABEL_W_MAX
    svg = _bar_chart(_rows(name), "v", 10.0)
    assert "…" in svg
    assert f"<title>{name}</title>" in svg


def test_label_text_is_escaped():
    from eval_pipeline.report import _bar_chart

    svg = _bar_chart(_rows("a<b>&c"), "v", 10.0)
    assert "a&lt;b&gt;&amp;c" in svg
