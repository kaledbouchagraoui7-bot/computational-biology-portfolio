# Clinical Literature, Biostatistics & Reference Sequence Suite

**Reproducible Regulatory Writing & Clinical Data Pipelines built for ICH E6(R2), ICH E9, EU MDR Annex XIV (MEDDEV 2.7/1 Rev 4), EFSA Health Claims, and ALCOA+ Compliance.**

![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green) [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/kaledbouchagraoui7-bot/computational-biology-portfolio/blob/main/computational-biology-portfolio/02_clinical_trial_eda.ipynb)

---

## Executive Summary

This suite operationalizes core clinical evidence and biostatistics disciplines expected under **ICH-GCP E6(R2)**, **ICH E9**, **EU MDR 2017/745 Annex XIV**, and **ALCOA+ data-integrity rules** (Attributable, Legible, Contemporaneous, Original, Accurate — plus Complete, Consistent, Enduring, Available). Every pipeline enforces:

1. **Deterministic Reproducibility:** Pinned dependencies, seeded synthetic QA benchmarks (`seed=2027` for byte-identical re-runs), and versioned public database accessions preserved per row.
2. **End-to-End Audit Traceability:** Every output links to primary `PMID` / `DOI` / RefSeq accession identifiers with printed transformation trails.
3. **Zero Fabrication & Privacy-by-Design:** Missing DOIs or extracted study conclusions are never fabricated — empty cells are highlighted in soft amber for qualified medical-writer sign-off, and zero identifiable patient PHI is processed.

---

## Module 1 — Genomic Reference Transcript Analyzer
**Script:** [`01_ncbi_sequence_analyzer.py`](01_ncbi_sequence_analyzer.py) · **Output:** `cftr_analysis.csv`

- **Retrieval:** Queries NCBI Entrez (`esearch` + `efetch`) with exponential-backoff retries (`2/4/8 s`) on HTTP `503`/`429`.
- **QC Metrics:** Ambiguity-aware GC percentage per transcript and longest 3-frame Open Reading Frame (ORF) verification with translation.
- **Traceability:** Retains exact RefSeq accession versions (e.g., `NM_000492.4`) in every row (`CROSS-CHECK NM_000492: expected ORF 4443 nt / 1480 aa -> PASS`).

---

## Module 2 — Clinical Trial Data Cleaning & Baseline "Table 1"
**Script:** [`02_clinical_trial_eda.py`](02_clinical_trial_eda.py) · **Outputs:** `table1_demographics.png`, `02_clinical_trial_eda.ipynb`, `cleaned_cohort.csv`

- **Auditable Cleaning Trail:** Deterministic de-duplication, CDISC-style controlled vocabulary harmonisation, out-of-range check flagging, and arm-stratified median imputation (`ICH E9 §5.2`).
- **Statistical Comparison:** Lightweight, SciPy-free Welch t-test and Pearson chi-square test verified to `<1e-9` precision.
- **Visual QA Figure:** Arm-stratified boxplot with raw observation points (`table1_demographics.png`).

---

## Module 3 — PRISMA 2020 Literature Synthesizer & Package Generator
**Scripts:** [`03_pubmed_literature_synthesizer.py`](03_pubmed_literature_synthesizer.py) · [`pilot_package_generator.py`](pilot_package_generator.py)

- **Dual-Source Resilience:** Primary NCBI Entrez XML retrieval with seamless failover to Europe PMC REST JSON (`cursorMark` deep pagination).
- **Arithmetic Conservation Enforcement:** Automatically validates `Identified - Duplicates = Screened = Excluded + Full-Text = Included` before emitting deliverables.
- **Turnkey Client Deliverables:**
  - Landscape Word Evidence Dossier (`.docx`) with Vancouver citations and soft amber QA verification highlights
  - 4-Sheet Filterable Excel Screening Workbook (`.xlsx`) with live `COUNTIFS` formulas and clickable `doi.org` links
  - 300-DPI Vector PDF & PNG PRISMA 2020 Flow Diagram

---

## Quickstart & Verification

```bash
pip install -r requirements.txt
python 01_ncbi_sequence_analyzer.py
python 02_clinical_trial_eda.py
python 03_pubmed_literature_synthesizer.py --query "GLP-1 receptor agonist cardiovascular outcomes" --max 30 --years 5 --output-dir dossier_glp1_safety
python pilot_package_generator.py --records records.csv --config config.json --output-dir output
python -m unittest test_pilot_package.py -v
```

---
*All demonstration clinical cohort tables use strictly seeded synthetic records and public NIH/Europe PMC metadata. Outputs require qualified Regulatory Affairs / Medical Writer sign-off prior to regulatory filing.*
