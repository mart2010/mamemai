# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Eval-side client of the SUT worker: implements the `RetrievalSut` port by driving
`sut_worker.py` under the server's own interpreter, against a Scratch Schema (EV-INV-6).

This module never imports `mamemai_server`; the worker does, in its own process."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from ..domain.retrieval import Hit
from ..usecases.retrieval import MemoryRecord, RetrievalQuery, SutDescription
from . import postgres_eval as pg

_ROOT = Path(__file__).resolve().parents[4]
SERVER_MIGRATION = _ROOT / "server" / "migrations" / "001_initial_schema.sql"
_WORKER = Path(__file__).with_name("sut_worker.py")
DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-large"


class SutWorkerError(RuntimeError):
    pass


def server_python() -> Path:
    override = os.environ.get("MAMEMAI_SERVER_PYTHON")
    if override:
        return Path(override)
    posix, windows = _ROOT / "server" / ".venv" / "bin" / "python", _ROOT / "server" / ".venv" / "Scripts" / "python.exe"
    return posix if posix.exists() else windows


class ServerBridgeRetrievalSut:
    """Context manager: Scratch Schema + worker on enter, both gone on exit (kept with `keep_scratch`)."""

    def __init__(self, *, model: str | None = None, device: str | None = None, keep_scratch: bool = False) -> None:
        self._model = model or os.environ.get("MAMEMAI_EVAL_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
        self._device = device or os.environ.get("MAMEMAI_EVAL_EMBEDDING_DEVICE") or None
        self._keep = keep_scratch
        self._schema = pg.scratch_schema_name(uuid4())
        self._admin = None
        self._proc: subprocess.Popen | None = None
        self._description: SutDescription | None = None

    def __enter__(self) -> "ServerBridgeRetrievalSut":
        self._admin = pg.connect()
        pg.create_scratch_schema(self._admin, self._schema, SERVER_MIGRATION)
        try:
            self._proc = subprocess.Popen(
                [str(server_python()), "-I", str(_WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=sys.stderr, text=True, encoding="utf-8", bufsize=1)
            info = self._call("init", dsn=pg.eval_dsn(), schema=self._schema, model=self._model, device=self._device)
            self._description = SutDescription(info["facts"], float(info["min_recall_similarity"]))
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        if self._proc is not None:
            try:
                self._call("close")
            except (SutWorkerError, OSError):
                self._proc.kill()
            self._proc.wait(timeout=30)
            self._proc = None
        if self._admin is not None:
            if not self._keep:
                pg.drop_scratch_schema(self._admin, self._schema)
            else:
                print(f"scratch schema kept: {self._schema}", file=sys.stderr)
            self._admin.close()
            self._admin = None

    def _call(self, op: str, **payload: object) -> dict:
        assert self._proc is not None and self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(json.dumps({"op": op, **payload}) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise SutWorkerError(f"SUT worker died during {op!r} (exit {self._proc.poll()}); see stderr above")
        reply = json.loads(line)
        if not reply.pop("ok"):
            raise SutWorkerError(f"SUT worker failed on {op!r}: {reply['error']}")
        return reply

    # --- RetrievalSut ---------------------------------------------------------------------------

    def describe(self) -> SutDescription:
        assert self._description is not None
        return self._description

    @staticmethod
    def _wire(records: Sequence[MemoryRecord]) -> list[dict]:
        return [{"fact_id": r.fact_id, "memory_type": r.memory_type, "text": r.text, "name": r.name,
                 "happened_on": r.happened_on.isoformat() if r.happened_on else None, "language": r.language.value,
                 "name_in_embedding": r.name_in_embedding} for r in records]

    def index(self, records: Sequence[MemoryRecord]) -> None:
        self._call("index", records=self._wire(records))

    def embed_passages(self, records: Sequence[MemoryRecord]) -> list[list[float]]:
        return self._call("embed_passages", records=self._wire(records))["vectors"]

    def search(self, queries: Sequence[RetrievalQuery], top_n: int, path: str = "planner",
               ef_search: int | None = None) -> list[list[Hit]]:
        reply = self._call("search", queries=[{"query_id": q.query_id, "text": q.text} for q in queries], top_n=top_n,
                           path=path, ef_search=ef_search)
        return [[Hit(fid, sim) for fid, sim in hits] for hits in reply["hits"]]

    def plan(self, path: str) -> str:
        return self._call("plan", path=path)["plan"]
