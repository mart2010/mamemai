# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Re-embed every stored memory row with e5's `passage: ` prefix (TR-509).

Why this exists: `concepts`, `procedures` and `episodes` each carry an `embedding`
column written by whatever scheme was in force at the time. Adding the prefixes changes
that scheme, and a prefixed query against unprefixed passages is *worse* than neither —
the two live on different scales, so every threshold reads noise. There is no migration
framework here (one schema file, no backfill mechanism), hence a script.

Idempotent: re-running re-encodes the same text to the same vector. Safe to run twice.

    uv run python scripts/reembed_memories.py --dry-run   # counts only, no writes
    uv run python scripts/reembed_memories.py --backup out.json
    uv run python scripts/reembed_memories.py

The text each row is embedded from must match what `MemoryUpserter` writes, or recall
silently degrades: `"{name}: {description}"` for concepts and procedures, the bare
summary for episodes. Those three expressions are duplicated here deliberately rather
than imported — upsert.py builds them from live domain objects mid-transaction, and a
backfill needs them from SQL. If upsert.py's format ever changes, this must change with
it; the test in tests/unit/test_reembed_format.py is what catches the drift.

Bundle-installed content is re-embedded too. It is authored elsewhere with embeddings
computed at install time, so a reinstall would write unprefixed vectors back into a
prefixed database — reinstall after upgrading, or re-run this.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mamemai_server.infrastructure import postgres  # noqa: E402
from mamemai_server.infrastructure.config import load_config  # noqa: E402
from mamemai_server.infrastructure.embedding import SentenceTransformerEmbeddingService  # noqa: E402

# (table, SQL expression producing the exact text upsert.py embeds)
TARGETS = [
    ("concepts", "name || ': ' || description"),
    ("procedures", "name || ': ' || description"),
    ("episodes", "summary"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--backup", type=Path, help="write existing embeddings to this JSON file first")
    args = ap.parse_args()

    cfg = load_config()
    conn = postgres.connect(cfg.database_url)

    rows: dict[str, list[tuple[int, str]]] = {}
    with conn.cursor() as cur:
        for table, expr in TARGETS:
            cur.execute(f"SELECT id, {expr} FROM {table} ORDER BY id")
            rows[table] = [(r[0], r[1]) for r in cur.fetchall()]
    total = sum(len(v) for v in rows.values())
    for table, items in rows.items():
        print(f"  {table:12} {len(items):4} row(s)")
    print(f"  {'total':12} {total:4}")

    if args.backup:
        # The old vectors are not recoverable by re-running anything — the code that
        # produced them is gone once this ships — so back them up before overwriting.
        with conn.cursor() as cur:
            dump = {}
            for table, _ in TARGETS:
                cur.execute(f"SELECT id, embedding::text FROM {table} ORDER BY id")
                dump[table] = {str(i): e for i, e in cur.fetchall()}
        args.backup.write_text(json.dumps(dump))
        print(f"  backed up existing embeddings -> {args.backup}")

    if args.dry_run:
        print("  dry run — nothing written")
        return 0

    print(f"  loading embedding model (device={cfg.embedding_device})…")
    service = SentenceTransformerEmbeddingService(device=cfg.embedding_device)

    written = 0
    for table, items in rows.items():
        for row_id, text in items:
            vector = service.embed_passage(text)
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {table} SET embedding = %s WHERE id = %s",
                    (postgres._vec(vector), row_id),
                )
            written += 1
            if written % 20 == 0:
                print(f"    {written}/{total}…")
    conn.commit()
    print(f"  re-embedded {written} row(s) with the '{'passage: '}' prefix")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
