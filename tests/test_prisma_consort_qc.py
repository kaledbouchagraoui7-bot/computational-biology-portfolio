"""Unit tests for the PRISMA 2020 manuscript QC checker."""

from __future__ import annotations

import json
from pathlib import Path

from src.prisma_consort_qc import (
    PRISMA_2020_ITEMS,
    check_prisma_2020,
    main,
    validate_manuscript_sections,
)

FULL = " ".join(" ".join(kw[:1]) for _, _, kw in PRISMA_2020_ITEMS)


def test_sections_complete_and_missing() -> None:
    ok = validate_manuscript_sections({k: "x" for k in ("title", "abstract", "methods", "results", "discussion", "funding")})
    assert ok["compliant"] is True
    bad = validate_manuscript_sections({"title": "Draft", "methods": "  "})
    assert bad["compliant"] is False
    assert "methods" in bad["missing_sections"]


def test_item_count_is_27() -> None:
    assert len(PRISMA_2020_ITEMS) == 27


def test_full_text_covers_all_items() -> None:
    report = check_prisma_2020(FULL)
    assert report["items_reported"] == 27
    assert report["coverage_pct"] == 100.0
    assert report["missing"] == []


def test_empty_text_reports_everything_missing() -> None:
    report = check_prisma_2020("")
    assert report["items_reported"] == 0
    assert len(report["missing"]) == 27


def test_partial_text_flags_prospero_and_funding() -> None:
    report = check_prisma_2020("Registered in PROSPERO. Funding: none.")
    assert "24" in report["reported"]
    assert "25" in report["reported"]
    assert "11" not in report["reported"]


def test_cli_pass_and_fail(tmp_path: Path, capsys) -> None:
    good = tmp_path / "good.md"
    good.write_text(FULL, encoding="utf-8")
    assert main([str(good)]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
    bad = tmp_path / "bad.md"
    bad.write_text("nothing relevant", encoding="utf-8")
    assert main([str(bad)]) == 1
    assert main([str(tmp_path / "nope.md")]) == 2
