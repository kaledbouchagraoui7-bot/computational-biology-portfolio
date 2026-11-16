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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
        ("side effect", "adverse", "safety", "unwanted", "reaction", "effet secondaire", "indésirable", "sécurité"),
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [
            "# EU CTR 536/2014 Annex V Plain-Language Summary Audit",
            "",
            f"- **Generated (UTC):** `{self.generated_utc}`",
            f"- **Source SHA-256:** `{self.source_sha256}`",
            f"- **Annex V Coverage:** `{self.annex_v_coverage_pct:.1f}%` ({len(self.annex_v_present)}/{len(ANNEX_V_SECTIONS)})",
            f"- **Flesch-Kincaid Grade Level:** `{self.readability.flesch_kincaid_grade:.2f}` ({self.readability_band})",
            f"- **Coleman-Liau Index:** `{self.readability.coleman_liau_index:.2f}`",
            f"- **Counts:** {self.readability.words} words, {self.readability.sentences} sentences, {self.readability.syllables} syllables",
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


def score_pls(text: str) -> PLSAuditReport:
    """Score a Plain-Language Summary against EU CTR 536/2014 Annex V."""
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
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

    return PLSAuditReport(
        generated_utc=datetime.now(timezone.utc).isoformat(),
        source_sha256=sha,
        readability=readability,
        annex_v_present=present,
        annex_v_missing=missing,
        annex_v_coverage_pct=coverage,
        jargon_flagged=jargon,
        readability_band=classify_readability_band(readability.flesch_kincaid_grade),
    )


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
