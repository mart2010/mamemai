# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""GenerateProfileAndLedger — EV-INV-3: the Ledger is written first, from a brief, by the
Reference LLM, then validated hard. A rejected answer is sent back with the exact errors
(bounded attempts); nothing invalid is ever returned."""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date

from ..domain.model import EvalLanguage, FactKind, Ledger, LedgerFact, SpeechArchetype, SyntheticUserProfile
from .ports import ReferenceLLM, ReferenceRequest

# The fictional "today" of each Scenario. Fixed by us, not chosen by the model (reproducible).
SCENARIO_DATES = (
    date(2026, 6, 8), date(2026, 6, 22), date(2026, 7, 13), date(2026, 8, 3), date(2026, 8, 24), date(2026, 9, 14),
)
LIFE_DOMAINS = ("famille", "travail", "voyage", "sante_sport", "cuisine", "maison", "loisirs", "finances", "projets")
KNOWLEDGE_DOMAINS = ("astronomie", "meteo_climat", "geographie", "ia", "histoire_sciences", "nature")
N_FACTS = (54, 66)
PER_SCENARIO = (7, 14)
MIN_KIND = {FactKind.EVENT: 18, FactKind.ATTRIBUTE: 15, FactKind.KNOWLEDGE: 8}
MIN_REVISIONS = 4
MAX_WORDS_PER_FACT = 40

_NAMESPACE = uuid.UUID("6f1c3a52-8f0e-4c6e-9a53-2d9b0f6f5a11")


@dataclass(frozen=True)
class ProfileBrief:
    language: EvalLanguage
    archetype: SpeechArchetype
    sketch: str  # who the person is, in one or two sentences — keeps the four profiles distinct people
    seed: int = 1

    @property
    def profile_id(self) -> uuid.UUID:
        return uuid.uuid5(_NAMESPACE, f"{self.language.value}/{self.archetype.value}/{self.seed}")


# One distinct fictional person per (language, archetype). Interests follow the user's stated fields
# (astronomy, weather/climate, geography, AI) plus everyday life.
BRIEFS: dict[tuple[EvalLanguage, SpeechArchetype], ProfileBrief] = {
    (EvalLanguage.FR, SpeechArchetype.HESITANT): ProfileBrief(
        EvalLanguage.FR, SpeechArchetype.HESITANT,
        "Femme de 66 ans, institutrice retraitée, veuve, vit à Quimper en Bretagne ; deux enfants adultes et trois "
        "petits-enfants ; jardinière, observe la météo et le ciel en amatrice ; curieuse mais peu à l'aise avec la technologie."),
    (EvalLanguage.FR, SpeechArchetype.FLUENT): ProfileBrief(
        EvalLanguage.FR, SpeechArchetype.FLUENT,
        "Homme de 34 ans, développeur logiciel, vit à Toulouse en couple, sans enfant ; randonneur en montagne, "
        "cuisine volontiers ; curieux d'intelligence artificielle, de géographie et de sciences ; organisé et direct."),
    (EvalLanguage.EN, SpeechArchetype.HESITANT): ProfileBrief(
        EvalLanguage.EN, SpeechArchetype.HESITANT,
        "Woman of 41, part-time bookkeeper and mother of two (a 9-year-old and a 13-year-old), lives in Manchester with "
        "her husband; runs and bakes; follows space and climate news with curiosity; anxious, apologetic, new to voice "
        "assistants."),
    (EvalLanguage.EN, SpeechArchetype.FLUENT): ProfileBrief(
        EvalLanguage.EN, SpeechArchetype.FLUENT,
        "Man of 52, civil engineer, lives in Bristol with his wife and teenage daughter; dinghy sailing and cycling; reads "
        "about artificial intelligence and geography; articulate, decisive, likes precise answers."),
}

_SYSTEM = (
    "You are a meticulous author of test data for a voice-assistant memory benchmark. You invent a fictional "
    "person and the facts of their life. You answer with one JSON object and nothing else (no markdown fences)."
)


def build_prompt(brief: ProfileBrief) -> str:
    lang = {"fr": "French", "en": "English"}[brief.language.value]
    learned = {"fr": "<first name> a appris que … / <first name> a discuté du fait que …",
               "en": "<first name> learned that … / <first name> discussed that …"}[brief.language.value]
    dates = ", ".join(f"scenario {i}: {d.isoformat()}" for i, d in enumerate(SCENARIO_DATES))
    return f"""Invent ONE fictional person and the ground-truth facts of their life, written in {lang}.

