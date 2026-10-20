# Computational Biology Portfolio + ⚡ GenevaOS

**Kaled Bouchagraoui** — two projects, one repository:

| # | Project | What it is | Entry point |
|---|---|---|---|
| 1 | 🧬 **Computational Biology Portfolio** | Three reproducible pipelines for Regulatory Affairs / Medical Science Writing: NCBI sequence QC, clinical-trial EDA with a regulatory "Table 1", PubMed literature synthesis — engineered around **ICH E6(R2) / E9 / M4** and **ALCOA+** data-integrity principles | [`computational-biology-portfolio/`](computational-biology-portfolio/README.md) |
| 2 | ⚡ **GenevaOS** | Offline-first command center for the Geneva Protocol (Bac 2027 grades w/ kill-switch, asthma-aware pace engine, skincare protocol, Swiss relocation finance, local-AI hub health) — **CLI + web app** sharing one SQLite database | `python main.py` / `python webapp.py` |

```
.
├── computational-biology-portfolio/          # Project 1 — scripts, artifacts, full dossier README
│   ├── 01_ncbi_sequence_analyzer.py          # CFTR reference-transcript QC (Entrez, ORF, GC, MANE cross-check)
│   ├── 02_clinical_trial_eda.py              # synthetic cohort → audit-trailed cleaning → Table 1 → figure → notebook
│   ├── 02_clinical_trial_eda.ipynb           # Colab-ready notebook (auto-emitted, runs clean in Colab)
│   ├── 03_pubmed_literature_synthesizer.py   # PubMed / Europe PMC surveillance → bibliography + evidence matrix
│   ├── cftr_analysis.csv · cleaned_cohort.csv · pubmed_bibliography.csv
│   ├── literature_matrix.md · table1_demographics.png · requirements.txt · LICENSE
├── computational-biology-portfolio-v1.1.zip  # packaged release of the folder above
├── main.py                                   # Project 2 — GenevaOS CLI (Typer + Rich)
├── database.py                               # SQLite persistence layer (geneva_os.db, auto-created)
├── webapp.py                                 # Project 2 — app-style web interface (stdlib-only, no new deps)
├── requirements.txt                          # GenevaOS deps: typer, rich, requests
└── LICENSE                                   # MIT
```

---

## 🧬 Project 1 — Computational Biology Portfolio

