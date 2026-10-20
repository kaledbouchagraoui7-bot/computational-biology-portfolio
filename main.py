"""
GenevaOS — Central command terminal for the Geneva Protocol.

  * Baccalauréat 2027 academic tracking (weighted /20, kill-switch logic)
  * Middle-distance running + asthma protocol pacing
  * Skincare / grooming master protocol by day of week
  * Swiss relocation finance & administration status
  * Sovereign AI hub health pings (LibreChat / Ollama)

Run with:  python main.py --help
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

import requests
import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text

import database as db

app = typer.Typer(
    name="GenevaOS",
    help="⚡ GENEVA OS // Swiss Relocation • Bac 2027 • Athletics • Skin • Hub",
    no_args_is_help=True,
    add_completion=False,
)
academics_app = typer.Typer(help="Baccalauréat 2027 academic control.", no_args_is_help=True)
run_app = typer.Typer(help="Middle-distance running & asthma protocol.", no_args_is_help=True)
skin_app = typer.Typer(help="Skincare / grooming master protocol.", no_args_is_help=True)
swiss_app = typer.Typer(help="Swiss relocation status & logistics.", no_args_is_help=True)
hub_app = typer.Typer(help="Sovereign AI hub operations.", no_args_is_help=True)

app.add_typer(academics_app, name="academics")
app.add_typer(run_app, name="run")
app.add_typer(skin_app, name="skin")
app.add_typer(swiss_app, name="swiss")
app.add_typer(hub_app, name="hub")

console = Console()

# ---------------------------------------------------------------- constants --
KILL_SWITCH_THRESHOLD = 14.5          # per-subject rolling average floor
CORE_SUBJECTS = ("SVT", "PC")         # Sciences de la Vie/Terre, Physique-Chimie
DAY1_THRESHOLD_CHF = 2500.0           # CHF liquidity required on arrival day

SHAVE_DAYS = {0, 2, 4, 6}             # Mon / Wed / Fri / Sun (weekday())

ENDPOINTS = {
    "LibreChat": "http://localhost:3080/health",
    "Ollama":    "http://localhost:11434/api/version",
}

# Administrative deadlines for the relocation (name -> planned due date).
ADMIN_DEADLINES: list[tuple[str, str]] = [
    ("UNIGE Portal (admission upload)",      "2027-01-15"),
    ("FMEL Housing Application",             "2027-02-01"),
    ("OCPM Registration (logement Genève)",  "2027-02-15"),
    ("Permis L / Cantonal filing",           "2027-06-01"),
]


# ------------------------------------------------------------- boot banner --
def _banner(title: str) -> Panel:
    return Panel(
        Text(f"◤ GENEVA OS ◢  ::  {title}", style="bold cyan", justify="center"),
        border_style="cyan",
        padding=(0, 2),
    )


def _ask_float(prompt: str, lo: float, hi: float) -> float:
    """Numeric prompt with range validation (rich-styled)."""
    while True:
        raw = typer.prompt(prompt)
        try:
            val = float(raw)
        except ValueError:
            console.print("[red bold]✗ Not a number. Re-enter.[/red bold]")
            continue
        if lo <= val <= hi:
            return val
        console.print(f"[red bold]✗ Out of range [{lo} – {hi}]. Re-enter.[/red bold]")


# ================================================================ ACADEMICS ==
@academics_app.command("log")
def academics_log(
    subject: Optional[str] = typer.Option(None, "--subject", "-s",
                                          help="SVT / PC / Math / Philo ... (skips prompt)"),
    score: Optional[float] = typer.Option(None, "--score", "-n", min=0, max=20,
                                          help="Score out of 20 (skips prompt)"),
    weight: Optional[float] = typer.Option(None, "--weight", "-w", min=0.1,
                                           help="Coefficient/weight (skips prompt)"),
    entry_type: Optional[str] = typer.Option(None, "--type", "-t",
                                             help="QCM / TP / Written (skips prompt)"),
):
    """Log a graded assessment and evaluate the KILL SWITCH."""
    console.print(_banner("ACADEMIC REGISTRY // BAC 2027"))

    # ---- interactive prompts (pre-filled if flags given) -------------------
    if subject is None:
        while True:
            subject = typer.prompt("Subject").strip().upper()
            if subject:
                break
            console.print("[red bold]✗ Subject cannot be empty.[/red bold]")

    if score is None:
        score = _ask_float("Score (out of 20)", 0.0, 20.0)

    if weight is None:
        weight = _ask_float("Weight / coefficient (> 0)", 0.1, 100.0)

    if entry_type is None:
        while True:
            entry_type = typer.prompt("Type (QCM / TP / Written)").strip().title()
            norm = {"Qcm": "QCM", "Tp": "TP", "Written": "Written"}.get(entry_type)
            if norm:
                entry_type = norm
                break
            console.print("[red bold]✗ Invalid type. Use QCM, TP or Written.[/red bold]")

    row_id = db.add_academic_entry(subject, score, weight, entry_type)

    # ---- weighted math ------------------------------------------------------
    subj_avg = db.rolling_weighted_average(subject)
    global_avg = db.rolling_weighted_average()
    n_subj = len([r for r in db.list_academics(10_000) if r[2] == subject.upper()])

    tbl = Table(title=f"Entry #{row_id} logged — {subject}",
                header_style="bold magenta", border_style="cyan", show_lines=True)
    tbl.add_column("Metric", style="cyan", width=28)
    tbl.add_column("Value", justify="right", style="bold white")
    tbl.add_row("This assessment", f"{score:.2f}/20  ({entry_type}, w={weight:g})")
    tbl.add_row(f"Rolling avg — {subject}", f"{subj_avg:.2f}/20  ({n_subj} entries)")
    tbl.add_row("Global rolling avg", f"{global_avg:.2f}/20")
    console.print(tbl)

    # ---- KILL SWITCH LOGIC --------------------------------------------------
    triggered = [
        s for s in CORE_SUBJECTS
        if (avg := db.rolling_weighted_average(s)) is not None and avg < KILL_SWITCH_THRESHOLD
    ]
    if triggered:
        detail = "\n".join(
            f"  ▶ {s}: rolling avg [bold]{db.rolling_weighted_average(s):.2f}/20[/bold] "
            f"< [bold]{KILL_SWITCH_THRESHOLD}[/bold]"
            for s in triggered
        )
        # NOTE: Text.assemble() does NOT parse [bold]-style markup — the detail
        # lines must go through Text.from_markup() or the tags print literally.
        body = Text.assemble(
            ("ACADEMIC KILL SWITCH TRIGGERED\n\n", "bold white"),
            ("Pause all side projects. Redirect 100% bandwidth to core subjects.\n\n", "bold white"),
            Text.from_markup(detail, style="bold yellow"),
        )
        console.print(Panel(body, title="☠ KILL SWITCH ☠",
                            border_style="red", style="red", padding=(1, 2)))
        raise typer.Exit(code=2)
    else:
        console.print(Panel("[green bold]STATUS NOMINAL — core subject averages ≥ "
                            f"{KILL_SWITCH_THRESHOLD}/20. Side projects cleared to run.[/green bold]",
                            title="✓ CLEARANCE", border_style="green"))


@academics_app.command("list")
def academics_list(limit: int = typer.Option(20, "--limit", "-l")):
    """Show the most recent academic entries and per-subject rollups."""
    rows = db.list_academics(limit)
    tbl = Table(title="Academic Ledger", header_style="bold magenta",
                border_style="cyan")
    tbl.add_column("ID", justify="right")
    tbl.add_column("Date", style="dim")
    tbl.add_column("Subject", style="cyan")
    tbl.add_column("Score", justify="right")
    tbl.add_column("Weight", justify="right")
    tbl.add_column("Type")
    for r in rows:
        color = "green" if r[3] >= 14.5 else ("yellow" if r[3] >= 12 else "red")
        tbl.add_row(str(r[0]), r[1], r[2], f"[{color}]{r[3]:.2f}[/{color}]",
                    f"{r[4]:g}", r[5])
    console.print(tbl)

    rollup = Table(title="Rolling Weighted Averages", header_style="bold magenta",
                   border_style="blue")
    rollup.add_column("Subject", style="cyan")
    rollup.add_column("Avg /20", justify="right")
    rollup.add_column("Status")
    subjects = sorted({r[2] for r in db.list_academics(10_000)})
    for s in subjects:
        avg = db.rolling_weighted_average(s)
        danger = s in CORE_SUBJECTS and avg < KILL_SWITCH_THRESHOLD
        status = "[red bold]KILL-SWITCH RISK[/red bold]" if danger else "[green]OK[/green]"
        rollup.add_row(s, f"{avg:.2f}", status)
    if subjects:
        console.print(rollup)
    else:
        console.print("[dim]No data yet — log your first grade with "
                      "`python main.py academics log`.[/dim]")


# ===================================================================== RUN ==
def parse_time_to_seconds(raw: str) -> Optional[float]:
    """
    Accept '1m23s', '1:23', '83', '1m23s+500ms', '90s', '2m', '1\'23"', '1′23″'.
    Rules:
      * 'Xm Ys (+Zms)'  → minutes + seconds + milliseconds
      * 'H:MM:SS' / 'MM:SS' → clock notation
      * "X'Y\"" / X′Y″   → prime/double-prime (feet-style) notation
      * trailing 's' or '"' → plain seconds
      * trailing 'm/min' → minutes
      * bare number      → plain seconds (unambiguous for track use).
    """
    t = raw.strip().lower().replace("′", "'").replace("″", '"')
    if not t:
        return None

    # prime notation: 1'23", 1'23, 83"
    m_prime = re.fullmatch(r"(?:(\d+(?:\.\d+)?)\s*'\s*)?(\d+(?:\.\d+)?)\s*\"?", t)
    if m_prime and ("'" in t or '"' in t):
        minutes = float(m_prime.group(1)) if m_prime.group(1) else 0.0
        return minutes * 60 + float(m_prime.group(2))

    # clock notation 1:23 or 1:02:03
    if ":" in t:
        parts = t.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
        if len(nums) == 3:
            return nums[0] * 3600 + nums[1] * 60 + nums[2]
        return None

    # millisecond suffix e.g. 1m23s+500ms
    ms_extra = 0.0
    if "+" in t:
        head, tail = t.split("+", 1)
        tail = tail.strip()
        if tail.endswith("ms"):
            try:
                ms_extra = float(tail[:-2]) / 1000.0
            except ValueError:
                return None
            t = head.strip()
        else:
            return None

    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)\s*m(?:in)?\s*)?"
                     r"(?:(\d+(?:\.\d+)?)\s*s?)?", t)
    if not m or (m.group(1) is None and m.group(2) is None):
        return None
    minutes = float(m.group(1)) if m.group(1) else 0.0
    seconds = float(m.group(2)) if m.group(2) else 0.0
    return minutes * 60 + seconds + ms_extra


def fmt_hms(total_seconds: float) -> str:
    total_seconds = round(total_seconds, 2)
    h = int(total_seconds // 3600)
    rem = total_seconds - h * 3600
    mins = int(rem // 60)
    secs = rem - mins * 60
    return (f"{h}:{mins:02d}:{secs:05.2f}" if h else f"{mins}:{secs:05.2f}")


MI_M = 1609.344
KM_M = 1000.0


def parse_distance_meters(raw: str) -> Optional[float]:
    """Accept '500', '500m', '1.5km', '1mi' -> meters (module-level so the
    web interface can reuse the exact same parser as the CLI)."""
    t = raw.strip().lower()
    try:
        if t.endswith("mi"):
            return float(t[:-2]) * MI_M
        if t.endswith("km"):
            return float(t[:-2]) * KM_M
        if t.endswith("m"):
            return float(t[:-1])
        return float(t)  # bare number => meters
    except ValueError:
        return None


@run_app.command("pace")
def run_pace(
    distance: Optional[str] = typer.Argument(None, help="Target distance, e.g. 500 or 500m or 1mi"),
    target: Optional[str] = typer.Argument(None, help="Target time, e.g. 1m23s or 1:23 or 83"),
    log: bool = typer.Option(False, "--log", help="Also save this session to the athletics DB."),
    warmup_done: Optional[bool] = typer.Option(None, "--warmup/--no-warmup",
                                               help="Asthma warm-up completed (needed with --log)."),
):
    """Compute required pace per 100m & km/h, then print the Asthma Protocol."""
    console.print(_banner("PACE ENGINE // ASTHMA-AWARE PROTOCOL"))

    parse_distance = parse_distance_meters

    while distance is None or (dist_m := parse_distance(distance)) is None or dist_m <= 0:
        if distance is not None:
            console.print("[red bold]✗ Bad distance. Examples: 500, 500m, 1.5km, 1mi[/red bold]")
        distance = typer.prompt("Target distance (e.g. 500m)")
    dist_m = parse_distance(distance)

    while target is None or (secs := parse_time_to_seconds(target)) is None or secs <= 0:
        if target is not None:
            console.print("[red bold]✗ Bad time. Examples: 1m23s, 1:23, 83 (seconds), 2m+500ms[/red bold]")
        target = typer.prompt("Target time (e.g. 1m23s)")
    secs = parse_time_to_seconds(target)

    # ---- actual math ---------------------------------------------------------
    pace_per_100 = secs / (dist_m / 100.0)          # seconds per 100 m
    speed_kmh = (dist_m / 1000.0) / (secs / 3600.0)  # km/h
    pace_per_km = secs / (dist_m / 1000.0)           # seconds per 1000 m

    tbl = Table(title=f"Required Pace — {dist_m:g} m in {fmt_hms(secs)}",
                header_style="bold magenta", border_style="cyan", show_lines=True)
    tbl.add_column("Split", style="cyan", width=18)
    tbl.add_column("Time", justify="right", style="bold white", width=14)
    tbl.add_column("Notes", style="dim")
    tbl.add_row("Per 100 m", f"{pace_per_100:.1f} s", "primary metronome split")
    tbl.add_row("Per 200 m", f"{pace_per_100 * 2:.1f} s", "half-lap cadence check")
    tbl.add_row("Per 400 m", f"{pace_per_100 * 4:.1f} s", "full lap (track)")
    tbl.add_row("Per km", f"{fmt_hms(pace_per_km)}", "running pace notation")
    tbl.add_row("Speed", f"{speed_kmh:.2f} km/h", f"{speed_kmh / 3.6:.2f} m/s")
    tbl.add_row("Total", f"{fmt_hms(secs)}", f"over {dist_m:g} m")
    console.print(tbl)

    # ---- asthma protocol -----------------------------------------------------
    checklist = Table.grid(padding=(0, 1))
    checklist.add_column(justify="left", style="bold yellow", width=4)
    checklist.add_column(style="white")
    checklist.add_row("1.", "Ventolin 15–20 mins prior? [dim](preventive bronchodilator window)[/dim]")
    checklist.add_row("2.", "15-min warm-up with strides? [dim](induces the Refractory Period → "
                            "protects the main effort from EIB)[/dim]")
    checklist.add_row("3.", "Post-run active walk? [dim](lactate flush + gradual HR drop, "
                            "avoids post-exercise bronchoconstriction)[/dim]")
    console.print(Panel(checklist, title="🫁 ASTHMA PROTOCOL CHECKLIST",
                        border_style="yellow", padding=(1, 2)))

    if log:
        if warmup_done is None:
            warmup_done = typer.confirm("Warm-up completed before this run?", default=False)
        rid = db.add_run_entry(dist_m, secs, warmup_done)
        flag = "[green]YES[/green]" if warmup_done else "[red]NO — PROTOCOL BREACH[/red]"
        console.print(f"[cyan]Logged run #{rid} • {dist_m:g} m @ {fmt_hms(secs)} "
                      f"• asthma warm-up: {flag}[/cyan]")


@run_app.command("log")
def run_log(
    distance: float = typer.Option(..., "--distance", "-d", min=1, help="Distance in meters"),
    time_: float = typer.Option(..., "--time", "-t", min=0.1, help="Total time in seconds"),
    warmup_done: bool = typer.Option(..., "--warmup/--no-warmup",
                                      help="Was the asthma warm-up completed?"),
):
    """Log a completed workout directly."""
    rid = db.add_run_entry(distance, time_, warmup_done)
    pace100 = time_ / (distance / 100.0)
    console.print(_banner("RUN LEDGER"))
    console.print(f"[cyan]Saved run #{rid}:[/cyan] {distance:g} m in {fmt_hms(time_)} "
                  f"→ [bold]{pace100:.1f} s/100m[/bold] "
                  f"({(distance / 1000.0) / (time_ / 3600.0):.2f} km/h) • "
                  f"warm-up: {'[green]done[/green]' if warmup_done else '[red]SKIPPED[/red]'}")


@run_app.command("history")
def run_history(limit: int = typer.Option(15, "--limit", "-l")):
    """Show past sessions with computed splits."""
    rows = db.list_runs(limit)
    if not rows:
        console.print("[dim]No runs logged yet.[/dim]")
        raise typer.Exit()
    tbl = Table(title="Run History", header_style="bold magenta", border_style="cyan")
    tbl.add_column("ID", justify="right")
    tbl.add_column("Date", style="dim")
    tbl.add_column("Dist", justify="right")
    tbl.add_column("Time", justify="right")
    tbl.add_column("/100m", justify="right", style="bold")
    tbl.add_column("Km/h", justify="right")
    tbl.add_column("Warm-up")
    for r in rows:
        p100 = r[3] / (r[2] / 100.0)
        kmh = (r[2] / 1000.0) / (r[3] / 3600.0)
        warm = "[green]✓[/green]" if r[4] else "[red]✗ breach[/red]"
        tbl.add_row(str(r[0]), r[1], f"{r[2]:g} m", fmt_hms(r[3]),
                    f"{p100:.1f} s", f"{kmh:.2f}", warm)
    console.print(tbl)


# ===================================================================== SKIN ==
AM_STEPS = ["BHA Cleanser", "Niacinamide", "Matte Moisturizer", "SPF 50+"]
PM_STANDARD = ["BHA Cleanser", "Wait 20 min", "Retinal 0.2%", "Azelaic Acid", "Moisturizer"]
PM_SHAVE = ["BHA Cleanser", "Zinc Oxide Barrier Cream", "— NO Retinal / Acids —"]


def skin_routine_for_day(weekday_index: int) -> tuple[list[str], bool]:
    """Return (pm_steps, is_shave_day) for weekday() index 0=Mon..6=Sun."""
    return (PM_SHAVE, True) if weekday_index in SHAVE_DAYS else (PM_STANDARD, False)


@skin_app.command("today")
def skin_today():
    """Print today's AM/PM routine based on the master protocol."""
    _render_skin(date.today())


