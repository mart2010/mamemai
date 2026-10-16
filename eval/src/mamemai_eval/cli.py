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
    run = sub.add_parser("run", help="execute a Suite against the System Under Test").add_subparsers(
        dest="run_cmd", required=True)
    rr = run.add_parser("retrieval", help="semantic search over a frozen Corpus (real embedding + pgvector)")
    rr.add_argument("--corpus", required=True, help="frozen Corpus version, e.g. v1")
    rr.add_argument("--language", choices=["en", "fr"], help="default: both, as separate Runs")
    rr.add_argument("--repeat", type=int, default=1, help="repeat N times to measure the Noise Floor")
    rr.add_argument("--keep-scratch", action="store_true", help="keep the Scratch Schema (debugging)")
    rr.add_argument("--device", help="embedding device (cpu/cuda); default: the server's auto-detection")
    sw = sub.add_parser("sweep", help="Threshold Sweep: re-score similarities under other cut-offs / thresholds").add_subparsers(
        dest="sweep_cmd", required=True)
    sr = sw.add_parser("retrieval", help="recall cut-off (FR-309) and merge/disambiguate thresholds (TR-602)")
    sr.add_argument("--corpus", required=True)
    sr.add_argument("--language", choices=["en", "fr"])
    sr.add_argument("--renderings", default="first-key-term,bare",
                    help="concept-naming rules to compare (sensitivity check): first-key-term, bare")
    sr.add_argument("--device")
    ra = run.add_parser("ann", help="HNSW index vs exact scan (S1.8), with an ef_search=1 positive control")
    ra.add_argument("--corpus", required=True)
    ra.add_argument("--device")
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
    if args.cmd == "run" and args.run_cmd == "ann":
        return _run_ann(args)
    if args.cmd == "run":
        return _run_retrieval(args)
    if args.cmd == "sweep":
        return _sweep_retrieval(args)
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


def _run_retrieval(args: argparse.Namespace) -> int:
    from .adapters.server_bridge import ServerBridgeRetrievalSut, SutWorkerError
    from .usecases.retrieval import ExecuteRetrievalSuite
    from .usecases.report import format_noise_floor, format_run

    with pg.connect() as conn:
        repo = pg.PgEvalRepository(conn)
        if not verify(repo, args.corpus):
            print(f"corpus {args.corpus} is missing or does not match its digest (EV-INV-5); not running")
            return 1
        corpus = repo.get_corpus(args.corpus)
        by_language: dict[EvalLanguage, list] = {}
        for pid in corpus.profile_ids:
            profile = repo.get_profile(pid)
            by_language.setdefault(profile.language, []).append((profile, repo.get_ledger(pid), repo.get_probes(pid)))
        suite = ExecuteRetrievalSuite(lambda: ServerBridgeRetrievalSut(device=args.device, keep_scratch=args.keep_scratch))
        for language, profiles in sorted(by_language.items()):
            if args.language and language.value != args.language:
                continue
            runs = []
            for i in range(args.repeat):
                try:
                    run, _ = suite.run(language=language, corpus_version=args.corpus, profiles=profiles)
                except SutWorkerError as e:
                    print(f"{language.value}: SUT failed: {e}")
                    return 5
                repo.save_run(run)
                runs.append(run)
                print(format_run(run, repeat=f"{i + 1}/{args.repeat}"))
            if len(runs) > 1:
                print(format_noise_floor(runs))
    return 0


def _run_ann(args: argparse.Namespace) -> int:
    from .adapters.server_bridge import ServerBridgeRetrievalSut, SutWorkerError
    from .usecases.ann_check import MeasureAnnRecall
    from .usecases.report import format_run

    with pg.connect() as conn:
        repo = pg.PgEvalRepository(conn)
        if not verify(repo, args.corpus):
            print(f"corpus {args.corpus} is missing or does not match its digest (EV-INV-5); not running")
            return 1
        by_language: dict[EvalLanguage, list] = {}
        for pid in repo.get_corpus(args.corpus).profile_ids:
            profile = repo.get_profile(pid)
            by_language.setdefault(profile.language, []).append((profile, repo.get_ledger(pid), repo.get_probes(pid)))
        check = MeasureAnnRecall(lambda: ServerBridgeRetrievalSut(device=args.device))
        for language, profiles in sorted(by_language.items()):
            for ef in (None, 1):  # default index setting, then the positive control
                try:
                    run = check.execute(profiles, args.corpus, ef_search=ef)
                except (SutWorkerError, RuntimeError) as e:
                    print(f"{language.value}: failed: {e}")
                    return 5
                repo.save_run(run)
                print(format_run(run, repeat="(control)" if ef else ""))
    return 0


