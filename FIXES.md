# FIXES.md — v1.1 bug-fix log

Every fix below was **reproduced first, then verified after** (live runs against
NCBI / Europe PMC, SciPy cross-checks, simulated Colab kernel, 27-check web-app
smoke test). Date: 2026-10-20.

## 🔴 Critical (broken functionality)

| # | Where | Bug | Fix | Verified by |
|---|---|---|---|---|
| 1 | `02_clinical_trial_eda.py` → emitted notebook | The auto-emitted `02_clinical_trial_eda.ipynb` **crashed on Colab at first run**: the embedded script kept its own `if __name__ == "__main__": sys.exit(main())` guard, which is also true inside a notebook → `main()` parsed the Jupyter kernel argv → `error: unrecognized arguments: -f …kernel.json` → exit 2 | `emit_notebook()` now strips the guard from the embedded source and appends a single notebook-safe `main([])` entry point | Simulated Colab: ran the emitted cell source with `-f kernel-1234.json` argv → **returncode 0, clean stderr** (was 2) |
| 2 | `02_clinical_trial_eda.py` → `_chi2_sf` | χ² p-values **wrong by up to ±0.04** despite the "exact to ~1e-12" claim: series branch initialised `ap = 1/a` instead of `a`; continued-fraction branch initialised `c = 1.0` instead of `1/FPMIN` (Numerical Recipes gser/gcf) | Both branches rewritten per NR §6.2 | All Table-1 p-values now match `scipy.stats.chi2_contingency` to <1e-9 (Sex 0.3989→0.2315, AE-severity 0.9504→0.8666); 6-point sweep across both branches <1e-9 |
| 3 | `03_pubmed_literature_synthesizer.py` | **DOI column empty for 100% of records** (0/25): code looked for `ArticleIdList` under `Article` (it lives under `PubmedData`) and read `EIdType` as a Python attribute (it lives in `.attributes`); single `ELocationID` elements were iterated as strings | DOI extraction rewritten against the real Entrez structure (`PubmedData/ArticleIdList[@IdType=doi]` + robust `ELocationID` handling) | Live run: **25/25 DOIs** on both NCBI and Europe PMC paths |
| 4 | `03_pubmed_literature_synthesizer.py` | **Europe PMC fallback produced zero output (exit 1)**: (a) `sort=P_PUB_DATE_DESC` is not a valid EPMC token — the server answered HTTP 503, misdiagnosed as a "core index outage"; (b) the lite-mode abstract fill POSTed to a non-existent `/articleformatting` endpoint → 404 → every record QC-dropped | (a) sort fixed to `P_PDATE_D desc`; (b) fill replaced with verified batched `/search` queries `(ext_id:PMID AND src:med)` + `resultType=core`; dead POST helper removed | `EUROPE_PMC_ONLY=1` live run: **exit 0, 25/25 records with DOIs and abstracts**, no lite degradation |
| 5 | `01_ncbi_sequence_analyzer.py` | Live runs shipped a **short 3-row panel instead of 5**: the CFTR title-verification gate ran *after* the fallback decision, so esearch noise counted toward the 5-record quota and suppressed the curated fallback | Gate applied immediately after the primary fetch; fallback/completion then top the panel up | Live run (with NCBI actively throttling): **5 rows, canonical order, `CROSS-CHECK NM_000492 → PASS`** |

## 🟠 Correctness / robustness