@skin_app.command("day")
def skin_day(day: str = typer.Argument(..., help="Day name: mon/tue/.../sun")):
    """Preview the routine for any given weekday."""
    names = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "wednesday": 2, "wed": 2,
             "thursday": 3, "thu": 3, "thur": 3, "friday": 4, "fri": 4,
             "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
    idx = names.get(day.strip().lower())
    if idx is None:
        console.print(f"[red bold]✗ Unknown day '{day}'. Use mon…sun.[/red bold]")
        raise typer.Exit(code=1)
    _render_skin(datetime(2026, 1, 5 + idx))  # a known Monday-anchored week


def _render_skin(d: date):
    wd = d.weekday()
    is_shave = wd in SHAVE_DAYS
    pm_steps = PM_SHAVE if is_shave else PM_STANDARD
    day_name = d.strftime("%A")

    console.print(_banner(f"DERMAL PROTOCOL // {day_name.upper()} {d.isoformat()}"))

    def step_panel(title: str, steps: list[str], border: str, note: str) -> Panel:
        body = Text(note + "\n", style="italic dim")
        for i, s in enumerate(steps, 1):
            body.append_text(Text(f"{i:>2}. ", style="bold cyan"))
            body.append_text(Text(s + "\n", style="white"))
        return Panel(body, title=title, border_style=border, padding=(1, 2))

    am = step_panel("☀ AM ROUTINE", AM_STEPS, "green",
                    "Morning — antioxidant shield + barrier + UV lock.")
    pm_note = ("Shave day detected → acid/retinoid blackout to protect "
               "compromised barrier." if is_shave else
               "Standard night — retinoid + azelaic actives after pH reset wait.")
    pm = step_panel("🌙 PM ROUTINE" + (" [SHAVE DAY]" if is_shave else ""),
                    pm_steps, "red" if is_shave else "blue", pm_note)

    console.print(am)
    console.print(pm)

    if is_shave:
        console.print(Panel("[bold red]⚠ WARNING:[/bold red] Absolutely no Retinal 0.2% "
                            "or acids on shave days. Zinc oxide only.",
                            border_style="red"))
    else:
        console.print(Panel("[bold green]✓ Non-shave day:[/bold green] full actives stack "
                            "cleared. Respect the 20-minute BHA → Retinal wait window.",
                            border_style="green"))