# TR-602 defaults, repeated here because eval/ reaches the server only through the SUT worker; the recall
# cut-off itself is read from the SUT (SutDescription.min_recall_similarity).
PRODUCTION_MERGE, PRODUCTION_DISAMBIGUATE = 0.93, 0.75
_RENDERING_NAMES = {"first-key-term": "FIRST_KEY_TERM", "bare": "BARE"}


def _sweep_retrieval(args: argparse.Namespace) -> int:
    from .adapters.server_bridge import ServerBridgeRetrievalSut, SutWorkerError
    from .domain.sweep import frange, lowest_safe_cutoff, lowest_safe_merge, sweep_cutoffs, sweep_thresholds
    from .usecases.report import (
        format_cutoff_sweep, format_pair_distributions, format_threshold_sweep)
    from .usecases.retrieval import ExecuteRetrievalSuite, Rendering
    from .usecases.sweep import MeasurePairSimilarities

    try:
        renderings = [Rendering[_RENDERING_NAMES[r.strip()]] for r in args.renderings.split(",")]
    except KeyError as e:
        print(f"unknown rendering {e}; choose from {sorted(_RENDERING_NAMES)}")
        return 2
    factory = lambda: ServerBridgeRetrievalSut(device=args.device)  # noqa: E731
    report: list[str] = [f"# Threshold Sweep — retrieval, corpus {args.corpus}", ""]
    with pg.connect() as conn:
        repo = pg.PgEvalRepository(conn)
        if not verify(repo, args.corpus):
            print(f"corpus {args.corpus} is missing or does not match its digest (EV-INV-5); not running")
            return 1
        by_language: dict[EvalLanguage, list] = {}
        for pid in repo.get_corpus(args.corpus).profile_ids:
            profile = repo.get_profile(pid)
            by_language.setdefault(profile.language, []).append((profile, repo.get_ledger(pid), repo.get_probes(pid)))
        for language, profiles in sorted(by_language.items()):
            if args.language and language.value != args.language:
                continue
            for rendering in renderings:
                header = f"## {language.value} · {rendering.value}"
                print(f"\n{header}")
                try:
                    run, outcomes = ExecuteRetrievalSuite(factory).run(
                        language=language, corpus_version=args.corpus, profiles=profiles, rendering=rendering)
                    pairs = [p for profile, ledger, _ in profiles
                             for p in MeasurePairSimilarities(factory).execute(profile, ledger, rendering)]
                except SutWorkerError as e:
                    print(f"SUT failed: {e}")
                    return 5
                repo.save_run(run)
                production = float(run.fingerprint.extra["cutoff"])
                results = [r for o in outcomes for r in o.results]
                grid = sorted({*frange(0.60, 0.96, 0.02), production})
                safe = lowest_safe_cutoff(results)
                cutoff_txt = format_cutoff_sweep(sweep_cutoffs(results, grid), safe, production)
                dist_txt = format_pair_distributions(pairs)
                thr = sweep_thresholds(pairs, [PRODUCTION_MERGE], frange(0.60, 0.90, 0.05)) + sweep_thresholds(
                    pairs, frange(0.85, 0.99, 0.02), [PRODUCTION_DISAMBIGUATE])
                thr_txt = format_threshold_sweep(thr, lowest_safe_merge(pairs), (PRODUCTION_MERGE, PRODUCTION_DISAMBIGUATE))
                block = [header, "", "```", f"run {run.run_id}  git {run.fingerprint.git_sha}  sut {dict(run.fingerprint.sut)}", "",
                         "recall cut-off", cutoff_txt, "", "pair similarities (passage↔passage, TR-601)", dist_txt, "",
                         "merge / disambiguate thresholds (TR-602)", thr_txt, "```", ""]
                print("\n".join(block[2:-2]))
                report += block
    out = Path(__file__).resolve().parents[2] / "results" / f"sweep_retrieval_{args.corpus}.md"
    out.write_text("\n".join(report), encoding="utf-8")
    print(f"\nreport written to {out}")
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
