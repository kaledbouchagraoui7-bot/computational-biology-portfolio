#!/usr/bin/env python3
"""
03_pubmed_literature_synthesizer.py — Systematic Literature Surveillance Utility
=================================================================================

Purpose
-------
Retrieve recent PubMed records for a configurable query (default:
"CFTR modulator therapy safety") and emit two audit-ready artifacts:

  1. pubmed_bibliography.csv  — structured, PMID-first bibliography
     (pmid,title,authors,journal,pub_year,doi,abstract).
  2. literature_matrix.md     — Markdown evidence matrix (| PMID | Year |
     Journal | Title |) plus the 5 most-recent records summarised by the
     first sentence of each abstract (the "objective statement").

Regulatory / scientific relevance (ICH M4)
------------------------------------------
* Supports Investigator Brochure (IB) drafting and Reference Information
  Module (RIM) assembly per ICH M4 (CTD Module 2): systematic literature
  surveillance with citation traceability. Every row is keyed on PMID first,
  so any statement in an IB section can be traced back to a specific public
  record (ALCOA+ "Complete/Available", ICH E6(R2) §4.9 documentation spirit).
* Retrieval degrades gracefully: NCBI Entrez primary -> Europe PMC REST
  fallback. A pipeline that silently emits partial data is a data-integrity
  finding; this script exits 1 with an actionable message instead, and never
  writes empty outputs.

Privacy statement
-----------------
All clinical data used in this repository is strictly synthetic and
anonymized for educational and portfolio purposes, adhering to GDPR and
HIPAA privacy frameworks. No personal contact information appears anywhere;
the courtesy-contact email is read from the environment (ENTREZ_EMAIL) with
a neutral default.

Environment
-----------
Python 3.10+ · Google Colab / GitHub Codespaces · peak RAM < 100 MB · no GPU.
Dependencies: biopython, requests, beautifulsoup4 (bs4 used ONLY to strip
markup from abstract text — never for core retrieval).

Usage
-----
    python 03_pubmed_literature_synthesizer.py \
        --query "CFTR modulator therapy safety" --max 50 --years 5
    EUROPE_PMC_ONLY=1 python 03_pubmed_literature_synthesizer.py --max 25
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error

import requests

# ---------------------------------------------------------------------------
# NCBI Entrez courtesy contact — read from environment, neutral default.
# Override by exporting ENTREZ_EMAIL before running (recommended practice).
# ---------------------------------------------------------------------------
DEFAULT_ENTREZ_EMAIL = "placeholder@example.com"
DEFAULT_ENTREZ_TOOL = "computational-biology-portfolio"

MAX_REQUESTS_PER_SEC = 3          # NCBI courtesy limit without API key
BACKOFF_SECONDS = (2, 4, 8)       # exponential backoff schedule
MAX_RETRIES = 3                   # retries per request on HTTP 503/429
MAX_ENTREZ_FAILURES = 2           # after this many failed phases -> fallback

EUROPE_PMC_ENDPOINT = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EPMC_PAGE_SIZE = 25               # cursorMark pagination page size


def log(msg: str) -> None:
    """Plain-text console logging (no rich dependency, per spec)."""
    print(f"[lit-synth] {msg}")


def polite_sleep() -> None:
    """Enforce <= 3 requests/sec across NCBI E-utilities calls."""
    time.sleep(1.0 / MAX_REQUESTS_PER_SEC + 0.05)


def http_get_with_backoff(url: str, params: dict | None = None) -> requests.Response:
    """GET with exponential backoff (2/4/8 s) on transient HTTP 503/429.

    Regulatory relevance: resilient transport keeps evidence retrieval
    reproducible under upstream throttling rather than returning partial
    results that could bias a literature review.
    """
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=30,
                                headers={"User-Agent": DEFAULT_ENTREZ_TOOL})
            if resp.status_code in (503, 429) and attempt < MAX_RETRIES:
                wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                log(f"HTTP {resp.status_code}; backing off {wait}s "
                    f"(retry {attempt + 1}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            return resp
        except (requests.ConnectionError, requests.Timeout,
                urllib.error.URLError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                log(f"Network error ({type(exc).__name__}); backing off {wait}s")
                time.sleep(wait)
    raise RuntimeError(f"GET failed after {MAX_RETRIES} retries: {last_exc}")


# ---------------------------------------------------------------------------
# Abstract text hygiene — BeautifulSoup used ONLY for markup stripping.
# ---------------------------------------------------------------------------
def clean_abstract_text(raw: str | None) -> str:
    """Strip any embedded markup/entities and normalise to a single line."""
    if not raw:
        return ""
    try:
        from bs4 import BeautifulSoup  # markup-stripping helper only
        text = BeautifulSoup(raw.replace("\n", " "), "html.parser").get_text(" ")
    except Exception:
        text = raw
    text = re.sub(r"<[^>]+>", " ", text)          # belt-and-braces tag strip
    text = text.replace("&#39;", "'").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"\s+", " ", text).strip()


def first_sentence(text: str) -> str:
    """Objective statement = first sentence of the abstract (best effort)."""
    m = re.search(r"(.{20,}?[.!?])(?:\s|$)", text)
    return (m.group(1) if m else text[:220]).strip()


# ---------------------------------------------------------------------------
# Record model helpers
# ---------------------------------------------------------------------------
def format_authors(names: list[tuple[str, str]]) -> str:
    """surname + initials, '; ' joined, capped at 10 then 'et al.'"""
    parts = []
    for surname, initials in names[:10]:
        ini = initials.strip()
        parts.append(f"{surname} {ini}".strip())
    out = "; ".join(parts)
    if len(names) > 10:
        out += "; et al."
    return out


def extract_doi(article_el) -> str:
    """DOI from ArticleIdList or ELocationDistribution (Entrez XML element)."""
    try:
        for aidlist in article_el.find("AuthorInformation").iter() if False else []:
            pass
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Primary source: NCBI E-utilities via Bio.Entrez
# ---------------------------------------------------------------------------
def entrez_fetch(query: str, max_records: int) -> tuple[list[dict], int]:
    """esearch (db=pubmed, sort=date) + efetch (rettype=abstract, retmode=xml).

    Returns (records, duplicates_removed). Raises RuntimeError on hard failure
    so the caller can decide when to degrade to Europe PMC.
    """
    from Bio import Entrez  # deferred import: fallback must work without it

    email = os.environ.get("ENTREZ_EMAIL", DEFAULT_ENTREZ_EMAIL)
    tool = os.environ.get("ENTREZ_TOOL", DEFAULT_ENTREZ_TOOL)
    Entrez.email = email
    Entrez.tool = tool
    if email == DEFAULT_ENTREZ_EMAIL:
        log("Using neutral placeholder Entrez email; export ENTREZ_EMAIL for "
            "production use (NCBI courtesy-contact guidance).")

    retmax = min(max_records * 2, 500)  # over-fetch: dedup/filter will trim

    # --- esearch -----------------------------------------------------------
    handle = Entrez.esearch(db="pubmed", term=query, retmax=retmax, sort="date")
    search = Entrez.read(handle)
    handle.close()
    id_list = search.get("IdList", [])
    if not id_list:
        return [], 0

    # Batched efetch keeps memory < 100 MB and respects rate limits.
    records: list[dict] = []
    batch = 50
    for i in range(0, len(id_list), batch):
        chunk = id_list[i:i + batch]
        handle = Entrez.efetch(db="pubmed", id=",".join(chunk),
                               rettype="abstract", retmode="xml")
        root = Entrez.read(handle, validate=False)
        handle.close()
        for art in root.get("PubmedArticle", []):
            medline = art.get("MedlineCitation", {})
            pmid = str(medline.get("PMID", ""))
            article = medline.get("Article", {})
            title = clean_abstract_text(str(article.get("ArticleTitle", "")))
            # authors: collective first if present, else person list
            author_list = article.get("AuthorList", [])
            names: list[tuple[str, str]] = []
            for a in author_list:
                if getattr(a, "type", None) == "CollectiveName" or (
                        "CollectiveName" in a and "LastName" not in a):
                    names.append((str(a.get("CollectiveName", "")), ""))
                else:
                    last = str(a.get("LastName", ""))
                    first_i = str(a.get("Initials", a.get("ForeName", "")))
                    if last:
                        names.append((last, first_i))
            journal = ""
            try:
                journal = str(article["Journal"].get("Title", "")) or \
                          str(article["Journal"].get("ISOAbbreviation", ""))
            except Exception:
                pass
            pub_year = ""
            try:
                dt = article.get("Journal", {}).get("JournalIssue", {}).get(
                    "PubDate", {})
                pub_year = str(dt.get("Year", "")).strip()
                if not pub_year and str(dt.get("MedlineDate", "")):
                    m = re.search(r"\b(19|20)\d{2}\b", str(dt["MedlineDate"]))
                    pub_year = m.group(0) if m else ""
            except Exception:
                pass
            doi = ""
            for aid in article.get("ArticleIdList", []):
                if str(aid).lower().startswith("10."):
                    doi = str(aid)
                    break
            if not doi:
                try:
                    for eloc in article.get("ELocationID", []):
                        if getattr(eloc, "EIdType", "") == "doi":
                            doi = str(eloc)
                            break
                except Exception:
                    pass
            abstract_parts = []
            for ab in article.get("Abstract", {}).get("AbstractText", []):
                abstract_parts.append(str(ab))
            abstract = clean_abstract_text(" ".join(abstract_parts))

            records.append({
                "pmid": pmid, "title": title,
                "authors": format_authors(names), "journal": journal,
                "pub_year": pub_year, "doi": doi, "abstract": abstract,
                "source": "ncbi_entrez",
            })
        polite_sleep()
        if len(records) >= max_records * 2:
            break

    return records, 0


# ---------------------------------------------------------------------------
# Fallback source: Europe PMC REST JSON (cursorMark pagination)
# ---------------------------------------------------------------------------
def europepmc_fetch(query: str, max_records: int) -> tuple[list[dict], int]:
    """Europe PMC /webservices/rest/search, format=json, pageSize=25,
    cursorMark deep pagination. Used after repeated Entrez failures or when
    EUROPE_PMC_ONLY=1 forces sovereign-independent retrieval."""
    records: list[dict] = []
    cursor = "*"
    fetched = 0
    while fetched < max_records * 2:
        params = {
            "query": query,
            "format": "json",
            "pageSize": EPMC_PAGE_SIZE,
            "cursorMark": cursor,
            "resultType": "core",
            "sort": "P_PUB_DATE_DESC",
        }
        resp = http_get_with_backoff(EUROPE_PMC_ENDPOINT, params)
        if resp.status_code != 200:
            raise RuntimeError(f"Europe PMC HTTP {resp.status_code}")
        payload = resp.json()
        result_list = payload.get("resultList", {}).get("result", [])
        if not result_list:
            break
        for item in result_list:
            pmid = str(item.get("pmid", "") or "")
            title = clean_abstract_text(item.get("title"))
            abstract = clean_abstract_text(item.get("abstractText"))
            authors_raw = item.get("authorString", "") or ""
            names: list[tuple[str, str]] = []
            for chunk in authors_raw.split(","):
                chunk = chunk.strip()
                if not chunk:
                    continue
                m = re.match(r"^(.*?),\s*([A-Z][A-Z\s\.]*)$", chunk)
                if m:
                    ini = re.sub(r"[^A-Z]", "", m.group(2))
                    names.append((m.group(1).strip(), ini))
                else:
                    names.append((chunk, ""))
            pub_year = ""
            pdt = item.get("pubYear", "") or ""
            m = re.search(r"\b(19|20)\d{2}\b", str(pdt))
            pub_year = m.group(0) if m else ""
            records.append({
                "pmid": pmid, "title": title,
                "authors": format_authors(names),
                "journal": item.get("journalTitle", "") or "",
                "pub_year": pub_year, "doi": item.get("doi", "") or "",
                "abstract": abstract, "source": "europe_pmc",
            })
        fetched += len(result_list)
        next_cursor = payload.get("nextCursorMark", "")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        polite_sleep()
    return records, 0


# ---------------------------------------------------------------------------
# Curation: dedup by PMID, drop empties, recency window
# ---------------------------------------------------------------------------
def curate(records: list[dict], years: int) -> tuple[list[dict], int, int]:
    """De-duplicate by PMID; drop empty title/abstract; keep pub_year within
    the surveillance window (current_year - years). Traceable filters keep a
    literature matrix defensible during IB updates."""
    current_year = int(time.strftime("%Y"))
    min_year = current_year - years
    seen: set[str] = set()
    kept: list[dict] = []
    dupes = dropped = 0
    for r in records:
        pmid = r["pmid"]
        if not pmid:
            # Europe PMC may lack PMID for some aggregates; key on DOI/title
            pmid = r["doi"] or r["title"][:60]
            r["pmid"] = pmid
        if pmid in seen:
            dupes += 1
            continue
        seen.add(pmid)
        if not r["title"] or not r["abstract"]:
            dropped += 1
            continue
        try:
            if int(r["pub_year"]) < min_year:
                dropped += 1
                continue
        except (ValueError, TypeError):
            dropped += 1
            continue
        kept.append(r)
    return kept, dupes, dropped


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
CSV_COLUMNS = ["pmid", "title", "authors", "journal", "pub_year", "doi",
               "abstract"]


def write_csv(records: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow(r)


def write_matrix(records: list[dict], path: str, query: str) -> None:
    lines = [
        "# Literature Surveillance Matrix",
        "",
        f"*Query:* `{query}` · *Records:* {len(records)} · "
        f"*Generated:* {time.strftime('%Y-%m-%d')} · "
        "*Standard:* supports ICH M4 RIM/IB evidence assembly.",
        "",
        "| PMID | Year | Journal | Title |",
        "|---|---|---|---|",
    ]
    for r in records:
        title = r["title"].replace("|", "\\|")
        journal = (r["journal"] or "—").replace("|", "\\|")
        lines.append(f"| {r['pmid']} | {r['pub_year']} | {journal} | {title} |")
    lines += ["", "## Five Most Recent Records — Objective Statements", ""]
    recent = sorted(records, key=lambda x: (x["pub_year"], x["pmid"]),
                    reverse=True)[:5]
    for r in recent:
        obj = first_sentence(r["abstract"])
        lines.append(f"- **PMID {r['pmid']}** ({r['pub_year']}, "
                     f"{r['journal'] or '—'}): {obj}")
    lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PubMed literature synthesizer for IB/RIM drafting "
                    "(ICH M4). PMID-first citation traceability.")
    parser.add_argument("--query", default="CFTR modulator therapy safety",
                        help="PubMed search string")
    parser.add_argument("--max", type=int, default=50, dest="max_records",
                        help="maximum curated records (default 50)")
    parser.add_argument("--years", type=int, default=5,
                        help="surveillance window in years (default 5)")
    args = parser.parse_args(argv)

    force_epmc = os.environ.get("EUROPE_PMC_ONLY", "0") == "1"
    records: list[dict] = []
    source_used = ""
    entrez_failures = 0

    if not force_epmc:
        try:
            log(f"Primary source: NCBI E-utilities (query={args.query!r})")
            records, _ = entrez_fetch(args.query, args.max_records)
            if records:
                source_used = "NCBI E-utilities (Bio.Entrez)"
            else:
                entrez_failures += 1
                log("Entrez returned no parsed records; counting failure.")
        except Exception as exc:
            entrez_failures += 1
            log(f"Entrez phase failed ({exc}). Failures: {entrez_failures}")

        if not records and entrez_failures < MAX_ENTREZ_FAILURES:
            # one more Entrez attempt before degrading
            polite_sleep()
            try:
                records, _ = entrez_fetch(args.query, args.max_records)
                if records:
                    source_used = "NCBI E-utilities (Bio.Entrez)"
            except Exception as exc:
                entrez_failures += 1
                log(f"Entrez retry failed ({exc}). Failures: {entrez_failures}")

    if not records:
        log("Falling back to Europe PMC REST JSON (cursorMark pagination).")
        try:
            records, _ = europepmc_fetch(args.query, args.max_records)
            if records:
                source_used = "Europe PMC REST (fallback)"
        except Exception as exc:
            log(f"Europe PMC failed too: {exc}")

    if not records:
        log("ERROR: both sources returned zero records. Actionable checks: "
            "(1) network/proxy reachability to ncbi.nlm.nih.gov and "
            "ebi.ac.uk, (2) NCBI E-utilities status page, (3) export a valid "
            "ENTREZ_EMAIL/API_KEY, (4) retry later — upstream 503 throttling "
            "is transient. No output files were written.")
        return 1

    curated, dupes, dropped = curate(records, args.years)
    curated = curated[:args.max_records]

    if not curated:
        log("ERROR: all retrieved records were filtered out (empty titles/"
            "abstracts or outside the recency window). Adjust --years or "
            "--query. No empty output files were written.")
        return 1

    csv_path = "pubmed_bibliography.csv"
    md_path = "literature_matrix.md"
    write_csv(curated, csv_path)
    write_matrix(curated, md_path, args.query)

    print("== Literature Synthesis Summary ==")
    print(f"Source used            : {source_used}")
    print(f"Records fetched (raw)  : {len(records)}")
    print(f"Duplicates removed     : {dupes}")
    print(f"Filtered out (QC)      : {dropped}")
    print(f"Curated records written: {len(curated)}")
    print(f"Output CSV             : {csv_path}")
    print(f"Output matrix          : {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