| # | Where | Bug | Fix | Verified by |
|---|---|---|---|---|
| 6 | `02` `generate_raw_cohort` | `rng.choice(patient_id, 3)` (with replacement) could pick the same ID twice → only 99 unique subjects → `assert n=100` **crashed on unlucky `--seed` values** | Redraw-on-collision loop, placed at the exact same RNG-stream position | Seed sweep 1000–1059: 0 assertion failures; seed=2027 output still **byte-identical** to the committed cohort |
| 7 | `02` `make_figure` | Figure **not reproducible**: seaborn `stripplot` jitter draws from the unseeded *legacy* global NumPy RNG → different PNG every run, contradicting the README claim | `np.random.seed(seed)` inside `make_figure` | Two consecutive runs → identical SHA-256 |
| 8 | `01` `analyze`/`longest_orf` | Minus-strand ORFs were "translated" from the forward slice → garbage protein (latent MANE cross-check failure mode) | `longest_orf` now reports `strand`; translation reverse-complements minus-strand slices | Cross-check still PASS; unit-scanned both strands |
| 9 | `01` `reverse_complement` | Complement table covered only ACGTN — other IUPAC ambiguity codes (R, Y, S, W, K, M, B, D, H, V) passed through uncomplemented | Full IUPAC map | Translate table audit |
| 10 | `01` `_entrez_request` | NCBI answers rate-limit blocks with an **HTTP-200 HTML "WWW Error Blocked" page** → cryptic `mismatched tag` XML parse errors, no retry | Block-page detector converts it into a retryable transient error; final-attempt sleep removed | Observed live: `[warn] … HTML block/throttle page …; retrying` → graceful curated fallback → 5 rows |
| 11 | `01` completion pass | A single-accession fetch returning no parseable records was **silently ignored** | Explicit `[warn]` line naming the accession | Live during throttled run |
| 12 | `01` `analyze` | Row order depended on which retrieval path answered → unstable CSV | Canonical curated-panel sort (MANE first) | Two different retrieval paths → identical row order |
| 13 | `03` `clean_abstract_text` | BeautifulSoup `MarkupResemblesLocatorWarning` noise on plain-text abstracts | bs4 invoked only when markup is actually present; warnings scoped | Clean stderr on live run |
| 14 | `03` `entrez_fetch` | Module promised backoff retries but the Entrez phase had none (constants only used by the EPMC helpers) | `_entrez_call_with_backoff` wraps esearch/efetch (2/4/8 s on 503/429) | Code path exercised during throttled runs |
| 15 | `main.py` kill-switch panel | `Text.assemble()` doesn't parse markup → panel printed literal `[bold]…[/bold]` tags | `Text.from_markup` for the detail lines | Panel now renders bold values; exit code 2 preserved |
| 16 | `main.py` `parse_time_to_seconds` | `1'23"` / `1′23″` notation rejected even though the code attempted to support it (half-baked replace) | Proper prime/double-prime parsing branch | `1'23"`, `1′23″`, `83`, `1:23`, `1m23s`, `2m+500ms`, `90s` → all correct; garbage still rejected |

## 🟡 Repo hygiene / docs

| # | Item | Fix |
|---|---|---|
| 17 | `publish-checkout-stage/` — byte-identical duplicate of the portfolio folder (staging leak) | Removed |
| 18 | `computational-biology-portfolio-v1.0.zip` contained the buggy v1.0 files | Rebuilt as **v1.1** from the fixed, regenerated folder; v1.0 removed |
| 19 | Root `.gitignore` ignored only `publish-checkout/` → `geneva_os.db` (personal data!), caches, venvs would be committed | Full ignore set added |
| 20 | Root README described **only GenevaOS** although the repo is named/described as the portfolio | Rewritten: portfolio-forward, web-app section, exact activation steps |
| 21 | Portfolio README Colab badge pointed to `colab.research.google.com/github/` (dead) | Points to the actual notebook in this repo |
| 22 | Portfolio README Project-1 sample rows in unstable order; Project-3 stats outdated | Sample = canonical order; "25 records, 25/25 PMIDs, 25/25 DOIs, 25/25 abstracts" |
| 23 | Stale Rule-3 comment claimed a `Biomarker=-40` injection that never existed and hard-coded counts | Comment now describes the mechanism; counts stay dynamic |
| 24 | Dead code: `extract_doi()` (unreachable `if False`), unused `scan()` ORF variant, `table1` always-true conditional, dead POST helper | Removed / simplified |
| 25 | No root LICENSE although README badges claim MIT | MIT LICENSE added at root (matches portfolio copy) |

## ✨ New

| # | Item | Notes |
|---|---|---|
| 26 | **`webapp.py` — app-style web interface** | Stdlib-only (no new dependencies), single embedded HTML/CSS/JS page, dark command-center UI: Dashboard, Academics, Athletics, Skincare, Swiss, AI Hub, Portfolio viewer. Shares `geneva_os.db` + logic with the CLI (`main.py` parsers/constants imported, not copied). Binds 127.0.0.1 by default; `--host/--port/--no-browser`; allow-listed artifact serving (path traversal blocked). **27/27 endpoint smoke checks passed**, JS syntax-verified with node |
| 27 | `main.py` refactor | `parse_distance` hoisted to module-level `parse_distance_meters` so CLI and web app share one parser |
| 28 | `02` `--csv` flag | Cleaned-cohort output path configurable like `--png`/`--notebook` |

## Artifacts regenerated after fixes

* `cftr_analysis.csv` — 5 rows, canonical panel order, cross-check PASS
* `table1_demographics.png` — deterministic (seeded jitter)
* `02_clinical_trial_eda.ipynb` — Colab-safe single entry point
* `cleaned_cohort.csv` — byte-identical to v1.0 (RNG stream preserved by design)
* `pubmed_bibliography.csv` + `literature_matrix.md` — 25 records, 25/25 DOIs, fresh live pull (2026-10-20)

**Unchanged on purpose:** the Welch biomarker p-value stays **0.0085** (README claim holds);
Table-1 χ² p-values changed *only* because they are now correct (fix #2).
