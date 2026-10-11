# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""GenerateProbes — Probes from a Ledger. The plan (which facts, which distractors) is
deterministic and seeded; the Reference LLM only writes the wording, and abstention questions
are independently verified to be unanswerable from the Ledger."""
from __future__ import annotations

import random
from dataclasses import dataclass

from ..domain.model import EvalLanguage, Ledger, LedgerFact, Probe, ProbeKind, SpeechArchetype, SyntheticUserProfile
from .ports import ReferenceLLM
from .reference_json import ReferenceJsonError, ask_validated

N_SINGLE, N_HARD, N_CROSS, N_AGG, N_ABSTAIN = 28, 14, 14, 4, 8
ABSTAIN_CANDIDATES = 14
MAX_ABSTAIN_ROUNDS = 3
_LANG_NAME = {EvalLanguage.FR: "French", EvalLanguage.EN: "English"}
_SYSTEM = ("You are a meticulous author of test queries for a voice-assistant memory benchmark. "
           "You answer with one JSON object and nothing else (no markdown fences).")


@dataclass(frozen=True)
class PlanItem:
    probe_id: str
    kind: ProbeKind
    target: LedgerFact
    distractors: tuple[LedgerFact, ...] = ()


def _ancestors(ledger: Ledger, fact: LedgerFact) -> tuple[LedgerFact, ...]:
    out, cur = [], fact
    while cur.supersedes:
        cur = ledger.get(cur.supersedes)
        out.append(cur)
    return tuple(out)


def plan(ledger: Ledger, prefix: str, seed: int) -> tuple[list[PlanItem], list[str]]:
    """Returns (items needing a written query, ids of single-fact items to translate)."""
    rng = random.Random(seed)
    current = sorted(ledger.current_facts(), key=lambda f: f.fact_id)
    updates = [f for f in current if f.supersedes]
    pool = [f for f in current if not f.supersedes]
    single = rng.sample(pool, min(N_SINGLE, len(pool)))
    taken = {f.fact_id for f in single}
    hard: list[PlanItem] = []
    candidates = [f for f in pool if f.fact_id not in taken] + single
    rng.shuffle(candidates)
    for f in candidates:
        if len(hard) == N_HARD:
            break
        close = [g for g in current if g.fact_id != f.fact_id and g.domain == f.domain and g.kind is f.kind
                 and not g.supersedes == f.fact_id]
        if close:
            hard.append(PlanItem("", ProbeKind.HARD_NEGATIVE, f, (rng.choice(close),)))
    items = [PlanItem("", ProbeKind.SINGLE_FACT, f) for f in single]
    items += hard
    items += [PlanItem("", ProbeKind.UPDATE, f, _ancestors(ledger, f)) for f in updates]
    counters: dict[ProbeKind, int] = {}
    numbered = []
    for it in items:
        counters[it.kind] = counters.get(it.kind, 0) + 1
        numbered.append(PlanItem(f"{prefix}-{it.kind.value}-{counters[it.kind]:02d}", it.kind, it.target, it.distractors))
    cross = [it.probe_id for it in numbered if it.kind is ProbeKind.SINGLE_FACT][:N_CROSS]
    return numbered, cross


def _leaks(query: str, terms: tuple[str, ...]) -> list[str]:
    return [t for t in terms if t.casefold() in query.casefold()]


class GenerateProbes:
    def __init__(self, llm: ReferenceLLM, model: str) -> None:
        self._llm, self._model = llm, model

    def execute(self, profile: SyntheticUserProfile, ledger: Ledger, seed: int = 1) -> list[Probe]:
        prefix = f"{profile.language.value}-{profile.archetype.value}"
        items, cross_ids = plan(ledger, prefix, seed)
        probes = self._written(profile, items)
        by_id = {p.probe_id: p for p in probes}
        probes += self._aggregation_and_abstention(profile, ledger, prefix)
        probes += self._cross_lingual(profile, ledger, [by_id[i] for i in cross_ids], prefix)
        texts = [p.text.casefold() for p in probes]
        if len(set(texts)) != len(texts):
            raise ReferenceJsonError("duplicate probe texts generated")
        return probes

    # -- step A: single_fact / hard_negative / update ------------------------------------------
    def _written(self, profile: SyntheticUserProfile, items: list[PlanItem]) -> list[Probe]:
        lang = _LANG_NAME[profile.language]
        spec = [{"id": it.probe_id, "mode": it.kind.value, "target_fact": it.target.text,
                 "distractor_facts": [d.text for d in it.distractors],
                 "answer_words_to_avoid": list(it.target.key_terms)} for it in items]
        prompt = f"""A voice assistant stores facts about its user {profile.display_name}. For each item below write ONE
