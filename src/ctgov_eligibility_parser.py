"""ClinicalTrials.gov API v2 Eligibility Criteria Parser & Arm Comparator.

Queries the public ClinicalTrials.gov v2 REST API (or parses an offline JSON
study fixture), extracts structured Inclusion and Exclusion criteria, and
generates a clean Markdown comparison table across trial arms.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

CTGOV_V2_BASE = "https://clinicaltrials.gov/api/v2/studies"


@dataclass(frozen=True)
class TrialArm:
    label: str
    arm_type: str
    description: str
    interventions: list[str]


@dataclass(frozen=True)
class ParsedEligibility:
    nct_id: str
    brief_title: str
    status: str
    sex: str
    minimum_age: str
    maximum_age: str
    healthy_volunteers: str
    inclusion_criteria: list[str]
    exclusion_criteria: list[str]
    arms: list[TrialArm]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [
            f"# {self.nct_id} — {self.brief_title}",
            "",
            f"- **Overall Status:** `{self.status}`",
            f"- **Eligible Sex:** `{self.sex}` | **Age:** `{self.minimum_age}` to `{self.maximum_age}` | **Healthy Volunteers:** `{self.healthy_volunteers}`",
            "",
            "## Trial Arms & Interventions Comparison",
            "| Arm Label | Type | Interventions | Summary Description |",
            "|---|---|---|---|",
        ]
        if self.arms:
            for arm in self.arms:
                iv = ", ".join(arm.interventions) if arm.interventions else "—"
                desc = arm.description.replace("|", "\\|").replace("\n", " ").strip()
                lines.append(f"| {arm.label} | {arm.arm_type} | {iv} | {desc} |")
        else:
            lines.append("| — | — | — | No arm groups reported |")

        lines.extend(["", f"## Inclusion Criteria ({len(self.inclusion_criteria)})"])
        if self.inclusion_criteria:
            lines.extend(f"- {item}" for item in self.inclusion_criteria)
        else:
            lines.append("- None parsed.")

        lines.extend(["", f"## Exclusion Criteria ({len(self.exclusion_criteria)})"])
        if self.exclusion_criteria:
            lines.extend(f"- {item}" for item in self.exclusion_criteria)
        else:
            lines.append("- None parsed.")

        return "\n".join(lines) + "\n"


def fetch_study_json(nct_id: str, timeout: float = 15.0) -> dict[str, Any]:
    """Fetch a study record from the public ClinicalTrials.gov API v2."""
    clean_id = nct_id.strip().upper()
    if not re.fullmatch(r"NCT\d{8}", clean_id):
        raise ValueError(f"Invalid NCT identifier: {nct_id!r}")
    url = f"{CTGOV_V2_BASE}/{clean_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Sentinel-CTgov-Parser/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def split_criteria_block(raw_text: str) -> tuple[list[str], list[str]]:
    """Split ClinicalTrials.gov eligibilityCriteria text into inclusion and exclusion lists."""
    if not raw_text.strip():
        return [], []

    inc_match = re.search(r"inclusion criteria\s*:?", raw_text, flags=re.IGNORECASE)
    exc_match = re.search(r"exclusion criteria\s*:?", raw_text, flags=re.IGNORECASE)

    inc_raw = ""
    exc_raw = ""
    if inc_match and exc_match:
        if inc_match.start() < exc_match.start():
            inc_raw = raw_text[inc_match.end() : exc_match.start()]
            exc_raw = raw_text[exc_match.end() :]
        else:
            exc_raw = raw_text[exc_match.end() : inc_match.start()]
            inc_raw = raw_text[inc_match.end() :]
    elif inc_match:
        inc_raw = raw_text[inc_match.end() :]
    elif exc_match:
        exc_raw = raw_text[exc_match.end() :]
    else:
        inc_raw = raw_text

    def _clean_lines(block: str) -> list[str]:
        items: list[str] = []
        for line in block.splitlines():
            stripped = re.sub(r"^\s*(?:[-*•]+|\d+[.)])\s*", "", line).strip()
            if stripped:
                items.append(stripped)
        return items

    return _clean_lines(inc_raw), _clean_lines(exc_raw)


def parse_study_json(payload: dict[str, Any]) -> ParsedEligibility:
    """Parse a ClinicalTrials.gov API v2 study JSON payload into structured fields."""
    proto = payload.get("protocolSection", {})
    ident = proto.get("identificationModule", {})
    status_mod = proto.get("statusModule", {})
    elig = proto.get("eligibilityModule", {})
    arms_mod = proto.get("armsInterventionsModule", {})

    nct_id = ident.get("nctId", "UNKNOWN")
    brief_title = ident.get("briefTitle", "Untitled Study")
    overall_status = status_mod.get("overallStatus", "UNKNOWN")

    inc, exc = split_criteria_block(elig.get("eligibilityCriteria", ""))

    # Map interventions to arm labels
    arm_to_ivs: dict[str, list[str]] = {}
    for iv in arms_mod.get("interventions", []):
        iv_name = iv.get("name", "Unnamed")
        iv_type = iv.get("type", "")
        display = f"{iv_type}: {iv_name}" if iv_type else iv_name
        for label in iv.get("armGroupLabels", []):
            arm_to_ivs.setdefault(label, []).append(display)

    arms: list[TrialArm] = []
    for ag in arms_mod.get("armGroups", []):
        label = ag.get("label", "Arm")
        arms.append(
            TrialArm(
                label=label,
                arm_type=ag.get("type", "OTHER"),
                description=ag.get("description", ""),
                interventions=arm_to_ivs.get(label, []),
            )
        )

    hv = elig.get("healthyVolunteers")
    hv_str = "Yes" if hv is True else ("No" if hv is False else "Not specified")

    return ParsedEligibility(
        nct_id=nct_id,
        brief_title=brief_title,
        status=overall_status,
        sex=elig.get("sex", "ALL"),
        minimum_age=elig.get("minimumAge", "N/A"),
        maximum_age=elig.get("maximumAge", "N/A"),
        healthy_volunteers=hv_str,
        inclusion_criteria=inc,
        exclusion_criteria=exc,
        arms=arms,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse ClinicalTrials.gov v2 eligibility criteria and arm comparisons."
    )
    parser.add_argument("source", help="NCT ID (e.g. NCT03719066) or path to local JSON fixture.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    src_path = Path(args.source)
    if src_path.exists() and src_path.is_file():
        payload = json.loads(src_path.read_text(encoding="utf-8"))
    else:
        payload = fetch_study_json(args.source)

    parsed = parse_study_json(payload)
    rendered = (
        json.dumps(parsed.to_dict(), indent=2)
        if args.format == "json"
        else parsed.to_markdown()
    )
    if args.out is not None:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
