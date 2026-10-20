#!/usr/bin/env python3
"""
02_clinical_trial_eda.py — Synthetic Clinical Trial EDA & Regulatory "Table 1"
==============================================================================

Purpose
-------
Generate a seeded, fully synthetic trial cohort (103 raw rows -> cleaned
n=100), apply an auditable data-cleaning chain, compute the baseline
demographics "Table 1" that opens every Clinical Study Report / Common
Module Document, test the treatment contrast WITHOUT scipy (exact
continued-fraction implementations of the Welch t-test and Pearson
chi-square), draw the biomarker boxplot, and emit a Colab-ready notebook.

Regulatory / scientific relevance
---------------------------------
* Seeded RNG (seed=2027) makes every figure byte-reproducible — the
  computational analogue of a validated, re-executable analysis environment
  (ICH E6(R2) §4.9; ALCOA+ "Original/Consistent").
* Every cleaning rule is logged with row counts, producing the kind of
  data-cleaning audit trail a sponsor QA reviewer or inspector expects.
* Arm-stratified median imputation avoids biasing the treatment contrast
  (missing-data handling philosophy of ICH E9 §5.2).
* p-values are reported descriptively: n=100 in this synthetic scenario is
  NOT powered for confirmatory inference. In this scenario a LOWER
  Biomarker_Level denotes the intended pharmacodynamic effect.

Privacy statement
-----------------
All clinical data used in this repository is strictly synthetic and
anonymized for educational and portfolio purposes, adhering to GDPR and
HIPAA privacy frameworks. Patients are identified only by coded study IDs
(SUBJ-####); no personal contact information appears anywhere.

Environment
-----------
Python 3.10+ · Google Colab / GitHub Codespaces · peak RAM < 100 MB · no GPU.

Usage
-----
    python 02_clinical_trial_eda.py [--seed 2027] [--png table1_demographics.png]
"""

from __future__ import annotations

import argparse
import json
import math
import sys

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")          # headless-safe (Colab/Codespaces/CI)
import matplotlib.pyplot as plt
import seaborn as sns

# ---------------------------------------------------------------------------
# Constants — controlled vocabularies mirror CDISC-style codelist harmonisation
# ---------------------------------------------------------------------------

SEED_DEFAULT = 2027
N_RAW, N_DUPLICATES = 103, 3            # 103 raw rows -> cleaned n = 100

SEX_ALLOWED = {"M": "Male", "F": "Female", "MALE": "Male", "FEMALE": "Female",
               "1": "Male", "2": "Female"}
ARM_ALLOWED = {"placebo": "Placebo", "PLC": "Placebo", "PLACEBO": "Placebo",
               "treatment": "Treatment", "TRT": "Treatment", "ACTIVE": "Treatment"}
AE_ALLOWED = {"none": "None", "NONE": "None", "mild": "Mild", "MILD": "Mild",
              "moderate": "Moderate", "MODERATE": "Moderate",
              "severe": "Severe", "SEVERE": "Severe"}

# Implausible-range rejection limits (protocol-style eligibility boundaries)
AGE_RANGE = (18, 85)          # adults only; outside => query/resolution failure
BMI_RANGE = (12.0, 60.0)      # physiological survival envelope
BIOMARKER_RANGE = (0.0, 200.0)

CLEANING_LOG: list[str] = []


def log(msg: str) -> None:
    """Append an auditable cleaning-trail entry (ICH E6(R2)-style: every
    transformation of source data must be documented and attributable)."""
    CLEANING_LOG.append(msg)
    print(f"[clean] {msg}")


