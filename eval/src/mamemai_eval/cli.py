# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""`mamemai-eval` command line."""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from pathlib import Path

from .adapters import corpus_files
from .adapters import postgres_eval as pg
from .adapters.claude_cli import ClaudeCliReferenceLLM
from .domain.model import EvalLanguage, Ledger, SpeechArchetype, SyntheticUserProfile
from .usecases.corpus import CorpusError, FreezeCorpus, ImportCorpus, verify
from .usecases.generate_ledger import BRIEFS, GenerateProfileAndLedger, LedgerGenerationError
from .usecases.generate_probes import GenerateProbes
from .usecases.reference_json import ReferenceJsonError
from .usecases.ports import FrozenCorpusError, QuotaExhausted
from .usecases.reference import CachingReferenceLLM

DRAFTS = Path(__file__).resolve().parents[2] / "drafts"
REFERENCE_MODEL = os.environ.get("MAMEMAI_EVAL_REFERENCE_MODEL", "claude-sonnet-5-5")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mamemai-eval")
    sub = parser.add_subparsers(dest="cmd", required=True)
    db = sub.add_parser("db", help="database administration").add_subparsers(dest="db_cmd", required=True)
    db.add_parser("migrate", help="create/upgrade schema `eval` in the eval database")
    gen = sub.add_parser("generate", help="generate ground truth with the Reference LLM").add_subparsers(
        dest="gen_cmd", required=True)
    led = gen.add_parser("ledger", help="one Synthetic User Profile + its Ledger")
    led.add_argument("--language", choices=["en", "fr"], required=True)
    led.add_argument("--archetype", choices=[a.value for a in SpeechArchetype], required=True)
    led.add_argument("--seed", type=int, default=1, help="a new seed makes a new person (needed for a new Corpus version)")
    led.add_argument("--force", action="store_true", help="delete and regenerate an existing (draft) profile")
    prb = gen.add_parser("probes", help="Probes for existing Ledgers (all four profiles unless restricted)")
    prb.add_argument("--language", choices=["en", "fr"])
    prb.add_argument("--archetype", choices=[a.value for a in SpeechArchetype])
    prb.add_argument("--seed", type=int, default=1)
    cor = sub.add_parser("corpus", help="frozen, versioned Corpora").add_subparsers(dest="corpus_cmd", required=True)
    frz = cor.add_parser("freeze", help="freeze every Profile in the database into an immutable Corpus and export it")
    frz.add_argument("--version", required=True)
    frz.add_argument("--notes", default="")
    ver = cor.add_parser("verify", help="check the database still matches a frozen Corpus digest")
    ver.add_argument("--version", required=True)
    imp = cor.add_parser("import", help="rebuild a Corpus from an exported directory (another machine / wiped DB)")
    imp.add_argument("directory", type=Path)
    cor.add_parser("list", help="list frozen Corpora")
    args = parser.parse_args(argv)

    if args.cmd == "db" and args.db_cmd == "migrate":
        with pg.connect() as conn:
            applied = pg.migrate(conn)
        print(f"applied {', '.join(applied)} to {pg.eval_dsn()}")
        return 0
    if args.cmd == "generate" and args.gen_cmd == "ledger":
        return _generate_ledger(EvalLanguage(args.language), SpeechArchetype(args.archetype), args.force, args.seed)
    if args.cmd == "corpus":
        return _corpus(args)
    if args.cmd == "generate" and args.gen_cmd == "probes":
        return _generate_probes(args.language, args.archetype, args.seed)
    return 2


def _corpus(args: argparse.Namespace) -> int:
    with pg.connect() as conn:
        repo = pg.PgEvalRepository(conn)
        try:
            if args.corpus_cmd == "list":
                for c in repo.list_corpora():
                    print(f"{c.version}  {c.digest[:12]}  {c.created_at:%Y-%m-%d}  {dict(c.counts)}  {c.notes}")
            elif args.corpus_cmd == "verify":
                ok = verify(repo, args.version)
                print(f"{args.version}: {'OK — database matches the frozen digest' if ok else 'MISMATCH'}")
                return 0 if ok else 1
            elif args.corpus_cmd == "import":
                manifest, files = corpus_files.read_corpus(args.directory)
                c = ImportCorpus(repo).execute(manifest, files)
                print(f"imported {c.version} ({c.digest[:12]}), verified={verify(repo, c.version)}")
            else:  # freeze
                corpus, files = FreezeCorpus(repo).execute(args.version, repo.list_profile_ids(), REFERENCE_MODEL, args.notes)
                target = corpus_files.write_corpus(corpus, files)
                print(f"frozen {corpus.version} ({corpus.digest[:12]}): {dict(corpus.counts)}\nexported to {target}")
        except (CorpusError, FileExistsError) as e:
            print(f"failed: {e}")
            return 1
    return 0


