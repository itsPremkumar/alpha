"""Tests for feedback categories and the feedback-to-eval exporter."""

from __future__ import annotations

import json

import pytest

from alpha.persistence.feedback.model import FEEDBACK_CATEGORIES, validate_category
from scripts.benchmark.feedback_export.export import (
    EXPORT_PROTOCOL_ID,
    SCHEMA_VERSION,
    build_eval_case,
    export_feedback_cases,
    main,
    validate_manifest,
)


def _row(**kwargs):
    row = {
        "feedback_id": "fb-1",
        "run_id": "r1",
        "thread_id": "t1",
        "message_id": None,
        "rating": -1,
        "comment": "The answer cited a file that does not exist.",
        "category": "grounding",
        "created_at": "2026-09-13T00:00:00+00:00",
    }
    row.update(kwargs)
    return row


class TestValidateCategory:
    def test_none_stays_none(self):
        assert validate_category(None) is None

    def test_known_categories_pass(self):
        for category in FEEDBACK_CATEGORIES:
            assert validate_category(category) == category

    def test_unknown_category_raises(self):
        with pytest.raises(ValueError):
            validate_category("vibes")


class TestBuildEvalCase:
    def test_negative_case_carries_comment_as_expected(self):
        case = build_eval_case(_row(), [{"role": "user", "content": "Summarize x"}])
        assert case["name"] == "feedback-fb-1"
        assert case["category"] == "grounding"
        assert case["rating"] == -1
        assert case["history"] == [{"role": "user", "content": "Summarize x"}]
        assert "does not exist" in (case["expected"] or "")
        assert case["source"]["run_id"] == "r1"

    def test_positive_case_has_no_expected(self):
        case = build_eval_case(_row(rating=1, comment="Great answer"), None)
        assert case["expected"] is None
        assert case["history"] == []

    def test_missing_category_becomes_uncategorized(self):
        case = build_eval_case(_row(category=None), None)
        assert case["category"] == "uncategorized"

    def test_invalid_rating_raises(self):
        with pytest.raises(ValueError):
            build_eval_case(_row(rating=0), None)

    def test_invalid_history_role_raises(self):
        with pytest.raises(ValueError):
            build_eval_case(_row(), [{"role": "system", "content": "x"}])


class TestExportManifest:
    def test_filters_and_manifest_shape(self):
        rows = [
            _row(feedback_id="fb-1", rating=-1, category="grounding"),
            _row(feedback_id="fb-2", rating=1, category="format"),
            _row(feedback_id="fb-3", rating=-1, category="latency"),
        ]
        manifest = export_feedback_cases(rows, {"r1": [{"role": "user", "content": "hi"}]}, categories=["grounding"], ratings=[-1])
        assert manifest["schema_version"] == SCHEMA_VERSION
        assert manifest["protocol_id"] == EXPORT_PROTOCOL_ID
        assert manifest["case_count"] == 1
        assert manifest["cases"][0]["name"] == "feedback-fb-1"
        assert validate_manifest(manifest) == []

    def test_validate_manifest_catches_drift(self):
        manifest = export_feedback_cases([_row()], None)
        manifest["case_count"] = 99
        assert any("case_count" in error for error in validate_manifest(manifest))
        bad = dict(manifest)
        bad["cases"] = [{"name": "x", "rating": 0, "category": "nope"}]
        bad["case_count"] = 1
        errors = validate_manifest(bad)
        assert any("rating" in error for error in errors)
        assert any("category" in error for error in errors)


class TestExportCli:
    def test_cli_writes_manifest(self, tmp_path, capsys):
        rows_path = tmp_path / "rows.json"
        rows_path.write_text(json.dumps([_row(), _row(feedback_id="fb-2", rating=1, category="format")]), encoding="utf-8")
        out_dir = tmp_path / "out"
        assert main(["--input-json", str(rows_path), "--output-dir", str(out_dir)]) == 0
        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["case_count"] == 2
        assert validate_manifest(manifest) == []
        assert "exported 2 cases" in capsys.readouterr().out

    def test_cli_filters_and_rejects_bad_input(self, tmp_path):
        rows_path = tmp_path / "rows.json"
        rows_path.write_text(json.dumps([_row()]), encoding="utf-8")
        out_dir = tmp_path / "out"
        assert main(["--input-json", str(rows_path), "--output-dir", str(out_dir), "--category", "format"]) == 0
        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["case_count"] == 0
        bad_path = tmp_path / "bad.json"
        bad_path.write_text(json.dumps([_row(rating=0)]), encoding="utf-8")
        assert main(["--input-json", str(bad_path), "--output-dir", str(out_dir)]) == 2
