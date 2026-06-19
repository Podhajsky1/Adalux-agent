"""
Jednorázový konverzní skript: .xls databáze obcí → SQLite.

Spusť lokálně jednou:
    python build_database.py "Databáze_..._2025.xls"

Vytvoří data/municipalities.db – malý, rychlý, nahraje se na Railway s kódem.
"""

import sys
import sqlite3
import re
import pandas as pd
from pathlib import Path

OUT_PATH = "data/municipalities.db"


def normalize_phone(raw: str) -> str:
    """Normalizuje český telefon na formát +420XXXXXXXXX. Vrací '' pokud neplatný."""
    if not raw or str(raw).strip().lower() in ("", "nan"):
        return ""
    digits = re.sub(r"\D", "", str(raw))
    if digits.startswith("420") and len(digits) == 12:
        return f"+{digits}"
    if len(digits) == 9:
        return f"+420{digits}"
    if digits.startswith("00420"):
        return f"+{digits[2:]}"
    return ""  # neplatný formát – přeskočit


def build(xls_path: str):
    print(f"Čtu {xls_path}...")
    df = pd.read_excel(xls_path, sheet_name="Ver.01-2025")
    print(f"Načteno {len(df)} řádků")

    Path("data").mkdir(exist_ok=True)
    conn = sqlite3.connect(OUT_PATH)
    cur = conn.cursor()

    cur.execute("DROP TABLE IF EXISTS municipalities")
    cur.execute("""
        CREATE TABLE municipalities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            statut TEXT,
            name TEXT NOT NULL,
            address TEXT,
            psc TEXT,
            posta TEXT,
            okres TEXT,
            kraj TEXT,
            ic TEXT,
            population INTEGER,
            phone TEXT,
            phone2 TEXT,
            email TEXT,
            web TEXT,
            mayor_name TEXT DEFAULT '',
            mayor_title TEXT DEFAULT '',
            mayor_lookup_done INTEGER DEFAULT 0,
            call_status TEXT DEFAULT 'pending',
            last_call_date TEXT,
            last_call_outcome TEXT,
            last_call_notes TEXT,
            callback_date TEXT
        )
    """)

    skipped_no_phone = 0
    inserted = 0

    for _, row in df.iterrows():
        phone = normalize_phone(row.get("Tel. 1", ""))
        phone2 = normalize_phone(row.get("Tel. 2", ""))

        if not phone:
            skipped_no_phone += 1
            continue

        email = str(row.get("E-mail 1", "")).strip()
        email = "" if email.lower() == "nan" else email

        web = str(row.get("Web", "")).strip()
        web = "" if web.lower() == "nan" else web

        cur.execute("""
            INSERT INTO municipalities
            (statut, name, address, psc, posta, okres, kraj, ic, population, phone, phone2, email, web)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(row.get("Statut", "")).strip(),
            str(row.get("Název města / obce", "")).strip(),
            str(row.get("Adresa", "")).strip(),
            str(row.get("PSČ", "")).strip(),
            str(row.get("Pošta", "")).strip(),
            str(row.get("Okres", "")).strip(),
            str(row.get("Kraj", "")).strip(),
            str(row.get("IČ", "")).strip(),
            int(row.get("Počet obyvatel", 0) or 0),
            phone,
            phone2,
            email,
            web,
        ))
        inserted += 1

    cur.execute("CREATE INDEX idx_name ON municipalities(name)")
    cur.execute("CREATE INDEX idx_kraj ON municipalities(kraj)")
    cur.execute("CREATE INDEX idx_status ON municipalities(call_status)")
    conn.commit()
    conn.close()

    print(f"\n✅ Hotovo: {inserted} obcí vloženo do {OUT_PATH}")
    print(f"⚠️  Přeskočeno (chybný/chybějící telefon): {skipped_no_phone}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Použití: python build_database.py <cesta_k_xls>")
        sys.exit(1)
    build(sys.argv[1])
