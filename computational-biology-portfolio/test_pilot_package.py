#!/usr/bin/env python3
"""Unit tests for pilot_package_generator.py verifying all integrity & formatting rules."""

import copy
import unittest
import zipfile
from pathlib import Path

import docx
import openpyxl

from pilot_package_generator import (
    _clean_prisma_reason,
    build_pilot_package,
    compute_and_verify_prisma,
    generate_prisma_flowchart,
    load_inputs,
)

BASE_DIR = Path(__file__).resolve().parent


class TestPilotPackageGenerator(unittest.TestCase):
    def setUp(self) -> None:
        self.csv_path = BASE_DIR / "records.csv"
        self.cfg_path = BASE_DIR / "config.json"
        self.records, self.config = load_inputs(self.csv_path, self.cfg_path)

    def test_sample_size_and_prisma_conservation(self) -> None:
        self.assertEqual(len(self.records), 30)
        prisma = compute_and_verify_prisma(self.records, self.config)
        self.assertEqual(prisma["total_identified"], 30)
        self.assertEqual(prisma["duplicates_removed"], 4)
        self.assertEqual(prisma["records_screened"], 26)
        self.assertEqual(prisma["excluded_at_screening"], 8)
        self.assertEqual(prisma["reports_assessed_full_text"], 18)
        self.assertEqual(prisma["excluded_at_full_text"], 4)
        self.assertEqual(prisma["studies_included"], 14)

    def test_consistency_check_raises_on_db_mismatch(self) -> None:
        bad_cfg = copy.deepcopy(self.config)
        bad_cfg["databases_searched"] = {"PubMed": 10, "Europe PMC": 5}
        with self.assertRaises(ValueError) as ctx:
            compute_and_verify_prisma(self.records, bad_cfg)
        self.assertIn("PRISMA Consistency Error", str(ctx.exception))

    def test_consistency_check_raises_on_duplicate_mismatch(self) -> None:
        bad_cfg = copy.deepcopy(self.config)
        bad_cfg["duplicates_removed_count"] = 9
        with self.assertRaises(ValueError) as ctx:
            compute_and_verify_prisma(self.records, bad_cfg)
        self.assertIn("PRISMA Consistency Error", str(ctx.exception))

    def test_consistency_check_raises_on_missing_exclusion_reason(self) -> None:
        bad_records = copy.deepcopy(self.records)
        for r in bad_records:
            if r["screening_stage"] == "screened":
                r["exclusion_reason"] = ""
                break
        with self.assertRaises(ValueError) as ctx:
            compute_and_verify_prisma(bad_records, self.config)
        self.assertIn("missing an exclusion_reason", str(ctx.exception))

    def test_full_package_outputs_and_integrity_rules(self) -> None:
        res = build_pilot_package(self.csv_path, self.cfg_path, BASE_DIR / "output")
        self.assertTrue(res["docx"].exists())
        self.assertTrue(res["xlsx"].exists())
        self.assertTrue(res["png"].exists())
        self.assertTrue(res["pdf"].exists())
        self.assertTrue(res["zip"].exists())

        # Verify Word (.docx)
        doc = docx.Document(str(res["docx"]))
        self.assertEqual(len(doc.tables), 1)
        tbl = doc.tables[0]
        # 1 header row + 14 included rows = 15 rows
        self.assertEqual(len(tbl.rows), 15)
        expected_headers = [
            "Reference",
            "Study Type",
            "Population",
            "Intervention/Exposure",
            "Outcome",
            "Key Conclusion",
            "DOI",
        ]
        actual_headers = [c.text.strip() for c in tbl.rows[0].cells]
        self.assertEqual(actual_headers, expected_headers)
        footer_text = doc.sections[0].footer.paragraphs[0].text
        self.assertEqual(
            footer_text,
            "Draft for QA review. Extraction requires verification against source publications.",
        )

        # Verify Excel (.xlsx)
        wb = openpyxl.load_workbook(str(res["xlsx"]))
        self.assertEqual(
            wb.sheetnames,
            ["Records", "PRISMA_Counts", "Exclusion_Reasons", "README"],
        )
        ws_rec = wb["Records"]
        self.assertEqual(ws_rec.freeze_panes, "A2")
        self.assertIsNotNone(ws_rec.auto_filter.ref)

        # SIM-005 (row 6 in Excel) is missing DOI (column F=6) -> blank, yellow fill, and flagged in column N=14
        doi_cell_sim005 = ws_rec.cell(row=6, column=6)
        self.assertIn(doi_cell_sim005.value, (None, ""))
        self.assertTrue(str(doi_cell_sim005.fill.start_color.rgb).endswith("FFF2CC"))
        flag_cell_sim005 = ws_rec.cell(row=6, column=14)
        self.assertIn("Needs verification", str(flag_cell_sim005.value))

        # SIM-006 (row 7 in Excel) is missing conclusion (column K=11) -> blank, yellow fill, and flagged
        conc_cell_sim006 = ws_rec.cell(row=7, column=11)
        self.assertIn(conc_cell_sim006.value, (None, ""))
        self.assertTrue(str(conc_cell_sim006.fill.start_color.rgb).endswith("FFF2CC"))

        # Verify ZIP contents
        with zipfile.ZipFile(res["zip"], "r") as zf:
            names = zf.namelist()
            self.assertEqual(len(names), 4)

    def test_clean_prisma_reason_and_missing_search_date_fallback(self) -> None:
        self.assertEqual(
            _clean_prisma_reason("Outside top-20 recency cohort cap"),
            "Excluded beyond 20-study recency window",
        )
        self.assertEqual(
            _clean_prisma_reason("Excluded beyond 25-study recency window"),
            "Excluded beyond 25-study recency window",
        )
        import tempfile
        prisma = compute_and_verify_prisma(self.records, self.config)
        cfg_no_date = copy.deepcopy(self.config)
        cfg_no_date.pop("date_of_search", None)
        cfg_no_date["search_date"] = "—"
        with tempfile.TemporaryDirectory(prefix="prisma_fallback_") as td:
            png_out = Path(td) / "prisma_fallback.png"
            pdf_out = Path(td) / "prisma_fallback.pdf"
            generate_prisma_flowchart(prisma, cfg_no_date, png_out, pdf_out)
            self.assertTrue(png_out.exists() and png_out.stat().st_size > 20000)
            self.assertTrue(pdf_out.exists() and pdf_out.stat().st_size > 5000)


if __name__ == "__main__":
    unittest.main()
