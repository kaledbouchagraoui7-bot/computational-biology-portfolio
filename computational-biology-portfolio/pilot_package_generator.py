#!/usr/bin/env python3
"""
pilot_package_generator.py — Automated Regulatory Literature Pilot Package Builder
===================================================================================

Purpose
-------
Given a CSV table of screened literature records (`records.csv`) and a study
configuration file (`config.json`), automatically generate a complete,
QA-ready regulatory literature pilot package:

  1. Word (.docx)     — Landscape clinical evidence table (Vancouver citations,
                        repeating shaded header row, Calibri 10, QA footer).
  2. Excel (.xlsx)    — Multi-sheet workbook ("Records", "PRISMA_Counts",
                        "Exclusion_Reasons" with formulas, "README") featuring
                        frozen headers, AutoFilter, clickable DOI links, yellow
                        highlighting for missing fields, and "Needs verification" flags.
  3. PRISMA 2020      — Print-ready visual PRISMA 2020 flow diagram exported as
     Flowchart          both 300 DPI PNG and vector PDF, with strict mathematical
                        consistency verification at every stage.
  4. Zipped Package   — `output/<project_slug>_pilot/` and
                        `output/<project_slug>_pilot.zip`.

Integrity Rules
---------------
* Never fabricates DOIs, citations, or conclusions.
* Any missing field in an included record (or missing DOI/conclusion) is left
  blank, highlighted in yellow in Excel, and flagged with "Needs verification".
* Enforces strict arithmetic conservation across all PRISMA stages; raises a
  descriptive ValueError if counts do not reconcile.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import textwrap
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import docx
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Inches, Pt, RGBColor


REQUIRED_CSV_COLUMNS = [
    "record_id",
    "title",
    "authors",
    "year",
    "journal",
    "doi",
    "study_type",
    "population",
    "intervention_exposure",
    "outcome",
    "conclusion",
    "screening_stage",
    "exclusion_reason",
]

VALID_STAGES = {"identified", "screened", "full_text", "included"}


# ---------------------------------------------------------------------------
# 1. Data Loading, Validation & PRISMA Math Consistency Engine
# ---------------------------------------------------------------------------

def load_inputs(csv_path: str | Path, config_path: str | Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    csv_path = Path(csv_path)
    config_path = Path(config_path)

    if not csv_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {csv_path}")
    if not config_path.exists():
        raise FileNotFoundError(f"Config JSON not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as fh:
        config = json.load(fh)

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f"CSV file {csv_path} is empty or missing headers.")
        missing_cols = [c for c in REQUIRED_CSV_COLUMNS if c not in reader.fieldnames]
        if missing_cols:
            raise ValueError(f"CSV is missing required columns: {missing_cols}")

        records: list[dict[str, str]] = []
        for idx, row in enumerate(reader, start=2):
            cleaned = {k: (row.get(k) or "").strip() for k in REQUIRED_CSV_COLUMNS}
            stage = cleaned["screening_stage"].lower()
            if stage not in VALID_STAGES:
                raise ValueError(
                    f"Row {idx} ({cleaned['record_id']}): invalid screening_stage "
                    f"'{cleaned['screening_stage']}'. Must be one of {sorted(VALID_STAGES)}."
                )
            cleaned["screening_stage"] = stage
            records.append(cleaned)

    if not records:
        raise ValueError("CSV contains zero data rows.")

    return records, config


def compute_and_verify_prisma(records: list[dict[str, str]], config: dict[str, Any]) -> dict[str, Any]:
    """Compute all PRISMA 2020 flow numbers from `records` and `config` and
    strictly assert mathematical conservation at every step."""

    stage_counts = Counter(r["screening_stage"] for r in records)
    n_csv_total = len(records)
    n_stage_identified = stage_counts.get("identified", 0)
    n_stage_screened = stage_counts.get("screened", 0)      # excluded at title/abstract screening
    n_stage_full_text = stage_counts.get("full_text", 0)    # excluded at full-text eligibility
    n_stage_included = stage_counts.get("included", 0)      # final included studies

    cfg_dupes = config.get("duplicates_removed_count")
    if cfg_dupes is None:
        duplicates_removed = n_stage_identified
    else:
        duplicates_removed = int(cfg_dupes)

    # Support two transparent conventions:
    # (A) Duplicate rows are present in records.csv with screening_stage == 'identified'
    # (B) Duplicate rows were removed prior to records.csv (n_stage_identified == 0)
    if n_stage_identified > 0:
        if cfg_dupes is not None and n_stage_identified != duplicates_removed:
            raise ValueError(
                f"PRISMA Consistency Error: records.csv contains {n_stage_identified} rows with "
                f"screening_stage='identified', but config.json specifies "
                f"duplicates_removed_count={duplicates_removed}. These must match."
            )
        total_identified = n_csv_total
        records_screened = n_csv_total - duplicates_removed
    else:
        total_identified = n_csv_total + duplicates_removed
        records_screened = n_csv_total

    excluded_at_screening = n_stage_screened
    reports_sought_and_assessed = records_screened - excluded_at_screening
    excluded_at_full_text = n_stage_full_text
    studies_included = n_stage_included

    # Database breakdown check
    db_raw = config.get("databases_searched", {})
    db_breakdown: dict[str, int] = {}
    if isinstance(db_raw, dict):
        for k, v in db_raw.items():
            db_breakdown[str(k)] = int(v)
        db_sum = sum(db_breakdown.values())
        if db_sum != total_identified:
            raise ValueError(
                f"PRISMA Consistency Error: sum of databases_searched counts ({db_sum}) "
                f"does not equal total records identified ({total_identified})."
            )
    elif isinstance(db_raw, list):
        # List of database names without individual counts
        for name in db_raw:
            db_breakdown[str(name)] = 0

    # Stage conservation checks
    if total_identified - duplicates_removed != records_screened:
        raise ValueError(
            f"PRISMA Consistency Error: Identified ({total_identified}) - "
            f"Duplicates ({duplicates_removed}) != Screened ({records_screened})."
        )

    if records_screened - excluded_at_screening != reports_sought_and_assessed:
        raise ValueError(
            f"PRISMA Consistency Error: Screened ({records_screened}) - "
            f"Excluded at screening ({excluded_at_screening}) != "
            f"Assessed for eligibility ({reports_sought_and_assessed})."
        )

    if reports_sought_and_assessed != (excluded_at_full_text + studies_included):
        raise ValueError(
            f"PRISMA Consistency Error: Reports assessed for eligibility "
            f"({reports_sought_and_assessed}) != Excluded at full-text "
            f"({excluded_at_full_text}) + Included ({studies_included})."
        )

    # Verify exclusion_reason integrity per row
    screening_reasons: Counter[str] = Counter()
    full_text_reasons: Counter[str] = Counter()
    all_exclusion_reasons: Counter[str] = Counter()

    for r in records:
        st = r["screening_stage"]
        reason = r["exclusion_reason"].strip()
        if st == "included":
            if reason:
                raise ValueError(
                    f"PRISMA Consistency Error: Record {r['record_id']} has "
                    f"screening_stage='included' but has exclusion_reason='{reason}'."
                )
        elif st in ("screened", "full_text"):
            if not reason:
                raise ValueError(
                    f"PRISMA Consistency Error: Record {r['record_id']} is excluded at "
                    f"stage '{st}' but is missing an exclusion_reason."
                )
            if st == "screened":
                screening_reasons[reason] += 1
            else:
                full_text_reasons[reason] += 1
            all_exclusion_reasons[reason] += 1
        elif st == "identified":
            label = reason or "Duplicate record"
            all_exclusion_reasons[label] += 1

    if sum(screening_reasons.values()) != excluded_at_screening:
        raise ValueError("PRISMA Consistency Error: Screening exclusion reason counts do not sum to excluded_at_screening.")
    if sum(full_text_reasons.values()) != excluded_at_full_text:
        raise ValueError("PRISMA Consistency Error: Full-text exclusion reason counts do not sum to excluded_at_full_text.")

    return {
        "total_identified": total_identified,
        "db_breakdown": db_breakdown,
        "duplicates_removed": duplicates_removed,
        "records_screened": records_screened,
        "excluded_at_screening": excluded_at_screening,
        "screening_reasons": dict(screening_reasons),
        "reports_assessed_full_text": reports_sought_and_assessed,
        "excluded_at_full_text": excluded_at_full_text,
        "full_text_reasons": dict(full_text_reasons),
        "all_exclusion_reasons": dict(all_exclusion_reasons),
        "studies_included": studies_included,
    }


def compute_verification_flag(record: dict[str, str]) -> str:
    """Return 'Needs verification' details if DOI or conclusion is missing."""
    missing = []
    if not record.get("doi", "").strip():
        missing.append("missing DOI")
    if not record.get("conclusion", "").strip():
        missing.append("missing conclusion")
    if missing:
        return f"Needs verification ({', '.join(missing)})"
    return "Verified complete"


def format_vancouver_reference(record: dict[str, str]) -> str:
    """Format a Vancouver-style citation string from available fields without
    fabricating any missing data."""
    authors_raw = record.get("authors", "").strip()
    if authors_raw:
        # Normalise semicolon-separated authors to comma-separated Vancouver style
        author_list = [a.strip() for a in re.split(r"[;,]", authors_raw) if a.strip()]
        if len(author_list) > 6:
            authors_str = ", ".join(author_list[:6]) + ", et al."
        else:
            authors_str = ", ".join(author_list)
        if not authors_str.endswith("."):
            authors_str += "."
    else:
        authors_str = "[Authors not stated]."

    title = record.get("title", "").strip() or "[Title not stated]"
    if not title.endswith("."):
        title += "."

    journal = record.get("journal", "").strip() or "[Journal not stated]"
    year = record.get("year", "").strip() or "[Year unknown]"
    rec_id = record.get("record_id", "").strip()

    cite = f"{authors_str} {title} {journal}. {year}"
    if rec_id:
        cite += f" [{rec_id}]."
    else:
        cite += "."
    return cite


# ---------------------------------------------------------------------------
# 2. Output 1: Word (.docx) Evidence Table Generator
# ---------------------------------------------------------------------------

def _set_cell_background(cell, hex_color: str) -> None:
    shading_elm = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>')
    cell._tc.get_or_add_tcPr().append(shading_elm)


def _set_repeat_table_header(row) -> None:
    trPr = row._tr.get_or_add_trPr()
    tblHeader = OxmlElement("w:tblHeader")
    trPr.append(tblHeader)


def _prevent_row_split(row) -> None:
    trPr = row._tr.get_or_add_trPr()
    cantSplit = OxmlElement("w:cantSplit")
    trPr.append(cantSplit)


def _set_table_borders(table, color: str = "BFBFBF", sz: str = "4") -> None:
    tblPr = table._tbl.tblPr
    borders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        f'<w:top w:val="single" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'<w:bottom w:val="single" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'<w:left w:val="single" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'<w:right w:val="single" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'<w:insideH w:val="single" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'<w:insideV w:val="single" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'</w:tblBorders>'
    )
    tblPr.append(borders)


def generate_word_docx(
    records: list[dict[str, str]],
    config: dict[str, Any],
    prisma: dict[str, Any],
    out_path: str | Path,
) -> None:
    doc = Document()

    # Configure Default Style to Calibri 10
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10)

    # Landscape orientation on section
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width = Inches(11.69)   # A4 Landscape width
    section.page_height = Inches(8.27)   # A4 Landscape height
    section.top_margin = Inches(0.6)
    section.bottom_margin = Inches(0.6)
    section.left_margin = Inches(0.6)
    section.right_margin = Inches(0.6)

    # Footer on every page
    footer = section.footer
    f_para = footer.paragraphs[0]
    f_para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    f_run = f_para.add_run(
        "Draft for QA review. Extraction requires verification against source publications."
    )
    f_run.font.name = "Calibri"
    f_run.font.size = Pt(8.5)
    f_run.font.italic = True
    f_run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)

    # Title Block
    title_p = doc.add_paragraph()
    title_p.paragraph_format.space_after = Pt(4)
    t_run = title_p.add_run(config.get("project_title", "Systematic Literature Evidence Table"))
    t_run.font.name = "Calibri"
    t_run.font.size = Pt(15)
    t_run.font.bold = True
    t_run.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)

    if config.get("is_fictional_sample"):
        banner_p = doc.add_paragraph()
        banner_p.paragraph_format.space_after = Pt(6)
        b_run = banner_p.add_run(
            "NOTICE: THIS DOCUMENT CONTAINS FICTIONAL SAMPLE DATA FOR PIPELINE DEMONSTRATION ONLY."
        )
        b_run.font.name = "Calibri"
        b_run.font.size = Pt(9.5)
        b_run.font.bold = True
        b_run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)

    # Metadata Summary Block
    db_raw = config.get("databases_searched", {})
    if isinstance(db_raw, dict):
        db_str = ", ".join(f"{k} (n={v})" for k, v in db_raw.items())
    elif isinstance(db_raw, list):
        db_str = ", ".join(str(x) for x in db_raw)
    else:
        db_str = str(db_raw)

    meta_items = [
        ("Target Indication", config.get("indication", "—")),
        ("Search String", config.get("search_string", "—")),
        ("Databases Searched", db_str),
        ("Date of Search", config.get("date_of_search", "—")),
        (
            "PRISMA Accounting",
            f"Identified: {prisma['total_identified']} | "
            f"Duplicates Removed: {prisma['duplicates_removed']} | "
            f"Screened: {prisma['records_screened']} | "
            f"Full-Text Assessed: {prisma['reports_assessed_full_text']} | "
            f"Included Studies: {prisma['studies_included']}",
        ),
    ]

    for label, val in meta_items:
        mp = doc.add_paragraph()
        mp.paragraph_format.space_after = Pt(2)
        mp.paragraph_format.line_spacing = 1.05
        r_lbl = mp.add_run(f"{label}: ")
        r_lbl.font.name = "Calibri"
        r_lbl.font.size = Pt(9.5)
        r_lbl.font.bold = True
        r_val = mp.add_run(str(val))
        r_val.font.name = "Calibri"
        r_val.font.size = Pt(9.5)

    doc.add_paragraph().paragraph_format.space_after = Pt(4)

    # Filter to Included records for the Clinical Evidence Table
    included_records = [r for r in records if r["screening_stage"] == "included"]

    headers = [
        "Reference",
        "Study Type",
        "Population",
        "Intervention/Exposure",
        "Outcome",
        "Key Conclusion",
        "DOI",
    ]
    # Total usable width = 11.69 - 1.2 = 10.49 inches
    col_widths = [
        Inches(2.05),  # Reference
        Inches(1.05),  # Study Type
        Inches(1.40),  # Population
        Inches(1.45),  # Intervention/Exposure
        Inches(1.35),  # Outcome
        Inches(2.14),  # Key Conclusion
        Inches(1.05),  # DOI
    ]

    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    _set_table_borders(table, color="BFBFBF", sz="4")

    # Format Header Row
    hdr_row = table.rows[0]
    _set_repeat_table_header(hdr_row)
    _prevent_row_split(hdr_row)
    for idx, text in enumerate(headers):
        cell = hdr_row.cells[idx]
        cell.width = col_widths[idx]
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        _set_cell_background(cell, "1F4E79")
        p = cell.paragraphs[0]
        p.paragraph_format.space_before = Pt(3)
        p.paragraph_format.space_after = Pt(3)
        run = p.add_run(text)
        run.font.name = "Calibri"
        run.font.size = Pt(10)
        run.font.bold = True
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    # Populate Data Rows
    for row_idx, rec in enumerate(included_records):
        row = table.add_row()
        _prevent_row_split(row)
        vancouver_ref = format_vancouver_reference(rec)
        flag = compute_verification_flag(rec)

        # Never invent missing DOI or conclusion; leave blank and highlight in yellow if missing
        conclusion_val = rec.get("conclusion", "").strip()
        doi_val = rec.get("doi", "").strip()

        row_values = [
            vancouver_ref,
            rec.get("study_type", "").strip(),
            rec.get("population", "").strip(),
            rec.get("intervention_exposure", "").strip(),
            rec.get("outcome", "").strip(),
            conclusion_val,
            doi_val,
        ]

        bg_fill = "F9FAFB" if row_idx % 2 == 1 else "FFFFFF"

        for c_idx, val in enumerate(row_values):
            cell = row.cells[c_idx]
            cell.width = col_widths[c_idx]
            cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
            if not val and c_idx in (5, 6):
                # Highlight missing conclusion or DOI cell in soft yellow for QA review
                _set_cell_background(cell, "FFF2CC")
            else:
                _set_cell_background(cell, bg_fill)

            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.05
            run = p.add_run(val)
            run.font.name = "Calibri"
            run.font.size = Pt(9.5)

            if c_idx == 0 and flag != "Verified complete":
                flag_run = p.add_run(f"\n[{flag}]")
                flag_run.font.name = "Calibri"
                flag_run.font.size = Pt(8.5)
                flag_run.font.bold = True
                flag_run.font.color.rgb = RGBColor(0xB4, 0x5F, 0x06)

    doc.save(str(out_path))


# ---------------------------------------------------------------------------
# 3. Output 2: Excel (.xlsx) Multi-Sheet Workbook Generator
# ---------------------------------------------------------------------------

def generate_excel_xlsx(
    records: list[dict[str, str]],
    config: dict[str, Any],
    prisma: dict[str, Any],
    out_path: str | Path,
) -> None:
    wb = openpyxl.Workbook()

    # Styles
    font_family = "Calibri"
    hdr_font = Font(name=font_family, size=10, bold=True, color="FFFFFF")
    hdr_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    subhdr_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    subhdr_font = Font(name=font_family, size=10, bold=True, color="1F4E79")
    body_font = Font(name=font_family, size=10, bold=False, color="000000")
    bold_font = Font(name=font_family, size=10, bold=True, color="000000")
    link_font = Font(name=font_family, size=10, underline="single", color="0563C1")
    warn_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    warn_font = Font(name=font_family, size=10, bold=True, color="9C6500")

    thin_side = Side(border_style="thin", color="D9D9D9")
    cell_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
    wrap_top = Alignment(wrap_text=True, vertical="top", horizontal="left")

    # -----------------------------------------------------------------------
    # Sheet 1: "Records"
    # -----------------------------------------------------------------------
    ws_rec = wb.active
    ws_rec.title = "Records"

    rec_headers = REQUIRED_CSV_COLUMNS + ["needs_verification"]
    ws_rec.append(rec_headers)
    ws_rec.freeze_panes = "A2"

    for col_idx in range(1, len(rec_headers) + 1):
        c = ws_rec.cell(row=1, column=col_idx)
        c.font = hdr_font
        c.fill = hdr_fill
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="left")
        c.border = cell_border

    for r_idx, rec in enumerate(records, start=2):
        flag = compute_verification_flag(rec)
        stage = rec["screening_stage"]
        row_vals = [rec.get(col, "") for col in REQUIRED_CSV_COLUMNS] + [flag]
        ws_rec.append(row_vals)

        for c_idx, col_name in enumerate(rec_headers, start=1):
            cell = ws_rec.cell(row=r_idx, column=c_idx)
            val = str(cell.value or "").strip()
            cell.font = body_font
            cell.alignment = wrap_top
            cell.border = cell_border

            # Integrity rule: if a field is missing, leave it blank and highlight in yellow
            # (Note: exclusion_reason is intentionally blank when screening_stage == 'included')
            is_missing_field = (val == "") and not (col_name == "exclusion_reason" and stage == "included")
            if is_missing_field:
                cell.fill = warn_fill

            # Clickable DOI hyperlink
            if col_name == "doi" and val:
                url = val if val.startswith("http") else f"https://doi.org/{val}"
                cell.hyperlink = url
                cell.font = link_font

            # Highlight verification flag when triggered
            if col_name == "needs_verification" and flag != "Verified complete":
                cell.fill = warn_fill
                cell.font = warn_font

    # AutoFilter on every column
    last_col_letter = get_column_letter(len(rec_headers))
    ws_rec.auto_filter.ref = f"A1:{last_col_letter}{len(records) + 1}"

    # Set tailored column widths
    col_widths_map = {
        "record_id": 12,
        "title": 38,
        "authors": 26,
        "year": 8,
        "journal": 28,
        "doi": 26,
        "study_type": 22,
        "population": 30,
        "intervention_exposure": 28,
        "outcome": 28,
        "conclusion": 42,
        "screening_stage": 16,
        "exclusion_reason": 30,
        "needs_verification": 34,
    }
    for idx, col_name in enumerate(rec_headers, start=1):
        ws_rec.column_dimensions[get_column_letter(idx)].width = col_widths_map.get(col_name, 20)

    # -----------------------------------------------------------------------
    # Sheet 2: "PRISMA_Counts"
    # -----------------------------------------------------------------------
    ws_prisma = wb.create_sheet(title="PRISMA_Counts")
    ws_prisma.append(["PRISMA 2020 Stage", "Metric / Box Description", "Count", "Source / Formula Basis"])
    ws_prisma.freeze_panes = "A2"

    for col_idx in range(1, 5):
        c = ws_prisma.cell(row=1, column=col_idx)
        c.font = hdr_font
        c.fill = hdr_fill
        c.border = cell_border

    prisma_rows = [
        ("Identification", "Total records identified from databases", prisma["total_identified"], "Sum of database search hits"),
    ]
    for db_name, db_cnt in prisma["db_breakdown"].items():
        prisma_rows.append(("Identification (Database)", f"Records identified: {db_name}", db_cnt, "config.json databases_searched"))

    prisma_rows.extend([
        ("Identification", "Duplicate records removed before screening", prisma["duplicates_removed"], "config.json / screening_stage='identified'"),
        ("Screening", "Records screened (title and abstract)", prisma["records_screened"], "Identified minus duplicates removed"),
        ("Screening", "Records excluded at title/abstract screening", prisma["excluded_at_screening"], "=COUNTIF(Records!L2:L31, \"screened\")"),
        ("Screening (Eligibility)", "Reports sought for retrieval & assessed for full-text eligibility", prisma["reports_assessed_full_text"], "Screened minus excluded at title/abstract"),
        ("Screening (Eligibility)", "Reports excluded at full-text eligibility review", prisma["excluded_at_full_text"], "=COUNTIF(Records!L2:L31, \"full_text\")"),
        ("Included", "Studies included in final synthesis", prisma["studies_included"], "=COUNTIF(Records!L2:L31, \"included\")"),
    ])

    for row in prisma_rows:
        ws_prisma.append(list(row))
        r_num = ws_prisma.max_row
        for c_i in range(1, 5):
            cell = ws_prisma.cell(row=r_num, column=c_i)
            cell.font = bold_font if row[0] == "Included" else body_font
            cell.border = cell_border
            cell.alignment = wrap_top

    ws_prisma.column_dimensions["A"].width = 26
    ws_prisma.column_dimensions["B"].width = 52
    ws_prisma.column_dimensions["C"].width = 14
    ws_prisma.column_dimensions["D"].width = 38

    # -----------------------------------------------------------------------
    # Sheet 3: "Exclusion_Reasons" (with dynamic Excel COUNTIFS formulas)
    # -----------------------------------------------------------------------
    ws_excl = wb.create_sheet(title="Exclusion_Reasons")
    ws_excl.append(["Exclusion Stage", "Exclusion Reason", "Count (Formula)", "Verified Count"])
    ws_excl.freeze_panes = "A2"

    for col_idx in range(1, 5):
        c = ws_excl.cell(row=1, column=col_idx)
        c.font = hdr_font
        c.fill = hdr_fill
        c.border = cell_border

    last_rec_row = len(records) + 1
    excl_entries: list[tuple[str, str, int]] = []
    if prisma["duplicates_removed"] > 0:
        excl_entries.append(("identified", "Duplicate record", prisma["duplicates_removed"]))
    for reason, cnt in sorted(prisma["screening_reasons"].items(), key=lambda x: (-x[1], x[0])):
        excl_entries.append(("screened", reason, cnt))
    for reason, cnt in sorted(prisma["full_text_reasons"].items(), key=lambda x: (-x[1], x[0])):
        excl_entries.append(("full_text", reason, cnt))

    for idx_e, (stg, reason, cnt) in enumerate(excl_entries, start=2):
        formula = f'=COUNTIFS(Records!$L$2:$L${last_rec_row}, A{idx_e}, Records!$M$2:$M${last_rec_row}, B{idx_e})'
        ws_excl.append([stg, reason, formula, cnt])
        for c_i in range(1, 5):
            cell = ws_excl.cell(row=idx_e, column=c_i)
            cell.font = body_font
            cell.border = cell_border
            cell.alignment = wrap_top

    total_row_idx = len(excl_entries) + 2
    ws_excl.append([
        "TOTAL EXCLUDED",
        "All exclusion stages combined",
        f"=SUM(C2:C{total_row_idx - 1})",
        sum(x[2] for x in excl_entries),
    ])
    for c_i in range(1, 5):
        cell = ws_excl.cell(row=total_row_idx, column=c_i)
        cell.font = subhdr_font
        cell.fill = subhdr_fill
        cell.border = cell_border

    ws_excl.column_dimensions["A"].width = 22
    ws_excl.column_dimensions["B"].width = 44
    ws_excl.column_dimensions["C"].width = 22
    ws_excl.column_dimensions["D"].width = 18

    # -----------------------------------------------------------------------
    # Sheet 4: "README"
    # -----------------------------------------------------------------------
    ws_readme = wb.create_sheet(title="README")
    ws_readme.append(["Section / Sheet", "Description & QA Guidance"])
    ws_readme.freeze_panes = "A2"
    for col_idx in range(1, 3):
        c = ws_readme.cell(row=1, column=col_idx)
        c.font = hdr_font
        c.fill = hdr_fill
        c.border = cell_border

    readme_rows = [
        ("Project Title", config.get("project_title", "—")),
        ("Target Indication", config.get("indication", "—")),
        ("Search String", config.get("search_string", "—")),
        ("Date of Search", config.get("date_of_search", "—")),
        ("Sample Data Notice", "FICTIONAL SAMPLE DATA FOR DEMO" if config.get("is_fictional_sample") else "Live Literature Run"),
        ("Sheet: Records", "Complete table of all identified, screened, full-text assessed, and included records. Frozen header row and AutoFilter enabled on all columns. DOIs are formatted as clickable hyperlinks."),
        ("Sheet: PRISMA_Counts", "Exact stage-by-stage numbers feeding the PRISMA 2020 flow diagram, verified for mathematical conservation."),
        ("Sheet: Exclusion_Reasons", "Breakdown of excluded records by screening stage and specific exclusion reason using live Excel COUNTIFS formulas."),
        ("Integrity & Highlighting Rule", "No citations, DOIs, or conclusions are ever fabricated. Any missing field is left blank, highlighted in soft yellow (#FFF2CC), and flagged in the 'needs_verification' column for manual QA check against the source publication."),
    ]
    for r in readme_rows:
        ws_readme.append(list(r))
        rn = ws_readme.max_row
        ws_readme.cell(row=rn, column=1).font = bold_font
        ws_readme.cell(row=rn, column=1).border = cell_border
        ws_readme.cell(row=rn, column=1).alignment = wrap_top
        ws_readme.cell(row=rn, column=2).font = body_font
        ws_readme.cell(row=rn, column=2).border = cell_border
        ws_readme.cell(row=rn, column=2).alignment = wrap_top

    ws_readme.column_dimensions["A"].width = 28
    ws_readme.column_dimensions["B"].width = 85

    wb.save(str(out_path))


# ---------------------------------------------------------------------------
# 4. Output 3: Print-Ready Visual PRISMA 2020 Flow Diagram (PNG 300 dpi + PDF)
# ---------------------------------------------------------------------------

def _draw_box(
    ax,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    body_lines: list[str],
    header_fill: str | None = None,
    box_fill: str = "#FFFFFF",
    edge_color: str = "#1A1A1A",
    fontsize: float = 9.0,
) -> None:
    """Draw a crisp, print-ready rounded box with optional top accent bar and
    auto-wrapped non-overlapping text."""
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.0,rounding_size=0.08",
        facecolor=box_fill,
        edgecolor=edge_color,
        linewidth=1.2,
        zorder=2,
    )
    ax.add_patch(patch)

    text_y = y + h - 0.16
    if title:
        ax.text(
            x + 0.16,
            text_y,
            title,
            ha="left",
            va="top",
            fontsize=fontsize + 0.5,
            fontweight="bold",
            family="sans-serif",
            color="#111111",
            zorder=3,
        )
        text_y -= 0.24

    if body_lines:
        body_text = "\n".join(body_lines)
        ax.text(
            x + 0.16,
            text_y,
            body_text,
            ha="left",
            va="top",
            fontsize=fontsize,
            family="sans-serif",
            color="#1A1A1A",
            linespacing=1.32,
            zorder=3,
        )


def _draw_phase_pill(ax, x: float, y: float, w: float, h: float, label: str) -> None:
    """Draw the vertical phase label bar on the far left (Identification, Screening, Included)."""
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.0,rounding_size=0.08",
        facecolor="#1F4E79",
        edgecolor="#1F4E79",
        linewidth=1.0,
        zorder=2,
    )
    ax.add_patch(patch)
    ax.text(
        x + w / 2.0,
        y + h / 2.0,
        label,
        ha="center",
        va="center",
        rotation=90,
        fontsize=10.0,
        fontweight="bold",
        family="sans-serif",
        color="#FFFFFF",
        zorder=3,
    )


def _draw_arrow(ax, x1: float, y1: float, x2: float, y2: float) -> None:
    ax.annotate(
        "",
        xy=(x2, y2),
        xytext=(x1, y1),
        arrowprops=dict(
            arrowstyle="-|>,head_length=0.6,head_width=0.35",
            color="#1A1A1A",
            lw=1.3,
            shrinkA=0,
            shrinkB=0,
        ),
        zorder=4,
    )


def generate_prisma_flowchart(
    prisma: dict[str, Any],
    config: dict[str, Any],
    png_path: str | Path,
    pdf_path: str | Path | None = None,
) -> None:
    """Render a clean, print-ready PRISMA 2020 flow diagram at 300 DPI PNG and vector PDF."""
    fig, ax = plt.subplots(figsize=(11.0, 8.5), dpi=300)
    fig.patch.set_facecolor("#FFFFFF")
    ax.set_facecolor("#FFFFFF")
    ax.set_xlim(0, 11.0)
    ax.set_ylim(0, 8.5)
    ax.axis("off")

    # Header Title Banner
    proj_title = config.get("project_title", "PRISMA 2020 Flow Diagram")
    ax.text(
        5.5,
        8.22,
        "PRISMA 2020 Flow Diagram — Systematic Literature Surveillance",
        ha="center",
        va="top",
        fontsize=12.5,
        fontweight="bold",
        family="sans-serif",
        color="#1F4E79",
    )
    ax.text(
        5.5,
        7.92,
        textwrap.shorten(proj_title, width=110, placeholder="..."),
        ha="center",
        va="top",
        fontsize=9.5,
        family="sans-serif",
        color="#333333",
    )

    # Coordinates layout:
    # Phase pills: x = 0.25, width = 0.45
    # Left column boxes: x = 0.95, width = 4.30 (x_right = 5.25, x_center = 3.10)
    # Right column boxes: x = 6.05, width = 4.65 (x_right = 10.70)
    lx, lw = 0.95, 4.30
    l_mid = lx + lw / 2.0
    rx, rw = 6.05, 4.65

    # --- PHASE 1: IDENTIFICATION (y = 5.85 to 7.55) ---
    _draw_phase_pill(ax, 0.25, 5.85, 0.45, 1.70, "Identification")

    db_lines = [f"Databases (total n = {prisma['total_identified']}):"]
    for db_name, db_cnt in prisma["db_breakdown"].items():
        db_lines.append(f"  • {db_name} (n = {db_cnt})")
    if not prisma["db_breakdown"]:
        db_lines.append(f"  • Combined databases (n = {prisma['total_identified']})")

    _draw_box(
        ax, lx, 5.85, lw, 1.70,
        title="Records identified from:",
        body_lines=db_lines,
        box_fill="#F8FAFC",
    )

    _draw_box(
        ax, rx, 6.10, rw, 1.25,
        title="Records removed before screening:",
        body_lines=[
            f"• Duplicate records removed (n = {prisma['duplicates_removed']})",
        ],
        box_fill="#FFFFFF",
    )

    # Horizontal arrow: Identification Left -> Identification Right
    _draw_arrow(ax, lx + lw, 6.72, rx, 6.72)

    # Vertical arrow: Identification Left -> Screening Row 1
    _draw_arrow(ax, l_mid, 5.85, l_mid, 5.20)

    # --- PHASE 2: SCREENING (y = 1.95 to 5.20) ---
    _draw_phase_pill(ax, 0.25, 1.95, 0.45, 3.25, "Screening")

    # Screening Row 1 Left: Records screened
    _draw_box(
        ax, lx, 3.95, lw, 1.25,
        title="Records screened",
        body_lines=[
            f"Title and abstract screening (n = {prisma['records_screened']})",
        ],
        box_fill="#F8FAFC",
    )

    # Screening Row 1 Right: Records excluded at title/abstract screening
    scr_lines = [f"Total excluded at screening (n = {prisma['excluded_at_screening']}):"]
    for reason, cnt in sorted(prisma["screening_reasons"].items(), key=lambda item: (-item[1], item[0])):
        wrapped = textwrap.wrap(f"• {reason} (n = {cnt})", width=52)
        scr_lines.extend(["  " + w_line if i > 0 else w_line for i, w_line in enumerate(wrapped)])
    if not prisma["screening_reasons"]:
        scr_lines = [f"Total excluded at title/abstract stage (n = {prisma['excluded_at_screening']})"]

    _draw_box(
        ax, rx, 3.85, rw, 1.45,
        title="Records excluded (title / abstract):",
        body_lines=scr_lines[:6],
        box_fill="#FFFFFF",
        fontsize=8.5,
    )

    # Horizontal arrow: Screened -> Excluded at screening
    _draw_arrow(ax, lx + lw, 4.58, rx, 4.58)

    # Vertical arrow: Screened -> Full-text assessed
    _draw_arrow(ax, l_mid, 3.95, l_mid, 3.30)

    # Screening Row 2 Left: Reports assessed for eligibility
    _draw_box(
        ax, lx, 2.00, lw, 1.30,
        title="Reports assessed for eligibility",
        body_lines=[
            f"Full-text reports sought & assessed (n = {prisma['reports_assessed_full_text']})",
            "Reports not retrieved (n = 0)",
        ],
        box_fill="#F8FAFC",
    )

    # Screening Row 2 Right: Reports excluded at full-text with reasons
    ft_lines = [f"Total excluded at full-text review (n = {prisma['excluded_at_full_text']}):"]
    for reason, cnt in sorted(prisma["full_text_reasons"].items(), key=lambda item: (-item[1], item[0])):
        wrapped = textwrap.wrap(f"• {reason} (n = {cnt})", width=52)
        ft_lines.extend(["  " + w_line if i > 0 else w_line for i, w_line in enumerate(wrapped)])
    if not prisma["full_text_reasons"]:
        ft_lines = [f"Total excluded at full-text stage (n = {prisma['excluded_at_full_text']})"]

    _draw_box(
        ax, rx, 1.95, rw, 1.50,
        title="Reports excluded (full-text reasons):",
        body_lines=ft_lines[:6],
        box_fill="#FFFFFF",
        fontsize=8.5,
    )

    # Horizontal arrow: Assessed -> Excluded full-text
    _draw_arrow(ax, lx + lw, 2.65, rx, 2.65)

    # Vertical arrow: Assessed -> Included
    _draw_arrow(ax, l_mid, 2.00, l_mid, 1.40)

    # --- PHASE 3: INCLUDED (y = 0.35 to 1.40) ---
    _draw_phase_pill(ax, 0.25, 0.35, 0.45, 1.05, "Included")

    _draw_box(
        ax, lx, 0.35, lw, 1.05,
        title="Studies included in review",
        body_lines=[
            f"Studies included in synthesis (n = {prisma['studies_included']})",
        ],
        box_fill="#EBF3FA",
        edge_color="#1F4E79",
    )

    # Footer caption on figure
    footer_note = (
        f"Source: PRISMA 2020 statement | Search date: {config.get('date_of_search', '—')} | "
        "Arithmetic conservation verified automatically."
    )
    if config.get("is_fictional_sample"):
        footer_note += " [FICTIONAL SAMPLE DATA FOR DEMO]"
    ax.text(
        5.5,
        0.08,
        footer_note,
        ha="center",
        va="bottom",
        fontsize=8.0,
        style="italic",
        color="#555555",
    )

    plt.tight_layout(pad=0.3)
    fig.savefig(str(png_path), dpi=300, facecolor="#FFFFFF")
    if pdf_path is not None:
        fig.savefig(str(pdf_path), format="pdf", facecolor="#FFFFFF")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 5. Output 4: Complete Package Orchestrator & Zip Bundler
# ---------------------------------------------------------------------------

def build_pilot_package(
    csv_path: str | Path,
    config_path: str | Path,
    base_output_dir: str | Path = "output",
) -> dict[str, Path]:
    """Load inputs, verify PRISMA conservation, build Word, Excel, PNG, PDF,
    and zip them into `output/<project_slug>_pilot.zip`."""
    records, config = load_inputs(csv_path, config_path)
    prisma = compute_and_verify_prisma(records, config)

    slug_raw = config.get("project_slug") or config.get("project_title", "literature")
    slug = re.sub(r"[^a-z0-9_]+", "_", slug_raw.lower()).strip("_")[:45] or "project"

    base_out = Path(base_output_dir)
    pkg_dir = base_out / f"{slug}_pilot"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    docx_path = pkg_dir / f"{slug}_evidence_table.docx"
    xlsx_path = pkg_dir / f"{slug}_literature_workbook.xlsx"
    png_path = pkg_dir / "prisma_flow_diagram.png"
    pdf_path = pkg_dir / "prisma_flow_diagram.pdf"

    generate_word_docx(records, config, prisma, docx_path)
    generate_excel_xlsx(records, config, prisma, xlsx_path)
    generate_prisma_flowchart(prisma, config, png_path, pdf_path)

    zip_path = base_out / f"{slug}_pilot.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_p in (docx_path, xlsx_path, png_path, pdf_path):
            zf.write(file_p, arcname=f"{slug}_pilot/{file_p.name}")

    return {
        "package_dir": pkg_dir,
        "docx": docx_path,
        "xlsx": xlsx_path,
        "png": png_path,
        "pdf": pdf_path,
        "zip": zip_path,
        "prisma": prisma,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a complete QA-ready Regulatory Literature Pilot Package "
                    "(.docx, .xlsx, PRISMA 2020 .png/.pdf, and .zip) from records.csv and config.json."
    )
    parser.add_argument("--csv", default="records.csv", help="Path to input records.csv")
    parser.add_argument("--config", default="config.json", help="Path to input config.json")
    parser.add_argument("--output-dir", default="output", help="Base output directory (default: output)")
    args = parser.parse_args(argv)

    result = build_pilot_package(args.csv, args.config, args.output_dir)
    p = result["prisma"]
    print("=" * 68)
    print("  PILOT PACKAGE GENERATED & PRISMA ARITHMETIC VERIFIED")
    print("=" * 68)
    print(f"Identified (DB total)   : {p['total_identified']}")
    print(f"Duplicates removed      : {p['duplicates_removed']}")
    print(f"Records screened        : {p['records_screened']}")
    print(f"Excluded at screening   : {p['excluded_at_screening']}")
    print(f"Assessed full-text      : {p['reports_assessed_full_text']}")
    print(f"Excluded at full-text   : {p['excluded_at_full_text']}")
    print(f"Studies included        : {p['studies_included']}")
    print("-" * 68)
    print(f"Word Evidence Table     : {result['docx']}")
    print(f"Excel Literature Book   : {result['xlsx']}")
    print(f"PRISMA 2020 PNG (300dpi): {result['png']}")
    print(f"PRISMA 2020 Vector PDF  : {result['pdf']}")
    print(f"Zipped Pilot Package    : {result['zip']}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