def _generate_probes(language: str | None, archetype: str | None, seed: int = 1) -> int:
    with pg.connect() as conn:
        repo = pg.PgEvalRepository(conn)
        llm = CachingReferenceLLM(ClaudeCliReferenceLLM(), pg.PgReferenceLLMCache(conn))
        for (lang, arch), base in BRIEFS.items():
            brief = dataclasses.replace(base, seed=seed)
            if (language and lang.value != language) or (archetype and arch.value != archetype):
                continue
            if not repo.profile_exists(brief.profile_id):
                print(f"{lang.value}/{arch.value}: no ledger yet, skipped")
                continue
            profile, ledger = repo.get_profile(brief.profile_id), repo.get_ledger(brief.profile_id)
            try:
                probes = GenerateProbes(llm, REFERENCE_MODEL).execute(profile, ledger)
            except QuotaExhausted as e:
                print(f"Reference LLM quota exhausted; re-run later, finished calls are cached.\n{e}")
                return 3
            except ReferenceJsonError as e:
                print(f"{lang.value}/{arch.value}: generation failed: {e}")
                return 4
            try:
                repo.save_probes(profile.profile_id, probes)
            except FrozenCorpusError:
                print(f"{lang.value}/{arch.value}: profile is in a frozen corpus; use --seed N for a new person")
                return 1
            kinds = {}
            for p in probes:
                kinds[p.kind.value] = kinds.get(p.kind.value, 0) + 1
            print(f"{lang.value}/{arch.value} ({profile.display_name}): {len(probes)} probes {kinds}\n"
                  f"  review file: {write_probe_review(profile, ledger, probes)}")
    return 0


def write_probe_review(profile: SyntheticUserProfile, ledger: Ledger, probes: list) -> Path:
    DRAFTS.mkdir(exist_ok=True)
    lines = [f"# Probes — {profile.display_name} ({profile.language.value} / {profile.archetype.value})", ""]
    for kind in dict.fromkeys(p.kind for p in probes):
        lines += [f"## {kind.value}", ""]
        for p in (p for p in probes if p.kind is kind):
            exp = "; ".join(f"`{i}` {ledger.get(i).text}" for i in p.expected_fact_ids) or "*(nothing — must abstain)*"
            neg = "; ".join(f"`{i}`" for i in p.hard_negative_fact_ids)
            lines.append(f"- [{p.language.value}] **{p.text}**  \n  → {exp}" + (f"  \n  ✗ not: {neg}" if neg else ""))
        lines.append("")
    path = DRAFTS / f"probes_{profile.language.value}_{profile.archetype.value}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _generate_ledger(language: EvalLanguage, archetype: SpeechArchetype, force: bool, seed: int = 1) -> int:
    brief = dataclasses.replace(BRIEFS[(language, archetype)], seed=seed)
    with pg.connect() as conn:
        repo = pg.PgEvalRepository(conn)
        if repo.profile_exists(brief.profile_id):
            if not force:
                print(f"profile {language.value}/{archetype.value} already exists; use --force to regenerate")
                return 1
            try:
                repo.delete_profile(brief.profile_id)
            except FrozenCorpusError:
                print("that profile belongs to a frozen corpus and cannot be regenerated; use --seed N for a new person")
                return 1
        llm = CachingReferenceLLM(ClaudeCliReferenceLLM(), pg.PgReferenceLLMCache(conn))
        try:
            profile, ledger = GenerateProfileAndLedger(llm, REFERENCE_MODEL).execute(brief)
        except QuotaExhausted as e:
            print(f"Reference LLM quota exhausted; re-run later, finished calls are cached.\n{e}")
            return 3
        except LedgerGenerationError as e:
            print(f"generation failed: {e}")
            return 4
        repo.save_profile_with_ledger(profile, ledger)
    path = write_review(profile, ledger)
    print(f"saved {profile.display_name} ({language.value}/{archetype.value}, {len(ledger.facts)} facts, "
          f"model {REFERENCE_MODEL})\nreview file: {path}")
    return 0


def write_review(profile: SyntheticUserProfile, ledger: Ledger) -> Path:
    """Human-readable draft for the user's review, grouped by Scenario."""
    DRAFTS.mkdir(exist_ok=True)
    lines = [f"# {profile.display_name} — {profile.language.value} / {profile.archetype.value}", "",
             profile.biography, "", f"*Speech:* {profile.speech_style}", ""]
    for i, d in enumerate(ledger.scenario_dates):
        lines += [f"## Scenario {i} — {d.isoformat()}", ""]
        for f in (f for f in ledger.facts if f.revealed_in == i):
            when = f" ({f.happened_on.isoformat()})" if f.happened_on else ""
            rev = f"  ⟵ replaces {f.supersedes}" if f.supersedes else ""
            lines.append(f"- `{f.fact_id}` **{f.kind.value}** [{f.domain}]{when} {f.text}{rev}")
        lines.append("")
    path = DRAFTS / f"ledger_{profile.language.value}_{profile.archetype.value}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


if __name__ == "__main__":
    sys.exit(main())