# ===================================================================== SWISS ==
@swiss_app.command("status")
def swiss_status():
    """CHF liquidity vs Day-1 threshold, progress bar, next admin deadline."""
    console.print(_banner("SWISS RELOCATION STATUS // GENÈVE"))

    liquidity = db.chf_liquidity()
    shortfall = max(DAY1_THRESHOLD_CHF - liquidity, 0.0)

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40),
        TextColumn("[progress.percentage]{task.percentage:>5.1f}%"),
        TextColumn("•"),
        TextColumn("[bold]{task.fields[val]}"),
        console=console,
        expand=False,
    ) as progress:
        progress.add_task(
            description="[cyan]CHF Day-1 Reserve[/cyan]",
            total=DAY1_THRESHOLD_CHF,
            completed=liquidity,
            val=f"CHF {liquidity:,.2f} / CHF {DAY1_THRESHOLD_CHF:,.2f}",
        )

    stats = Table(header_style="bold magenta", border_style="cyan", show_header=False)
    stats.add_column("Indicator", style="cyan")
    stats.add_column("Value", justify="right", style="bold white")
    stats.add_row("Current liquidity", f"CHF {liquidity:,.2f}")
    stats.add_row("Day-1 threshold", f"CHF {DAY1_THRESHOLD_CHF:,.2f}")
    if shortfall > 0:
        stats.add_row("[red]Shortfall[/red]", f"[red bold]CHF {shortfall:,.2f}[/red bold]")
        verdict = Panel(
            f"[bold red]THRESHOLD NOT MET.[/bold red] Secure additional CHF "
            f"{shortfall:,.2f} via Summer Work / Tutoring / Grants before departure.",
            title="⚠ FINANCIAL GATE", border_style="red")
    else:
        stats.add_row("[green]Surplus[/green]", f"[green bold]CHF {-shortfall:,.2f}[/green bold]")
        verdict = Panel("[bold green]DAY-1 THRESHOLD CLEARED. Liquidity gate OPEN.[/bold green]",
                        title="✓ FINANCIAL GATE", border_style="green")
    console.print(stats)
    console.print(verdict)

    # ---- next administrative deadline ---------------------------------------
    today = date.today()
    upcoming = []
    for name, iso in ADMIN_DEADLINES:
        due = datetime.strptime(iso, "%Y-%m-%d").date()
        if due >= today:
            upcoming.append((due, name))
    upcoming.sort()

    tbl = Table(title="Administrative Pipeline", header_style="bold magenta",
                border_style="blue")
    tbl.add_column("Priority", justify="center", style="bold")
    tbl.add_column("Deadline", style="cyan")
    tbl.add_column("Task")
    tbl.add_column("Days left", justify="right")
    if upcoming:
        due, name = upcoming[0]
        delta = (due - today).days
        console.print(Panel(f"[bold yellow]NEXT IMMEDIATE DEADLINE:[/bold yellow] "
                            f"{name} — due {due.isoformat()} ([bold]{delta} days[/bold])",
                            border_style="yellow"))
        for i, (d, n) in enumerate(upcoming, 1):
            dl = (d - today).days
            urg = "red" if dl <= 30 else ("yellow" if dl <= 90 else "white")
            tbl.add_row(f"P{i}", d.isoformat(), n, f"[{urg}]{dl}[/{urg}]")
    else:
        tbl.add_row("—", "—", "[dim]All registered deadlines passed — update the "
                            "pipeline in main.py::ADMIN_DEADLINES[/dim]", "—")
    console.print(tbl)


