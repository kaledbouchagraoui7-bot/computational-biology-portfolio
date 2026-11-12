#!/usr/bin/env python3
"""
Ground-Truth Benchmark: Reproducing PubMed/MEDLINE Identification & Cross-Database
Deduplication Against a Published Peer-Reviewed PRISMA 2020 Systematic Review.

Reference Paper:
  "Sports genomics research in Turkish athlete populations 2019 to 2026: a systematic review"
  PMCID: PMC13739473 | PMID: 42992339 (Published 2026)
  Reported PubMed/MEDLINE count (Table 2, 2019/01/01-2026/04/01): 225 records
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()

PUBMED_QUERY = (
    '("sport"[Title/Abstract] OR "athlete"[Title/Abstract] OR '
    '"athletic performance"[Title/Abstract] OR "spor"[Title/Abstract]) AND '
    '("genetic"[Title/Abstract] OR "genetik"[Title/Abstract]) AND '
    '("polymorphism"[Title/Abstract] OR "genotype"[Title/Abstract] OR '
    '"polimorfizm"[Title/Abstract]) AND '
    '("2019/01/01"[Date - Publication] : "2026/04/01"[Date - Publication])'
)

EPMC_QUERY = (
    '(TITLE_ABS:"sport" OR TITLE_ABS:"athlete" OR TITLE_ABS:"athletic performance" OR TITLE_ABS:"spor") AND '
    '(TITLE_ABS:"genetic" OR TITLE_ABS:"genetik") AND '
    '(TITLE_ABS:"polymorphism" OR TITLE_ABS:"genotype" OR TITLE_ABS:"polimorfizm") AND '
    'PUB_YEAR:[2019 TO 2026]'
)


def fetch_pubmed_ids_and_summaries(query: str) -> tuple[int, list[dict]]:
    esearch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(
        {"db": "pubmed", "term": query, "retmax": 500, "retmode": "json"}
    )
    res = json.loads(urllib.request.urlopen(esearch_url, timeout=25).read().decode("utf-8"))
    total_count = int(res["esearchresult"]["count"])
    pmids = res["esearchresult"]["idlist"]

    records: list[dict] = []
    for i in range(0, len(pmids), 100):
        batch = pmids[i : i + 100]
        sum_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urllib.parse.urlencode(
            {"db": "pubmed", "id": ",".join(batch), "retmode": "json"}
        )
        sdata = json.loads(urllib.request.urlopen(sum_url, timeout=25).read().decode("utf-8")).get("result", {})
        for pid in batch:
            item = sdata.get(pid, {})
            doi = ""
            for aid in item.get("articleids", []):
                if aid.get("idtype") == "doi":
                    doi = aid.get("value", "").strip().lower()
                    break
            records.append(
                {
                    "source": "PubMed",
                    "pmid": str(pid),
                    "doi": doi,
                    "title": (item.get("title") or "").strip(),
                    "year": (item.get("pubdate") or "")[:4],
                }
            )
    return total_count, records


def fetch_epmc_records(query: str) -> tuple[int, list[dict]]:
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(
        {"query": query, "format": "json", "pageSize": 500}
    )
    data = json.loads(urllib.request.urlopen(url, timeout=25).read().decode("utf-8"))
    hit_count = int(data.get("hitCount", 0))
    out: list[dict] = []
    for r in data.get("resultList", {}).get("result", []):
        out.append(
            {
                "source": "Europe PMC",
                "pmid": str(r.get("pmid") or "").strip(),
                "doi": str(r.get("doi") or "").strip().lower(),
                "title": str(r.get("title") or "").strip(),
                "year": str(r.get("pubYear") or ""),
            }
        )
    return hit_count, out


def deduplicate_records(records: list[dict]) -> tuple[list[dict], int]:
    seen_pmids: set[str] = set()
    seen_dois: set[str] = set()
    seen_titles: set[str] = set()
    unique: list[dict] = []
    dups = 0
    for rec in records:
        pid = rec["pmid"]
        doi = rec["doi"]
        norm_title = _norm_title(rec["title"])
        is_dup = (
            (pid and pid in seen_pmids)
            or (doi and doi in seen_dois)
            or (norm_title and norm_title in seen_titles)
        )
        if is_dup:
            dups += 1
            continue
        if pid:
            seen_pmids.add(pid)
        if doi:
            seen_dois.add(doi)
        if norm_title:
            seen_titles.add(norm_title)
        unique.append(rec)
    return unique, dups


def main() -> int:
    pm_count, pm_records = fetch_pubmed_ids_and_summaries(PUBMED_QUERY)
    epmc_count, epmc_records = fetch_epmc_records(EPMC_QUERY)
    combined = pm_records + epmc_records
    unique, dups = deduplicate_records(combined)

    turkish_hits = [
        r for r in unique
        if any(k in r["title"].lower() for k in ("turk", "türk", "turkey", "türkiye", "anatolia"))
    ]

    summary = {
        "benchmark_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reference_paper": {
            "pmcid": "PMC13739473",
            "pmid": "42992339",
            "title": "Sports genomics research in Turkish athlete populations 2019 to 2026: a systematic review",
            "published_pubmed_count": 225,
            "published_all_7_db_identified": 1740,
            "published_all_7_db_unique": 1023,
            "published_included_from_db": 61,
        },
        "pipeline_reproduction": {
            "pubmed_live_count": pm_count,
            "pubmed_delta_vs_paper": pm_count - 225,
            "europe_pmc_live_count": epmc_count,
            "combined_raw": len(combined),
            "duplicates_removed": dups,
            "unique_pool": len(unique),
            "title_turkish_keyword_hits_sample": len(turkish_hits),
        },
    }

    art_dir = HERE / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    out_json = art_dir / "ground_truth_benchmark.json"
    out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
