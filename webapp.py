#!/usr/bin/env python3
"""
GenevaOS Web — app-style interface for the Geneva Protocol + portfolio viewer.

Zero new dependencies: Python stdlib HTTP server + the same SQLite database
(geneva_os.db) and the same business logic (main.py / database.py) the CLI
uses. CLI and web UI share state — log a grade in the browser, see it in
`python main.py academics list`, and vice versa.

Run:
    python webapp.py                # http://127.0.0.1:8000 (auto-opens browser)
    python webapp.py --port 8080    # custom port
    python webapp.py --host 0.0.0.0 # expose on LAN (e.g. use from your phone)
    python webapp.py --no-browser   # don't auto-open

Offline-first: the UI is a single embedded HTML page with inline CSS/JS —
no CDNs, no external fonts, no tracking. Binds to localhost by default.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

import database as db
from main import (
    ADMIN_DEADLINES,
    AM_STEPS,
    CORE_SUBJECTS,
    DAY1_THRESHOLD_CHF,
    ENDPOINTS,
    KILL_SWITCH_THRESHOLD,
    PM_SHAVE,
    PM_STANDARD,
    SHAVE_DAYS,
    fmt_hms,
    parse_distance_meters,
    parse_time_to_seconds,
)

ROOT = Path(__file__).resolve().parent
PORTFOLIO_DIR = ROOT / "computational-biology-portfolio"
MAX_BODY_BYTES = 64 * 1024

DAY_INDEX = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "wednesday": 2,
             "wed": 2, "thursday": 3, "thu": 3, "thur": 3, "friday": 4, "fri": 4,
             "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}

ARTIFACTS = {
    "cftr_analysis.csv": "text/csv; charset=utf-8",
    "cleaned_cohort.csv": "text/csv; charset=utf-8",
    "pubmed_bibliography.csv": "text/csv; charset=utf-8",
    "literature_matrix.md": "text/markdown; charset=utf-8",
    "table1_demographics.png": "image/png",
    "02_clinical_trial_eda.ipynb": "application/json",
    "README.md": "text/markdown; charset=utf-8",
}


# ---------------------------------------------------------------- helpers --
def _kill_switch_state() -> dict:
    """Same rule as the CLI: any CORE_SUBJECT rolling avg < threshold."""
    subjects = []
    triggered = []
    for s in CORE_SUBJECTS:
        avg = db.rolling_weighted_average(s)
        subjects.append({"subject": s, "avg": round(avg, 2) if avg is not None else None})
        if avg is not None and avg < KILL_SWITCH_THRESHOLD:
            triggered.append(s)
    return {"triggered": triggered, "threshold": KILL_SWITCH_THRESHOLD,
            "subjects": subjects}


def _rollups() -> list:
    subjects = sorted({r[2] for r in db.list_academics(10_000)})
    out = []
    for s in subjects:
        avg = db.rolling_weighted_average(s)
        risk = s in CORE_SUBJECTS and avg is not None and avg < KILL_SWITCH_THRESHOLD
        out.append({"subject": s, "avg": round(avg, 2) if avg is not None else None,
                    "core": s in CORE_SUBJECTS, "risk": risk})
    return out


def _swiss_state() -> dict:
    liquidity = db.chf_liquidity()
    shortfall = max(DAY1_THRESHOLD_CHF - liquidity, 0.0)
    today = date.today()
    deadlines = []
    for name, iso in ADMIN_DEADLINES:
        due = datetime.strptime(iso, "%Y-%m-%d").date()
        deadlines.append({"task": name, "due": iso, "days_left": (due - today).days})
    deadlines.sort(key=lambda d: d["due"])
    upcoming = [d for d in deadlines if d["days_left"] >= 0]
    return {"liquidity": round(liquidity, 2), "threshold": DAY1_THRESHOLD_CHF,
            "shortfall": round(shortfall, 2),
            "surplus": round(max(liquidity - DAY1_THRESHOLD_CHF, 0.0), 2),
            "percent": round(min(100.0, 100.0 * liquidity / DAY1_THRESHOLD_CHF), 1),
            "cleared": liquidity >= DAY1_THRESHOLD_CHF,
            "next": upcoming[0] if upcoming else None,
            "deadlines": deadlines}


def _hub_ping(timeout: float = 1.2) -> dict:
    def probe(item):
        name, url = item
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.ok:
                detail = f"HTTP {resp.status_code}"
                try:
                    body = resp.json()
                    if isinstance(body, dict) and "version" in body:
                        detail += f" • v{body['version']}"
                except ValueError:
                    pass
                return {"name": name, "url": url, "online": True, "detail": detail}
            return {"name": name, "url": url, "online": False,
                    "detail": f"HTTP {resp.status_code}"}
        except requests.exceptions.RequestException as exc:
            return {"name": name, "url": url, "online": False,
                    "detail": type(exc).__name__}

    with ThreadPoolExecutor(max_workers=len(ENDPOINTS)) as pool:
        results = list(pool.map(probe, ENDPOINTS.items()))
    return {"services": results, "all_up": all(r["online"] for r in results)}


def _skin_state(day: str) -> dict:
    if day in ("", "today"):
        d = date.today()
    else:
        idx = DAY_INDEX.get(day.strip().lower())
        if idx is None:
            raise ValueError(f"unknown day '{day}'")
        d = datetime(2026, 1, 5 + idx).date()  # Monday-anchored preview week
    wd = d.weekday()
    is_shave = wd in SHAVE_DAYS
    return {"day": d.strftime("%A"), "date": d.isoformat(), "shave_day": is_shave,
            "am": AM_STEPS, "pm": PM_SHAVE if is_shave else PM_STANDARD}


def _pace(distance: str, target: str) -> dict:
    dist_m = parse_distance_meters(distance or "")
    if dist_m is None or dist_m <= 0:
        raise ValueError("bad distance — examples: 500, 500m, 1.5km, 1mi")
    secs = parse_time_to_seconds(target or "")
    if secs is None or secs <= 0:
        raise ValueError("bad time — examples: 1m23s, 1:23, 83, 2m+500ms, 1'23\"")
    per100 = secs / (dist_m / 100.0)
    return {"distance_m": round(dist_m, 2), "seconds": round(secs, 3),
            "total_hms": fmt_hms(secs),
            "per100_s": round(per100, 2), "per200_s": round(per100 * 2, 2),
            "per400_s": round(per100 * 4, 2), "per_km_hms": fmt_hms(secs / (dist_m / 1000.0)),
            "kmh": round((dist_m / 1000.0) / (secs / 3600.0), 2),
            "ms": round((dist_m / 1000.0) / (secs / 3600.0) / 3.6, 2)}


def _rows_of(runs) -> list:
    out = []
    for r in runs:
        dist, secs = r[2], r[3]
        out.append({"id": r[0], "date": r[1], "distance_m": dist,
                    "hms": fmt_hms(secs), "per100": round(secs / (dist / 100.0), 2),
                    "kmh": round((dist / 1000.0) / (secs / 3600.0), 2),
                    "warmup": bool(r[4])})
    return out


def _portfolio_state() -> dict:
    def read_csv(name, limit=None):
        path = PORTFOLIO_DIR / name
        if not path.exists():
            return {"exists": False, "columns": [], "rows": [], "total": 0}
        with open(path, newline="", encoding="utf-8") as fh:
            rdr = list(csv.reader(fh))
        if not rdr:
            return {"exists": True, "columns": [], "rows": [], "total": 0}
        cols, rows = rdr[0], rdr[1:]
        return {"exists": True, "columns": cols, "total": len(rows),
                "rows": rows[:limit] if limit else rows}

    matrix_path = PORTFOLIO_DIR / "literature_matrix.md"
    matrix = matrix_path.read_text(encoding="utf-8") if matrix_path.exists() else ""
    return {
        "cftr": read_csv("cftr_analysis.csv"),
        "cohort": read_csv("cleaned_cohort.csv", limit=12),
        "bibliography": read_csv("pubmed_bibliography.csv", limit=12),
        "matrix_md": matrix,
        "figure_url": "/artifact/table1_demographics.png"
                      if (PORTFOLIO_DIR / "table1_demographics.png").exists() else None,
        "found": PORTFOLIO_DIR.exists(),
    }


def _dashboard() -> dict:
    ks = _kill_switch_state()
    swiss = _swiss_state()
    runs = db.list_runs(3)
    acad = db.list_academics(5)
    skin = _skin_state("today")
    return {
        "kill_switch": ks,
        "swiss": swiss,
        "recent_runs": _rows_of(runs),
        "recent_grades": [{"id": r[0], "date": r[1], "subject": r[2], "score": r[3],
                           "weight": r[4], "type": r[5]} for r in acad],
        "skin_today": skin,
        "academic_count": len(db.list_academics(100_000)),
        "run_count": len(db.list_runs(100_000)),
    }


# ------------------------------------------------------------------- page --
PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>⚡ GENEVA OS</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>⚡</text></svg>">
<style>
:root{
  --bg:#070b12; --panel:#0e1524; --panel2:#131c30; --line:#1e2a44;
  --cy:#22d3ee; --cy-dim:#0e7490; --tx:#dbe7ff; --dim:#7d8db0;
  --gr:#34d399; --rd:#f87171; --yl:#fbbf24; --mg:#c084fc;
  --mono:ui-monospace,'Cascadia Mono','JetBrains Mono',Consolas,monospace;
  --sans:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font-family:var(--sans);min-height:100vh}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
  background:radial-gradient(900px 400px at 70% -10%,rgba(34,211,238,.07),transparent 60%),
             radial-gradient(700px 400px at -10% 110%,rgba(192,132,252,.05),transparent 60%)}
#layout{display:flex;min-height:100vh;position:relative;z-index:1}
/* sidebar */
nav{width:230px;flex-shrink:0;background:var(--panel);border-right:1px solid var(--line);
    padding:22px 14px;display:flex;flex-direction:column;gap:4px;position:sticky;top:0;height:100vh}
.logo{font-family:var(--mono);font-size:17px;letter-spacing:2px;color:var(--cy);
      padding:0 10px 18px;font-weight:700;text-shadow:0 0 18px rgba(34,211,238,.45)}
.logo small{display:block;color:var(--dim);font-size:10px;letter-spacing:3px;margin-top:4px;text-shadow:none}
nav button{all:unset;cursor:pointer;padding:10px 12px;border-radius:9px;color:var(--dim);
  font-size:14px;display:flex;gap:10px;align-items:center;transition:.15s}
nav button:hover{background:var(--panel2);color:var(--tx)}
nav button.on{background:linear-gradient(90deg,rgba(34,211,238,.14),rgba(34,211,238,.03));
  color:var(--cy);box-shadow:inset 2px 0 0 var(--cy)}
.navsep{margin:12px 10px 6px;font-size:10px;letter-spacing:2.5px;color:#3b4a6b;font-family:var(--mono)}
main{flex:1;padding:30px 34px 80px;max-width:1150px}
/* headings & cards */
h1{font-family:var(--mono);font-size:20px;letter-spacing:1.5px;color:var(--tx);margin-bottom:4px}
h1 .tag{color:var(--cy)}
.sub{color:var(--dim);font-size:13px;margin-bottom:24px}
h2{font-size:14px;letter-spacing:1px;color:var(--dim);text-transform:uppercase;
   font-family:var(--mono);margin:26px 0 12px}
.grid{display:grid;gap:14px}
.g2{grid-template-columns:1fr 1fr}.g3{grid-template-columns:repeat(3,1fr)}
@media(max-width:900px){.g2,.g3{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 20px}
.card h3{font-family:var(--mono);font-size:12px;letter-spacing:2px;color:var(--dim);
   text-transform:uppercase;margin-bottom:12px;display:flex;justify-content:space-between;align-items:center}
.big{font-size:26px;font-weight:700;font-family:var(--mono)}
.ok{color:var(--gr)}.bad{color:var(--rd)}.warn{color:var(--yl)}.info{color:var(--cy)}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:11px;
  font-family:var(--mono);letter-spacing:1px;border:1px solid}
.pill.ok{border-color:rgba(52,211,153,.4);background:rgba(52,211,153,.08)}
.pill.bad{border-color:rgba(248,113,113,.4);background:rgba(248,113,113,.08)}
.pill.warn{border-color:rgba(251,191,36,.4);background:rgba(251,191,36,.08)}
/* progress */
.bar{height:10px;border-radius:99px;background:var(--panel2);overflow:hidden;margin-top:10px;border:1px solid var(--line)}
.bar i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--cy-dim),var(--cy));
  box-shadow:0 0 12px rgba(34,211,238,.5);transition:width .5s}
.bar i.full{background:linear-gradient(90deg,#059669,var(--gr));box-shadow:0 0 12px rgba(52,211,153,.5)}
/* tables */
table{width:100%;border-collapse:collapse;font-size:13px}
th{font-family:var(--mono);font-size:10.5px;letter-spacing:1.5px;text-transform:uppercase;
   color:var(--dim);text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
td{padding:8px 10px;border-bottom:1px solid rgba(30,42,68,.5);vertical-align:top}
tr:hover td{background:rgba(34,211,238,.03)}
td.num,th.num{text-align:right;font-family:var(--mono)}
.scroll{overflow:auto;max-height:420px;border-radius:10px}
/* forms */
form{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end}
label{display:block;font-size:11px;font-family:var(--mono);letter-spacing:1px;
  color:var(--dim);text-transform:uppercase;margin-bottom:5px}
input,select{background:var(--panel2);border:1px solid var(--line);color:var(--tx);
  padding:9px 12px;border-radius:9px;font-size:14px;font-family:var(--sans);outline:none;min-width:0}
input:focus,select:focus{border-color:var(--cy);box-shadow:0 0 0 3px rgba(34,211,238,.12)}
.btn{all:unset;cursor:pointer;background:linear-gradient(135deg,var(--cy-dim),#155e75);color:#eaffff;
  padding:10px 18px;border-radius:9px;font-size:13.5px;font-weight:600;letter-spacing:.5px;
  box-shadow:0 2px 14px rgba(34,211,238,.25);transition:.15s;text-align:center}
.btn:hover{filter:brightness(1.2)}.btn:active{transform:translateY(1px)}
.btn.ghost{background:var(--panel2);border:1px solid var(--line);color:var(--dim);box-shadow:none}
.btn.ghost:hover{color:var(--tx);border-color:var(--cy-dim)}
/* day pills */
.days{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.days button{all:unset;cursor:pointer;padding:8px 15px;border-radius:999px;border:1px solid var(--line);
  color:var(--dim);font-size:13px;font-family:var(--mono);transition:.15s}
.days button:hover{border-color:var(--cy-dim);color:var(--tx)}
.days button.on{border-color:var(--cy);color:var(--cy);background:rgba(34,211,238,.08)}
/* steps list */
ol.steps{list-style:none;counter-reset:s}
ol.steps li{counter-increment:s;padding:9px 0 9px 40px;position:relative;border-bottom:1px dashed rgba(30,42,68,.6);font-size:14px}
ol.steps li::before{content:counter(s,decimal-leading-zero);position:absolute;left:0;top:9px;
  font-family:var(--mono);color:var(--cy);font-size:12px}
ol.steps li:last-child{border-bottom:0}
/* misc */
.note{font-size:12.5px;color:var(--dim);line-height:1.55}
.kv{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px dashed rgba(30,42,68,.6);font-size:13.5px}
.kv:last-child{border-bottom:0}
.kv b{font-family:var(--mono)}
figure img{width:100%;border-radius:12px;border:1px solid var(--line)}
figcaption{font-size:12px;color:var(--dim);margin-top:8px;line-height:1.5}
.hidden{display:none!important}
#toast{position:fixed;bottom:24px;right:24px;z-index:50;display:flex;flex-direction:column;gap:8px}
.tmsg{background:var(--panel2);border:1px solid var(--line);border-left:3px solid var(--cy);
  padding:12px 16px;border-radius:10px;font-size:13.5px;max-width:340px;box-shadow:0 8px 30px rgba(0,0,0,.5);
  animation:in .25s ease}
.tmsg.bad{border-left-color:var(--rd)}.tmsg.ok{border-left-color:var(--gr)}
@keyframes in{from{opacity:0;transform:translateY(8px)}to{opacity:1}}
.mdtable td,.mdtable th{font-size:12.5px}
a{color:var(--cy)}
.muted{color:var(--dim)}
.banner{border-radius:12px;padding:14px 18px;margin-bottom:16px;font-size:14px;border:1px solid}
.banner.red{background:rgba(248,113,113,.07);border-color:rgba(248,113,113,.35);color:#fecaca}
.banner.green{background:rgba(52,211,153,.06);border-color:rgba(52,211,153,.3);color:#bbf7d0}
footer{margin-top:50px;color:#3b4a6b;font-size:11.5px;font-family:var(--mono);letter-spacing:1px}
@media(max-width:760px){
  nav{width:64px;padding:16px 8px}
  nav button span.lbl,nav .navsep,.logo small{display:none}
  .logo{font-size:20px;padding:0 6px 14px;text-align:center}
  main{padding:20px 16px 60px}
}
</style>
</head>
<body>
<div id="layout">
<nav>
  <div class="logo">⚡ GENEVA<small>PROTOCOL v1.1</small></div>
  <button data-p="dash" class="on">📊 <span class="lbl">Dashboard</span></button>
  <div class="navsep"><span class="lbl">PROTOCOL</span></div>
  <button data-p="acad">🎓 <span class="lbl">Academics</span></button>
  <button data-p="run">🏃 <span class="lbl">Athletics</span></button>
  <button data-p="skin">🧴 <span class="lbl">Skincare</span></button>
  <button data-p="swiss">🇨🇭 <span class="lbl">Swiss</span></button>
  <button data-p="hub">🤖 <span class="lbl">AI Hub</span></button>
  <div class="navsep"><span class="lbl">SHOWCASE</span></div>
  <button data-p="folio">🧬 <span class="lbl">Portfolio</span></button>
</nav>
<main>

<!-- ================= DASHBOARD ================= -->
<section id="p-dash">
  <h1><span class="tag">◤</span> COMMAND DASHBOARD <span class="tag">◢</span></h1>
  <div class="sub" id="dash-date"></div>
  <div id="dash-kill"></div>
  <div class="grid g3">
    <div class="card"><h3>CHF Day-1 Reserve</h3>
      <div class="big" id="d-chf">—</div>
      <div class="note" id="d-chf-sub"></div>
      <div class="bar"><i id="d-chf-bar" style="width:0%"></i></div>
    </div>
    <div class="card"><h3>Next Deadline</h3>
      <div class="big" id="d-dl-days">—</div>
      <div class="note" id="d-dl-name"></div>
    </div>
    <div class="card"><h3>Today's Dermal Protocol</h3>
      <div class="big" id="d-skin">—</div>
      <div class="note" id="d-skin-sub"></div>
    </div>
  </div>
  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h3>Core Subject Averages <span id="d-ks-pill"></span></h3>
      <div id="d-cores"></div></div>
    <div class="card"><h3>Latest Sessions</h3>
      <div class="scroll"><table><thead><tr><th>Date</th><th class="num">Dist</th><th class="num">Time</th><th class="num">km/h</th><th>Warm-up</th></tr></thead>
      <tbody id="d-runs"></tbody></table></div></div>
  </div>
  <div class="card" style="margin-top:14px"><h3>Recent Grades</h3>
    <div class="scroll"><table><thead><tr><th>Date</th><th>Subject</th><th class="num">Score</th><th class="num">Weight</th><th>Type</th></tr></thead>
    <tbody id="d-grades"></tbody></table></div></div>
</section>

<!-- ================= ACADEMICS ================= -->
<section id="p-acad" class="hidden">
  <h1>🎓 ACADEMIC REGISTRY <span class="tag">// BAC 2027</span></h1>
  <div class="sub">Weighted /20 ledger with kill-switch logic — SVT or PC rolling average below 14.5 pauses all side projects.</div>
  <div id="acad-banner"></div>
  <div class="card"><h3>Log an assessment</h3>
    <form id="f-acad">
      <div><label>Subject</label><input name="subject" placeholder="SVT / PC / Math…" required maxlength="20" style="width:150px"></div>
      <div><label>Score /20</label><input name="score" type="number" step="0.25" min="0" max="20" required style="width:110px"></div>
      <div><label>Weight</label><input name="weight" type="number" step="0.5" min="0.1" max="100" value="1" required style="width:90px"></div>
      <div><label>Type</label><select name="type"><option>QCM</option><option>TP</option><option>Written</option></select></div>
      <button class="btn" type="submit">Log grade</button>
    </form></div>
  <h2>Rolling weighted averages</h2>
  <div class="card"><div id="acad-roll"></div></div>
  <h2>Ledger</h2>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th>ID</th><th>Date</th><th>Subject</th><th class="num">Score</th><th class="num">Weight</th><th>Type</th></tr></thead>
    <tbody id="acad-rows"></tbody></table></div></div>
</section>

<!-- ================= RUNNING ================= -->
<section id="p-run" class="hidden">
  <h1>🏃 PACE ENGINE <span class="tag">// ASTHMA-AWARE</span></h1>
  <div class="sub">Middle-distance splits + the 3-step EIB protocol. Same parser as the CLI (1m23s · 1:23 · 83 · 2m+500ms · 1'23").</div>
  <div class="grid g2">
    <div class="card"><h3>Pace calculator</h3>
      <form id="f-pace">
        <div><label>Distance</label><input name="distance" value="800m" required style="width:120px"></div>
        <div><label>Target time</label><input name="target" value="2m45s" required style="width:130px"></div>
        <button class="btn" type="submit">Compute</button>
      </form>
      <div id="pace-out" style="margin-top:14px"></div>
    </div>
    <div class="card"><h3>🫁 Asthma protocol checklist</h3>
      <ol class="steps">
        <li>Ventolin 15–20 min prior <span class="muted">(preventive bronchodilator window)</span></li>
        <li>15-min warm-up with strides <span class="muted">(induces the refractory period → protects the main effort from EIB)</span></li>
        <li>Post-run active walk <span class="muted">(lactate flush + gradual HR drop)</span></li>
      </ol>
      <h3 style="margin-top:18px">Log a completed session</h3>
      <form id="f-run">
        <div><label>Distance (m)</label><input name="distance" type="number" min="1" step="1" value="1500" required style="width:110px"></div>
        <div><label>Time</label><input name="target" value="5m30s" required style="width:110px"></div>
        <div><label>Warm-up done?</label><select name="warmup"><option value="1">Yes</option><option value="0">No — breach</option></select></div>
        <button class="btn" type="submit">Save</button>
      </form>
    </div>
  </div>
  <h2>History</h2>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th>ID</th><th>Date</th><th class="num">Dist</th><th class="num">Time</th><th class="num">s/100m</th><th class="num">km/h</th><th>Warm-up</th></tr></thead>
    <tbody id="run-rows"></tbody></table></div></div>
</section>

<!-- ================= SKIN ================= -->
<section id="p-skin" class="hidden">
  <h1>🧴 DERMAL PROTOCOL</h1>
  <div class="sub">AM/PM master routine, auto-adjusted for shave days (Mon / Wed / Fri / Sun → acid &amp; retinoid blackout).</div>
  <div class="days" id="skin-days"></div>
  <div id="skin-head"></div>
  <div class="grid g2">
    <div class="card" style="border-color:rgba(52,211,153,.3)"><h3>☀ AM ROUTINE</h3><ol class="steps" id="skin-am"></ol></div>
    <div class="card" id="skin-pm-card"><h3 id="skin-pm-title">🌙 PM ROUTINE</h3><ol class="steps" id="skin-pm"></ol></div>
  </div>
  <div id="skin-warn" style="margin-top:14px"></div>
</section>

<!-- ================= SWISS ================= -->
<section id="p-swiss" class="hidden">
  <h1>🇨🇭 SWISS RELOCATION <span class="tag">// GENÈVE</span></h1>
  <div class="sub">CHF liquidity vs the 2,500 Day-1 threshold + administrative pipeline.</div>
  <div class="grid g3">
    <div class="card"><h3>Liquidity</h3><div class="big info" id="s-liq">—</div>
      <div class="bar"><i id="s-bar" style="width:0%"></i></div>
      <div class="note" id="s-gate" style="margin-top:10px"></div></div>
    <div class="card"><h3>Day-1 threshold</h3><div class="big" id="s-thr">—</div>
      <div class="note" id="s-delta"></div></div>
    <div class="card"><h3>Record an inflow</h3>
      <form id="s-fund">
        <div><label>Amount CHF</label><input name="amount" type="number" min="0.01" step="0.01" required style="width:120px"></div>
        <div><label>Source</label><select name="source"><option>Summer Work</option><option>Tutoring</option><option>Grants</option></select></div>
        <button class="btn" type="submit">Fund</button>
      </form></div>
  </div>
  <h2>Administrative pipeline</h2>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th>Priority</th><th>Deadline</th><th>Task</th><th class="num">Days left</th></tr></thead>
    <tbody id="s-dl"></tbody></table></div></div>
  <h2>CHF ledger</h2>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th>ID</th><th>Date</th><th class="num">Amount</th><th>Source</th></tr></thead>
    <tbody id="s-ledger"></tbody></table></div>
    <div class="kv" style="margin-top:8px"><span>Total liquidity</span><b id="s-total">—</b></div></div>
</section>

<!-- ================= HUB ================= -->
<section id="p-hub" class="hidden">
  <h1>🤖 SOVEREIGN AI HUB</h1>
  <div class="sub">Local inference + chat stack health (LibreChat :3080 / Ollama :11434). Zero cloud dependency.</div>
  <div id="hub-banner"></div>
  <div class="grid g2" id="hub-cards"></div>
  <div style="margin-top:16px"><button class="btn ghost" id="hub-again">↻ Re-ping</button></div>
</section>

<!-- ================= PORTFOLIO ================= -->
<section id="p-folio" class="hidden">
  <h1>🧬 COMPUTATIONAL BIOLOGY PORTFOLIO</h1>
  <div class="sub">Read-only showcase of the three pipelines — NCBI sequence QC, regulatory Table 1 EDA, PubMed literature synthesis (ICH E6(R2) / E9 / M4 · ALCOA+).</div>
  <div id="folio-body"></div>
</section>

<footer>GENEVA OS // OFFLINE-FIRST · LOCAL SQLITE · NO CLOUD · NO TRACKING</footer>
</main>
</div>
<div id="toast"></div>

<script>
"use strict";
const $=s=>document.querySelector(s), $$=s=>document.querySelectorAll(s);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function toast(msg,kind){const t=document.createElement("div");t.className="tmsg "+(kind||"");t.textContent=msg;
  $("#toast").appendChild(t);setTimeout(()=>t.remove(),4200);}
async function api(path,opt){
  const r=await fetch(path,opt);
  let j=null; try{j=await r.json();}catch(e){}
  if(!r.ok) throw new Error((j&&j.error)||("HTTP "+r.status));
  return j;
}
const post=(path,body)=>api(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
const fv=(f,n)=>f.querySelector('[name="'+n+'"]').value;   /* explicit form-field read: identical in every DOM */
const setFv=(f,n,v)=>{f.querySelector('[name="'+n+'"]').value=v;};

/* ---------- navigation ---------- */
let curDay="today";
const loaders={dash:loadDash,acad:loadAcad,run:loadRun,skin:()=>loadSkin(curDay),swiss:loadSwiss,hub:loadHub,folio:loadFolio};
$$("nav button").forEach(b=>b.addEventListener("click",()=>{
  $$("nav button").forEach(x=>x.classList.remove("on"));b.classList.add("on");
  $$("main section").forEach(s=>s.classList.add("hidden"));
  $("#p-"+b.dataset.p).classList.remove("hidden");
  Promise.resolve((loaders[b.dataset.p]||(()=>{}))()).catch(e=>toast(e.message,"bad"));
}));

/* ---------- dashboard ---------- */
async function loadDash(){
  const d=await api("/api/dashboard");
  $("#dash-date").textContent=new Date().toLocaleDateString(undefined,{weekday:"long",year:"numeric",month:"long",day:"numeric"});
  const ks=d.kill_switch, on=ks.triggered.length>0;
  $("#dash-kill").innerHTML=on
    ?`<div class="banner red">☠ <b>ACADEMIC KILL SWITCH TRIGGERED</b> — ${esc(ks.triggered.join(", "))} below ${ks.threshold}/20. Pause all side projects; redirect 100% bandwidth to core subjects.</div>`
    :`<div class="banner green">✓ <b>STATUS NOMINAL</b> — core subject averages ≥ ${ks.threshold}/20 (or no data yet). Side projects cleared to run.</div>`;
  $("#d-chf").textContent="CHF "+d.swiss.liquidity.toLocaleString("en",{minimumFractionDigits:2});
  $("#d-chf-sub").textContent=d.swiss.cleared?("threshold CLEARED · surplus CHF "+d.swiss.surplus.toLocaleString("en",{minimumFractionDigits:2}))
    :("shortfall CHF "+d.swiss.shortfall.toLocaleString("en",{minimumFractionDigits:2})+" · "+d.swiss.percent+"% of goal");
  const bar=$("#d-chf-bar");bar.style.width=d.swiss.percent+"%";bar.classList.toggle("full",d.swiss.cleared);
  if(d.swiss.next){$("#d-dl-days").textContent=d.swiss.next.days_left+" d";$("#d-dl-name").textContent=d.swiss.next.task+" · due "+d.swiss.next.due;}
  else{$("#d-dl-days").textContent="—";$("#d-dl-name").textContent="all deadlines passed";}
  $("#d-skin").textContent=d.skin_today.day+(d.skin_today.shave_day?" 🪒":"");
  $("#d-skin-sub").textContent=d.skin_today.shave_day?"Shave day — zinc oxide only, NO retinal/acids":"Full actives stack cleared — respect the 20-min wait window";
  $("#d-ks-pill").innerHTML=on?'<span class="pill bad">KILL-SWITCH</span>':'<span class="pill ok">NOMINAL</span>';
  $("#d-cores").innerHTML=ks.subjects.map(s=>`<div class="kv"><span>${esc(s.subject)} ${s.core?'<span class="muted">(core)</span>':""}</span><b class="${s.avg===null?"muted":(s.avg<ks.threshold?"bad":"ok")}">${s.avg===null?"no data":s.avg.toFixed(2)+"/20"}</b></div>`).join("")||'<div class="note">No grades yet.</div>';
  $("#d-runs").innerHTML=d.recent_runs.map(r=>`<tr><td class="muted">${esc(r.date)}</td><td class="num">${r.distance_m} m</td><td class="num">${esc(r.hms)}</td><td class="num">${r.kmh}</td><td>${r.warmup?'<span class="ok">✓</span>':'<span class="bad">✗ breach</span>'}</td></tr>`).join("")||'<tr><td colspan="5" class="muted">No runs logged yet.</td></tr>';
  $("#d-grades").innerHTML=d.recent_grades.map(g=>`<tr><td class="muted">${esc(g.date)}</td><td>${esc(g.subject)}</td><td class="num ${g.score>=14.5?"ok":(g.score>=12?"warn":"bad")}">${g.score.toFixed(2)}</td><td class="num">${g.weight}</td><td>${esc(g.type)}</td></tr>`).join("")||'<tr><td colspan="5" class="muted">No grades yet — log one under Academics.</td></tr>';
}

/* ---------- academics ---------- */
async function loadAcad(){
  const [lst,rollJ]=await Promise.all([api("/api/academics?limit=50"),api("/api/rollups")]);
  const roll=rollJ.rollups;
  const ks=lst.kill_switch,on=ks.triggered.length>0;
  $("#acad-banner").innerHTML=on
    ?`<div class="banner red">☠ KILL SWITCH ACTIVE for ${esc(ks.triggered.join(", "))} — exit code 2 in CLI. All side projects paused.</div>`
    :`<div class="banner green">✓ Clearance — core averages ≥ ${ks.threshold}/20.</div>`;
  $("#acad-roll").innerHTML=roll.length?`<table><thead><tr><th>Subject</th><th class="num">Rolling avg /20</th><th>Status</th></tr></thead><tbody>`+
    roll.map(r=>`<tr><td>${esc(r.subject)} ${r.core?'<span class="muted">(core)</span>':""}</td><td class="num ${r.avg<ks.threshold&&r.core?"bad":"ok"}">${r.avg===null?"—":r.avg.toFixed(2)}</td>
    <td>${r.risk?'<span class="pill bad">KILL-SWITCH RISK</span>':'<span class="pill ok">OK</span>'}</td></tr>`).join("")+`</tbody></table>`
    :'<div class="note">No data yet — log your first grade above.</div>';
  $("#acad-rows").innerHTML=lst.entries.map(e=>`<tr><td class="muted">${e.id}</td><td class="muted">${esc(e.date)}</td><td>${esc(e.subject)}</td>
    <td class="num ${e.score>=14.5?"ok":(e.score>=12?"warn":"bad")}">${e.score.toFixed(2)}</td><td class="num">${e.weight}</td><td>${esc(e.type)}</td></tr>`).join("")
    ||'<tr><td colspan="6" class="muted">Empty ledger.</td></tr>';
}
$("#f-acad").addEventListener("submit",async ev=>{
  ev.preventDefault();const f=ev.target;
  try{
    const r=await post("/api/academics",{subject:fv(f,"subject"),score:parseFloat(fv(f,"score")),weight:parseFloat(fv(f,"weight")),type:fv(f,"type")});
    if(r.kill_switch.triggered.length){toast("☠ KILL SWITCH TRIGGERED — "+r.kill_switch.triggered.join(", ")+" below threshold","bad");}
    else{toast(`Entry #${r.id} logged — ${r.subject} rolling avg ${r.subject_avg===null?"—":r.subject_avg.toFixed(2)}/20`,"ok");}
    setFv(f,"subject","");setFv(f,"score","");loadAcad();
  }catch(e){toast(e.message,"bad");}
});

/* ---------- running ---------- */
function paceTable(p){
  return `<table><tbody>
  <tr><td>Per 100 m</td><td class="num"><b>${p.per100_s} s</b></td><td class="muted">primary metronome split</td></tr>
  <tr><td>Per 200 m</td><td class="num">${p.per200_s} s</td><td class="muted">half-lap cadence</td></tr>
  <tr><td>Per 400 m</td><td class="num">${p.per400_s} s</td><td class="muted">full lap (track)</td></tr>
  <tr><td>Per km</td><td class="num">${esc(p.per_km_hms)}</td><td class="muted">pace notation</td></tr>
  <tr><td>Speed</td><td class="num"><b class="info">${p.kmh} km/h</b></td><td class="muted">${p.ms} m/s</td></tr>
  <tr><td>Total</td><td class="num">${esc(p.total_hms)}</td><td class="muted">over ${p.distance_m} m</td></tr>
  </tbody></table>`;
}
async function loadRun(){
  const lst=await api("/api/runs?limit=20");
  $("#run-rows").innerHTML=lst.runs.map(r=>`<tr><td class="muted">${r.id}</td><td class="muted">${esc(r.date)}</td>
   <td class="num">${r.distance_m} m</td><td class="num">${esc(r.hms)}</td><td class="num"><b>${r.per100} s</b></td><td class="num">${r.kmh}</td>
   <td>${r.warmup?'<span class="ok">✓</span>':'<span class="bad">✗ breach</span>'}</td></tr>`).join("")
   ||'<tr><td colspan="7" class="muted">No runs logged yet.</td></tr>';
}
async function doPace(){
  const f=$("#f-pace");
  try{
    const p=await api(`/api/pace?distance=${encodeURIComponent(fv(f,"distance"))}&target=${encodeURIComponent(fv(f,"target"))}`);
    $("#pace-out").innerHTML=`<div class="note" style="margin-bottom:8px">Required pace — <b class="info">${esc(p.total_hms)}</b> over ${p.distance_m} m</div>`+paceTable(p);
  }catch(e){toast(e.message,"bad");$("#pace-out").innerHTML=`<div class="banner red">${esc(e.message)}</div>`;}
}
$("#f-pace").addEventListener("submit",ev=>{ev.preventDefault();doPace();});
$("#f-run").addEventListener("submit",async ev=>{
  ev.preventDefault();const f=ev.target;
  try{
    const r=await post("/api/runs",{distance:parseFloat(fv(f,"distance")),target:fv(f,"target"),warmup:fv(f,"warmup")==="1"});
    toast(`Run #${r.id} saved — ${esc(r.hms)} over ${r.distance_m} m`,"ok");loadRun();
  }catch(e){toast(e.message,"bad");}
});

/* ---------- skin ---------- */
function skinPills(){
  const days=["today","mon","tue","wed","thu","fri","sat","sun"];
  $("#skin-days").innerHTML=days.map(d=>`<button data-d="${d}" class="${d===curDay?"on":""}">${d==="today"?"⦿ Today":d[0].toUpperCase()+d.slice(1)}</button>`).join("");
  $$("#skin-days button").forEach(b=>b.addEventListener("click",()=>{curDay=b.dataset.d;skinPills();loadSkin(curDay);}));
}
async function loadSkin(day){
  skinPills();
  try{
    const s=await api("/api/skin?day="+encodeURIComponent(day||"today"));
    $("#skin-head").innerHTML=`<div class="note" style="margin-bottom:12px">Showing <b class="info">${esc(s.day)} ${esc(s.date)}</b>${s.shave_day?' &nbsp;<span class="pill bad">SHAVE DAY</span>':' &nbsp;<span class="pill ok">FULL ACTIVES</span>'}</div>`;
    $("#skin-am").innerHTML=s.am.map(x=>`<li>${esc(x)}</li>`).join("");
    $("#skin-pm-title").innerHTML="🌙 PM ROUTINE"+(s.shave_day?' <span class="pill bad">SHAVE DAY</span>':"");
    $("#skin-pm").innerHTML=s.pm.map(x=>`<li>${esc(x)}</li>`).join("");
    $("#skin-pm-card").style.borderColor=s.shave_day?"rgba(248,113,113,.4)":"rgba(34,211,238,.3)";
    $("#skin-warn").innerHTML=s.shave_day
      ?`<div class="banner red">⚠ Absolutely no Retinal 0.2% or acids on shave days — compromised barrier. Zinc oxide only.</div>`
      :`<div class="banner green">✓ Non-shave day: full actives stack cleared. Respect the 20-minute BHA → Retinal wait window.</div>`;
  }catch(e){toast(e.message,"bad");}
}

/* ---------- swiss ---------- */
async function loadSwiss(){
  const [s,led]=await Promise.all([api("/api/swiss"),api("/api/swiss/ledger?limit=30")]);
  $("#s-liq").textContent="CHF "+s.liquidity.toLocaleString("en",{minimumFractionDigits:2});
  const bar=$("#s-bar");bar.style.width=s.percent+"%";bar.classList.toggle("full",s.cleared);
  $("#s-gate").innerHTML=s.cleared?'<span class="pill ok">GATE OPEN</span>':'<span class="pill bad">GATE CLOSED</span>';
  $("#s-thr").textContent="CHF "+s.threshold.toLocaleString("en",{minimumFractionDigits:2});
  $("#s-delta").innerHTML=s.cleared?`Surplus <b class="ok">CHF ${s.surplus.toLocaleString("en",{minimumFractionDigits:2})}</b>`
    :`Shortfall <b class="bad">CHF ${s.shortfall.toLocaleString("en",{minimumFractionDigits:2})}</b> — secure via Summer Work / Tutoring / Grants`;
  $("#s-dl").innerHTML=s.deadlines.map((d,i)=>{
    const cls=d.days_left<0?"muted":(d.days_left<=30?"bad":(d.days_left<=90?"warn":""));
    return `<tr><td class="muted">P${i+1}</td><td>${esc(d.due)}</td><td>${esc(d.task)}</td><td class="num ${cls}">${d.days_left<0?"passed":d.days_left}</td></tr>`;}).join("");
  $("#s-ledger").innerHTML=led.entries.map(e=>`<tr><td class="muted">${e.id}</td><td class="muted">${esc(e.date)}</td>
    <td class="num ${e.amount>=0?"ok":"bad"}">${e.amount>=0?"+":""}${e.amount.toLocaleString("en",{minimumFractionDigits:2})}</td><td>${esc(e.source)}</td></tr>`).join("")
    ||'<tr><td colspan="4" class="muted">No entries yet — record your first inflow above.</td></tr>';
  $("#s-total").textContent="CHF "+s.liquidity.toLocaleString("en",{minimumFractionDigits:2});
}
$("#s-fund").addEventListener("submit",async ev=>{
  ev.preventDefault();const f=ev.target;
  try{
    const r=await post("/api/swiss/fund",{amount:parseFloat(fv(f,"amount")),source:fv(f,"source")});
    toast(`Logged +CHF ${r.amount.toLocaleString("en",{minimumFractionDigits:2})} (${r.source})`,"ok");
    setFv(f,"amount","");loadSwiss();
  }catch(e){toast(e.message,"bad");}
});

/* ---------- hub ---------- */
async function loadHub(){
  $("#hub-cards").innerHTML='<div class="card note">Pinging local endpoints…</div>';
  try{
    const h=await api("/api/hub");
    $("#hub-banner").innerHTML=h.all_up
      ?`<div class="banner green">◉ <b>SOVEREIGN AI HUB ONLINE</b> — local inference + chat stack operational. Zero cloud dependency.</div>`
      :`<div class="banner red">◉ <b>HUB OFFLINE</b> — bring up LibreChat (3080) and Ollama (11434) via start-hub, then re-ping.</div>`;
    $("#hub-cards").innerHTML=h.services.map(s=>`<div class="card"><h3>${esc(s.name)} <span class="pill ${s.online?"ok":"bad"}">${s.online?"● ONLINE":"● OFFLINE"}</span></h3>
      <div class="note" style="font-family:var(--mono)">${esc(s.url)}</div>
      <div class="note" style="margin-top:6px">${esc(s.detail)}</div></div>`).join("");
  }catch(e){$("#hub-cards").innerHTML=`<div class="card note bad">${esc(e.message)}</div>`;}
}
$("#hub-again").addEventListener("click",loadHub);

/* ---------- portfolio ---------- */
function renderTable(t,maxRows){
  if(!t.exists)return '<div class="note">artifact not generated yet — run the script once.</div>';
  const rows=(maxRows?t.rows.slice(0,maxRows):t.rows);
  const numCols=new Set();
  if(t.rows.length)t.rows[0].forEach((c,i)=>{if(/^-?\d+(\.\d+)?$/.test(String(c).trim()))numCols.add(i);});
  return `<div class="scroll"><table class="mdtable"><thead><tr>${t.columns.map(c=>`<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${
    rows.map(r=>`<tr>${r.map((c,i)=>{
      let v=esc(c);
      if(/^10\.\d{4,}\S*$/.test(String(c).trim())) v=`<a href="https://doi.org/${esc(c.trim())}" target="_blank" rel="noopener">${esc(c)}</a>`;
      return `<td${numCols.has(i)?' class="num"':""}>${v}</td>`;}).join("")}</tr>`).join("")
  }</tbody></table></div>`+(t.total>rows.length?`<div class="note" style="margin-top:8px">showing ${rows.length} of ${t.total} rows · full data in the CSV artifact</div>`:"");
}
function mdToHtml(md){
  const lines=esc(md).split("\n");
  let html="",inT=false,inL=false;
  const inline=s=>s.replace(/\*\*([^*]+)\*\*/g,"<b>$1</b>")
                   .replace(/\*([^*]+)\*/g,"<i>$1</i>")
                   .replace(/`([^`]+)`/g,"<code>$1</code>");
  const closeT=()=>{if(inT){html+="</tbody></table></div>";inT=false;}};
  const closeL=()=>{if(inL){html+="</ul>";inL=false;}};
  for(const raw of lines){
    const ln=raw.trim();
    if(ln.startsWith("|")){
      if(/^\|[\s\-|:]+\|$/.test(ln))continue;            /* separator row */
      closeL();
      const cells=ln.split("|").slice(1,-1).map(c=>inline(c.trim()));
      if(!inT){
        html+='<div class="scroll"><table class="mdtable"><thead><tr>'
            + cells.map(c=>`<th>${c}</th>`).join("")
            + "</tr></thead><tbody>";
        inT=true;
      }else{
        html+="<tr>"+cells.map(c=>`<td>${c}</td>`).join("")+"</tr>";
      }
      continue;
    }
    closeT();
    if(/^#{1,3}\s/.test(ln)){closeL();html+=`<h2>${inline(ln.replace(/^#+\s/,""))}</h2>`;continue;}
    if(ln.startsWith("- ")){if(!inL){closeT();html+="<ul>";inL=true;}html+=`<li>${inline(ln.slice(2))}</li>`;continue;}
    if(ln==="")continue;
    closeL();html+=`<p class="note" style="margin:6px 0">${inline(ln)}</p>`;
  }
  closeT();closeL();
  return html;
}
async function loadFolio(){
  const p=await api("/api/portfolio");
  if(!p.found){$("#folio-body").innerHTML='<div class="banner red">Portfolio folder not found next to webapp.py.</div>';return;}
  $("#folio-body").innerHTML=`
  <div class="grid g3">
    <div class="card"><h3>Project 1 · NCBI sequence QC</h3><div class="big info">${p.cftr.total||0}</div><div class="note">CFTR reference transcripts · versioned accessions · MANE cross-check</div></div>
    <div class="card"><h3>Project 2 · Clinical EDA</h3><div class="big info">n=${p.cohort.total||0}</div><div class="note">cleaned analysis population · regulatory Table 1 · seeded (seed=2027)</div></div>
    <div class="card"><h3>Project 3 · Literature</h3><div class="big info">${p.bibliography.total||0}</div><div class="note">curated records · PMID-first traceability · DOI-resolved</div></div>
  </div>
  <h2>Figure 1 — Biomarker by treatment arm</h2>
  ${p.figure_url?`<figure><img src="${p.figure_url}" alt="Biomarker level by treatment arm"><figcaption>Synthetic cohort (seed=2027). Lower biomarker = intended PD effect. Welch p = 0.0085, descriptive only — n=100 is not powered for confirmatory inference.</figcaption></figure>`:""}
  <h2>cftr_analysis.csv <span class="muted">(audit-ready, canonical panel order)</span></h2>
  <div class="card">${renderTable(p.cftr)}</div>
  <h2>cleaned_cohort.csv <span class="muted">(post-cleaning analysis population)</span></h2>
  <div class="card">${renderTable(p.cohort,10)}</div>
  <h2>pubmed_bibliography.csv <span class="muted">(PMID-first, DOI-linked)</span></h2>
  <div class="card">${renderTable(p.bibliography,10)}</div>
  <h2>literature_matrix.md</h2>
  <div class="card">${p.matrix_md?mdToHtml(p.matrix_md):'<div class="note">not generated yet</div>'}</div>`;
}

/* ---------- boot ---------- */
loadDash().catch(e=>toast(e.message,"bad"));
doPace();   /* pre-render the pace table with the default 800 m / 2m45s */
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- server --
class Handler(BaseHTTPRequestHandler):
    server_version = "GenevaOS-Web/1.1"

    def log_message(self, fmt, *args):  # keep the terminal clean
        pass

    # -- response helpers ---------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _err(self, msg, code=400):
        self._json({"ok": False, "error": msg}, code)

    # -- routing --------------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path.rstrip("/") or "/"
        try:
            if path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/dashboard":
                self._json({"ok": True, **_dashboard()})
            elif path == "/api/academics":
                limit = min(int(q.get("limit", ["50"])[0]), 1000)
                entries = [{"id": r[0], "date": r[1], "subject": r[2],
                            "score": r[3], "weight": r[4], "type": r[5]}
                           for r in db.list_academics(limit)]
                self._json({"ok": True, "entries": entries,
                            "kill_switch": _kill_switch_state()})
            elif path == "/api/rollups":
                self._json(_rollups_json())
            elif path == "/api/runs":
                limit = min(int(q.get("limit", ["20"])[0]), 1000)
                self._json({"ok": True, "runs": _rows_of(db.list_runs(limit))})
            elif path == "/api/pace":
                self._json({"ok": True,
                            **_pace(q.get("distance", [""])[0],
                                    q.get("target", [""])[0])})
            elif path == "/api/skin":
                self._json({"ok": True, **_skin_state(q.get("day", ["today"])[0])})
            elif path == "/api/swiss":
                self._json({"ok": True, **_swiss_state()})
            elif path == "/api/swiss/ledger":
                limit = min(int(q.get("limit", ["30"])[0]), 1000)
                entries = [{"id": r[0], "date": r[1], "amount": r[2], "source": r[3]}
                           for r in db.list_finance(limit)]
                self._json({"ok": True, "entries": entries})
            elif path == "/api/hub":
                self._json({"ok": True, **_hub_ping()})
            elif path == "/api/portfolio":
                self._json({"ok": True, **_portfolio_state()})
            elif path.startswith("/artifact/"):
                self._artifact(path[len("/artifact/"):])
            else:
                self._err("not found", 404)
        except ValueError as exc:
            self._err(str(exc))
        except Exception as exc:  # never kill the server on a bad request
            self._err(f"internal error: {exc}", 500)

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self._err("body too large", 413)
                return
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._err("invalid JSON body")
                return
            if path == "/api/academics":
                self._post_academic(payload)
            elif path == "/api/runs":
                self._post_run(payload)
            elif path == "/api/swiss/fund":
                self._post_fund(payload)
            else:
                self._err("not found", 404)
        except ValueError as exc:
            self._err(str(exc))
        except Exception as exc:
            self._err(f"internal error: {exc}", 500)

    # -- POST handlers ------------------------------------------------------
    def _post_academic(self, p: dict):
        subject = str(p.get("subject", "")).strip().upper()
        if not subject or len(subject) > 20:
            raise ValueError("subject must be 1-20 characters")
        try:
            score = float(p.get("score"))
            weight = float(p.get("weight", 1))
        except (TypeError, ValueError):
            raise ValueError("score and weight must be numbers")
        if not (0 <= score <= 20):
            raise ValueError("score must be within 0-20")
        if not (0.1 <= weight <= 100):
            raise ValueError("weight must be within 0.1-100")
        norm = {"qcm": "QCM", "tp": "TP", "written": "Written"}
        entry_type = norm.get(str(p.get("type", "")).strip().lower())
        if entry_type is None:
            raise ValueError("type must be QCM, TP or Written")
        row_id = db.add_academic_entry(subject, score, weight, entry_type)
        subj_avg = db.rolling_weighted_average(subject)
        self._json({"ok": True, "id": row_id, "subject": subject,
                    "subject_avg": round(subj_avg, 2) if subj_avg is not None else None,
                    "global_avg": (lambda g: round(g, 2) if g is not None else None)(
                        db.rolling_weighted_average()),
                    "kill_switch": _kill_switch_state()})

    def _post_run(self, p: dict):
        try:
            distance = float(p.get("distance"))
        except (TypeError, ValueError):
            raise ValueError("distance must be a number (meters)")
        if not (1 <= distance <= 1_000_000):
            raise ValueError("distance must be within 1-1,000,000 m")
        if "time_seconds" in p:
            try:
                secs = float(p.get("time_seconds"))
            except (TypeError, ValueError):
                raise ValueError("time_seconds must be a number")
        else:
            secs = parse_time_to_seconds(str(p.get("target", "")))
            if secs is None:
                raise ValueError("could not parse time — use e.g. 5m30s or 1:23")
        if not (0.1 <= secs <= 86_400):
            raise ValueError("time must be within 0.1 s and 24 h")
        warmup = bool(p.get("warmup", False))
        rid = db.add_run_entry(distance, secs, warmup)
        self._json({"ok": True, "id": rid, "distance_m": distance,
                    "seconds": secs, "hms": fmt_hms(secs), "warmup": warmup})

    def _post_fund(self, p: dict):
        try:
            amount = float(p.get("amount"))
        except (TypeError, ValueError):
            raise ValueError("amount must be a number")
        if not (0.01 <= amount <= 1_000_000):
            raise ValueError("amount must be within 0.01-1,000,000 CHF")
        raw_src = str(p.get("source", "")).strip()
        norm = {s.lower().replace(" ", ""): s for s in db.FINANCE_SOURCES}
        source = norm.get(raw_src.lower().replace(" ", ""))
        if source is None:
            raise ValueError("source must be one of: " + ", ".join(db.FINANCE_SOURCES))
        fid = db.add_finance_entry(amount, source)
        self._json({"ok": True, "id": fid, "amount": amount, "source": source,
                    **_swiss_state()})

    # -- artifact serving (allow-listed, no traversal) -----------------------
    def _artifact(self, name: str):
        if name not in ARTIFACTS:
            self._err("unknown artifact", 404)
            return
        path = PORTFOLIO_DIR / name
        if not path.is_file():
            self._err("artifact not generated yet — run the portfolio script first", 404)
            return
        self._send(200, path.read_bytes(), ARTIFACTS[name])


def _rollups_json():
    return {"ok": True, "rollups": _rollups(),
            "kill_switch": _kill_switch_state()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="GenevaOS Web — app-style local interface (stdlib-only).")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (default 127.0.0.1 = this machine only)")
    ap.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    ap.add_argument("--no-browser", action="store_true",
                    help="do not auto-open the browser")
    args = ap.parse_args(argv)

    # Touch the DB once so the schema exists before the first request.
    db.get_conn().close()

    url = f"http://{'localhost' if args.host in ('127.0.0.1', 'localhost') else args.host}:{args.port}"
    print("== ⚡ GENEVA OS // WEB COMMAND CENTER ==")
    print(f"  Serving on {url}  (Ctrl+C to stop)")
    print(f"  Database : {db.DB_PATH}")
    print(f"  Portfolio: {PORTFOLIO_DIR}")
    if args.host not in ("127.0.0.1", "localhost"):
        print("  [warn] bound to a non-local address — anyone on your network "
              "can read/modify your data.")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutdown. Data persists in geneva_os.db.")
    except OSError as exc:
        print(f"[fatal] could not bind {args.host}:{args.port} — {exc}\n"
              f"        try another port: python webapp.py --port 8080")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