@swiss_app.command("fund")
def swiss_fund(
    amount: Optional[float] = typer.Option(None, "--amount", "-a",
                                           help="CHF amount (positive inflow)"),
    source: Optional[str] = typer.Option(None, "--source", "-s",
                                         help="Summer Work / Tutoring / Grants"),
):
    """Record a CHF inflow toward the Day-1 threshold."""
    if amount is None:
        amount = _ask_float("Amount in CHF", 0.01, 1_000_000)
    while source not in db.FINANCE_SOURCES:
        if source is not None:
            console.print(f"[red bold]✗ Invalid source. Choose: "
                          f"{', '.join(db.FINANCE_SOURCES)}[/red bold]")
        src = typer.prompt(f"Source ({' / '.join(db.FINANCE_SOURCES)})")
        norm = {s.lower().replace(" ", ""): s for s in db.FINANCE_SOURCES}
        source = norm.get(src.strip().lower().replace(" ", ""))
    fid = db.add_finance_entry(amount, source)
    console.print(f"[cyan]Logged entry #{fid}:[/cyan] [green bold]+CHF {amount:,.2f}"
                  f"[/green bold] ({source})")


@swiss_app.command("ledger")
def swiss_ledger(limit: int = typer.Option(20, "--limit", "-l")):
    """Show the finance ledger."""
    rows = db.list_finance(limit)
    tbl = Table(title="CHF Ledger", header_style="bold magenta", border_style="cyan")
    tbl.add_column("ID", justify="right")
    tbl.add_column("Date", style="dim")
    tbl.add_column("Amount CHF", justify="right")
    tbl.add_column("Source", style="cyan")
    for r in rows:
        color = "green" if r[2] >= 0 else "red"
        sign = "+" if r[2] >= 0 else ""
        tbl.add_row(str(r[0]), r[1], f"[{color}]{sign}{r[2]:,.2f}[/{color}]", r[3])
    console.print(tbl)
    console.print(f"Total liquidity: [bold]CHF {db.chf_liquidity():,.2f}[/bold]")