question or request, in {lang}, that the USER would say aloud to the assistant and whose answer is the "target_fact".
{_style(profile)}
Rules:
- First person ("my", "I"), natural spoken wording, one sentence. Never third person, never the user's first name.
- NEVER include any of the "answer_words_to_avoid" (they are the answer), nor other details that give the answer away.
- mode "single_fact": a plain question about the target fact, worded differently from the fact text.
- mode "hard_negative": the "distractor_facts" are similar stored facts that must NOT be a good answer: make the question
  specific enough to the target that the distractors clearly do not answer it.
- mode "update": the target fact REPLACED older facts (the distractors): ask about the CURRENT situation ("now", "these days",
  "currently" or equivalent), so only the target is right.
Items (JSON): {spec!r}
Output: {{"queries": [{{"id": "<id>", "query": "<the question>"}}, ...]}}  — exactly one entry per item, same ids."""
        by_id = {it.probe_id: it for it in items}

        def validate(d: dict) -> list[Probe]:
            got = {q.get("id"): str(q.get("query", "")).strip() for q in d.get("queries", []) if isinstance(q, dict)}
            if set(got) != set(by_id):
                raise ValueError(f"missing or extra ids: {sorted(set(by_id) ^ set(got))[:6]}")
            problems, out = [], []
            for pid, it in by_id.items():
                q = got.get(pid, "")
                if not q:
                    problems.append(f"{pid}: empty query")
                    continue
                if leaked := _leaks(q, it.target.key_terms):
                    problems.append(f"{pid}: query contains answer words {leaked}")
                    continue
                out.append(Probe(pid, it.kind, profile.language, q, (it.target.fact_id,),
                                 tuple(d.fact_id for d in it.distractors)))
            if problems:
                raise ValueError("; ".join(problems))
            return out

        return ask_validated(self._llm, self._model, _SYSTEM, prompt, validate)

    # -- step B: aggregation + abstention ------------------------------------------------------
    def _aggregation_and_abstention(self, profile: SyntheticUserProfile, ledger: Ledger, prefix: str) -> list[Probe]:
        lang = _LANG_NAME[profile.language]
        facts = "\n".join(f"{f.fact_id}: {f.text}" for f in ledger.current_facts())
        known = {f.fact_id for f in ledger.current_facts()}

        def ask(n_abstain: int, with_agg: bool, rejected: list[str]) -> dict:
            agg = (f"- \"aggregation\": exactly {N_AGG} questions whose COMPLETE answer needs 2 to 4 of the facts together "
                   "(e.g. 'who are my children', 'what do I do to keep fit'); give the ids of every fact needed.\n"
                   if with_agg else "")
            avoid = f"\nThese earlier candidates were answerable and rejected, do not repeat their topics: {rejected}" if rejected else ""
            prompt = f"""Current known facts about the user {profile.display_name} (the only things the assistant has stored):
{facts}

Write test questions, in {lang}, as the USER would ask a voice assistant, first person, natural spoken wording.
{_style(profile)}
{agg}- "abstention": exactly {n_abstain} plausible questions about the user's own life, tastes or history that NONE of the facts
  above answers, even partly, and that cannot be inferred from them (ask about things never mentioned).{avoid}
Output: {{{('"aggregation": [{"query": "...", "fact_ids": ["fNN", ...]}], ' if with_agg else '')}"abstention": [{{"query": "..."}}]}}"""
            return ask_validated(self._llm, self._model, _SYSTEM, prompt, lambda d: _check_b(d, n_abstain, with_agg, known))

        out: list[Probe] = []
        kept: list[str] = []
        rejected: list[str] = []
        first = True
        for _ in range(MAX_ABSTAIN_ROUNDS):
            data = ask(ABSTAIN_CANDIDATES if first else ABSTAIN_CANDIDATES, first, rejected)
            if first:
                for i, a in enumerate(data["aggregation"], 1):
                    out.append(Probe(f"{prefix}-aggregation-{i:02d}", ProbeKind.AGGREGATION, profile.language,
                                     a["query"], tuple(a["fact_ids"])))
            first = False
            answerable = self._answerable(ledger, data["abstention"])
            kept += [q for q in data["abstention"] if q not in answerable and q not in kept]
            rejected += [q for q in data["abstention"] if q in answerable]
            if len(kept) >= N_ABSTAIN:
                break
        if len(kept) < N_ABSTAIN:
            raise ReferenceJsonError(f"only {len(kept)} verified abstention questions after {MAX_ABSTAIN_ROUNDS} rounds")
        out += [Probe(f"{prefix}-abstention-{i:02d}", ProbeKind.ABSTENTION, profile.language, q)
                for i, q in enumerate(kept[:N_ABSTAIN], 1)]
        return out

    def _answerable(self, ledger: Ledger, queries: list[str]) -> set[str]:
        """Independent check: which candidate abstention questions can some stored fact answer?"""
        facts = "\n".join(f"{f.fact_id}: {f.text}" for f in ledger.facts)
        listing = [{"id": str(i), "query": q} for i, q in enumerate(queries)]
        prompt = f"""Stored facts (including outdated ones):
{facts}

