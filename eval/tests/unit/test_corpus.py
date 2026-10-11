# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: EV-INV-5 — frozen Corpora: validation, canonical digest, immutability, re-import."""
import dataclasses
from datetime import UTC, datetime

import pytest

from mamemai_eval.domain.model import EvalLanguage, Probe, ProbeKind, SpeechArchetype
from mamemai_eval.usecases.corpus import (
    CorpusError, FreezeCorpus, ImportCorpus, from_files, digest_of, to_files, validate_content, verify,
)
from mamemai_eval.usecases.ports import FrozenCorpusError
from tests.fakes import FakeEvalRepository
from tests.helpers import sample_content

NOW = datetime(2026, 10, 11, tzinfo=UTC)


def seeded_repo(*contents):
    repo = FakeEvalRepository()
    for c in contents:
        repo.save_profile_with_ledger(c.profile, c.ledger)
        repo.save_probes(c.profile.profile_id, list(c.probes))
    return repo


def test_round_trip_through_files_is_lossless_and_digest_is_stable():
    c = sample_content()
    files = to_files([c])
    [back] = from_files(files)
    assert to_files([back]) == files  # canonical form survives a round trip byte for byte
    assert back.profile == c.profile and set(back.probes) == set(c.probes) and set(back.ledger.facts) == set(c.ledger.facts)
    assert back.ledger.scenario_dates == c.ledger.scenario_dates
    assert digest_of(files) == digest_of(to_files([c]))
    changed = dataclasses.replace(c, probes=c.probes[:-1] + (dataclasses.replace(c.probes[-1], text="Changed ?"),))
    assert digest_of(to_files([changed])) != digest_of(files)


def test_digest_does_not_depend_on_profile_order():
    a, b = sample_content(), sample_content(EvalLanguage.EN, SpeechArchetype.FLUENT)
    assert digest_of(to_files([a, b])) == digest_of(to_files([b, a]))


@pytest.mark.parametrize("mutate, expect", [
    (lambda c: dataclasses.replace(c, probes=()), "no probes"),
    (lambda c: dataclasses.replace(c, probes=c.probes[:-1]), "no cross_lingual"),
    (lambda c: dataclasses.replace(c, probes=c.probes + (Probe("x", ProbeKind.SINGLE_FACT, EvalLanguage.FR, "q", ("zzz",)),)),
     "unknown fact"),
    (lambda c: dataclasses.replace(c, probes=c.probes[:5] + (dataclasses.replace(c.probes[5], language=EvalLanguage.FR),)),
     "cross_lingual probe in language fr"),
    (lambda c: dataclasses.replace(c, probes=(dataclasses.replace(c.probes[2], expected_fact_ids=("f01",)),) + c.probes[:2] + c.probes[3:]),
     "revises nothing"),
])
def test_validation_blocks_a_bad_freeze(mutate, expect):
    problems = validate_content(mutate(sample_content()))
    assert any(expect in p for p in problems), problems


def test_freeze_stores_the_corpus_and_makes_profiles_immutable():
    c = sample_content()
    repo = seeded_repo(c)
    corpus, files = FreezeCorpus(repo).execute("v1", [c.profile.profile_id], "claude-x", "reviewed", NOW)
    assert corpus.digest == digest_of(files) and corpus.counts["probes"] == 6 and corpus.counts["facts"] == 4
    assert repo.is_frozen(c.profile.profile_id) and verify(repo, "v1")
    with pytest.raises(FrozenCorpusError):
        repo.delete_profile(c.profile.profile_id)
    with pytest.raises(FrozenCorpusError):
        repo.save_probes(c.profile.profile_id, [])


def test_a_version_is_never_overwritten_and_names_are_checked():
    c = sample_content()
    repo = seeded_repo(c)
    FreezeCorpus(repo).execute("v1", [c.profile.profile_id], "m", now=NOW)
    with pytest.raises(CorpusError, match="already exists"):
        FreezeCorpus(repo).execute("v1", [c.profile.profile_id], "m", now=NOW)
    with pytest.raises(CorpusError, match="version must look like"):
        FreezeCorpus(repo).execute("final", [c.profile.profile_id], "m", now=NOW)
    with pytest.raises(CorpusError, match="already in a frozen corpus"):
        FreezeCorpus(repo).execute("v2", [c.profile.profile_id], "m", now=NOW)


def test_import_rebuilds_an_identical_corpus_elsewhere_and_rejects_tampering():
    c = sample_content()
    src = seeded_repo(c)
    corpus, files = FreezeCorpus(src).execute("v1", [c.profile.profile_id], "m", "n", NOW)
    manifest = {"version": "v1", "digest": corpus.digest, "created_at": NOW.isoformat(), "generated_with": "m",
                "profile_ids": [str(c.profile.profile_id)], "counts": dict(corpus.counts), "notes": "n"}
    dst = FakeEvalRepository()
    imported = ImportCorpus(dst).execute(manifest, files)
    assert imported.digest == corpus.digest and verify(dst, "v1") and dst.is_frozen(c.profile.profile_id)
    assert ImportCorpus(dst).execute(manifest, files) == imported  # idempotent
    bad = dict(files, **{"probes.jsonl": files["probes.jsonl"].replace(b"eclipse", b"ECLIPSE")})
    with pytest.raises(CorpusError, match="manifest digest"):
        ImportCorpus(FakeEvalRepository()).execute(manifest, bad)
