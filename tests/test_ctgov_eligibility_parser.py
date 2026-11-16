"""Unit tests for ClinicalTrials.gov API v2 Eligibility Criteria Parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ctgov_eligibility_parser import (
    fetch_study_json,
    main,
    parse_study_json,
    split_criteria_block,
)

FIXTURE_PAYLOAD = {
    "protocolSection": {
        "identificationModule": {
            "nctId": "NCT03719066",
            "briefTitle": "CardioMetabolic Biomarker Trial",
        },
        "statusModule": {"overallStatus": "COMPLETED"},
        "eligibilityModule": {
            "sex": "ALL",
            "minimumAge": "18 Years",
            "maximumAge": "75 Years",
            "healthyVolunteers": False,
            "eligibilityCriteria": (
                "Inclusion Criteria:\n"
                "- Adults aged 18 to 75 years\n"
                "- Written informed consent\n\n"
                "Exclusion Criteria:\n"
                "- Pregnancy or lactation\n"
                "- Severe renal impairment"
            ),
        },
        "armsInterventionsModule": {
            "armGroups": [
                {
                    "label": "Active Arm",
                    "type": "EXPERIMENTAL",
                    "description": "Oral tablet 10 mg once daily",
                },
                {
                    "label": "Placebo Arm",
                    "type": "PLACEBO_COMPARATOR",
                    "description": "Matched oral placebo once daily",
                },
            ],
            "interventions": [
                {"name": "Drug X 10mg", "type": "DRUG", "armGroupLabels": ["Active Arm"]},
                {"name": "Matched Placebo", "type": "DRUG", "armGroupLabels": ["Placebo Arm"]},
            ],
        },
    }
}


def test_split_criteria_block() -> None:
    inc, exc = split_criteria_block(
        FIXTURE_PAYLOAD["protocolSection"]["eligibilityModule"]["eligibilityCriteria"]
    )
    assert inc == ["Adults aged 18 to 75 years", "Written informed consent"]
    assert exc == ["Pregnancy or lactation", "Severe renal impairment"]


def test_parse_study_json_and_markdown_table() -> None:
    parsed = parse_study_json(FIXTURE_PAYLOAD)
    assert parsed.nct_id == "NCT03719066"
    assert len(parsed.arms) == 2
    assert parsed.arms[0].interventions == ["DRUG: Drug X 10mg"]
    md = parsed.to_markdown()
    assert "| Active Arm | EXPERIMENTAL | DRUG: Drug X 10mg |" in md
    assert "## Inclusion Criteria (2)" in md
    assert "## Exclusion Criteria (2)" in md


def test_invalid_nct_id_raises() -> None:
    with pytest.raises(ValueError, match="Invalid NCT identifier"):
        fetch_study_json("INVALID123")


def test_cli_offline_fixture(tmp_path: Path) -> None:
    fixture_file = tmp_path / "study.json"
    out_file = tmp_path / "study.md"
    fixture_file.write_text(json.dumps(FIXTURE_PAYLOAD), encoding="utf-8")
    rc = main([str(fixture_file), "--out", str(out_file)])
    assert rc == 0
    assert "NCT03719066" in out_file.read_text(encoding="utf-8")
