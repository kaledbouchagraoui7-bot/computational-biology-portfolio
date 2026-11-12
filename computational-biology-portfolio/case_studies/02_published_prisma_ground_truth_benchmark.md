# Case Study 02: Ground-Truth Benchmark Against a Published Peer-Reviewed PRISMA 2020 Systematic Review

**Reference Systematic Review:** *Sports genomics research in Turkish athlete populations 2019 to 2026: a systematic review* ([PMC13739473](https://pmc.ncbi.nlm.nih.gov/articles/PMC13739473/) · PMID `42992339`, published 2026)  
**Reproducibility Script:** [`run_prisma_ground_truth_benchmark.py`](run_prisma_ground_truth_benchmark.py)  
**Saved Benchmark Output:** [`artifacts/ground_truth_benchmark.json`](artifacts/ground_truth_benchmark.json)

---

## 1. Objective

Unit tests verify that Python code runs without crashing, not that retrieval and deduplication match published scientific practice. To test our retrieval and deduplication pipeline against an independent peer-reviewed benchmark, we extracted the **exact PubMed/MEDLINE Boolean query and date window (`2019/01/01`–`2026/04/01`)** published in Table 2 of `PMC13739473` and ran our open-source API retrieval + deduplication logic on **2026-11-12 UTC**.

---

## 2. Quantitative Comparison: Published Paper vs. Live Script

| Metric | Published Paper (`PMC13739473`, Table 2) | Live Script Output (`2026-11-12`) | Delta & Explanation |
|---|---:|---:|---|
| **PubMed/MEDLINE Identification (`n`)** | **`225`** | **`226`** | **`+1` (`99.56%` match)** — minor post-cutoff retrospective indexing in NCBI PubMed |
| **Europe PMC Secondary Retrieval (`n`)** | *Not used separately in Table 2* | **`196`** | Adds open-access European preprint/journal coverage |
| **Combined Raw Pool (`PubMed + Europe PMC`)** | — | **`422`** | — |
| **Automated Duplicates Removed (PMID/DOI/Title)** | — | **`171`** | Cross-database overlap stripped deterministically |
| **Unique Screened Pool (`2` APIs)** | *1,023 across 7 databases* | **`251`** | Paper also queried Web of Science, Scopus, SPORTDiscus, Google Scholar, TRDizin & DergiPark |
| **Title-Only Population Keyword Match (`Turk*`)** | *61 included from full-text screening* | **`5`** | Demonstrates why **human abstract/full-text review is mandatory** (population origin is usually in Methods, not Titles) |

---

## 3. Key Scientific Takeaways

1. **Exact PubMed Query Reproduction (`226` vs. `225`):** Re-running the published Boolean string via NCBI E-utilities reproduces the authors' reported MEDLINE identification count within 1 record (`0.44%` indexing drift).
2. **Clear Boundary Between Mechanical Prep and Systematic Review:** Automated deduplication cleanly collapses `422` raw API records down to `251` unique records (`171` duplicates removed). However, the authors' final `61` database-derived inclusions required querying non-PubMed national registries (`TRDizin`, `DergiPark`) and reading full-text Methods sections to confirm Turkish athlete cohorts—confirming that automation prepares the screening pool, while **human domain experts perform eligibility and bias appraisal**.

---

## 4. Reproduce Locally

```bash
python computational-biology-portfolio/case_studies/run_prisma_ground_truth_benchmark.py
```
