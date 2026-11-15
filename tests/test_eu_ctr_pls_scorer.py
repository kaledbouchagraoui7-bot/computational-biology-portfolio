"""Unit tests for EU CTR 536/2014 Plain-Language Summary Scorer."""

from __future__ import annotations

from pathlib import Path
from src.eu_ctr_pls_scorer import (
    compute_readability,
    count_syllables,
    find_unexplained_jargon,
    main,
    score_pls,
)

COMPLETE_PLS = """
Trial NCT03719066 was sponsored by Example Clinical Research.
The purpose of this study was to find out if the new medicine helped adults aged 18 to 65.
Participants received either the study medicine or a placebo group treatment.
The main results showed improved recovery compared with placebo.
Few unwanted side effects or adverse reactions were reported.
Thank you to all patients who joined. For further information, contact the sponsor.
"""


def test_syllable_counter_and_readability() -> None:
    assert count_syllables("cat") == 1
    assert count_syllables("medicine") >= 2
    metrics = compute_readability(
        "Participants received one medicine tablet daily. Doctors monitored safety outcomes carefully."
    )
    assert metrics.words == 11
    assert metrics.sentences == 2
    assert metrics.flesch_kincaid_grade > 0


def test_annex_v_full_coverage_and_sha() -> None:
    report = score_pls(COMPLETE_PLS)
    assert report.annex_v_coverage_pct == 100.0
    assert report.annex_v_missing == []
    assert len(report.source_sha256) == 64
    md = report.to_markdown()
    assert "Annex V Coverage" in md


def test_jargon_flagging_skips_inline_explanation() -> None:
    raw = "This randomized double-blind study measured efficacy and dyspnea."
    flagged_terms = {item["term"] for item in find_unexplained_jargon(raw)}
    assert "randomized" in flagged_terms
    assert "double-blind" in flagged_terms
    assert "efficacy" in flagged_terms
    assert "dyspnea" in flagged_terms

    explained = "This randomized (assigned by chance) study checked efficacy (how well it worked)."
    flagged_explained = {item["term"] for item in find_unexplained_jargon(explained)}
    assert "randomized" not in flagged_explained
    assert "efficacy" not in flagged_explained


def test_cli_json_output(tmp_path: Path) -> None:
    in_file = tmp_path / "pls.md"
    out_file = tmp_path / "report.json"
    in_file.write_text(COMPLETE_PLS, encoding="utf-8")
    rc = main([str(in_file), "--format", "json", "--out", str(out_file)])
    assert rc == 0
    assert '"annex_v_coverage_pct": 100.0' in out_file.read_text(encoding="utf-8")
