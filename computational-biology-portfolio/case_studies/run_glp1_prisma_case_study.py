#!/usr/bin/env python3
"""
Reproducible Clinical Literature & Biostats Case Study:
Dual-Source PRISMA 2020 Screening (PubMed + Europe PMC) & ICH E9 Baseline Verification
for GLP-1 Receptor Agonist Cardiorenal Safety Evidence.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

COMPBIO_PKG = Path(__file__).resolve().parent.parent
if str(COMPBIO_PKG) not in sys.path:
    sys.path.insert(0, str(COMPBIO_PKG))

import importlib.util

def _load_mod(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, str(file_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    lit_synth = _load_mod("lit_synth", COMPBIO_PKG / "03_pubmed_literature_synthesizer.py")
    pilot_gen = _load_mod("pilot_gen", COMPBIO_PKG / "pilot_package_generator.py")

    out_dir = Path(__file__).resolve().parent / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    query = '("semaglutide" OR "GLP-1 receptor agonist") AND ("cardiovascular" OR "renal") AND ("clinical trial" OR "meta-analysis")'
    # Deterministic dual-source fetch: try PubMed + Europe PMC; fall back gracefully to PubMed if EPMC hits SSL proxy hiccup
    epmc_records = []
    try:
        epmc_records, _ = lit_synth.europepmc_fetch(query, max_records=30)
    except Exception as exc:
        print(f"[CaseStudy2] Europe PMC fetch note: {exc}")

    pm_records = []
    try:
        pm_records, _total_pm = lit_synth.entrez_fetch(query, max_records=30)
    except Exception as exc:
        print(f"[CaseStudy2] PubMed fetch note: {exc}")

    merged = epmc_records + pm_records
    kept, normalized_all, curate_stats = lit_synth.curate(merged, years=5, max_records=25)

    cfg = {
        "project_title": "GLP-1 Cardiorenal Safety PRISMA 2020 Benchmark (2021–2026)",
        "project_slug": "glp1_cardiorenal_study",
        "databases_searched": {
            "Europe PMC REST": len(epmc_records),
            "NCBI PubMed E-utilities": len(pm_records),
        },
        "duplicates_removed_count": int(curate_stats.get("duplicates_removed", 0)),
        "search_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "date_of_search": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "is_fictional_sample": False,
    }
    prisma_verified = pilot_gen.compute_and_verify_prisma(normalized_all, cfg)
    png_p = out_dir / "glp1_prisma_2020_flowchart.png"
    pdf_p = out_dir / "glp1_prisma_2020_flowchart.pdf"
    pilot_gen.generate_prisma_flowchart(prisma_verified, cfg, png_p, pdf_p)

    summary = {
        "query": query,
        "databases_searched": cfg["databases_searched"],
        "curate_stats": curate_stats,
        "prisma_verified": prisma_verified,
        "top_included_studies": [
            {
                "pmid": r.get("pmid"),
                "year": r.get("pub_year"),
                "journal": r.get("journal"),
                "study_type": r.get("study_type"),
                "title": r.get("title"),
                "doi": r.get("doi"),
            }
            for r in kept[:10]
        ],
    }
    (out_dir / "glp1_study_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("PRISMA_VERIFIED:", prisma_verified)
    print("CURATE_STATS:", curate_stats)
    print("DATABASES:", cfg["databases_searched"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
