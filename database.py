"""
GenevaOS - SQLite persistence layer.

Offline-first local database (geneva_os.db) for:
  * academics   : Baccalauréat 2027 grading (weighted, out of 20)
  * athletics   : middle-session times + asthma warm-up compliance
  * finance     : CHF liquidity tracking toward the Day-1 threshold
"""

from __future__ import annotations

import sqlite3
from datetime import date as _date
from pathlib import Path
from typing import Optional

# DB lives next to the code -> fully offline, no cloud.
DB_PATH = Path(__file__).resolve().parent / "geneva_os.db"

# ------------------------------------------------------------------ schema --
SCHEMA = """
CREATE TABLE IF NOT EXISTS academics (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    date     TEXT    NOT NULL,               -- ISO yyyy-mm-dd
    subject  TEXT    NOT NULL,               -- e.g. SVT, PC, Math, Philo...
    score    REAL    NOT NULL CHECK (score >= 0 AND score <= 20),
    weight   REAL    NOT NULL CHECK (weight > 0),
    type     TEXT    NOT NULL CHECK (type IN ('QCM','TP','Written'))
);

CREATE TABLE IF NOT EXISTS athletics (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    date               TEXT    NOT NULL,
    distance           REAL    NOT NULL,     -- meters
    time_seconds       REAL    NOT NULL,     -- total elapsed seconds
    asthma_warmup_done INTEGER NOT NULL DEFAULT 0   -- boolean (0/1)
);

CREATE TABLE IF NOT EXISTS finance (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    date      TEXT NOT NULL,
    amount_chf REAL NOT NULL,               -- positive = inflow, negative = outflow
    source    TEXT NOT NULL CHECK (source IN ('Summer Work','Tutoring','Grants'))
);
"""


def get_conn() -> sqlite3.Connection:
    """Return a connection with the schema guaranteed to exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


# --------------------------------------------------------------- academics --
def add_academic_entry(subject: str, score: float, weight: float,
                       entry_type: str, entry_date: Optional[str] = None) -> int:
    conn = get_conn()
    with conn:
        cur = conn.execute(
            "INSERT INTO academics (date, subject, score, weight, type) "
            "VALUES (?, ?, ?, ?, ?)",
            (entry_date or _date.today().isoformat(),
             subject.strip().upper(), score, weight, entry_type),
        )
    row_id = cur.lastrowid
    conn.close()
    return row_id


def rolling_weighted_average(subject: Optional[str] = None) -> Optional[float]:
    """
    Weighted average on a 20-point scale:
        avg = Σ(score_i × weight_i) / Σ(weight_i)

    Pass `subject` to restrict the roll to one subject (kill-switch check),
    or None for the global all-subject rolling average.
    Returns None when there is no data yet.
    """
    conn = get_conn()
    if subject:
        row = conn.execute(
            "SELECT SUM(score * weight) / SUM(weight) FROM academics "
            "WHERE subject = ?",
            (subject.strip().upper(),),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT SUM(score * weight) / SUM(weight) FROM academics"
        ).fetchone()
    conn.close()
    return row[0] if row and row[0] is not None else None


def list_academics(limit: int = 50) -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, date, subject, score, weight, type FROM academics "
        "ORDER BY date DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return rows


# --------------------------------------------------------------- athletics --
def add_run_entry(distance_m: float, time_seconds: float,
                  asthma_warmup_done: bool,
                  entry_date: Optional[str] = None) -> int:
    conn = get_conn()
    with conn:
        cur = conn.execute(
            "INSERT INTO athletics (date, distance, time_seconds, asthma_warmup_done) "
            "VALUES (?, ?, ?, ?)",
            (entry_date or _date.today().isoformat(),
             distance_m, time_seconds, int(asthma_warmup_done)),
        )
    row_id = cur.lastrowid
    conn.close()
    return row_id


def list_runs(limit: int = 50) -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, date, distance, time_seconds, asthma_warmup_done "
        "FROM athletics ORDER BY date DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return rows


# ----------------------------------------------------------------- finance --
FINANCE_SOURCES = ("Summer Work", "Tutoring", "Grants")


def add_finance_entry(amount_chf: float, source: str,
                      entry_date: Optional[str] = None) -> int:
    conn = get_conn()
    with conn:
        cur = conn.execute(
            "INSERT INTO finance (date, amount_chf, source) VALUES (?, ?, ?)",
            (entry_date or _date.today().isoformat(), amount_chf, source),
        )
    row_id = cur.lastrowid
    conn.close()
    return row_id


def chf_liquidity() -> float:
    """Total current liquidity in CHF (sum of all recorded movements)."""
    conn = get_conn()
    row = conn.execute("SELECT COALESCE(SUM(amount_chf), 0) FROM finance").fetchone()
    conn.close()
    return float(row[0])


def list_finance(limit: int = 50) -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, date, amount_chf, source FROM finance "
        "ORDER BY date DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return rows
