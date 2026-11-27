"""Unit tests for EU CTR 536/2014 Plain-Language Summary Scorer."""

from __future__ import annotations

from pathlib import Path

from src.eu_ctr_pls_scorer import (
    LIMITATION_NOTE,
    RULE_CATALOG_VERSION,
    check_numerical_consistency,
    compare_translation_alignment,
    compute_readability,
    count_syllables,
    find_unexplained_jargon,
    main,
    score_plain_language_summary,
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


def test_french_annex_v_full_coverage() -> None:
    fr_pls = (
        "Essai NCT03719066 conduit par le promoteur Example Recherche (contact).\n"
        "L'objectif principal de cette étude concernait des patients adultes.\n"
        "Chaque groupe a reçu le médicament étudié ou un placebo.\n"
        "Les résultats ont montré une amélioration clinique et peu d'effets secondaires.\n"
        "Nous remercions les volontaires. Pour plus d'informations, contactez le centre.\n"
    )
    report = score_pls(fr_pls)
    assert report.annex_v_coverage_pct == 100.0
    assert report.annex_v_missing == []


def test_french_jargon_flagging_and_inline_explanation() -> None:
    raw_fr = "Cet essai randomisé en double aveugle a suivi chaque effet indésirable."
    flagged = {item["term"] for item in find_unexplained_jargon(raw_fr)}
    assert "randomisé" in flagged
    assert "double aveugle" in flagged
    assert "effet indésirable" in flagged

    explained_fr = "Cet essai randomisé (réparti par tirage au sort) en double aveugle (ni médecin ni patient) est terminé."
    flagged_exp = {item["term"] for item in find_unexplained_jargon(explained_fr)}
    assert "randomisé" not in flagged_exp
    assert "double aveugle" not in flagged_exp


def test_structured_rule_traceability_and_promotional_claims() -> None:
    promo_pls = COMPLETE_PLS + "\nThis miracle guaranteed cure is risk-free for every patient.\n"
    res = score_plain_language_summary(promo_pls)
    assert res["rule_catalog_version"] == RULE_CATALOG_VERSION
    assert res["limitation_note"] == LIMITATION_NOTE
    assert res["compliance_passed"] is False
    assert len(res["promotional_claims_detected"]) >= 2
    rule_ids = {r["rule_id"] for r in res["rule_evaluations"]}
    assert {
        "EU_CTR_ANNEX_V_REQUIRED_SECTIONS",
        "EU_CTR_ANNEX_V_PROMOTIONAL_LANGUAGE",
        "EU_CTR_ANNEX_V_READABILITY",
        "EU_CTR_ANNEX_V_JARGON",
    } <= rule_ids


def test_numerical_consistency_and_bilingual_alignment() -> None:
    bad_nums = "A total of 120 patients joined: Group A: 50 patients and Group B: 50 patients. Improvement was 105%."
    num_res = check_numerical_consistency(bad_nums)
    assert num_res["consistent"] is False
    assert len(num_res["findings"]) == 2

    en_pls = COMPLETE_PLS
    fr_pls = (
        "Essai NCT03719066 conduit par le promoteur Example Recherche (contact).\n"
        "L'objectif principal de cette étude concernait des patients adultes.\n"
        "Chaque groupe a reçu le médicament étudié ou un placebo.\n"
        "Les résultats ont montré une amélioration clinique et peu d'effets secondaires.\n"
        "Nous remercions les volontaires. Pour plus d'informations, contactez le centre.\n"
    )
    align = compare_translation_alignment(en_pls, fr_pls)
    assert align["aligned"] is True
    assert align["en_coverage_pct"] == 100.0
    assert align["fr_coverage_pct"] == 100.0

