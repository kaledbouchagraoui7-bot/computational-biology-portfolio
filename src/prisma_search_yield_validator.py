"""PRISMA Search-Strategy Yield Sensitivity & Deduplication Scope Validator.

Quantifies deduplication yield, database overlap across PubMed and Europe PMC
exports, search-strategy sensitivity/drift against a benchmark reference set
(e.g., known included trials), and flags suspicious scope loss (e.g., overly
restrictive MeSH/boolean filters or missing identifier fields).

Pure Python 3.12 standard library, deterministic, zero network calls.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

DOI_CLEAN_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/", flags=re.IGNORECASE)
TITLE_NORM_RE = re.compile(r"[^a-z0-9]+")


def normalize_doi(raw: Any) -> str:
    """Normalize a DOI string to lowercase canonical prefix/suffix form."""
    val = str(raw or "").strip()
    if not val:
        return ""
    val = DOI_CLEAN_RE.sub("", val).strip().lower()
    if val.startswith("doi:"):
        val = val[4:].strip()
    return val


def normalize_pmid(raw: Any) -> str:
    """Normalize a PubMed ID (PMID) to digits only."""
    val = str(raw or "").strip()
    if not val:
        return ""
    m = re.search(r"\b(\d{4,10})\b", val)
    return m.group(1) if m else ""


def normalize_title_year_key(title: Any, year: Any = "") -> str:
    """Build a deterministic fallback fingerprint from normalized title and year."""
    t_norm = TITLE_NORM_RE.sub(" ", str(title or "").lower()).strip()
    if not t_norm:
        return ""
    y_norm = str(year or "").strip()[:4]
    return f"title:{t_norm}|year:{y_norm}"


def record_keys(record: dict[str, Any]) -> list[str]:
    """Return all deterministic matching keys (`doi:...`, `pmid:...`, `title:...`) for a citation record."""
    keys: list[str] = []
    doi = normalize_doi(record.get("doi"))
    if doi:
        keys.append(f"doi:{doi}")
    pmid = normalize_pmid(record.get("pmid"))
    if pmid:
        keys.append(f"pmid:{pmid}")
    ty = normalize_title_year_key(record.get("title"), record.get("year"))
    if ty:
        keys.append(ty)
    return keys


def deduplicate_exports(
    pubmed_records: list[dict[str, Any]],
    europe_pmc_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deduplicate PubMed and Europe PMC citation exports and compute overlap/yield metrics.

    Each record may provide `pmid`, `doi`, `title`, and `year`. Records sharing
    any normalized DOI, PMID, or normalized (title, year) key are merged into
    a single canonical cluster while tracking source database provenance.
    """
    clusters: list[dict[str, Any]] = []
    key_to_cluster: dict[str, int] = {}
    missing_id_records = 0

    tagged_inputs: list[tuple[str, dict[str, Any]]] = [
        *[("pubmed", r) for r in pubmed_records if isinstance(r, dict)],
        *[("europe_pmc", r) for r in europe_pmc_records if isinstance(r, dict)],
    ]
    total_raw = len(tagged_inputs)

    for source_db, rec in tagged_inputs:
        doi = normalize_doi(rec.get("doi"))
        pmid = normalize_pmid(rec.get("pmid"))
        if not doi and not pmid:
            missing_id_records += 1
        keys = record_keys(rec)
        if not keys:
            keys = [f"unindexed:{len(clusters)}"]

        matched_idx: int | None = None
        for k in keys:
            if k in key_to_cluster:
                matched_idx = key_to_cluster[k]
                break

        if matched_idx is None:
            matched_idx = len(clusters)
            clusters.append(
                {
                    "canonical_id": f"doi:{doi}" if doi else (f"pmid:{pmid}" if pmid else keys[0]),
                    "doi": doi,
                    "pmid": pmid,
                    "title": str(rec.get("title") or "").strip(),
                    "year": str(rec.get("year") or "").strip(),
                    "sources": {source_db},
                    "raw_occurrences": 1,
                    "keys": set(keys),
                }
            )
        else:
            cl = clusters[matched_idx]
            cl["sources"].add(source_db)
            cl["raw_occurrences"] += 1
            if doi and not cl["doi"]:
                cl["doi"] = doi
            if pmid and not cl["pmid"]:
                cl["pmid"] = pmid
            if not cl["title"] and rec.get("title"):
                cl["title"] = str(rec.get("title")).strip()
            cl["keys"].update(keys)

        for k in keys:
            key_to_cluster[k] = matched_idx

    unique_count = len(clusters)
    duplicates_removed = max(0, total_raw - unique_count)
    dedup_yield_pct = round(100.0 * unique_count / total_raw, 2) if total_raw > 0 else 0.0
    duplicate_rate_pct = round(100.0 * duplicates_removed / total_raw, 2) if total_raw > 0 else 0.0

    pubmed_only = sum(1 for c in clusters if c["sources"] == {"pubmed"})
    epmc_only = sum(1 for c in clusters if c["sources"] == {"europe_pmc"})
    both_sources = sum(1 for c in clusters if "pubmed" in c["sources"] and "europe_pmc" in c["sources"])
    overlap_jaccard_pct = round(100.0 * both_sources / unique_count, 2) if unique_count > 0 else 0.0
    missing_id_pct = round(100.0 * missing_id_records / total_raw, 2) if total_raw > 0 else 0.0

    serialized_clusters = [
        {
            "canonical_id": c["canonical_id"],
            "doi": c["doi"],
            "pmid": c["pmid"],
            "title": c["title"],
            "year": c["year"],
            "sources": sorted(c["sources"]),
            "raw_occurrences": c["raw_occurrences"],
        }
        for c in clusters
    ]

    return {
        "pubmed_raw_count": len(pubmed_records),
        "europe_pmc_raw_count": len(europe_pmc_records),
        "total_raw_count": total_raw,
        "unique_count": unique_count,
        "duplicates_removed": duplicates_removed,
        "dedup_yield_pct": dedup_yield_pct,
        "duplicate_rate_pct": duplicate_rate_pct,
        "pubmed_only_count": pubmed_only,
        "europe_pmc_only_count": epmc_only,
        "cross_db_overlap_count": both_sources,
        "cross_db_overlap_jaccard_pct": overlap_jaccard_pct,
        "missing_identifier_count": missing_id_records,
        "missing_identifier_pct": missing_id_pct,
        "unique_records": serialized_clusters,
    }


