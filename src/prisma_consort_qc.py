"""PRISMA 2020 systematic-review manuscript QC checker.

Checks a Markdown/plain-text manuscript against the PRISMA 2020 reporting
checklist (Page et al., BMJ 2021;372:n71) using transparent keyword evidence,
and verifies the core IMRaD sections. Pure standard library, deterministic,
no network access. Keyword matching is a *screening aid*: it flags likely
omissions, it does not replace human review of the full checklist.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REQUIRED_SECTIONS: tuple[str, ...] = (
    "title",
    "abstract",
    "methods",
    "results",
    "discussion",
    "funding",
)

# (item id, description, evidence keywords - any match counts as reported)
PRISMA_2020_ITEMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("1", "Title identifies the report as a systematic review", ("systematic review", "meta-analysis")),
    ("2", "Structured abstract (PRISMA 2020 for Abstracts)", ("abstract",)),
    ("3", "Rationale in context of existing knowledge", ("rationale", "background", "introduction")),
    ("4", "Explicit objectives / questions (PICO)", ("objective", "pico", "research question", "aim")),
    ("5", "Eligibility criteria", ("eligibility", "inclusion criteria", "exclusion criteria")),
    ("6", "Information sources with dates last searched", ("information sources", "databases", "last searched", "pubmed", "embase")),
    ("7", "Full search strategy", ("search strategy", "search string", "boolean")),
    ("8", "Selection process (screeners, independence)", ("selection process", "screened", "independent reviewer", "two reviewers")),
    ("9", "Data collection process", ("data collection", "data extraction", "extracted")),
    ("10", "Data items (outcomes, variables)", ("data items", "outcomes sought", "variables")),
    ("11", "Risk of bias assessment", ("risk of bias", "rob 2", "robins", "newcastle-ottawa")),
    ("12", "Effect measures", ("effect measure", "risk ratio", "odds ratio", "mean difference")),
    ("13", "Synthesis methods", ("synthesis", "random-effects", "fixed-effect", "heterogeneity")),
    ("14", "Reporting bias assessment", ("reporting bias", "publication bias", "funnel plot")),
    ("15", "Certainty of evidence assessment", ("certainty of evidence", "grade")),
    ("16", "Study selection (flow diagram)", ("flow diagram", "records identified", "records screened", "prisma flow")),
    ("17", "Study characteristics", ("study characteristics", "characteristics of included")),
    ("18", "Risk of bias in studies (results)", ("risk of bias in", "bias assessment results")),
    ("19", "Results of individual studies", ("individual studies", "forest plot")),
    ("20", "Results of syntheses", ("pooled", "results of syntheses", "i2", "i²")),
    ("21", "Reporting biases (results)", ("publication bias", "reporting biases", "funnel")),
    ("22", "Certainty of evidence (results)", ("certainty", "grade")),
    ("23", "Discussion: interpretation and limitations", ("limitations", "interpretation", "implications")),
    ("24", "Registration and protocol", ("prospero", "registration", "protocol", "osf")),
    ("25", "Support / funding", ("funding", "financial support", "sponsor")),
    ("26", "Competing interests", ("competing interests", "conflict of interest", "conflicts of interest")),
    ("27", "Availability of data, code and materials", ("data availability", "available at", "open data", "supplementary")),
)


def validate_manuscript_sections(sections: dict[str, str]) -> dict[str, Any]:
    """Return completeness of the required IMRaD sections of a manuscript."""
    missing = [s for s in REQUIRED_SECTIONS if not str(sections.get(s, "")).strip()]
    return {"compliant": not missing, "missing_sections": missing}


def check_prisma_2020(text: str) -> dict[str, Any]:
    """Screen free text against the 27 PRISMA 2020 checklist items."""
    lowered = text.lower()
    reported: list[str] = []
    missing: list[dict[str, str]] = []
    for item_id, description, keywords in PRISMA_2020_ITEMS:
        if any(re.search(rf"(?<!\w){re.escape(k)}", lowered) for k in keywords):
            reported.append(item_id)
        else:
            missing.append({"item": item_id, "description": description})
    total = len(PRISMA_2020_ITEMS)
    return {
        "items_total": total,
        "items_reported": len(reported),
        "coverage_pct": round(100.0 * len(reported) / total, 1),
        "reported": reported,
        "missing": missing,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PRISMA 2020 manuscript QC screen.")
    parser.add_argument("manuscript", type=Path, help="Markdown or text manuscript")
    parser.add_argument("--min-coverage", type=float, default=80.0, help="Pass threshold (%%)")
    args = parser.parse_args(argv)
    try:
        text = args.manuscript.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = check_prisma_2020(text)
    report["passed"] = report["coverage_pct"] >= args.min_coverage
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
