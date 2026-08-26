"""Quick check: macro_events store contents (read-only)."""

import sqlite3

conn = sqlite3.connect("data/trading_intelligence.db")
conn.row_factory = sqlite3.Row
n = conn.execute("SELECT COUNT(*) AS n FROM macro_events").fetchone()["n"]
high = conn.execute("SELECT COUNT(*) AS n FROM macro_events WHERE impact='High'").fetchone()["n"]
released = conn.execute(
    "SELECT COUNT(*) AS n FROM macro_events WHERE actual IS NOT NULL"
).fetchone()["n"]
print(f"macro_events={n}  high_impact={high}  released={released}")
for r in conn.execute(
    "SELECT currency,title,scheduled_at,impact FROM macro_events "
    "WHERE impact='High' ORDER BY scheduled_at LIMIT 5"
):
    print(" HIGH:", r["currency"], "|", r["title"], "|", r["scheduled_at"])
