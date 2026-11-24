"""EU CTR 536/2014 Annex V Plain-Language Summary (PLS) Readability & Jargon Scorer.

Pure-Python CLI and library for scoring clinical trial lay summaries against
EU Clinical Trials Regulation (EU) No 536/2014 Annex V mandatory sections,
computing Flesch-Kincaid Grade Level and Coleman-Liau readability indices,
flagging unexplained medical/statistical jargon, and emitting an ALCOA+ audit report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RULE_CATALOG_VERSION: str = "EU-CTR-536/2014-Annex-V-v2.1"
REGULATION_REF: str = "Regulation (EU) No 536/2014 Article 37 & Annex V"
LIMITATION_NOTE: str = (
    "Deterministic heuristic pre-screening aid; does not replace formal regulatory "
    "medical writing review or Ethics Committee approval."
)
PROMOTIONAL_CLAIMS_TERMS: tuple[str, ...] = (
    "miracle",
    "100% cure",
    "guaranteed cure",
    "risk-free",
    "completely safe",
    "best drug ever",
    "revolutionary breakthrough",
    "guérison garantie",
    "sans aucun risque",
)

# EU CTR 536/2014 Annex V mandatory elements (key, display label, keyword triggers)
ANNEX_V_SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "trial_identification",
        "1. Trial identification (title, EU CT / NCT ID, dates)",
        ("nct", "eu ct", "eudract", "trial", "study title", "identifier", "essai", "étude"),
    ),
    (
        "sponsor_contact",
        "2. Sponsor name and contact details",
        ("sponsor", "conducted by", "funded by", "contact", "promoteur", "financé par"),
    ),
    (
        "general_purpose",
        "3. General information & main objective",
        ("purpose", "aim", "why", "objective", "find out", "designed to", "objectif", "but de cette étude"),
    ),
    (
        "population",
        "4. Study population & eligibility",
        ("participants", "patients", "volunteers", "adults", "aged", "joined", "volontaires", "adultes"),
    ),
    (
        "treatments",
        "5. Investigational treatments / interventions tested",
        ("medicine", "treatment", "placebo", "dose", "received", "group", "médicament", "traitement", "groupe"),
    ),
    (
        "adverse_reactions",
        "6. Adverse reactions & safety summary",
        ("side effect", "adverse", "safety", "unwanted", "reaction", "effet secondaire", "effets secondaires", "indésirable", "sécurité"),
    ),
    (
        "overall_results",
        "7. Overall efficacy & main results",
        ("result", "outcome", "showed", "compared", "effect", "finding", "résultat", "montré", "comparé"),
    ),
    (
        "next_steps",
        "8. Comments on outcome & follow-up / next steps",
        ("next", "future", "further", "more information", "thank", "suite", "remercions", "plus d'informations"),
    ),
)

# High-friction medical/statistical jargon with plain-language alternatives (EN + FR)
JARGON_GLOSSARY: dict[str, str] = {
    "randomised": "assigned by chance (like flipping a coin)",
    "randomized": "assigned by chance (like flipping a coin)",
    "randomisé": "réparti par tirage au sort",
    "placebo-controlled": "compared against an inactive look-alike",
    "double-blind": "neither participants nor doctors knew which treatment was given",
    "double aveugle": "ni les participants ni les médecins ne savaient quel traitement était administré",
    "efficacy": "how well the treatment worked",
    "adverse events": "unwanted medical problems",
    "adverse event": "unwanted medical problem",
    "effet indésirable": "problème médical non souhaité",
    "effets indésirables": "problèmes médicaux non souhaités",
    "pharmacokinetics": "how the body processes the medicine",
    "pharmacodynamics": "how the medicine affects the body",
    "hazard ratio": "relative rate of an event over time",
    "confidence interval": "range of likely values",
    "p-value": "statistical probability measure",
    "statistically significant": "unlikely to be due to chance alone",
    "prophylaxis": "preventive treatment",
    "myocardial infarction": "heart attack",
    "hypertension": "high blood pressure",
    "dyspnoea": "shortness of breath",
    "dyspnea": "shortness of breath",
    "nausea": "feeling sick to the stomach",
    "cohort": "group of participants",
    "inclusion criteria": "rules for who could join",
    "exclusion criteria": "rules for who could not join",
    "critères d'inclusion": "règles définissant qui pouvait participer",
}


@dataclass(frozen=True)
class ReadabilityMetrics:
    words: int
    sentences: int
    syllables: int
    characters: int
    flesch_kincaid_grade: float
    coleman_liau_index: float


@dataclass(frozen=True)
class PLSAuditReport:
    generated_utc: str
    source_sha256: str
    readability: ReadabilityMetrics
    annex_v_present: list[str]
    annex_v_missing: list[str]
    annex_v_coverage_pct: float
    jargon_flagged: list[dict[str, str]]
    readability_band: str
    rule_catalog_version: str = RULE_CATALOG_VERSION
    promotional_claims_detected: list[str] = field(default_factory=list)
    compliance_passed: bool = True
    rule_evaluations: list[dict[str, Any]] = field(default_factory=list)
    limitation_note: str = LIMITATION_NOTE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [
            "# EU CTR 536/2014 Annex V Plain-Language Summary Audit",
            "",
            f"- **Generated (UTC):** `{self.generated_utc}`",
            f"- **Rule Catalog Version:** `{self.rule_catalog_version}`",
            f"- **Source SHA-256:** `{self.source_sha256}`",
            f"- **Compliance Status:** `{'PASS' if self.compliance_passed else 'NEEDS_REVISION'}`",
            f"- **Annex V Coverage:** `{self.annex_v_coverage_pct:.1f}%` ({len(self.annex_v_present)}/{len(ANNEX_V_SECTIONS)})",
            f"- **Flesch-Kincaid Grade Level:** `{self.readability.flesch_kincaid_grade:.2f}` ({self.readability_band})",
            f"- **Coleman-Liau Index:** `{self.readability.coleman_liau_index:.2f}`",
            f"- **Counts:** {self.readability.words} words, {self.readability.sentences} sentences, {self.readability.syllables} syllables",
            f"- **Limitation Note:** {self.limitation_note}",
            "",
            "## Annex V Checklist",
            *(f"- [x] {item}" for item in self.annex_v_present),
            *(f"- [ ] {item}" for item in self.annex_v_missing),
            "",
            "## Unexplained Jargon Flagged",
        ]
        if self.jargon_flagged:
            for item in self.jargon_flagged:
                lines.append(
                    f"- **`{item['term']}`** -> Suggested plain term: *{item['plain_alternative']}*"
                )
        else:
            lines.append("- None detected.")
        lines.extend(["", "## Structured Rule Evaluations"])
        for rule in self.rule_evaluations:
            lines.append(
                f"- `{rule['rule_id']}` (`{rule['severity']}`, `passed={rule['passed']}` — {rule['regulation_ref']})"
            )
        return "\n".join(lines) + "\n"


def count_syllables(word: str) -> int:
    """Estimate English syllable count for a single word."""
    cleaned = re.sub(r"[^a-z]", "", word.lower())
    if not cleaned:
        return 0
    if len(cleaned) <= 3:
        return 1
    cleaned = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", cleaned)
    cleaned = re.sub(r"^y", "", cleaned)
    groups = re.findall(r"[aeiouy]{1,2}", cleaned)
    return max(1, len(groups))


def compute_readability(text: str) -> ReadabilityMetrics:
    """Compute Flesch-Kincaid Grade Level and Coleman-Liau Index."""
    words = re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", text)
    raw_sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    word_count = len(words)
    sentence_count = max(1, len(raw_sentences))

    if word_count == 0:
        return ReadabilityMetrics(0, 0, 0, 0, 0.0, 0.0)

    syllable_count = sum(max(1, count_syllables(w)) for w in words)
    char_count = sum(len(re.sub(r"[^A-Za-z0-9]", "", w)) for w in words)

    fk_grade = max(
        0.0,
        0.39 * (word_count / sentence_count) + 11.8 * (syllable_count / word_count) - 15.59,
    )
    l_val = (char_count / word_count) * 100.0
    s_val = (sentence_count / word_count) * 100.0
    cli = 0.0588 * l_val - 0.296 * s_val - 15.8

    return ReadabilityMetrics(
        words=word_count,
        sentences=len(raw_sentences),
        syllables=syllable_count,
        characters=char_count,
        flesch_kincaid_grade=round(fk_grade, 2),
        coleman_liau_index=round(cli, 2),
    )


def find_unexplained_jargon(text: str) -> list[dict[str, str]]:
    """Flag technical terms unless an inline parenthetical explanation is present."""
    lower = text.lower()
    flagged: list[dict[str, str]] = []
    for term, plain in JARGON_GLOSSARY.items():
        pattern = rf"\b{re.escape(term)}\b"
        match = re.search(pattern, lower)
        if not match:
            continue
        # Consider explained if followed shortly by parentheses or "meaning" / "which means"
        window = lower[match.end() : match.end() + 45]
        if "(" in window or "meaning" in window or "means" in window:
            continue
        flagged.append({"term": term, "plain_alternative": plain})
    return flagged


def classify_readability_band(fk_grade: float) -> str:
    """Classify Flesch-Kincaid grade against EU CTR lay-summary target (Grade 6-8)."""
    if fk_grade <= 6.5:
        return "Lay-friendly (<= Grade 6.5)"
    if fk_grade <= 8.5:
        return "Target EU CTR range (Grade 6.5-8.5)"
    if fk_grade <= 11.0:
        return "Moderately complex (simplify sentences/jargon)"
    return "Too technical for general lay audience (> Grade 11)"


def detect_promotional_claims(text: str) -> list[str]:
    """Detect non-neutral / promotional claims disallowed in lay trial summaries."""
    lower = (text or "").lower()
    return [term for term in PROMOTIONAL_CLAIMS_TERMS if re.search(rf"\b{re.escape(term)}\b", lower)]


def score_pls(text: str) -> PLSAuditReport:
    """Score a Plain-Language Summary against EU CTR 536/2014 Annex V with structured rule traceability."""
    lower = text.lower()
    present: list[str] = []
    missing: list[str] = []
    for _key, label, triggers in ANNEX_V_SECTIONS:
        if any(t in lower for t in triggers):
            present.append(label)
        else:
            missing.append(label)

    coverage = round((len(present) / len(ANNEX_V_SECTIONS)) * 100.0, 1)
    readability = compute_readability(text)
    jargon = find_unexplained_jargon(text)
    promo = detect_promotional_claims(text)
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

    sec_ok = len(missing) == 0
    read_ok = readability.flesch_kincaid_grade <= 11.0
    jargon_ok = len(jargon) == 0
    promo_ok = len(promo) == 0

    rule_evaluations = [
        {
            "rule_id": "EU_CTR_ANNEX_V_REQUIRED_SECTIONS",
            "regulation_ref": REGULATION_REF,
            "severity": "PASS" if sec_ok else "CRITICAL",
            "passed": sec_ok,
            "evidence_spans": list(missing),
        },
        {
            "rule_id": "EU_CTR_ANNEX_V_PROMOTIONAL_LANGUAGE",
            "regulation_ref": REGULATION_REF,
            "severity": "PASS" if promo_ok else "CRITICAL",
            "passed": promo_ok,
            "evidence_spans": list(promo),
        },
        {
            "rule_id": "EU_CTR_ANNEX_V_READABILITY",
            "regulation_ref": REGULATION_REF,
            "severity": "PASS" if read_ok else "MAJOR",
            "passed": read_ok,
            "evidence_spans": [f"FK={readability.flesch_kincaid_grade:.2f}"],
        },
        {
            "rule_id": "EU_CTR_ANNEX_V_JARGON",
            "regulation_ref": REGULATION_REF,
            "severity": "PASS" if jargon_ok else "MINOR",
            "passed": jargon_ok,
            "evidence_spans": [j["term"] for j in jargon],
        },
    ]
    compliance_passed = sec_ok and promo_ok and read_ok

    return PLSAuditReport(
        generated_utc=datetime.now(timezone.utc).isoformat(),
        source_sha256=sha,
        readability=readability,
        annex_v_present=present,
        annex_v_missing=missing,
        annex_v_coverage_pct=coverage,
        jargon_flagged=jargon,
        readability_band=classify_readability_band(readability.flesch_kincaid_grade),
        rule_catalog_version=RULE_CATALOG_VERSION,
        promotional_claims_detected=promo,
        compliance_passed=compliance_passed,
        rule_evaluations=rule_evaluations,
        limitation_note=LIMITATION_NOTE,
    )


def score_plain_language_summary(text: str) -> dict[str, Any]:
    """Dictionary wrapper exposing structured EU CTR Annex V rule evaluations & readability metrics."""
    rep = score_pls(text)
    payload = rep.to_dict()
    payload["readability_score"] = rep.readability.flesch_kincaid_grade
    payload["jargon_detected"] = [item["term"] for item in rep.jargon_flagged]
    payload["missing_sections"] = list(rep.annex_v_missing)
    payload["numerical_consistency"] = check_numerical_consistency(text)
    return payload


def check_numerical_consistency(text: str) -> dict[str, Any]:
    """
    Detect explicit enrollment / percentage inconsistencies in synthetic PLS text
    (e.g., mismatched total N vs. arm sums when stated as 'total of N' alongside 'group A + group B').
    """
    findings: list[str] = []
    pcts = [float(x) for x in re.findall(r"\b(\d+(?:\.\d+)?)\s*%", text or "")]
    bad_pcts = [p for p in pcts if p > 100.0]
    if bad_pcts:
        findings.append(f"percentage_exceeds_100:{bad_pcts}")
    total_m = re.search(r"total\s+of\s+(\d+)\s+(?:patients|participants|volunteers)", (text or "").lower())
    arms = [int(x) for x in re.findall(r"(?:arm|group)\s+[ab12][:\s]+(\d+)\s+(?:patients|participants)", (text or "").lower())]
    if total_m and len(arms) >= 2:
        stated_total = int(total_m.group(1))
        arm_sum = sum(arms[:2])
        if stated_total != arm_sum:
            findings.append(f"enrollment_mismatch:stated_total={stated_total}_vs_arm_sum={arm_sum}")
    return {"consistent": len(findings) == 0, "percentages_checked": len(pcts), "findings": findings}


def compare_translation_alignment(en_text: str, fr_text: str) -> dict[str, Any]:
    """Compare EN vs FR EU CTR Annex V structural coverage and flag missing translated elements."""
    en_rep = score_pls(en_text)
    fr_rep = score_pls(fr_text)
    en_set = set(en_rep.annex_v_present)
    fr_set = set(fr_rep.annex_v_present)
    return {
        "aligned": en_set == fr_set,
        "en_coverage_pct": en_rep.annex_v_coverage_pct,
        "fr_coverage_pct": fr_rep.annex_v_coverage_pct,
        "missing_in_fr": sorted(en_set - fr_set),
        "missing_in_en": sorted(fr_set - en_set),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score an EU CTR 536/2014 Plain-Language Summary."
    )
    parser.add_argument("input_file", type=Path, help="Path to Markdown or text PLS file.")
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output report format.",
    )
    parser.add_argument("--out", type=Path, default=None, help="Optional output file path.")
    args = parser.parse_args(argv)

    text = args.input_file.read_text(encoding="utf-8")
    report = score_pls(text)
    rendered = (
        json.dumps(report.to_dict(), indent=2)
        if args.format == "json"
        else report.to_markdown()
    )
    if args.out is not None:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