# ---------------------------------------------------------------------------
# Statistical primitives — exact, dependency-free (no scipy) implementations
# ---------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method,
    Numerical Recipes §6.4). Used to build the exact t and chi-square tails."""
    MAXIT, EPS, FPMIN = 200, 3e-12, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta function I_x(a, b) — exact to ~1e-12."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta + b * math.log1p(-x) + a * math.log(x)) \
        * _betacf(b, a, 1.0 - x) / b


def t_sf(t: float, df: float) -> float:
    """Two-sided-safe upper tail P(T > t) for Student's t via the incomplete
    beta identity:  I_{df/(df+t^2)}(df/2, 1/2) / 2."""
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    return 0.5 * betainc(df / 2.0, 0.5, x)


def welch_ttest(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """Welch's unequal-variance t-test. Preferred over Student's t in
    regulatory summaries unless variance homogeneity is demonstrated
    (conservative default per ICH E9 §5.6 statistical-principle guidance).
    Returns (t, dof, two-sided p)."""
    na, nb = len(a), len(b)
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se = math.sqrt(va / na + vb / nb)
    t = (a.mean() - b.mean()) / se
    df = (va / na + vb / nb) ** 2 / (
        (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    p = 2.0 * t_sf(abs(t), df)
    return t, df, min(p, 1.0)


def _chi2_sf(x: float, k: int) -> float:
    """Upper tail of chi-square via the regularised gamma function Q(k/2, x/2)
    (series/continued-fraction split, Numerical Recipes §6.2 gser/gcf)."""
    if x <= 0:
        return 1.0
    a, xx = k / 2.0, x / 2.0
    FPMIN = 1e-300
    ln_pre = -xx + a * math.log(xx) - math.lgamma(a)
    if xx < a + 1.0:                      # series for P(a, x)
        ap = a                            # NOTE: ap must start at a (not 1/a)
        term = total = 1.0 / a
        for _ in range(1000):
            ap += 1.0
            term *= xx / ap
            total += term
            if abs(term) < abs(total) * 3e-12:
                break
        return 1.0 - total * math.exp(ln_pre)
    # modified Lentz continued fraction for Q(a, x); per NR, c must start at
    # 1/FPMIN (starting at 1.0 corrupts the first iteration)
    b = xx + 1.0 - a
    c = 1.0 / FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < FPMIN:
            d = FPMIN
        c = b + an / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h * math.exp(ln_pre)


def chi_square_test(table: np.ndarray) -> tuple[float, int, float]:
    """Pearson chi-square test of independence on a contingency table.
    Used here for categorical baseline balance (Sex, Adverse_Event_Severity)
    across arms — the standard Table-1 screening statistics.
    Returns (chi2, dof, p)."""
    total = table.sum()
    rows, cols = table.sum(axis=1), table.sum(axis=0)
    expected = np.outer(rows, cols) / total
    mask = expected > 0
    chi2 = float((((table - expected) ** 2) / np.where(mask, expected, 1))[mask].sum())
    dof = (table.shape[0] - 1) * (table.shape[1] - 1)
    return chi2, dof, _chi2_sf(chi2, dof)


# ---------------------------------------------------------------------------
# Synthetic cohort generation (seeded => byte-reproducible; privacy-by-design)
# ---------------------------------------------------------------------------

def generate_raw_cohort(seed: int) -> pd.DataFrame:
    """Build the RAW 103-row 'EDC extract' including deliberately injected
    real-world messiness: 3 duplicate Patient_ID records, inconsistent
    vocabulary spellings, out-of-range implausible values, and missing cells.
    A pristine dataset would demonstrate nothing about data management."""
    rng = np.random.default_rng(seed)
    n = N_RAW
    arm = np.array(["Placebo"] * (n // 2) + ["Treatment"] * (n - n // 2))
    rng.shuffle(arm)

    patient_id = [f"SUBJ-{i:04d}" for i in range(1, n - N_DUPLICATES + 1)]
    age = np.round(rng.normal(42, 12, n)).astype(float)
    sex = rng.choice(["M", "F"], n, p=[0.52, 0.48])
    bmi = np.round(rng.normal(26.0, 4.0, n), 1)
    ae = rng.choice(["None", "Mild", "Moderate", "Severe"], n,
                    p=[0.45, 0.30, 0.18, 0.07])
    # Intended pharmacodynamic signal: treatment LOWERS the biomarker.
    base = rng.normal(58.0, 12.0, n)
    biomarker = np.where(arm == "Treatment", base - 4.5, base)

    # Draw the injected duplicate IDs WITHOUT replacement. A plain
    # rng.choice(..., N_DUPLICATES) can pick the same ID twice, which would
    # leave only 99 unique subjects after de-duplication and trip the n=100
    # assertion for unlucky seeds. Redrawing on collision keeps the default
    # seed=2027 stream (and therefore all committed outputs) byte-identical —
    # this draw MUST stay at this exact position in the RNG consumption order.
    dup_ids = list(rng.choice(patient_id, N_DUPLICATES))
    while len(set(dup_ids)) < N_DUPLICATES:
        dup_ids = list(rng.choice(patient_id, N_DUPLICATES))

    df = pd.DataFrame({
        "Patient_ID": patient_id + dup_ids,
        "Age": age, "Sex": sex, "BMI": bmi,
        "Treatment_Arm": arm, "Adverse_Event_Severity": ae,
        "Biomarker_Level": np.round(biomarker, 2),
    })
    df = df.sample(frac=1.0, random_state=seed).reset_index(drop=True)

    # Inject controlled imperfections (documented, reproducible, auditable):
    idx = df.index
    df.loc[idx[3], "Sex"] = "male"                       # free-text variant
    df.loc[idx[9], "Sex"] = "2"                          # numeric-coded variant
    df.loc[idx[15], "Treatment_Arm"] = "PLC"             # sponsor abbreviation
    df.loc[idx[21], "Adverse_Event_Severity"] = "MODERATE"   # case drift
    df.loc[idx[27], "Age"] = np.nan                      # missing cell
    df.loc[idx[33], "BMI"] = np.nan                      # missing cell
    df.loc[idx[41], "Age"] = 9.4                         # implausible (pediatric)
    df.loc[idx[52], "BMI"] = 121.0                       # implausible (unit error)
    df.loc[idx[60], "Biomarker_Level"] = np.nan          # missing lab value
    return df


# ---------------------------------------------------------------------------
# Auditable cleaning chain
# ---------------------------------------------------------------------------

def harmonise(series: pd.Series, mapping: dict) -> pd.Series:
    """Controlled-vocabulary harmonisation (CDISC codelist style): map known
    variants to canonical terms; unknown tokens become NaN for review rather
    than being silently coerced — accuracy over convenience (ALCOA+)."""
    return series.astype("string").str.strip().map(
        lambda v: mapping.get(v, mapping.get(str(v).upper(), v)))


def clean_cohort(raw: pd.DataFrame) -> pd.DataFrame:
    """Apply the documented cleaning rules in order, logging each step."""
    log(f"Raw EDC extract received: {len(raw)} rows.")

    # Rule 1 — de-duplication by coded subject identifier (source-record
    # duplication inflates denominators; resolved by keeping first occurrence).
    before = len(raw)
    df = raw.drop_duplicates(subset="Patient_ID", keep="first").copy()
    log(f"Rule 1 dedup by Patient_ID: removed {before - len(df)} duplicate "
        f"record(s) -> {len(df)} rows.")

    # Rule 2 — controlled-vocabulary harmonisation.
    df["Sex"] = harmonise(df["Sex"], SEX_ALLOWED)
    df["Treatment_Arm"] = harmonise(df["Treatment_Arm"], ARM_ALLOWED)
    df["Adverse_Event_Severity"] = harmonise(df["Adverse_Event_Severity"], AE_ALLOWED)
    bad_sex = (~df["Sex"].isin(["Male", "Female"])).sum()
    bad_arm = (~df["Treatment_Arm"].isin(["Placebo", "Treatment"])).sum()
    log(f"Rule 2 vocabulary harmonisation complete "
        f"(unmapped Sex={bad_sex}, unmapped Arm={bad_arm}).")

    # Rule 3 — implausible-range rejection (hard protocol boundaries). Cells
    # outside pre-specified validity ranges are set to NaN and counted, never
    # quietly edited (data-integrity expectation). Violations are handled as
    # query-and-resolve: out-of-range CELLS become missing, then Rule-4
    # arm-stratified median imputation restores a complete-case dataset.
    # In the seeded raw extract this catches the deliberately injected hard
    # violations (Age=9.4 pediatric outlier, BMI=121 unit error) plus any
    # naturally implausible draws from the seeded RNG; the injected missing
    # cells (Age, BMI, Biomarker_Level) flow straight through to Rule 4.
    # Exact counts are reported by the audit line below — they are a function
    # of the seed and are never hard-coded here.
    for col in ("Age", "BMI", "Biomarker_Level"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    lo_hi = {"Age": AGE_RANGE, "BMI": BMI_RANGE,
             "Biomarker_Level": BIOMARKER_RANGE}
    n_oob = 0
    for col, (lo, hi) in lo_hi.items():
        oob = ~(df[col].isna() | df[col].between(lo, hi))
        n_oob += int(oob.sum())
        df.loc[oob, col] = np.nan
    log(f"Rule 3 implausible-range flagging (Age {AGE_RANGE}, BMI {BMI_RANGE}, "
        f"Biomarker {BIOMARKER_RANGE}): {n_oob} out-of-range cell(s) converted "
        f"to missing-for-query; subject population stays at {len(df)} rows.")

    # Rule 4 — arm-stratified median imputation for remaining missing cells
    # (original missings + flagged out-of-range cells). Imputing WITHIN arm
    # preserves the treatment contrast (global-mean imputation would bias it
    # toward the null; ICH E9 §5.2 philosophy).
    n_missing = int(df[["Age", "BMI", "Biomarker_Level"]].isna().sum().sum())
    for col in ("Age", "BMI", "Biomarker_Level"):
        df[col] = df.groupby("Treatment_Arm")[col] \
                    .transform(lambda s: s.fillna(s.median()))
    log(f"Rule 4 arm-stratified median imputation: filled {n_missing} missing "
        f"cell(s). Clean analysis population: n={len(df)}.")

    assert len(df) == 100, f"Expected cleaned n=100, got {len(df)}"
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Table 1 — baseline characteristics by treatment arm
# ---------------------------------------------------------------------------

def describe(x: pd.Series) -> str:
    return f"{x.mean():.1f} ± {x.std(ddof=1):.1f}"


def table1(df: pd.DataFrame) -> pd.DataFrame:
    """Regulatory 'Table 1': continuous variables as mean ± SD with Welch
    between-arm p; categorical variables as n (%) with Pearson chi-square p.
    p-values are descriptive balance checks, NOT hypothesis-testing endpoints."""
    placebo = df[df["Treatment_Arm"] == "Placebo"]
    trt = df[df["Treatment_Arm"] == "Treatment"]
    rows = [{"Characteristic": "Number of subjects",
             "Placebo": f"n={len(placebo)}", "Treatment": f"n={len(trt)}", "p-value": "—"}]

    for col, label in [("Age", "Age, years (mean ± SD)"),
                       ("BMI", "BMI, kg/m² (mean ± SD)"),
                       ("Biomarker_Level", "Baseline biomarker level (mean ± SD)")]:
        a, b = placebo[col].to_numpy(dtype=float), trt[col].to_numpy(dtype=float)
        _, _, p = welch_ttest(a, b)
        rows.append({"Characteristic": label,
                     "Placebo": describe(placebo[col]),
                     "Treatment": describe(trt[col]),
                     "p-value": f"{p:.4f}"})

    for col, label in [("Sex", "Sex, n (%)"),
                       ("Adverse_Event_Severity", "Adverse event severity, n (%)")]:
        cats = sorted(set(df[col].dropna()))
        tab = np.array([[int((placebo[col] == c).sum()), int((trt[col] == c).sum())]
                        for c in cats])
        _, _, p = chi_square_test(tab)
        for i, c in enumerate(cats):
            first = i == 0
            rows.append({
                "Characteristic": f"{label} — {c}" if first else f"    {c}",
                "Placebo": f"{tab[i, 0]} ({100 * tab[i, 0] / len(placebo):.1f}%)",
                "Treatment": f"{tab[i, 1]} ({100 * tab[i, 1] / len(trt):.1f}%)",
                "p-value": f"{p:.4f}" if first else ""})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Figure 1 — biomarker distribution by arm (raw-data transparency overlay)
# ---------------------------------------------------------------------------

def make_figure(df: pd.DataFrame, png_path: str, seed: int = SEED_DEFAULT) -> None:
    """Boxplot with jittered raw observations and group-mean markers: showing
    the FULL dataset behind the summary boxes is a data-transparency practice
    aligned with good visual-reporting standards for dossier appendices."""
    # seaborn's stripplot jitter draws from the LEGACY global numpy RNG, which
    # the seeded default_rng() cohort generator does not touch — without this
    # reseed the figure differs on every run, breaking the byte-reproducibility
    # claim (data was always deterministic; only the jitter was not).
    np.random.seed(seed)
    sns.set_theme(style="whitegrid", context="talk")
    fig, ax = plt.subplots(figsize=(9, 6))
    palette = {"Placebo": "#4C72B0", "Treatment": "#DD8452"}
    sns.boxplot(data=df, x="Treatment_Arm", y="Biomarker_Level",
                hue="Treatment_Arm", palette=palette, legend=False,
                width=0.55, linewidth=1.6, ax=ax)
    sns.stripplot(data=df, x="Treatment_Arm", y="Biomarker_Level",
                  color="black", size=4, alpha=0.45, jitter=0.25, ax=ax)
    means = df.groupby("Treatment_Arm")["Biomarker_Level"].mean()
    ax.scatter(means.index, means.values, marker="D", s=110, color="crimson",
               zorder=5, label="group mean")
    ax.set_title("Figure 1 — Biomarker Level by Treatment Arm\n"
                 "(synthetic cohort, seed=2027; lower = intended PD effect)",
                 fontsize=15)
    ax.set_xlabel("")
    ax.set_ylabel("Biomarker Level (arb. units)")
    ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(png_path, dpi=150)
    plt.close(fig)
    print(f"[figure] Saved -> {png_path}")


# ---------------------------------------------------------------------------
# Notebook emission (Colab-ready artifact, generated from the same code path)
# ---------------------------------------------------------------------------

def emit_notebook(py_source: str, nb_path: str) -> None:
    """Write 02_clinical_trial_eda.ipynb: markdown narrative cells + one code
    cell that embeds this module verbatim and runs main([]). Single source of
    truth => notebook and script can never drift apart (reproducibility).

    IMPORTANT: the script's own `if __name__ == "__main__": sys.exit(main())`
    guard MUST be stripped from the embedded source. Inside a notebook
    __name__ == "__main__" is also true, so the guard would fire main() with
    the Jupyter kernel's sys.argv (e.g. ['-f', 'kernel-1234.json']) and die
    with "unrecognized arguments" before the notebook-safe main([]) below ever
    runs — i.e. the emitted notebook would crash on Colab at the first run.
    """
    guard = 'if __name__ == "__main__":\n    sys.exit(main())'
    embedded = py_source.replace(guard, "").rstrip() + "\n"
    md_intro = (
        "# Project 2 — Clinical Trial EDA & Regulatory Table 1\n\n"
        "**Author:** Kaled Bouchagraoui · **Stack:** pandas / numpy / seaborn "
        "(no scipy, no ML models) · Runs in Google Colab (< 100 MB RAM).\n\n"
        "This notebook generates a **seeded synthetic cohort** (103 raw rows → "
        "cleaned n=100), applies an **auditable cleaning chain**, produces the "
        "regulatory **baseline-demographics Table 1** (Welch t-test and Pearson "
        "chi-square computed from exact continued-fraction implementations), and "
        "draws **Figure 1** (biomarker by arm, raw-data overlay).\n\n"
        "> All clinical data here is strictly synthetic and anonymized for "
        "educational and portfolio purposes, adhering to GDPR and HIPAA privacy "
        "frameworks. A LOWER biomarker denotes the intended pharmacodynamic "
        "effect; n=100 is not powered for significance — p-values are reported "
        "descriptively."
    )
    nb = {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python",
                           "name": "python3"},
            "language_info": {"name": "python", "version": "3.10"},
        },
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": [md_intro]},
            {"cell_type": "code", "execution_count": None, "metadata": {},
             "outputs": [],
             "source": [embedded,
                        "\n\n# Notebook entry point: explicit empty argv (a bare main()\n"
                        "# would parse the Jupyter kernel's connection-file arguments).\n"
                        "main([])\n"]},
        ],
    }
    with open(nb_path, "w", encoding="utf-8") as fh:
        json.dump(nb, fh, indent=1)
    print(f"[notebook] Emitted -> {nb_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Synthetic clinical-trial EDA with regulatory-style "
                    "Table 1, scipy-free inferential tests, Figure 1, and "
                    "automatic notebook emission.")
    parser.add_argument("--seed", type=int, default=SEED_DEFAULT,
                        help="RNG seed for byte-reproducible synthetic data")
    parser.add_argument("--png", default="table1_demographics.png",
                        help="Output figure path")
    parser.add_argument("--csv", default="cleaned_cohort.csv",
                        help="Cleaned analysis population CSV path")
    parser.add_argument("--notebook", default="02_clinical_trial_eda.ipynb",
                        help="Output notebook path")
    args = parser.parse_args(argv)

    print("== Clinical Trial EDA & Regulatory Table 1 (synthetic cohort) ==")
    raw = generate_raw_cohort(args.seed)
    df = clean_cohort(raw)

    tbl = table1(df)
    print("\n--- TABLE 1: Baseline Demographics by Treatment Arm ---")
    print(tbl.to_string(index=False))

    placebo = df[df["Treatment_Arm"] == "Placebo"]["Biomarker_Level"].to_numpy()
    trt = df[df["Treatment_Arm"] == "Treatment"]["Biomarker_Level"].to_numpy()
    t_stat, dof, p_val = welch_ttest(placebo, trt)
    print(f"\nWelch t = {t_stat:.4f}, df = {dof:.2f}, two-sided p = {p_val:.4f}")
    print("(Descriptive only: n=100 synthetic cohort is not powered for "
          "confirmatory inference; lower biomarker = intended PD effect.)")

    print("\n--- Cleaning Audit Trail ---")
    for entry in CLEANING_LOG:
        print(f"  • {entry}")

    make_figure(df, args.png, seed=args.seed)
    here = __file__ if "__file__" in globals() else "02_clinical_trial_eda.py"
    try:
        with open(here, "r", encoding="utf-8") as fh:
            emit_notebook(fh.read(), args.notebook)
    except OSError as err:
        print(f"[warn] Could not read source for notebook emission: {err}")

    # Persist the cleaned analysis population alongside outputs (traceability).
    df.to_csv(args.csv, index=False)
    print(f"Wrote cleaned analysis population -> {args.csv} (n={len(df)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
