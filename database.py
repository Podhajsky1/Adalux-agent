"""
Přístup k databázi obcí (SQLite) – náhrada za web search před každým hovorem.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager

DB_PATH = "data/municipalities.db"


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_municipality(name: str) -> dict | None:
    """Najde obec podle názvu (case-insensitive, přesná shoda)."""
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM municipalities WHERE LOWER(name) = LOWER(?)", (name.strip(),)
        ).fetchone()
        return dict(row) if row else None


def search_municipalities(query: str, limit: int = 10) -> list[dict]:
    """Fulltextové vyhledání podle části názvu (pro UI autocomplete)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM municipalities WHERE name LIKE ? ORDER BY population DESC LIMIT ?",
            (f"%{query.strip()}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_pending_batch(
    limit: int = 50,
    kraj: str | None = None,
    min_population: int | None = None,
    max_population: int | None = None,
) -> list[dict]:
    """
    Vrátí obce čekající na zavolání (status 'pending' nebo 'callback' s callback_date <= dnes).
    Filtry jsou volitelné pro cílení kampaně (kraj, velikost obce).
    """
    where = ["(call_status = 'pending' OR (call_status = 'callback' AND callback_date <= ?))"]
    params = [datetime.now().strftime("%Y-%m-%d")]

    if kraj:
        where.append("kraj = ?")
        params.append(kraj)
    if min_population is not None:
        where.append("population >= ?")
        params.append(min_population)
    if max_population is not None:
        where.append("population <= ?")
        params.append(max_population)

    sql = f"SELECT * FROM municipalities WHERE {' AND '.join(where)} ORDER BY population DESC LIMIT ?"
    params.append(limit)

    with _conn() as c:
        rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def update_call_result(
    municipality_id: int,
    outcome: str,
    notes: str = "",
    callback_date: str | None = None,
    mayor_name: str | None = None,
) -> None:
    """Zapíše výsledek hovoru zpět do databáze (perzistentní stav mezi kampaněmi)."""
    status_map = {
        "meeting_agreed": "done",
        "not_interested": "done",
        "send_email": "done",
        "voicemail": "pending",        # zkusit znovu jindy
        "no_answer": "pending",
        "wrong_person": "pending",
        "callback_requested": "callback",
    }
    new_status = status_map.get(outcome, "pending")

    with _conn() as c:
        c.execute("""
            UPDATE municipalities
            SET call_status = ?, last_call_date = ?, last_call_outcome = ?,
                last_call_notes = ?, callback_date = ?,
                mayor_name = COALESCE(?, mayor_name),
                mayor_lookup_done = 1
            WHERE id = ?
        """, (
            new_status,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            outcome,
            notes,
            callback_date,
            mayor_name,
            municipality_id,
        ))
        c.commit()


def stats() -> dict:
    """Souhrnné statistiky databáze pro dashboard."""
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM municipalities").fetchone()[0]
        by_status = c.execute(
            "SELECT call_status, COUNT(*) as cnt FROM municipalities GROUP BY call_status"
        ).fetchall()
        return {
            "total": total,
            "by_status": {r["call_status"]: r["cnt"] for r in by_status},
        }


def reset_status(municipality_id: int) -> None:
    """Umožní znovu zavolat obci (manuální reset z dashboardu)."""
    with _conn() as c:
        c.execute(
            "UPDATE municipalities SET call_status = 'pending', callback_date = NULL WHERE id = ?",
            (municipality_id,),
        )
        c.commit()
