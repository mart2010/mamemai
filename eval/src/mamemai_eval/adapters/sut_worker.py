# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""SUT worker: runs under `server/.venv`'s interpreter, NOT the eval one (PLAN: bridge decision).

A standalone script — it must not import `mamemai_eval` (not installed in that venv). It speaks
line-delimited JSON on stdin/stdout and is the only place in `eval/` that imports `mamemai_server`
(EV-INV-1 allows eval → server; the server never imports this).

Requests:  {"op": "init", "dsn", "schema", "model", "device"|null}
           {"op": "index", "records": [{fact_id, memory_type, text, name, happened_on, language}]}
           {"op": "search", "queries": [{query_id, text}], "top_n": int}
           {"op": "close"}
Replies:   {"ok": true, ...} | {"ok": false, "error": str}

stdout is the protocol channel, so fd 1 is re-pointed at stderr before any library can print to it."""
from __future__ import annotations

import json
import os
import sys
import traceback
import uuid
from datetime import UTC, datetime

_PROTO = os.fdopen(os.dup(1), "w", encoding="utf-8")
os.dup2(2, 1)


class Worker:
    def __init__(self) -> None:
        self.conn = None
        self.repo = None
        self.embedder = None
        self.persona_id: uuid.UUID | None = None
        self.conversation_id: int | None = None
        self.key_of: dict[tuple[str, int], str] = {}

    def init(self, dsn: str, schema: str, model: str, device: str | None) -> dict:
        from mamemai_server.domain.model import AssistantPersona, Language
        from mamemai_server.infrastructure.embedding import SentenceTransformerEmbeddingService
        from mamemai_server.infrastructure.postgres import PSMemoryRepository, PSPersonaRepository, connect
        from mamemai_server.infrastructure.recall_gate import MIN_RECALL_SIMILARITY

        self.conn = connect(dsn, schema)
        self.repo = PSMemoryRepository(self.conn)
        self.embedder = SentenceTransformerEmbeddingService(model, device)
        now = datetime.now(UTC)
        self.persona_id = uuid.uuid4()
        PSPersonaRepository(self.conn).save(AssistantPersona(
            id=self.persona_id, name="eval", system_prompt="eval", languages=[], response_language=Language("en"),
            voices={"default": "af_heart"}, is_system=False, created_at=now, updated_at=now))
        self.conversation_id = self.conn.execute(
            "INSERT INTO conversations (started_at, persona_id) VALUES (%s, %s) RETURNING id",
            (now, self.persona_id)).fetchone()[0]
        index_type = self.conn.execute(
            "SELECT string_agg(DISTINCT am.amname, ',') FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_am am ON am.oid = c.relam WHERE c.relname = 'concepts_embedding_hnsw'").fetchone()[0]
        device_used = str(getattr(self.embedder._model, "device", "?"))
        return {"min_recall_similarity": MIN_RECALL_SIMILARITY,
                "facts": {"embedding_model": model, "embedding_device": device_used,
                          "vector_index": index_type or "none", "metric": "cosine"}}

    @staticmethod
    def _passage_text(r: dict) -> str:
        """The exact string the product embeds for this record (TR-601)."""
        from mamemai_server.services.upsert import embeddable_text

        if r["memory_type"] == "episode" or not r.get("name_in_embedding", True):
            return r["text"]
        return embeddable_text(r["name"], r["text"])

    def embed_passages(self, records: list[dict]) -> dict:
        return {"vectors": [self.embedder.embed_passage(self._passage_text(r)) for r in records]}

    def index(self, records: list[dict]) -> dict:
        from mamemai_server.domain.model import Concept, Episode, Language

        for r in records:
            if r["memory_type"] == "episode":
                happened = datetime.fromisoformat(r["happened_on"]).replace(hour=12, tzinfo=UTC)
                item_id = self.repo.upsert_episode(Episode(
                    id=None, summary=r["text"], happened_at=happened, origin_conversation_id=self.conversation_id,
                    embedding=self.embedder.embed_passage(self._passage_text(r))))
            else:
                item_id = self.repo.upsert_concept(Concept(
                    id=None, persona_id=self.persona_id, name=r["name"], description=r["text"],
                    language=Language(r["language"]),
                    embedding=self.embedder.embed_passage(self._passage_text(r))))
            self.key_of[(r["memory_type"], item_id)] = r["fact_id"]
        # Fresh tables have no statistics, so until autovacuum analyzes them the planner's choice between a
        # scan and the index is arbitrary — and changes mid-run. Analyze now so the access path is deterministic.
        self.conn.execute("ANALYZE")
        return {"indexed": len(records)}

    def plan(self, path: str) -> dict:
        """The planner's choice for the concept search under `path`, as evidence of what a search really does."""
        self._set_path(path)
        try:
            rows = self.conn.execute(
                "EXPLAIN SELECT id FROM concepts WHERE persona_id = %s ORDER BY embedding <=> %s LIMIT 10",
                (self.persona_id, self.embedder_zero())).fetchall()  # same shape as PSMemoryRepository.search
        finally:
            self._set_path("planner")
        import re

        lines = [re.sub(r"'\[[^\]]*\]'::vector", "<vec>", r[0]).strip() for r in rows]
        return {"plan": " | ".join(re.sub(r"\s*\(cost=.*$", "", ln) for ln in lines)}

    def embedder_zero(self):
        import numpy as np

        v = np.zeros(1024, dtype=np.float32)
        v[0] = 1.0
        return v

    def _set_path(self, path: str) -> None:
        """planner: Postgres decides (a tiny table is scanned, a big one uses the index, like production).
        index: forbid sequential scans so the HNSW index is really exercised. exact: forbid the index."""
        if path not in ("planner", "index", "exact"):
            raise ValueError(f"unknown access path {path!r}")
        self.conn.execute(f"SET enable_indexscan = {'off' if path == 'exact' else 'on'}")
        self.conn.execute(f"SET enable_seqscan = {'off' if path == 'index' else 'on'}")

    def search(self, queries: list[dict], top_n: int, path: str = "planner", ef_search: int | None = None) -> dict:
        from mamemai_server.domain.model import Concept, MemoryType

        self._set_path(path)
        if ef_search is not None:
            self.conn.execute(f"SET hnsw.ef_search = {int(ef_search)}")
        try:
            return self._search(queries, top_n, Concept, MemoryType)
        finally:
            self._set_path("planner")
            self.conn.execute("RESET hnsw.ef_search")

    def _search(self, queries: list[dict], top_n: int, Concept, MemoryType) -> dict:  # noqa: N803
        out = []
        for q in queries:
            rows = self.repo.search(self.embedder.embed_query(q["text"]), (MemoryType.EPISODE, MemoryType.CONCEPT),
                                    top_n, self.persona_id)
            hits = []
            for similarity, item in rows:  # already 1 − cosine distance (TR-509)
                kind = "concept" if isinstance(item, Concept) else "episode"
                hits.append([self.key_of[(kind, item.id)], float(similarity)])
            out.append(hits)
        return {"hits": out}

    def close(self) -> dict:
        if self.conn is not None:
            self.conn.close()
        return {}


def main() -> int:
    worker = Worker()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            op = req.pop("op")
            reply = {"ok": True, **getattr(worker, op)(**req)}
        except Exception as e:  # noqa: BLE001 — every failure must reach the client as a reply
            traceback.print_exc()
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            op = None
        _PROTO.write(json.dumps(reply) + "\n")
        _PROTO.flush()
        if op == "close":
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
