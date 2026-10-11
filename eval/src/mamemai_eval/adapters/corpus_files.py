# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Exported Corpora: eval/corpora/<version>/{MANIFEST.json, *.jsonl} — reviewed in git."""
from __future__ import annotations

import json
from pathlib import Path

from ..domain.model import Corpus
from ..usecases.corpus import FILES

CORPORA_DIR = Path(__file__).resolve().parents[3] / "corpora"


def write_corpus(corpus: Corpus, files: dict[str, bytes], root: Path = CORPORA_DIR) -> Path:
    target = root / corpus.version
    if target.exists():
        raise FileExistsError(f"{target} exists; a frozen corpus export is never overwritten")
    target.mkdir(parents=True)
    for name, data in files.items():
        (target / name).write_bytes(data)
    manifest = {
        "version": corpus.version, "digest": corpus.digest, "created_at": corpus.created_at.isoformat(),
        "generated_with": corpus.generated_with, "profile_ids": [str(p) for p in corpus.profile_ids],
        "counts": dict(corpus.counts), "notes": corpus.notes, "files": list(FILES),
    }
    (target / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def read_corpus(directory: Path) -> tuple[dict, dict[str, bytes]]:
    manifest = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))
    return manifest, {name: (directory / name).read_bytes() for name in FILES}
