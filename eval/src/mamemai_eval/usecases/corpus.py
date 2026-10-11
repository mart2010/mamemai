# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Freezing, verifying and importing Corpora (EV-INV-5). The canonical form is four sorted JSONL
files; the digest is the sha256 over them, so the DB, the exported files and a re-import on
another machine can all be proven identical."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from ..domain.model import (
    Corpus, EvalLanguage, FactKind, Ledger, LedgerFact, Probe, ProbeKind, SpeechArchetype, SyntheticUserProfile,
)
from .ports import EvalRepository

FILES = ("profiles.jsonl", "ledger_facts.jsonl", "scenario_dates.jsonl", "probes.jsonl")
_VERSION = re.compile(r"^v\d+$")


class CorpusError(Exception):
    pass


@dataclass(frozen=True)
class ProfileContent:
    profile: SyntheticUserProfile
    ledger: Ledger
    probes: tuple[Probe, ...]


# -- canonical serialisation -----------------------------------------------------------------

def _line(d: dict) -> bytes:
    return (json.dumps(d, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def to_files(contents: list[ProfileContent]) -> dict[str, bytes]:
    contents = sorted(contents, key=lambda c: str(c.profile.profile_id))
    rows: dict[str, list[dict]] = {name: [] for name in FILES}
    for c in contents:
        pid = str(c.profile.profile_id)
        p = c.profile
        rows["profiles.jsonl"].append({
            "profile_id": pid, "language": p.language.value, "display_name": p.display_name, "seed": p.seed,
            "biography": p.biography, "archetype": p.archetype.value, "speech_style": p.speech_style})
        for f in sorted(c.ledger.facts, key=lambda f: f.fact_id):
            rows["ledger_facts.jsonl"].append({
                "profile_id": pid, "fact_id": f.fact_id, "kind": f.kind.value, "text": f.text, "revealed_in": f.revealed_in,
                "domain": f.domain, "happened_on": f.happened_on.isoformat() if f.happened_on else None,
                "paraphrases": list(f.paraphrases), "supersedes": f.supersedes, "key_terms": list(f.key_terms)})
        for i, d in enumerate(c.ledger.scenario_dates):
            rows["scenario_dates.jsonl"].append({"profile_id": pid, "idx": i, "on_date": d.isoformat()})
        for q in sorted(c.probes, key=lambda q: q.probe_id):
            rows["probes.jsonl"].append({
                "profile_id": pid, "probe_id": q.probe_id, "kind": q.kind.value, "language": q.language.value,
                "text": q.text, "expected": list(q.expected_fact_ids), "hard_negatives": list(q.hard_negative_fact_ids)})
    return {name: b"".join(_line(r) for r in rs) for name, rs in rows.items()}


def digest_of(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for name in FILES:
        h.update(name.encode() + b"\0" + files[name] + b"\0")
    return h.hexdigest()


def from_files(files: dict[str, bytes]) -> list[ProfileContent]:
    def rows(name: str) -> list[dict]:
        return [json.loads(line) for line in files[name].decode("utf-8").splitlines() if line.strip()]

    out = []
    for p in rows("profiles.jsonl"):
        pid = UUID(p["profile_id"])
        facts = tuple(LedgerFact(
            f["fact_id"], FactKind(f["kind"]), f["text"], f["revealed_in"], f["domain"],
            date.fromisoformat(f["happened_on"]) if f["happened_on"] else None, tuple(f["paraphrases"]), f["supersedes"],
            tuple(f["key_terms"])) for f in rows("ledger_facts.jsonl") if f["profile_id"] == p["profile_id"])
        dates = tuple(date.fromisoformat(d["on_date"]) for d in sorted(
            (d for d in rows("scenario_dates.jsonl") if d["profile_id"] == p["profile_id"]), key=lambda d: d["idx"]))
        probes = tuple(Probe(q["probe_id"], ProbeKind(q["kind"]), EvalLanguage(q["language"]), q["text"],
                             tuple(q["expected"]), tuple(q["hard_negatives"]))
                       for q in rows("probes.jsonl") if q["profile_id"] == p["profile_id"])
        out.append(ProfileContent(
            SyntheticUserProfile(pid, EvalLanguage(p["language"]), p["display_name"], p["seed"], p["biography"],
                                 SpeechArchetype(p["archetype"]), p["speech_style"]),
            Ledger(pid, facts, dates), probes))
    return out


# -- validation ------------------------------------------------------------------------------

def validate_content(c: ProfileContent) -> list[str]:
    """Problems that must block a freeze (an empty list means freezable)."""
    name = f"{c.profile.language.value}/{c.profile.archetype.value}"
    problems = []
    ids = {f.fact_id for f in c.ledger.facts}
    if not c.probes:
        problems.append(f"{name}: no probes")
    kinds = {q.kind for q in c.probes}
    for needed in ProbeKind:
        if needed not in kinds:
            problems.append(f"{name}: no {needed.value} probes")
    texts = [q.text.casefold() for q in c.probes]
    if len(set(texts)) != len(texts):
        problems.append(f"{name}: duplicate probe texts")
    for q in c.probes:
        for fid in (*q.expected_fact_ids, *q.hard_negative_fact_ids):
            if fid not in ids:
                problems.append(f"{name}/{q.probe_id}: unknown fact {fid}")
        same = q.language is c.profile.language
        if (q.kind is ProbeKind.CROSS_LINGUAL) == same:
            problems.append(f"{name}/{q.probe_id}: {q.kind.value} probe in language {q.language.value}")
        if q.kind is ProbeKind.UPDATE and not c.ledger.get(q.expected_fact_ids[0]).supersedes:
            problems.append(f"{name}/{q.probe_id}: update probe targets a fact that revises nothing")
    return problems


def _counts(contents: list[ProfileContent]) -> dict[str, int]:
    counts = {"profiles": len(contents), "facts": sum(len(c.ledger.facts) for c in contents),
              "probes": sum(len(c.probes) for c in contents)}
    for k in ProbeKind:
        counts[f"probes.{k.value}"] = sum(1 for c in contents for q in c.probes if q.kind is k)
    return counts


# -- use cases -------------------------------------------------------------------------------

def load_contents(repo: EvalRepository, profile_ids: list[UUID]) -> list[ProfileContent]:
    return [ProfileContent(repo.get_profile(pid), repo.get_ledger(pid), tuple(repo.get_probes(pid))) for pid in profile_ids]


class FreezeCorpus:
    def __init__(self, repo: EvalRepository) -> None:
        self._repo = repo

    def execute(self, version: str, profile_ids: list[UUID], generated_with: str, notes: str = "",
                now: datetime | None = None) -> tuple[Corpus, dict[str, bytes]]:
        if not _VERSION.match(version):
            raise CorpusError(f"version must look like v1, v2 …, got {version!r}")
        try:
            self._repo.get_corpus(version)
        except KeyError:
            pass
        else:
            raise CorpusError(f"corpus {version} already exists; frozen means immutable — make a new version")
        if not profile_ids:
            raise CorpusError("no profiles to freeze")
        contents = load_contents(self._repo, profile_ids)
        problems = [p for c in contents for p in validate_content(c)]
        already = [str(c.profile.profile_id) for c in contents if self._repo.is_frozen(c.profile.profile_id)]
        if already:
            problems.append(f"profiles already in a frozen corpus: {already} (copy them to freeze a new version)")
        if problems:
            raise CorpusError("; ".join(problems))
        files = to_files(contents)
        corpus = Corpus(version, digest_of(files), now or datetime.now(UTC), generated_with,
                        tuple(c.profile.profile_id for c in sorted(contents, key=lambda c: str(c.profile.profile_id))),
                        _counts(contents), notes)
        self._repo.save_corpus(corpus)
        return corpus, files


def verify(repo: EvalRepository, version: str) -> bool:
    """Does the database still hold exactly what was frozen? (Triggers should make `False` impossible.)"""
    corpus = repo.get_corpus(version)
    return digest_of(to_files(load_contents(repo, list(corpus.profile_ids)))) == corpus.digest


class ImportCorpus:
    """Rebuild a Corpus from its exported files (another machine, a wiped database)."""

    def __init__(self, repo: EvalRepository) -> None:
        self._repo = repo

    def execute(self, manifest: dict, files: dict[str, bytes]) -> Corpus:
        if digest_of(files) != manifest["digest"]:
            raise CorpusError("files do not match the manifest digest — corrupted or edited after the freeze")
        try:
            existing = self._repo.get_corpus(manifest["version"])
        except KeyError:
            existing = None
        if existing is not None:
            if existing.digest != manifest["digest"]:
                raise CorpusError(f"{manifest['version']} exists with a different digest")
            return existing
        contents = from_files(files)
        problems = [p for c in contents for p in validate_content(c)]
        if problems:
            raise CorpusError("; ".join(problems))
        for c in contents:
            if self._repo.profile_exists(c.profile.profile_id):
                raise CorpusError(f"profile {c.profile.profile_id} already exists outside this corpus")
        for c in contents:
            self._repo.save_profile_with_ledger(c.profile, c.ledger)
            self._repo.save_probes(c.profile.profile_id, list(c.probes))
        corpus = Corpus(manifest["version"], manifest["digest"], datetime.fromisoformat(manifest["created_at"]),
                        manifest["generated_with"], tuple(UUID(p) for p in manifest["profile_ids"]),
                        manifest["counts"], manifest.get("notes", ""))
        self._repo.save_corpus(corpus)
        return corpus