Person: {brief.sketch}
Speech type (affects only the biography's tone, NOT the facts): {brief.archetype.value}
Variation seed: {brief.seed}

The person will talk with a voice assistant over {len(SCENARIO_DATES)} separate conversations (scenarios 0..{len(SCENARIO_DATES) - 1}),
on these dates: {dates}.

Produce between {N_FACTS[0]} and {N_FACTS[1]} facts, each revealed by the user in exactly one scenario (field "revealed_in"),
with {PER_SCENARIO[0]}–{PER_SCENARIO[1]} facts per scenario. Three kinds:
- "event": something that happened in the user's own life at an identifiable time or place ("happened_on" = ISO date, on or before
  the date of the scenario that reports it; most within the weeks before it, a few years earlier). Never about the conversation
  itself or the assistant. At least {MIN_KIND[FactKind.EVENT]}.
- "attribute": a stable fact about the user — relationships, home, job, possessions, tastes, habits and routines, skills.
  "happened_on" is null. At least {MIN_KIND[FactKind.ATTRIBUTE]}.
- "knowledge": a piece of everyday general knowledge the user learned or discussed with the assistant (common-knowledge level,
  strictly correct, self-contained, naming its topic), phrased as "{learned}".
  "happened_on" is null. At least {MIN_KIND[FactKind.KNOWLEDGE]}.

Revisions: at least {MIN_REVISIONS} attributes must REPLACE an earlier attribute (the person moved, changed job, adopted a pet, gave
up a hobby…). The new fact has "supersedes" = the id of the older one and a LATER "revealed_in"; its text states the new situation
in full. Only attributes can supersede; an attribute is superseded at most once.

Each fact: one atomic, self-contained sentence in the third person using the person's first name, at most {MAX_WORDS_PER_FACT} words,
no pronoun-only references. "paraphrases": exactly 2 other ways to say the same thing with different wording.
"key_terms": 1 to 3 distinctive strings copied EXACTLY (same spelling and accents) from the fact's own text that any faithful
restatement must contain — a place, a name, a number, a rare noun (e.g. "Quimper", "Pimprenelle", "3,8 centimètres"); never a
first name alone, never a common word. For a revision, the key terms must be those of the NEW situation.
"domain" must be one of — for event/attribute: {", ".join(LIFE_DOMAINS)} ; for knowledge: {", ".join(KNOWLEDGE_DOMAINS)}.
Spread domains widely; include astronomy/weather/geography/AI themes where natural, but most facts are ordinary daily life.
Ids are "f01", "f02", … in order, unique.

JSON shape:
{{"profile": {{"display_name": "<first name>", "biography": "<120-200 words, in {lang}>",
              "speech_style": "<2-3 sentences, in {lang}, on how this person talks>"}},
 "facts": [{{"fact_id": "f01", "kind": "event|attribute|knowledge", "text": "...", "revealed_in": 0, "domain": "...",
            "happened_on": "YYYY-MM-DD or null", "paraphrases": ["...", "..."], "key_terms": ["..."],
            "supersedes": "fNN or null"}}, ...]}}
