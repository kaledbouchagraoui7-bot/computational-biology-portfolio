"""Automated ICH E3 / CONSORT 2010 Checklist Linter CLI.

Validates Clinical Study Report (CSR) synopses and manuscript drafts against
mandatory ICH E3 Section 2 & CONSORT 2010 checklist items, checks numerical
consistency between Markdown tables and prose, and emits a SHA-256 ALCOA+ log.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ICH_E3_CONSORT_ITEMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("title_id", "ICH E3 / CONSORT 1a: Study title & trial identifier (NCT/EudraCT)", ("nct", "eudract", "title", "protocol")),
    ("objectives", "ICH E3 §2 / CONSORT 2b: Primary & secondary objectives", ("objective", "primary endpoint", "aim")),
    ("design", "ICH E3 / CONSORT 3a/8: Study design, randomization & blinding", ("random", "blind", "design", "phase", "arm")),
    ("eligibility", "CONSORT 4a: Eligibility criteria & study setting", ("inclusion", "exclusion", "eligible", "aged", "setting")),
    ("sample_size", "CONSORT 7a / ICH E3: Sample size & statistical methods", ("sample size", "statistical", "power", "p-value", "anova", "t-test")),
    ("participant_flow", "CONSORT 13a: Participant flow & enrollment counts", ("enrolled", "randomized", "completed", "discontinued", "n =")),
    ("baseline", "CONSORT 15: Baseline demographic & clinical characteristics", ("baseline", "demographic", "age", "sex", "table 1")),
    ("efficacy_results", "ICH E3 / CONSORT 17a: Primary efficacy outcome & precision", ("primary outcome", "efficacy", "confidence interval", "mean", "difference")),
    ("safety_harms", "ICH E3 / CONSORT 19: Adverse events & safety harms", ("adverse", "safety", "serious adverse", "sae", "tolerability")),
    ("conclusions", "ICH E3 / CONSORT 22: Interpretation & overall conclusions", ("conclusion", "summary", "interpretation", "limitation")),
)


@dataclass(frozen=True)
class ConsistencyMismatch:
    label: str
    prose_values: list[int]
    table_values: list[int]
    detail: str


@dataclass(frozen=True)
class LinterResult:
    generated_utc: str
    source_sha256: str
    checklist_passed: list[str]
    checklist_missing: list[str]
    completeness_pct: float
    numeric_mismatches: list[ConsistencyMismatch]
    is_clean: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [
            "# ICH E3 / CONSORT 2010 Checklist & Numeric Consistency Audit",
            "",
            f"- **Generated (UTC):** `{self.generated_utc}`",
            f"- **Source SHA-256:** `{self.source_sha256}`",
            f"- **Checklist Completeness:** `{self.completeness_pct:.1f}%` ({len(self.checklist_passed)}/{len(ICH_E3_CONSORT_ITEMS)})",
            f"- **Numeric Mismatches:** `{len(self.numeric_mismatches)}`",
            f"- **Overall Status:** `{'PASS' if self.is_clean else 'NEEDS_REVIEW'}`",
            "",
            "## Checklist Items",
            *(f"- [x] {item}" for item in self.checklist_passed),
            *(f"- [ ] {item}" for item in self.checklist_missing),
            "",
            "## Table-vs-Prose Numeric Consistency",
        ]
        if self.numeric_mismatches:
            for m in self.numeric_mismatches:
                lines.append(
                    f"- **Mismatch ({m.label}):** prose={m.prose_values}, table={m.table_values} — {m.detail}"
                )
        else:
            lines.append("- No table-vs-prose sample-count mismatches detected.")
        return "\n".join(lines) + "\n"


def _split_tables_and_prose(markdown_text: str) -> tuple[str, str]:
    table_lines: list[str] = []
    prose_lines: list[str] = []
    for line in markdown_text.splitlines():
        if line.strip().startswith("|") and "|" in line.strip()[1:]:
            table_lines.append(line)
        else:
            prose_lines.append(line)
    return "\n".join(table_lines), "\n".join(prose_lines)


def check_numeric_consistency(markdown_text: str) -> list[ConsistencyMismatch]:
    """Compare sample counts (`N = X` / `Total N`) between prose and Markdown tables."""
    tables, prose = _split_tables_and_prose(markdown_text)
    if not tables.strip():
        return []

    prose_n = [int(m) for m in re.findall(r"\bN\s*=\s*(\d+)\b", prose, flags=re.IGNORECASE)]
    table_n = [int(m) for m in re.findall(r"\bN\s*=\s*(\d+)\b", tables, flags=re.IGNORECASE)]

    mismatches: list[ConsistencyMismatch] = []
    if prose_n and table_n and set(prose_n) != set(table_n):
        mismatches.append(
            ConsistencyMismatch(
                label="Total / Arm Sample Size (N=)",
                prose_values=sorted(set(prose_n)),
                table_values=sorted(set(table_n)),
                detail="Sample size N= cited in prose does not match N= values inside Markdown tables.",
            )
        )
    return mismatches


def lint_document(markdown_text: str) -> LinterResult:
    """Lint a CSR synopsis or clinical manuscript draft."""
    lower = markdown_text.lower()
    passed: list[str] = []
    missing: list[str] = []
    for _key, label, triggers in ICH_E3_CONSORT_ITEMS:
        if any(t in lower for t in triggers):
            passed.append(label)
        else:
            missing.append(label)

    completeness = round((len(passed) / len(ICH_E3_CONSORT_ITEMS)) * 100.0, 1)
    mismatches = check_numeric_consistency(markdown_text)
    sha = hashlib.sha256(markdown_text.encode("utf-8")).hexdigest()
    is_clean = (len(missing) == 0) and (len(mismatches) == 0)

    return LinterResult(
        generated_utc=datetime.now(timezone.utc).isoformat(),
        source_sha256=sha,
        checklist_passed=passed,
        checklist_missing=missing,
        completeness_pct=completeness,
        numeric_mismatches=mismatches,
        is_clean=is_clean,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lint CSR synopses and manuscripts against ICH E3 / CONSORT 2010."
    )
    parser.add_argument("input_file", type=Path, help="Path to Markdown CSR or manuscript.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--audit-log", type=Path, default=None, help="Append ALCOA+ JSONL audit entry.")
    args = parser.parse_args(argv)

    text = args.input_file.read_text(encoding="utf-8")
    result = lint_document(text)

    if args.audit_log is not None:
        args.audit_log.parent.mkdir(parents=True, exist_ok=True)
        with args.audit_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(result.to_dict()) + "\n")

    print(json.dumps(result.to_dict(), indent=2) if args.format == "json" else result.to_markdown())
    return 0 if result.is_clean else 2


if __name__ == "__main__":
    raise SystemExit(main())