For each question below, list the ids of every fact that would let an assistant answer it, fully or partly, or from which the
answer can reasonably be inferred. Use an empty list if no fact is relevant.
Questions (JSON): {listing!r}
Output: {{"checks": [{{"id": "<id>", "fact_ids": ["fNN", ...]}}, ...]}} — one entry per question."""

        def validate(d: dict) -> set[str]:
            checks = {str(c.get("id")): c.get("fact_ids") for c in d.get("checks", []) if isinstance(c, dict)}
            if set(checks) != {x["id"] for x in listing}:
                raise ValueError("checks must cover every question id exactly once")
            return {queries[int(i)] for i, ids in checks.items() if ids}

        return ask_validated(self._llm, self._model, _SYSTEM, prompt, validate)

    # -- step D: cross-lingual -----------------------------------------------------------------
    def _cross_lingual(self, profile: SyntheticUserProfile, ledger: Ledger, sources: list[Probe], prefix: str) -> list[Probe]:
        other = EvalLanguage.EN if profile.language is EvalLanguage.FR else EvalLanguage.FR
        listing = [{"id": p.probe_id, "query": p.text} for p in sources]
        prompt = f"""Translate each spoken user query below into {_LANG_NAME[other]}. Keep the spoken register and any
hesitations natural in the target language; do not add or remove information.
Queries (JSON): {listing!r}
Output: {{"translations": [{{"id": "<id>", "query": "<translation>"}}, ...]}} — one entry per query, same ids."""
        by_id = {p.probe_id: p for p in sources}

        def validate(d: dict) -> list[Probe]:
            got = {t.get("id"): str(t.get("query", "")).strip() for t in d.get("translations", []) if isinstance(t, dict)}
            if set(got) != set(by_id):
                raise ValueError(f"missing or extra ids: {sorted(set(by_id) ^ set(got))[:6]}")
            problems, out = [], []
            for i, (pid, src) in enumerate(by_id.items(), 1):
                q = got[pid]
                terms = tuple(t for fid in src.expected_fact_ids for t in ledger.get(fid).key_terms)
                if not q:
                    problems.append(f"{pid}: empty translation")
                elif leaked := _leaks(q, terms):
                    problems.append(f"{pid}: translation contains answer words {leaked}")
                else:
                    out.append(Probe(f"{prefix}-cross_lingual-{i:02d}", ProbeKind.CROSS_LINGUAL, other, q,
                                     src.expected_fact_ids))
            if problems:
                raise ValueError("; ".join(problems))
            return out

        return ask_validated(self._llm, self._model, _SYSTEM, prompt, validate)


def _style(profile: SyntheticUserProfile) -> str:
    if profile.archetype is SpeechArchetype.HESITANT:
        fillers = "'euh', 'ben'" if profile.language is EvalLanguage.FR else "'um', 'uh'"
        return (f"Speaker style: hesitant — about half of the questions should contain a filler ({fillers}: ONLY this "
                "language's own fillers), a false start or a trailing '…', like real spoken hesitation; the others are plain.")
    return "Speaker style: fluent — clean, complete, well-formed questions."


def _check_b(d: dict, n_abstain: int, with_agg: bool, known: set[str]) -> dict:
    problems = []
    abst = [str(a.get("query", "")).strip() for a in d.get("abstention", []) if isinstance(a, dict)]
    if len(abst) != n_abstain or not all(abst):
        problems.append(f"need exactly {n_abstain} non-empty abstention questions, got {len(abst)}")
    agg = []
    if with_agg:
        for a in d.get("aggregation", []):
            ids = tuple(a.get("fact_ids", [])) if isinstance(a, dict) else ()
            if not 2 <= len(ids) <= 4 or not set(ids) <= known or not str(a.get("query", "")).strip():
                problems.append(f"bad aggregation entry: {a!r}"[:200])
            else:
                agg.append({"query": str(a["query"]).strip(), "fact_ids": ids})
        if len(agg) != N_AGG and not problems:
            problems.append(f"need exactly {N_AGG} aggregation questions, got {len(agg)}")
    if problems:
        raise ValueError("; ".join(problems))
    return {"aggregation": agg, "abstention": abst}