def evaluate_search_strategy_sensitivity(
    dedup_result: dict[str, Any],
    *,
    benchmark_ids: list[str] | None = None,
    baseline_unique_count: int | None = None,
    min_sensitivity_pct: float = 95.0,
    max_yield_drop_pct: float = 25.0,
) -> dict[str, Any]:
    """Evaluate search-strategy yield sensitivity, recall of benchmark trials, and scope drift flags.

    Flags suspicious search-strategy drift or missing screening scope:
      - `BENCHMARK_RECALL_BELOW_THRESHOLD`: known benchmark trial(s) missed by the boolean query.
      - `SEVERE_YIELD_DROP_DRIFT`: unique yield dropped by more than `max_yield_drop_pct` vs. baseline.
      - `SINGLE_SOURCE_SCOPE_GAP`: one of PubMed or Europe PMC returned 0 records.
      - `ZERO_CROSS_DB_OVERLAP`: both databases returned records but 0 overlap (disjoint query drift).
      - `HIGH_MISSING_IDENTIFIER_RATE`: >20% of raw records lack both DOI and PMID.
    """
    unique_records = dedup_result.get("unique_records") or []
    observed_keys: set[str] = set()
    for rec in unique_records:
        if rec.get("doi"):
            observed_keys.add(f"doi:{normalize_doi(rec['doi'])}")
            observed_keys.add(normalize_doi(rec["doi"]))
        if rec.get("pmid"):
            observed_keys.add(f"pmid:{normalize_pmid(rec['pmid'])}")
            observed_keys.add(normalize_pmid(rec["pmid"]))

    normalized_benchmarks: list[str] = []
    for raw_b in benchmark_ids or []:
        b = str(raw_b or "").strip()
        if not b:
            continue
        if b.lower().startswith("doi:") or "/" in b:
            normalized_benchmarks.append(f"doi:{normalize_doi(b)}")
        elif b.lower().startswith("pmid:") or b.isdigit():
            normalized_benchmarks.append(f"pmid:{normalize_pmid(b)}")
        else:
            normalized_benchmarks.append(b.lower())

    captured_benchmarks = [b for b in normalized_benchmarks if b in observed_keys]
    missed_benchmarks = [b for b in normalized_benchmarks if b not in observed_keys]
    sensitivity_pct = (
        round(100.0 * len(captured_benchmarks) / len(normalized_benchmarks), 2)
        if normalized_benchmarks
        else 100.0
    )

    unique_count = int(dedup_result.get("unique_count", 0))
    yield_delta_pct = 0.0
    if baseline_unique_count is not None and baseline_unique_count > 0:
        yield_delta_pct = round(
            100.0 * (unique_count - baseline_unique_count) / float(baseline_unique_count),
            2,
        )

    flags: list[str] = []
    if normalized_benchmarks and sensitivity_pct < min_sensitivity_pct:
        flags.append("BENCHMARK_RECALL_BELOW_THRESHOLD")
    if baseline_unique_count is not None and yield_delta_pct < -abs(max_yield_drop_pct):
        flags.append("SEVERE_YIELD_DROP_DRIFT")
    if int(dedup_result.get("pubmed_raw_count", 0)) == 0 or int(dedup_result.get("europe_pmc_raw_count", 0)) == 0:
        flags.append("SINGLE_SOURCE_SCOPE_GAP")
    elif int(dedup_result.get("cross_db_overlap_count", 0)) == 0:
        flags.append("ZERO_CROSS_DB_OVERLAP")
    if float(dedup_result.get("missing_identifier_pct", 0.0)) > 20.0:
        flags.append("HIGH_MISSING_IDENTIFIER_RATE")

    return {
        "passed": len(flags) == 0,
        "sensitivity_pct": sensitivity_pct,
        "min_sensitivity_pct": min_sensitivity_pct,
        "benchmark_total": len(normalized_benchmarks),
        "benchmark_captured_count": len(captured_benchmarks),
        "benchmark_missed": missed_benchmarks,
        "baseline_unique_count": baseline_unique_count,
        "yield_delta_pct": yield_delta_pct,
        "scope_drift_flags": flags,
    }


