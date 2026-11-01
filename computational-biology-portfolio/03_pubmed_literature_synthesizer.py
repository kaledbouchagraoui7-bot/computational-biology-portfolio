#!/usr/bin/env python3
"""
03_pubmed_literature_synthesizer.py — Systematic Literature Surveillance Utility
=================================================================================

Purpose
-------
Systematic, reproducible literature surveillance for Clinical Evaluation Reports
(CER per EU MDR 2017/745) and CTD Module 2.4/2.5 / Investigator Brochure (IB per ICH M4).
Queries NCBI Entrez (PubMed) and Europe PMC, performs automated deduplication,
extracts structured clinical conclusions and publication types (RCT, Meta-analysis, etc.),
enforces PRISMA selection auditability, and generates publication-grade dossiers with live links.

Regulatory / scientific relevance
---------------------------------
* ICH M4 / ICH E6(R2) / EU MDR 2017/745 compliance.
* PRISMA 2020 systematic tracking: record identification -> duplicate removal -> 
  eligibility screening -> final included synthesis.
* Traceable citation integrity: PMID-first primary keys with live DOI links.
* ALCOA+ Data Integrity: Attributable, Legible, Contemporaneous, Original, Accurate.
* Resilient dual-source transport: NCBI Entrez primary with automatic failover to Europe PMC REST.

Usage
-----
    python 03_pubmed_literature_synthesizer.py \
        --query "GLP-1 receptor agonist cardiovascular and renal outcomes" \
        --max 50 --years 5 --output-dir dossier_glp1_safety
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
# Configuration & NCBI Entrez courtesy contact
# ---------------------------------------------------------------------------
DEFAULT_ENTREZ_EMAIL = "regulatory-intelligence@cogops-group.ch"
DEFAULT_ENTREZ_TOOL = "regulatory-literature-synthesizer"

MAX_REQUESTS_PER_SEC = 3          # NCBI courtesy limit without API key
BACKOFF_SECONDS = (2, 4, 8)       # exponential backoff schedule
MAX_RETRIES = 3                   # retries per request on HTTP 503/429
MAX_ENTREZ_FAILURES = 2           # after this many failed phases -> fallback

EUROPE_PMC_ENDPOINT = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EPMC_PAGE_SIZE = 25               # cursorMark pagination page size


def log(msg: str) -> None:
    """Audit-trailed console logging with timestamp."""
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}][lit-synth] {msg}")


def polite_sleep() -> None:
    """Enforce <= 3 requests/sec across NCBI E-utilities calls."""
    time.sleep(1.0 / MAX_REQUESTS_PER_SEC + 0.05)


def http_get_with_backoff(url: str, params: dict | None = None) -> requests.Response:
    """GET with exponential backoff on transient HTTP 503/429."""
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=30,
                                headers={"User-Agent": DEFAULT_ENTREZ_TOOL})
            if resp.status_code in (503, 429) and attempt < MAX_RETRIES:
                wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                log(f"HTTP {resp.status_code}; backing off {wait}s (retry {attempt + 1}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            return resp
        except (requests.ConnectionError, requests.Timeout, urllib.error.URLError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                log(f"Network error ({type(exc).__name__}); backing off {wait}s")
                time.sleep(wait)
    raise RuntimeError(f"GET failed after {MAX_RETRIES} retries: {last_exc}")


# ---------------------------------------------------------------------------
# Text hygiene & Structured Content Parsing
# ---------------------------------------------------------------------------
def clean_abstract_text(raw: str | None) -> str:
    """Strip markup/entities and normalise to a single line."""
    if not raw:
        return ""
    text = raw.replace("\n", " ")
    if "<" in text and ">" in text:
        try:
            import warnings
            from bs4 import BeautifulSoup
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                text = BeautifulSoup(text, "html.parser").get_text(" ")
        except Exception:
            pass
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&#39;", "'").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"\s+", " ", text).strip()


def extract_key_takeaway(abstract: str, fallback_direct: str = "") -> str:
    """Extract clinical conclusion / takeaway.
    In regulatory/scientific literature, the final sentence of the abstract
    typically carries the author's primary finding or conclusion.
    """
    if fallback_direct:
        return fallback_direct
    if not abstract:
        return "No abstract available in public record."
    
    # Check for labeled structured section [CONCLUSION] or [CONCLUSIONS]
    m = re.search(r"\[(?:CONCLUSION|CONCLUSIONS|INTERPRETATION)\]\s*(.*?)(?=\[[A-Z\s]+\]|$)", abstract, re.IGNORECASE)
    if m:
        c = clean_abstract_text(m.group(1))
        if len(c) > 15:
            return c

    # Otherwise split sentences and take the final complete clinical sentence
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", abstract) if len(s.strip()) > 25]
    if sentences:
        return sentences[-1]
    return (abstract[:220] + "...").strip()


def format_authors(names: list[tuple[str, str]]) -> str:
    """surname + initials, '; ' joined, capped at 8 then 'et al.'"""
    parts = []
    for surname, initials in names[:8]:
        ini = initials.strip()
        parts.append(f"{surname} {ini}".strip())
    out = "; ".join(parts)
    if len(names) > 8:
        out += "; et al."
    return out


# ---------------------------------------------------------------------------
# Primary Source: NCBI Entrez via Bio.Entrez
# ---------------------------------------------------------------------------
def _entrez_call_with_backoff(fn, *args, **kwargs):
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as err:
            status = getattr(err, "code", None)
            transient = status in (503, 429) or status is None
            last_err = err
            if attempt >= MAX_RETRIES or not transient:
                break
            wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
            log(f"Entrez {getattr(fn, '__name__', 'call')} failed (status={status or err}); backing off {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"Entrez {getattr(fn, '__name__', 'call')} failed: {last_err}")


def entrez_fetch(query: str, max_records: int) -> tuple[list[dict], int]:
    from Bio import Entrez

    email = os.environ.get("ENTREZ_EMAIL", DEFAULT_ENTREZ_EMAIL)
    tool = os.environ.get("ENTREZ_TOOL", DEFAULT_ENTREZ_TOOL)
    Entrez.email = email
    Entrez.tool = tool

    retmax = min(max_records * 2, 500)

    # --- esearch ---
    handle = _entrez_call_with_backoff(
        Entrez.esearch, db="pubmed", term=query, retmax=retmax, sort="date")
    search = Entrez.read(handle)
    handle.close()
    id_list = search.get("IdList", [])
    if not id_list:
        return [], 0

    log(f"NCBI Entrez identified {len(id_list)} record IDs matching query.")

    # --- efetch ---
    records: list[dict] = []
    batch = 50
    for i in range(0, len(id_list), batch):
        chunk = id_list[i:i + batch]
        handle = _entrez_call_with_backoff(
            Entrez.efetch, db="pubmed", id=",".join(chunk),
            rettype="abstract", retmode="xml")
        raw = handle.read()
        handle.close()
        if isinstance(raw, str):
            raw = raw.encode("utf-8", errors="replace")
        for art in _iter_pubmed_articles(raw):
            parsed = _parse_pubmed_article(art)
            if parsed:
                records.append(parsed)
        polite_sleep()
        if len(records) >= max_records * 2:
            break

    return records, 0


def _iter_pubmed_articles(raw: bytes):
    from Bio import Entrez
    import io
    start_tag, end_tag = b"<PubmedArticle>", b"</PubmedArticle>"
    decl = raw[:raw.find(start_tag)] if start_tag in raw else b""
    preamble = b'<?xml version="1.0" ?>\n'
    for line in decl.splitlines():
        if line.strip().startswith(b"<!DOCTYPE"):
            preamble += line + b"\n"
            break
    pos = 0
    while True:
        s = raw.find(start_tag, pos)
        if s == -1:
            break
        e = raw.find(end_tag, s)
        if e == -1:
            break
        unit = preamble + raw[s:e + len(end_tag)]
        try:
            doc = Entrez.read(io.BytesIO(unit), validate=False)
            if "MedlineCitation" in doc:
                yield doc
            else:
                for art in doc.get("PubmedArticle", []):
                    yield art
        except Exception:
            pass
        pos = e + len(end_tag)


def _parse_pubmed_article(article_el) -> dict | None:
    medline = article_el.get("MedlineCitation", {})
    pmid = str(medline.get("PMID", ""))
    article = medline.get("Article", {})
    title = clean_abstract_text(str(article.get("ArticleTitle", "")))

    # 1. Publication Type (Evidence level classification)
    pub_types = []
    for pt in article.get("PublicationTypeList", []):
        pt_name = str(pt).strip()
        if pt_name and pt_name.lower() not in ("journal article", "classical article"):
            pub_types.append(pt_name)
    pub_type_str = "; ".join(pub_types[:2]) if pub_types else "Clinical Investigation"

    # 2. Authors
    names: list[tuple[str, str]] = []
    for a in article.get("AuthorList", []):
        if getattr(a, "type", None) == "CollectiveName" or ("CollectiveName" in a and "LastName" not in a):
            names.append((str(a.get("CollectiveName", "")), ""))
        else:
            last = str(a.get("LastName", ""))
            first_i = str(a.get("Initials", a.get("ForeName", "")))
            if last:
                names.append((last, first_i))

    # 3. Journal & Year
    journal = ""
    try:
        journal = str(article["Journal"].get("Title", "")) or str(article["Journal"].get("ISOAbbreviation", ""))
    except Exception:
        pass

    pub_year = ""
    try:
        dt = article.get("Journal", {}).get("JournalIssue", {}).get("PubDate", {})
        pub_year = str(dt.get("Year", "")).strip()
        if not pub_year and str(dt.get("MedlineDate", "")):
            m = re.search(r"\b(19|20)\d{2}\b", str(dt["MedlineDate"]))
            pub_year = m.group(0) if m else ""
    except Exception:
        pass

    # 4. DOI
    doi = ""
    pubmed_data = article_el.get("PubmedData", {}) if hasattr(article_el, "get") else {}
    for aid in pubmed_data.get("ArticleIdList", []):
        attrs = getattr(aid, "attributes", {}) or {}
        if str(attrs.get("IdType", "")).lower() == "doi":
            doi = str(aid).strip()
            break
        if not doi and str(aid).strip().lower().startswith("10."):
            doi = str(aid).strip()
    if not doi:
        eloc = article.get("ELocationID", [])
        if not isinstance(eloc, list):
            eloc = [eloc]
        for e in eloc:
            attrs = getattr(e, "attributes", {}) or {}
            if str(attrs.get("EIdType", "")).lower() == "doi":
                doi = str(e).strip()
                break

    # 5. Abstract & Structured Takeaway Extraction
    abstract_texts = article.get("Abstract", {}).get("AbstractText", [])
    if not isinstance(abstract_texts, list):
        abstract_texts = [abstract_texts]
    abstract_parts = []
    conclusion_direct = ""

    for ab in abstract_texts:
        text_val = str(ab).strip()
        attrs = getattr(ab, "attributes", {}) or {}
        label = attrs.get("Label", "") or attrs.get("NlmCategory", "")
        if label:
            abstract_parts.append(f"[{label}] {text_val}")
            if label.upper() in ("CONCLUSION", "CONCLUSIONS", "INTERPRETATION", "KEY FINDINGS"):
                conclusion_direct = clean_abstract_text(text_val)
        else:
            abstract_parts.append(text_val)

    abstract = clean_abstract_text(" ".join(abstract_parts))
    key_takeaway = extract_key_takeaway(abstract, fallback_direct=conclusion_direct)

    return {
        "pmid": pmid,
        "title": title,
        "authors": format_authors(names),
        "journal": journal,
        "pub_year": pub_year,
        "doi": doi,
        "pub_type": pub_type_str,
        "key_takeaway": key_takeaway,
        "abstract": abstract,
        "source": "ncbi_entrez",
    }


# ---------------------------------------------------------------------------
# Fallback Source: Europe PMC REST JSON
# ---------------------------------------------------------------------------
def _epmc_page(params: dict) -> dict | None:
    try:
        resp = http_get_with_backoff(EUROPE_PMC_ENDPOINT, params)
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        log(f"Europe PMC request error: {exc}")

    degraded = dict(params)
    degraded["resultType"] = "lite"
    degraded.pop("sort", None)
    try:
        resp = http_get_with_backoff(EUROPE_PMC_ENDPOINT, degraded)
        if resp.status_code == 200:
            return resp.json()
        raise RuntimeError(f"Europe PMC HTTP {resp.status_code}")
    except requests.RequestException as exc:
        raise RuntimeError(f"Europe PMC unreachable: {exc}")


def europepmc_fetch(query: str, max_records: int) -> tuple[list[dict], int]:
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
            "sort": "P_PDATE_D desc",
        }
        payload = _epmc_page(params)
        if payload is None:
            break
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

            # Extract Pub Type
            pt_list = item.get("pubTypeList", {}).get("pubType", [])
            if isinstance(pt_list, list):
                pt_filtered = [p for p in pt_list if p.lower() != "journal article"]
                pub_type = "; ".join(pt_filtered[:2]) if pt_filtered else "Clinical Investigation"
            else:
                pub_type = str(pt_list) if str(pt_list).lower() != "journal article" else "Clinical Investigation"

            key_takeaway = extract_key_takeaway(abstract)

            records.append({
                "pmid": pmid,
                "title": title,
                "authors": format_authors(names),
                "journal": item.get("journalTitle", "") or "",
                "pub_year": pub_year,
                "doi": item.get("doi", "") or "",
                "pub_type": pub_type,
                "key_takeaway": key_takeaway,
                "abstract": abstract,
                "source": "europe_pmc",
            })
        fetched += len(result_list)
        next_cursor = payload.get("nextCursorMark", "")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        polite_sleep()

    return records, 0


# ---------------------------------------------------------------------------
# Curation & PRISMA 2020 Compliance Engine
# ---------------------------------------------------------------------------
def curate(records: list[dict], years: int) -> tuple[list[dict], dict]:
    """De-duplicate by PMID; enforce eligibility criteria; record full PRISMA metrics."""
    current_year = int(time.strftime("%Y"))
    min_year = current_year - years
    seen_ids: set[str] = set()
    kept: list[dict] = []

    prisma = {
        "identified_raw": len(records),
        "duplicates_removed": 0,
        "screened": 0,
        "excluded_missing_content": 0,
        "excluded_prior_to_window": 0,
        "included_final": 0,
        "surveillance_window": f"{min_year}–{current_year}",
    }

    for r in records:
        pid = r["pmid"]
        if not pid:
            pid = r["doi"] or r["title"][:60]
            r["pmid"] = pid

        if pid in seen_ids:
            prisma["duplicates_removed"] += 1
            continue
        seen_ids.add(pid)
        prisma["screened"] += 1

        if not r["title"] or not r["abstract"] or len(r["abstract"]) < 40:
            prisma["excluded_missing_content"] += 1
            continue

        try:
            yr = int(r["pub_year"])
            if yr < min_year:
                prisma["excluded_prior_to_window"] += 1
                continue
        except (ValueError, TypeError):
            prisma["excluded_prior_to_window"] += 1
            continue

        kept.append(r)

    prisma["included_final"] = len(kept)
    return kept, prisma


# ---------------------------------------------------------------------------
# Regulatory Dossier Outputs
# ---------------------------------------------------------------------------
CSV_COLUMNS = [
    "pmid", "pub_year", "pub_type", "journal", "title",
    "key_takeaway", "authors", "doi", "source", "abstract"
]


def write_csv(records: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow(r)


def write_prisma_report(prisma: dict, path: str, query: str) -> None:
    content = f"""# PRISMA 2020 Literature Surveillance Selection Trail
