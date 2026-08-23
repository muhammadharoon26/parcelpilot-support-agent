"""Load the assessment workbook into SQLite, mirroring each sheet as-is plus a meta table for the snapshot time."""

import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import PROCESSED_DIR, RAW_DIR, SQLITE_PATH

WORKBOOK = RAW_DIR / "ParcelPilot_Assessment_Data.xlsx"
SHEETS = ["accounts", "orders", "tickets"]


def read_snapshot_time(workbook_path) -> str:
    """The README sheet is a key/value list; the snapshot row is the reference time for every time-based answer."""
    readme = pd.read_excel(workbook_path, sheet_name="README", header=None)
    for _, row in readme.iterrows():
        if str(row[0]).strip().lower() == "dataset snapshot":
            return str(row[1]).strip()
    raise ValueError("README sheet has no 'Dataset snapshot' row")


def main() -> None:
    if not WORKBOOK.exists():
        raise SystemExit(f"Missing workbook: {WORKBOOK}. Drop the candidate pack into data/raw/ first.")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)

    for sheet in SHEETS:
        df = pd.read_excel(WORKBOOK, sheet_name=sheet)
        df.to_sql(sheet, conn, if_exists="replace", index=False)
        print(f"loaded {sheet}: {len(df)} rows")

    conn.execute("DROP TABLE IF EXISTS meta")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta VALUES ('reference_time', ?)", (read_snapshot_time(WORKBOOK),))

    # Escalations are created by the agent at runtime; recreate empty so demo runs start clean.
    conn.execute("DROP TABLE IF EXISTS escalations")
    conn.execute(
        """CREATE TABLE escalations (
            escalation_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            ticket_id TEXT,
            reason TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    conn.commit()

    reference_time = conn.execute("SELECT value FROM meta WHERE key='reference_time'").fetchone()[0]
    conn.close()
    print(f"reference_time: {reference_time}")
    print(f"wrote {SQLITE_PATH}")


if __name__ == "__main__":
    main()
