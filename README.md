# ⚡ GENEVA OS

Offline-first CLI command center for the **Geneva Protocol**:

| Domain | What it tracks |
|---|---|
| 🎓 Academics | Baccalauréat 2027 weighted grades (/20) with **Kill-Switch** logic |
| 🏃 Athletics | Middle-distance pace engine + asthma (EIB) protocol |
| 🧴 Skincare | AM/PM master routine, auto-adjusted for shave days |
| 🇨🇭 Swiss | CHF liquidity vs the 2,500 Day-1 threshold + admin deadlines |
| 🤖 Hub | Local AI stack health (LibreChat :3080 / Ollama :11434) |

No cloud. No web framework. Everything persists to a local SQLite file
(`geneva_os.db`, created automatically on first run).

## Install

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Requires Python **3.10+**.

## Usage

```bash
python main.py --help
```

### A. Log an academic grade (KILL SWITCH check)

```bash
python main.py academics log            # interactive prompts
python main.py academics log -s SVT -n 16.5 -w 4 -t QCM   # non-interactive
python main.py academics list           # ledger + per-subject rolling averages
```

Rolling average = Σ(score × weight) / Σ(weight). If **SVT** or **PC** drops
below **14.5/20**, a red KILL SWITCH panel fires and the process exits with
code `2` — pause all side projects.

### B. Pace calculator (asthma-aware)

```bash
python main.py run pace 500m 1m23s
python main.py run pace 800 1:55          # clock notation, bare seconds OK
python main.py run pace 1.5km 5m30s --log --warmup   # also save to DB
python main.py run history
```

Outputs required splits (per 100 m / 200 m / 400 m / km), speed in km/h, and
the 3-step Asthma Protocol Checklist (Ventolin window → Refractory-Period
warm-up → post-run active walk).

### C. Skincare protocol

```bash
python main.py skin today    # uses current weekday
python main.py skin day wed  # preview any day (Mon/Wed/Fri/Sun = shave days)
```

* **AM (every day):** BHA Cleanser → Niacinamide → Matte Moisturizer → SPF 50+
* **PM (non-shave):** BHA Cleanser → wait 20 min → Retinal 0.2% → Azelaic Acid → Moisturizer
* **PM (shave days Mon/Wed/Fri/Sun):** BHA Cleanser → Zinc Oxide Barrier Cream — **no Retinal/acids**

### D. Swiss relocation status

```bash
python main.py swiss status                    # liquidity bar + next deadline
python main.py swiss fund -a 800 -s "Summer Work"
python main.py swiss ledger
```

Deadline pipeline (UNIGE Portal → FMEL Housing → OCPM Registration → Permis L)
is defined in `ADMIN_DEADLINES` at the top of `main.py` — edit dates there.

### E. Sovereign AI hub ping

```bash
python main.py hub ping
```

GET-pings `http://localhost:3080/health` (LibreChat) and
`http://localhost:11434/api/version` (Ollama). Green **SOVEREIGN AI HUB ONLINE**
panel if both respond, otherwise red **HUB OFFLINE - Run start-hub.ps1**
(exit code `1`).

## Files

```
main.py          # Typer CLI routing + rich rendering
database.py      # sqlite3 schema + query helpers (geneva_os.db)
requirements.txt
README.md
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | Hub offline / bad input |
| 2 | **Academic Kill Switch triggered** |