def validate_prisma_search_yield(
    payload: dict[str, Any],
    *,
    min_sensitivity_pct: float = 95.0,
    max_yield_drop_pct: float = 25.0,
) -> dict[str, Any]:
    """Run end-to-end PRISMA search-strategy deduplication and yield sensitivity validation."""
    pubmed = list(payload.get("pubmed_records") or [])
    epmc = list(payload.get("europe_pmc_records") or [])
    benchmarks = list(payload.get("benchmark_ids") or [])
    baseline = payload.get("baseline_unique_count")
    baseline_int = int(baseline) if baseline is not None else None

    dedup = deduplicate_exports(pubmed, epmc)
    sensitivity = evaluate_search_strategy_sensitivity(
        dedup,
        benchmark_ids=benchmarks,
        baseline_unique_count=baseline_int,
        min_sensitivity_pct=min_sensitivity_pct,
        max_yield_drop_pct=max_yield_drop_pct,
    )
    return {
        "passed": sensitivity["passed"],
        "deduplication": {k: v for k, v in dedup.items() if k != "unique_records"},
        "sensitivity": sensitivity,
        "unique_records": dedup["unique_records"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PRISMA Search-Strategy Yield Sensitivity & Deduplication Scope Validator."
    )
    parser.add_argument("input_json", type=Path, help="Path to JSON export bundle (pubmed_records, europe_pmc_records, benchmark_ids)")
    parser.add_argument("--min-sensitivity", type=float, default=95.0, help="Minimum required benchmark recall (%%)")
    parser.add_argument("--max-yield-drop", type=float, default=25.0, help="Maximum allowed unique yield drop vs baseline (%%)")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = validate_prisma_search_yield(
        payload,
        min_sensitivity_pct=args.min_sensitivity,
        max_yield_drop_pct=args.max_yield_drop,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
