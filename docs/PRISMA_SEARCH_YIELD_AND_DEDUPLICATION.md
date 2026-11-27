# PRISMA Search-Strategy Yield Sensitivity & Deduplication Scope

**Module:** [`src/prisma_search_yield_validator.py`](../src/prisma_search_yield_validator.py)  
**Tests:** [`tests/test_prisma_search_yield_validator.py`](../tests/test_prisma_search_yield_validator.py)  
**Practitioner Motivation:** Incorporated directly from clinical-research practitioner feedback (Dr. Peyrottes, HEGP) on quantifying database overlap and detecting silent scope loss before full-text screening.

---

## 1. Why Deduplication Scope & Search-Strategy Yield Matter

When combining exports from **PubMed** and **Europe PMC**, two silent failure modes can compromise a PRISMA 2020 systematic review:
1. **Over-deduplication / Under-deduplication:** Incomplete DOI or PMID metadata causes duplicate records to survive into manual screening, or overly loose title matching merges distinct studies.
2. **Search-Strategy Drift / Scope Loss:** An overly restrictive MeSH/boolean filter silently drops known benchmark trials (e.g., missing a pivotal RCT) before screening even begins.

---

## 2. Deterministic Deduplication & Sensitivity Pipeline

`prisma_search_yield_validator.py` runs **100% offline** (pure Python 3.12 standard library, zero network calls) and computes:
- **Canonical Key Matching (`record_keys`)**: Matches records deterministically across `doi:...`, `pmid:...`, and normalized `title:...|year:...` fallback keys.
- **Database Overlap & Yield (`deduplicate_exports`)**: Quantifies raw PubMed vs. Europe PMC counts, unique canonical clusters, cross-database overlap rate, and records missing both DOI and PMID.
- **Benchmark Sensitivity Check**: Evaluates recall against a known reference set of included trials and flags suspicious scope loss.

---

## 3. Quick CLI Usage

```bash
python3 -m src.prisma_search_yield_validator \
  --pubmed pubmed_export.json \
  --europe-pmc epmc_export.json \
  --benchmark benchmark_pmids.json
```