Full documentation: **[`computational-biology-portfolio/README.md`](computational-biology-portfolio/README.md)** ·
One-click notebook: **[Open Project 2 in Google Colab](https://colab.research.google.com/github/kaledbouchagraoui7-bot/computational-biology-portfolio/blob/main/computational-biology-portfolio/02_clinical_trial_eda.ipynb)**

* **01 — CFTR Reference Transcript Analyzer.** Fetches the curated 5-record CFTR mRNA reference panel from NCBI (with graceful fallbacks), computes GC content and longest ORF on both strands, retains versioned accessions in every row, and gates on a built-in cross-check: `NM_000492 → 4,443 nt / 1,480 aa → PASS`.
* **02 — Clinical Trial EDA & Regulatory Table 1.** Seeded synthetic cohort (seed=2027, 103 raw rows → n=100), fully logged cleaning chain (dedup, CDISC-style harmonisation, range rejection, arm-stratified median imputation), baseline Table 1 with **SciPy-free** Welch t-test and Pearson χ² (validated to <1e-9 against SciPy), transparency figure, deterministic byte-reproducible outputs, and an auto-emitted Colab notebook that shares one source of truth with the script.
* **03 — Literature Synthesizer.** PMID-first bibliography + Markdown evidence matrix for IB/RIM assembly (ICH M4). NCBI E-utilities primary with backoff retries; Europe PMC REST fallback with cursorMark pagination and verified `ext_id` abstract batch-fill; never writes partial or empty deliverables.

### Run it (Colab / Codespaces / local)
```bash
cd computational-biology-portfolio
pip install -r requirements.txt                       # pinned, Python 3.10+
python 01_ncbi_sequence_analyzer.py                   # needs network (NCBI)
python 02_clinical_trial_eda.py                       # fully offline, ~2 s
python 03_pubmed_literature_synthesizer.py --query "CFTR modulator therapy safety" --max 25
```
Courtesy contact for NCBI: `export ENTREZ_EMAIL=you@example.com` (optional but recommended).

---

## ⚡ Project 2 — GenevaOS (web app + CLI)

Offline-first. No cloud, no tracking. Everything persists to a local SQLite file
(`geneva_os.db`, created automatically on first run). **The web app and the CLI
share the same database and the same logic** — data entered in the browser shows
up in `python main.py academics list`, and vice versa.

### 🖥 The web app (easiest way to use it)

```bash
python webapp.py            # starts on http://localhost:8000 and opens your browser
```

Zero extra dependencies (Python stdlib only — `typer`/`rich`/`requests` from
`requirements.txt` are already enough). A dark, app-style single-page interface:

| Tab | What you get |
|---|---|
| 📊 Dashboard | Kill-switch status, CHF progress bar, next deadline countdown, today's skin protocol, latest runs & grades |
| 🎓 Academics | Log grades with a form, live rolling weighted averages, red KILL-SWITCH banner when SVT/PC < 14.5 |
| 🏃 Athletics | Pace calculator (splits per 100/200/400 m & km, km/h), asthma checklist, session logger + history |
| 🧴 Skincare | Day-picker with AM/PM routines; shave days (Mon/Wed/Fri/Sun) auto-blackout retinal/acids |
| 🇨🇭 Swiss | Liquidity vs the CHF 2,500 Day-1 gate, inflow form, ledger, admin-deadline pipeline |
| 🤖 AI Hub | One-click health ping of LibreChat (:3080) and Ollama (:11434) |
| 🧬 Portfolio | Read-only showcase: Figure 1, all three artifact tables (DOI-linked), rendered literature matrix |

Useful flags: `--port 8080` (another port), `--host 0.0.0.0` (use it from your
phone on the same Wi-Fi), `--no-browser` (don't auto-open). Binds to
`127.0.0.1` by default — nothing is reachable from outside your machine.

### ⌨️ The CLI

```bash
python main.py --help
python main.py academics log -s SVT -n 16.5 -w 4 -t QCM   # or: academics log  (interactive)
python main.py academics list
python main.py run pace 800 2:45                          # 1m23s / 1:23 / 83 / 2m+500ms / 1'23" all work
python main.py run pace 1.5km 5m30s --log --warmup
python main.py run history
python main.py skin today                                 # skin day wed  → preview any day
python main.py swiss status
python main.py swiss fund -a 800 -s "Summer Work"
python main.py swiss ledger
python main.py hub ping
```

**Kill switch:** if the SVT or PC rolling weighted average drops below **14.5/20**,
the CLI fires a red panel and exits with code `2` (web app: red banner) — pause all
side projects, redirect bandwidth to core subjects.

**Admin deadlines** (UNIGE Portal → FMEL Housing → OCPM → Permis L) are defined in
`ADMIN_DEADLINES` at the top of `main.py` — edit dates there; both UIs follow.

---

## Install / activate (exact steps)

Requires **Python 3.10+** ([python.org](https://www.python.org/downloads/) — tick
"Add python.exe to PATH" on Windows).

**Windows (PowerShell):**
```powershell
git clone https://github.com/kaledbouchagraoui7-bot/computational-biology-portfolio.git
cd computational-biology-portfolio
python -m venv .venv
.venv\Scripts\Activate.ps1        # if blocked: Set-ExecutionPolicy -Scope Process RemoteSigned
pip install -r requirements.txt
python webapp.py                  # ⚡ app opens at http://localhost:8000
```

**macOS / Linux:**
```bash
git clone https://github.com/kaledbouchagraoui7-bot/computational-biology-portfolio.git
cd computational-biology-portfolio
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 webapp.py
```

**GitHub Codespaces:** open the repo → Codespace → in the terminal:
`pip install -r requirements.txt && python webapp.py` → click the forwarded port 8000.

For the portfolio pipelines additionally:
`pip install -r computational-biology-portfolio/requirements.txt` (pinned stack).

## Exit codes (CLI)

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | Hub offline / bad input / retrieval failure |
| 2 | **Academic Kill Switch triggered** |

## Changelog

See **[FIXES.md](FIXES.md)** for the full v1.1 bug-fix log (Colab notebook crash,
χ² accuracy, DOI extraction, EPMC fallback, NCBI panel completion, determinism,
and more) with verification evidence.

---
*MIT © 2026 Kaled Bouchagraoui — synthetic clinical data only; GDPR/HIPAA-safe by design.*