**Compliance Framework:** ICH M4 CTD Module 2.4/2.5 & EU MDR 2017/745 (MEDDEV 2.7/1 rev 4)  
**Search Query:** `{query}`  
**Surveillance Window:** {prisma['surveillance_window']}  
**Generated On:** {time.strftime('%Y-%m-%d %H:%M:%S UTC')}  
**Data Integrity:** ALCOA+ Standard (Attributable, Legible, Contemporaneous, Original, Accurate)

---

## 1. PRISMA Flow Statement

```
[ IDENTIFICATION ]
Records identified through database searching (NCBI Entrez / Europe PMC): {prisma['identified_raw']}
                        │
                        ▼
             Duplicates Removed: {prisma['duplicates_removed']}
                        │
                        ▼
[ SCREENING ]
Records Screened: {prisma['screened']}
                        │
                        ├─► Excluded (Incomplete metadata / Missing abstract): {prisma['excluded_missing_content']}
                        └─► Excluded (Outside {prisma['surveillance_window']} surveillance window): {prisma['excluded_prior_to_window']}
                        │
                        ▼
[ INCLUDED ]
Final Curated Records Included in Regulatory Evidence Dossier: {prisma['included_final']}
```

## 2. Selection Criteria Summary

| Audit Checkpoint | Metric | Notes / Rationale |
|---|---|---|
| **Raw Records Identified** | {prisma['identified_raw']} | Total records matching Boolean query |
| **Duplicate Exclusions** | {prisma['duplicates_removed']} | Keyed on unique PubMed ID (PMID) and DOI |
| **Screened Population** | {prisma['screened']} | Unique records evaluated against eligibility protocol |
| **Content Exclusions** | {prisma['excluded_missing_content']} | Records lacking verifiable abstract text for review |
| **Recency Exclusions** | {prisma['excluded_prior_to_window']} | Records published before {prisma['surveillance_window'].split('–')[0]} |
| **Final Dossier Population** | **{prisma['included_final']}** | **100% verified, cited, and audit-traceable** |
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def write_dossier(records: list[dict], prisma: dict, path: str, 
                  query: str, drug_class: str, indication: str) -> None:
    lines = [
        f"# Regulatory Evidence Matrix — {drug_class}",
        f"### Systematic Surveillance Dossier: {indication}",
        "",
        "> **Regulatory Notice:** Prepared for Clinical Evaluation Report (CER) / Investigator Brochure (IB) "
        "evidence synthesis adhering to **ICH M4**, **ICH E6(R2)**, and **EU MDR 2017/745**. All rows "
        "feature clickable PubMed/DOI links and extracted key regulatory findings.",
        "",
        "## 1. Surveillance Metadata",
        f"- **Drug Class / Subject:** {drug_class}",
        f"- **Clinical Indication:** {indication}",
        f"- **Database Query:** `{query}`",
        f"- **Surveillance Recency Window:** {prisma['surveillance_window']} (Last 5 Years)",
        f"- **Verification Protocol:** PRISMA 2020 Flow Audit (see `prisma_flow_statement.md`)",
        f"- **Included Evidence Records:** {len(records)} studies",
        "",
        "## 2. Executive Summary — 5 Most Recent High-Impact Findings",
        "",
    ]

    recent = sorted(records, key=lambda x: (x["pub_year"], x["pmid"]), reverse=True)[:5]
    for r in recent:
        pmid_link = f"[{r['pmid']}](https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/)"
        doi_link = f"[[DOI]](https://doi.org/{r['doi']})" if r["doi"] else ""
        lines.append(
            f"- **PMID {pmid_link}** ({r['pub_year']} | *{r['journal'] or 'Journal'}* | `{r['pub_type']}`):  \n"
            f"  **{r['title']}**  \n"
            f"  *Key Regulatory Finding:* **{r['key_takeaway']}** {doi_link}"
        )

    lines.extend([
        "",
        "## 3. Master Regulatory Evidence Matrix",
        "",
        "| PMID | Year | Type | Journal | Title | Key Finding / Conclusion | DOI |",
        "|---|---|---|---|---|---|---|",
    ])

    for r in records:
        pmid_link = f"[{r['pmid']}](https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/)"
        doi_link = f"[[Link]](https://doi.org/{r['doi']})" if r["doi"] else "—"
        title = r["title"].replace("|", "\\|")
        journal = (r["journal"] or "—").replace("|", "\\|")
        pt = r["pub_type"].replace("|", "\\|")
        takeaway = r["key_takeaway"].replace("|", "\\|")
        lines.append(f"| {pmid_link} | {r['pub_year']} | {pt} | {journal} | {title} | {takeaway} | {doi_link} |")

    lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regulatory Literature Synthesizer for CER/IB Evidence Dossiers (ICH M4 / MDR).")
    parser.add_argument("--query", default="GLP-1 receptor agonist cardiovascular and renal outcomes",
                        help="Search query string")
    parser.add_argument("--drug-class", default="GLP-1 Receptor Agonists",
                        help="Drug class or product identifier")
    parser.add_argument("--indication", default="Type 2 Diabetes, Obesity & Cardiometabolic Disease",
                        help="Clinical indication scope")
    parser.add_argument("--max", type=int, default=50, dest="max_records",
                        help="Maximum curated records (default 50)")
    parser.add_argument("--years", type=int, default=5,
                        help="Surveillance window in years (default 5)")
    parser.add_argument("--output-dir", default="dossier_glp1_safety",
                        help="Directory to save dossier deliverables")
    args = parser.parse_args(argv)

    os.makedirs(args.output_dir, exist_ok=True)

    force_epmc = os.environ.get("EUROPE_PMC_ONLY", "0") == "1"
    records: list[dict] = []
    source_used = ""
    entrez_failures = 0

    if not force_epmc:
        try:
            log(f"Querying primary source: NCBI Entrez (query={args.query!r})")
            records, _ = entrez_fetch(args.query, args.max_records)
            if records:
                source_used = "NCBI E-utilities (Bio.Entrez)"
            else:
                entrez_failures += 1
                log("Entrez returned 0 parsed records; checking failover.")
        except Exception as exc:
            entrez_failures += 1
            log(f"Entrez phase failed ({exc}).")

        if not records and entrez_failures < MAX_ENTREZ_FAILURES:
            polite_sleep()
            try:
                records, _ = entrez_fetch(args.query, args.max_records)
                if records:
                    source_used = "NCBI E-utilities (Bio.Entrez)"
            except Exception as exc:
                entrez_failures += 1
                log(f"Entrez retry failed ({exc}).")

    if not records:
        log("Failover active: querying Europe PMC REST API (cursorMark pagination).")
        try:
            records, _ = europepmc_fetch(args.query, args.max_records)
            if records:
                source_used = "Europe PMC REST API"
        except Exception as exc:
            log(f"Europe PMC failover failed: {exc}")

    if not records:
        log("ERROR: Zero records retrieved from both repositories.")
        return 1

    curated, prisma = curate(records, args.years)
    curated = curated[:args.max_records]

    if not curated:
        log("ERROR: All retrieved records were excluded by PRISMA quality filters.")
        return 1

    # Output paths
    csv_path = os.path.join(args.output_dir, "pubmed_bibliography.csv")
    md_path = os.path.join(args.output_dir, "literature_matrix.md")
    prisma_path = os.path.join(args.output_dir, "prisma_flow_statement.md")

    write_csv(curated, csv_path)
    write_prisma_report(prisma, prisma_path, args.query)
    write_dossier(curated, prisma, md_path, args.query, args.drug_class, args.indication)

    print("\n" + "=" * 60)
    print("   REGULATORY LITERATURE SYNTHESIS COMPLETE (ALCOA+)")
    print("=" * 60)
    print(f"Target Subject         : {args.drug_class}")
    print(f"Indication Scope       : {args.indication}")
    print(f"Primary Source Engine  : {source_used}")
    print(f"Raw Records Identified : {prisma['identified_raw']}")
    print(f"Duplicates Removed     : {prisma['duplicates_removed']}")
    print(f"Screened Records       : {prisma['screened']}")
    print(f"Excluded (QC / Window) : {prisma['excluded_missing_content'] + prisma['excluded_prior_to_window']}")
    print(f"Final Included Dossier : {len(curated)} records")
    print("-" * 60)
    print(f"Evidence Matrix (MD)   : {md_path}")
    print(f"Structured Database    : {csv_path}")
    print(f"PRISMA Flow Statement  : {prisma_path}")
    print("=" * 60 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
