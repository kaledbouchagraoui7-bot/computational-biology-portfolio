"""Unit tests for the PRISMA Search-Strategy Yield Sensitivity & Deduplication Scope Validator."""

from __future__ import annotations

import json
from pathlib import Path

from src.prisma_search_yield_validator import (
    deduplicate_exports,
    evaluate_search_strategy_sensitivity,
    main,
    normalize_doi,
    normalize_pmid,
    validate_prisma_search_yield,
)


def test_normalize_doi_and_pmid() -> None:
    assert normalize_doi("https://doi.org/10.1056/NEJMoa2301972") == "10.1056/nejmoa2301972"
    assert normalize_doi("DOI: 10.1016/S0140-6736(24)00123-4") == "10.1016/s0140-6736(24)00123-4"
    assert normalize_pmid("PMID: 38456789") == "38456789"
    assert normalize_pmid("") == ""


def test_deduplicate_exports_cross_db_overlap_and_title_fallback() -> None:
    pubmed = [
        {"pmid": "11111111", "doi": "10.1000/trial1", "title": "GLP-1 Cardiorenal Outcomes Trial A", "year": "2025"},
        {"pmid": "22222222", "doi": "", "title": "SGLT2 Inhibitor Meta-Analysis B", "year": "2024"},
        {"pmid": "11111111", "doi": "https://doi.org/10.1000/TRIAL1", "title": "GLP-1 Cardiorenal Outcomes Trial A", "year": "2025"},
    ]
    epmc = [
        {"pmid": "", "doi": "10.1000/TRIAL1", "title": "GLP-1 Cardiorenal Outcomes Trial A", "year": "2025"},
        {"pmid": "", "doi": "", "title": "SGLT2 inhibitor meta-analysis B!", "year": "2024"},
        {"pmid": "33333333", "doi": "10.1000/trial3", "title": "Novel Renal Endpoint Study C", "year": "2026"},
    ]
    res = deduplicate_exports(pubmed, epmc)
    assert res["total_raw_count"] == 6
    assert res["unique_count"] == 3
    assert res["duplicates_removed"] == 3
    assert res["dedup_yield_pct"] == 50.0
    assert res["cross_db_overlap_count"] == 2
    assert res["europe_pmc_only_count"] == 1
    assert res["pubmed_only_count"] == 0


def test_sensitivity_passes_when_all_benchmarks_captured_and_stable_yield() -> None:
    pubmed = [
        {"pmid": "10001", "doi": "10.1000/a", "title": "Study A", "year": "2025"},
        {"pmid": "10002", "doi": "10.1000/b", "title": "Study B", "year": "2025"},
    ]
    epmc = [
        {"pmid": "10002", "doi": "10.1000/b", "title": "Study B", "year": "2025"},
        {"pmid": "10003", "doi": "10.1000/c", "title": "Study C", "year": "2026"},
    ]
    dedup = deduplicate_exports(pubmed, epmc)
    sens = evaluate_search_strategy_sensitivity(
        dedup,
        benchmark_ids=["10001", "doi:10.1000/b", "PMID:10003"],
        baseline_unique_count=3,
    )
    assert sens["passed"] is True
    assert sens["sensitivity_pct"] == 100.0
    assert sens["benchmark_missed"] == []
    assert sens["scope_drift_flags"] == []


def test_sensitivity_flags_missed_benchmarks_and_yield_drop_drift() -> None:
    pubmed = [{"pmid": "10001", "doi": "10.1000/a", "title": "Study A", "year": "2025"}]
    epmc = [{"pmid": "10001", "doi": "10.1000/a", "title": "Study A", "year": "2025"}]
    dedup = deduplicate_exports(pubmed, epmc)
    sens = evaluate_search_strategy_sensitivity(
        dedup,
        benchmark_ids=["10001", "10002"],
        baseline_unique_count=10,
        min_sensitivity_pct=95.0,
        max_yield_drop_pct=25.0,
    )
    assert sens["passed"] is False
    assert sens["sensitivity_pct"] == 50.0
    assert "pmid:10002" in sens["benchmark_missed"]
    assert "BENCHMARK_RECALL_BELOW_THRESHOLD" in sens["scope_drift_flags"]
    assert "SEVERE_YIELD_DROP_DRIFT" in sens["scope_drift_flags"]


def test_flags_single_source_gap_and_missing_identifiers() -> None:
    pubmed = [
        {"title": "Unindexed Abstract 1", "year": "2025"},
        {"title": "Unindexed Abstract 2", "year": "2025"},
    ]
    dedup = deduplicate_exports(pubmed, [])
    sens = evaluate_search_strategy_sensitivity(dedup)
    assert sens["passed"] is False
    assert "SINGLE_SOURCE_SCOPE_GAP" in sens["scope_drift_flags"]
    assert "HIGH_MISSING_IDENTIFIER_RATE" in sens["scope_drift_flags"]


def test_cli_json_pass_and_fail(tmp_path: Path, capsys) -> None:
    good_file = tmp_path / "good_export.json"
    good_file.write_text(
        json.dumps(
            {
                "pubmed_records": [{"pmid": "12345", "doi": "10.1000/x", "title": "Trial X", "year": "2025"}],
                "europe_pmc_records": [{"pmid": "12345", "doi": "10.1000/x", "title": "Trial X", "year": "2025"}],
                "benchmark_ids": ["12345"],
                "baseline_unique_count": 1,
            }
        ),
        encoding="utf-8",
    )
    assert main([str(good_file)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["passed"] is True

    bad_file = tmp_path / "bad_export.json"
    bad_file.write_text(
        json.dumps(
            {
                "pubmed_records": [{"pmid": "12345", "doi": "10.1000/x", "title": "Trial X", "year": "2025"}],
                "europe_pmc_records": [],
                "benchmark_ids": ["12345", "99999"],
            }
        ),
        encoding="utf-8",
    )
    assert main([str(bad_file)]) == 1
    assert main([str(tmp_path / "missing.json")]) == 2


def test_validate_prisma_search_yield_end_to_end() -> None:
    res = validate_prisma_search_yield(
        {
            "pubmed_records": [{"pmid": "10001", "doi": "10.1000/a", "title": "Trial A", "year": "2025"}],
            "europe_pmc_records": [
                {"pmid": "10001", "doi": "10.1000/a", "title": "Trial A", "year": "2025"},
                {"pmid": "10002", "doi": "10.1000/b", "title": "Trial B", "year": "2026"},
            ],
            "benchmark_ids": ["10001", "10002"],
            "baseline_unique_count": 2,
        }
    )
    assert res["passed"] is True
    assert res["deduplication"]["unique_count"] == 2
    assert res["sensitivity"]["sensitivity_pct"] == 100.0
