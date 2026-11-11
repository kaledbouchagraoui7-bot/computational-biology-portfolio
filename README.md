<div align="center">
  <img src="assets/sentinel_logo_editorial.png" alt="Sentinel — Clinical Data, PRISMA 2020 & Sequence QC Suite" width="640" />
</div>

# Clinical Literature, Biostatistics & Sequence QC Automation

**ALCOA+ Reproducible Pipelines for EU MDR Annex XIV (MEDDEV 2.7/1 Rev 4), ICH E6(R2) / E9 / M4, and EFSA / Cosmetovigilance Evidence Dossiers.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/) [![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE) [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/kaledbouchagraoui7-bot/computational-biology-portfolio/blob/main/computational-biology-portfolio/02_clinical_trial_eda.ipynb)

---

## Executive Capabilities

This repository automates the three highest-friction deliverables in **clinical evaluation, medical writing, and biostatistics QA** under strict **ALCOA+ traceability** and **zero-fabrication guarantees**:

| Module | Regulatory Standard | Key Automated Deliverables |
|---|---|---|
| **01 — Reference Transcript & Variant QC**<br>[`01_ncbi_sequence_analyzer.py`](computational-biology-portfolio/01_ncbi_sequence_analyzer.py) | **ICH E6(R2) §4.9 · FDA/IVDR Genetic Biomarker Dossiers** | Versioned NCBI RefSeq/Entrez mRNA retrieval (`NM_000492.4`), ambiguity-aware GC screening, 3-frame ORF verification, and immutable accession-version logging. |
| **02 — Clinical Trial Data Cleaning & Baseline Table 1**<br>[`02_clinical_trial_eda.py`](computational-biology-portfolio/02_clinical_trial_eda.py) | **ICH E9 §5.2 · CDISC Harmonisation · CONSORT** | Auditable de-duplication and outlier trail (`cleaned_cohort.csv`), baseline **Table 1** (Welch t-test & Pearson χ² validated to `<1e-9` vs. SciPy), and transparent distribution figure ([`table1_demographics.png`](computational-biology-portfolio/table1_demographics.png)). |
| **03 — Systematic Literature & PRISMA 2020 Packager**<br>[`03_pubmed_literature_synthesizer.py`](computational-biology-portfolio/03_pubmed_literature_synthesizer.py) + [`pilot_package_generator.py`](computational-biology-portfolio/pilot_package_generator.py) | **EU MDR 2017/745 Annex XIV · MEDDEV 2.7/1 Rev 4 · ICH M4** | Dual-source NCBI PubMed + Europe PMC REST queries, exact PRISMA 2020 conservation accounting, Vancouver-cited **Landscape Word Evidence Table (`.docx`)**, **4-sheet Excel Screening Matrix (`.xlsx`)** with soft amber QA highlights on missing fields, and **300-DPI PRISMA 2020 Flow Diagram (`.png` / `.pdf`)**. |

---

## Sample Turnkey Output (`dossier_glp1_safety/`)

- **Live Literature Matrix:** [`computational-biology-portfolio/dossier_glp1_safety/literature_matrix.md`](computational-biology-portfolio/dossier_glp1_safety/literature_matrix.md)
- **Full Documentation & Quickstart:** [`computational-biology-portfolio/README.md`](computational-biology-portfolio/README.md)
- **Regulatory & Privacy Compliance Framework:** [`LEGAL_COMPLIANCE_AND_ETHICS.md`](LEGAL_COMPLIANCE_AND_ETHICS.md)

## Quickstart

```bash
cd computational-biology-portfolio
pip install -r requirements.txt
python 01_ncbi_sequence_analyzer.py
python 02_clinical_trial_eda.py
python 03_pubmed_literature_synthesizer.py --query "cardiovascular implantable medical device safety" --max 30 --years 5 --output-dir dossier_sample
python -m unittest test_pilot_package.py -v
```

---
*MIT © 2026 Sentinel Regulatory & Technical Operations — Strict public-registry and synthetic-cohort traceability only. Zero patient PHI ingested.*