"""


class LedgerGenerationError(Exception):
    pass


# Cheap language sanity: a French fact should contain French function words and no English ones.
_FR_WORDS = {"le", "la", "les", "de", "du", "des", "un", "une", "et", "à", "en", "est", "au", "aux", "pour", "dans", "que"}
_EN_WORDS = {"the", "and", "with", "his", "her", "has", "have", "was", "were", "of", "to", "in"}


def _language_problems(language: EvalLanguage, facts: list[LedgerFact]) -> list[str]:
    own, foreign = (_FR_WORDS, _EN_WORDS) if language is EvalLanguage.FR else (_EN_WORDS, _FR_WORDS)
    bad = []
    for f in facts:
        words = set(re.findall(r"[\w']+", f.text.lower()))
        if not words & own or len(words & foreign) >= 3:
            bad.append(f.fact_id)
    return [f"facts not written in {language.value}: {', '.join(bad[:8])}"] if len(bad) > max(2, len(facts) // 20) else []


def parse_and_validate(raw: str, brief: ProfileBrief) -> tuple[SyntheticUserProfile, Ledger]:
    """Raises ValueError listing EVERY problem found, so one repair round can fix them all."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object found in the answer")
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}") from e

    problems: list[str] = []
    facts: list[LedgerFact] = []
    for i, d in enumerate(data.get("facts", [])):
        try:
            facts.append(LedgerFact(
                fact_id=str(d["fact_id"]), kind=FactKind(d["kind"]), text=str(d["text"]).strip(),
                revealed_in=int(d["revealed_in"]), domain=str(d["domain"]),
                happened_on=date.fromisoformat(d["happened_on"]) if d.get("happened_on") else None,
                paraphrases=tuple(str(p).strip() for p in d.get("paraphrases", [])), supersedes=d.get("supersedes") or None,
                key_terms=tuple(str(t).strip() for t in d.get("key_terms", [])),
            ))
        except (KeyError, ValueError, TypeError) as e:
            problems.append(f"fact #{i + 1} ({d.get('fact_id') if isinstance(d, dict) else '?'}): {e!r}")

    if not N_FACTS[0] <= len(facts) <= N_FACTS[1]:
        problems.append(f"{len(facts)} facts; need {N_FACTS[0]}–{N_FACTS[1]}")
    kinds = Counter(f.kind for f in facts)
    for kind, minimum in MIN_KIND.items():
        if kinds[kind] < minimum:
            problems.append(f"only {kinds[kind]} {kind.value} facts; need at least {minimum}")
    revisions = sum(1 for f in facts if f.supersedes)
    if revisions < MIN_REVISIONS:
        problems.append(f"only {revisions} revisions; need at least {MIN_REVISIONS}")
    per = Counter(f.revealed_in for f in facts)
    for s in range(len(SCENARIO_DATES)):
        if not PER_SCENARIO[0] <= per[s] <= PER_SCENARIO[1]:
            problems.append(f"scenario {s} reveals {per[s]} facts; need {PER_SCENARIO[0]}–{PER_SCENARIO[1]}")
    for f in facts:
        allowed = KNOWLEDGE_DOMAINS if f.kind is FactKind.KNOWLEDGE else LIFE_DOMAINS
        if f.domain not in allowed:
            problems.append(f"{f.fact_id}: domain {f.domain!r} not allowed for {f.kind.value}")
        if len(f.text.split()) > MAX_WORDS_PER_FACT:
            problems.append(f"{f.fact_id}: more than {MAX_WORDS_PER_FACT} words")
        if not 1 <= len(f.key_terms) <= 3:
            problems.append(f"{f.fact_id}: needs 1–3 key_terms")
        if len(f.paraphrases) != 2 or any(not p for p in f.paraphrases):
            problems.append(f"{f.fact_id}: needs exactly 2 non-empty paraphrases")
    texts = Counter(f.text.lower() for f in facts)
    problems += [f"duplicate fact text: {t[:50]!r}" for t, c in texts.items() if c > 1]
    problems += _language_problems(brief.language, facts)

    profile_d = data.get("profile") or {}
    for key in ("display_name", "biography", "speech_style"):
        if not str(profile_d.get(key, "")).strip():
            problems.append(f"profile.{key} missing")

    ledger = None
    if not problems:
        try:
            ledger = Ledger(brief.profile_id, tuple(facts), SCENARIO_DATES)
        except ValueError as e:
            problems.append(f"ledger inconsistent: {e}")
    if problems:
        raise ValueError("; ".join(problems))
    profile = SyntheticUserProfile(
        brief.profile_id, brief.language, str(profile_d["display_name"]).strip(), brief.seed,
        str(profile_d["biography"]).strip(), brief.archetype, str(profile_d["speech_style"]).strip())
    return profile, ledger


class GenerateProfileAndLedger:
    def __init__(self, llm: ReferenceLLM, model: str, max_attempts: int = 3) -> None:
        self._llm, self._model, self._max_attempts = llm, model, max_attempts

    def execute(self, brief: ProfileBrief) -> tuple[SyntheticUserProfile, Ledger]:
        prompt = build_prompt(brief)
        last_error = ""
        for _ in range(self._max_attempts):
            answer = self._llm.complete(ReferenceRequest(_SYSTEM, prompt, self._model)).text
            try:
                return parse_and_validate(answer, brief)
            except ValueError as e:
                last_error = str(e)
                prompt = (
                    build_prompt(brief)
                    + f"\n\nYour previous answer was REJECTED for these reasons:\n{last_error}\n\n"
                    f"Previous answer:\n{answer}\n\nReturn the complete corrected JSON object."
                )
        raise LedgerGenerationError(f"no valid ledger after {self._max_attempts} attempts; last errors: {last_error}")
