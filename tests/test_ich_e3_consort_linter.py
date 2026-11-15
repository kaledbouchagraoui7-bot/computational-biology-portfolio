"""Unit tests for ICH E3 / CONSORT 2010 Checklist Linter."""

from __future__ import annotations

from pathlib import Path
from src.ich_e3_consort_linter import check_numeric_consistency, lint_document, main

CLEAN_CSR = """
# Protocol NCT03719066 Synopsis
Objective: Evaluate primary endpoint reduction at Week 12.
Design: Randomized double-blind phase 2 trial.
Eligibility: Inclusion criteria required aged >= 18 at hospital setting.
Statistical Methods: Sample size N = 120 provided 80% power (t-test, p-value < 0.05).
Participant Flow: Total enrolled N = 120; 115 completed.
Baseline: Table 1 demographics (age, sex) were balanced.

| Arm | Enrolled |
|---|---|
| Total | N = 120 |

Primary Outcome: Efficacy showed mean difference 4.2 (95% confidence interval).
Safety: Adverse events were mild; 0 serious adverse events (SAE).
Conclusions: Overall interpretation supports further study with noted limitations.
"""


def test_clean_csr_passes_100_percent() -> None:
    res = lint_document(CLEAN_CSR)
    assert res.completeness_pct == 100.0
    assert res.numeric_mismatches == []
    assert res.is_clean is True


def test_table_vs_prose_mismatch_detected() -> None:
    mismatched = CLEAN_CSR.replace("| Total | N = 120 |", "| Total | N = 98 |")
    mismatches = check_numeric_consistency(mismatched)
    assert len(mismatches) == 1
    assert mismatches[0].prose_values == [120]
    assert mismatches[0].table_values == [98]
    res = lint_document(mismatched)
    assert res.is_clean is False


def test_cli_audit_log_written(tmp_path: Path) -> None:
    csr_file = tmp_path / "csr.md"
    audit_log = tmp_path / "audit.jsonl"
    csr_file.write_text(CLEAN_CSR, encoding="utf-8")
    rc = main([str(csr_file), "--audit-log", str(audit_log)])
    assert rc == 0
    assert audit_log.exists()
    assert '"is_clean": true' in audit_log.read_text(encoding="utf-8")