# ====================================================================== HUB ==
@hub_app.command("ping")
def hub_ping(timeout: float = typer.Option(3.0, "--timeout", "-t", min=0.5,
                                           help="HTTP timeout in seconds")):
    """Ping LibreChat & Ollama; report Sovereign AI Hub state."""
    console.print(_banner("SOVEREIGN AI HUB // HEALTH PROBE"))

    results: list[tuple[str, bool, str]] = []
    for name, url in ENDPOINTS.items():
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.ok:
                detail = f"HTTP {resp.status_code}"
                try:  # Ollama returns {"version": "..."}
                    body = resp.json()
                    if isinstance(body, dict) and "version" in body:
                        detail += f" • v{body['version']}"
                except ValueError:
                    txt = resp.text.strip().replace("\n", " ")[:60]
                    if txt:
                        detail += f" • {txt}"
                results.append((name, True, detail))
            else:
                results.append((name, False, f"HTTP {resp.status_code}"))
        except requests.exceptions.RequestException as exc:
            results.append((name, False, type(exc).__name__))

    tbl = Table(header_style="bold magenta", border_style="cyan", show_lines=False)
    tbl.add_column("Service", style="cyan")
    tbl.add_column("Endpoint", style="dim")
    tbl.add_column("State", justify="center")
    tbl.add_column("Detail")
    all_up = True
    for name, ok, detail in results:
        state = "[green bold]● ONLINE[/green bold]" if ok else "[red bold]● OFFLINE[/red bold]"
        all_up &= ok
        tbl.add_row(name, ENDPOINTS[name], state, detail)
    console.print(tbl)

    if all_up:
        console.print(Panel("[bold green]SOVEREIGN AI HUB ONLINE[/bold green]\n"
                            "Local inference + chat stack operational. Zero cloud dependency.",
                            title="◉ HUB STATUS", border_style="green", padding=(1, 2)))
    else:
        console.print(Panel("[bold red]HUB OFFLINE - Run start-hub.ps1[/bold red]\n"
                            "Bring up LibreChat (3080) and Ollama (11434), then re-ping.",
                            title="◉ HUB STATUS", border_style="red", padding=(1, 2)))
        raise typer.Exit(code=1)


# ===================================================================== MAIN ==
@app.callback()
def main_callback():
    """⚡ GENEVA OS — offline-first command center."""
    pass


if __name__ == "__main__":
    app()
