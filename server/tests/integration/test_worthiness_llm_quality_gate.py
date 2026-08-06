# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Live LLM quality gate for the worthiness judgment (FR-307).

**Not part of continuous testing** — gated behind `MAMEMAI_TEST_LLM_QUALITY_GATE=1`, like
`test_tutor_llm_quality_gate.py`. A report card for a prompt/model pairing, to be re-run
whenever either changes. A FAIL here is a finding about the pairing, not necessarily a
defect in this test.

Exists because the worthiness prompt is only verifiable by running it. On 2026-08-02 the
shipped wording scored **23/45** against this set: every negative case right (20/20) and
almost every positive one wrong, including a plain English "I went to Norway last weekend
and finally saw the northern lights". Live, that had silently discarded every French
conversation in the database (0/4 worthy, against 9/18 for English) and thrown away a
37-minute astronomy session whole. Two changes took the same set to **40/45**:

1. The prompt states its YES criteria as their own enumerated list and restricts NO to
   "nothing but" the excluded categories. A language-neutrality clause and few-shot
   examples were each measured on top and changed nothing, so neither shipped.
2. Long conversations are judged in windows (`worthiness_transcripts`). Length, not
   content, was what broke the remaining case: the same 72-turn conversation scored 0/5
   whole, 5/5 on its first 24 turns, 4/5 on its last 24, and 5/5 even on the 18 turns of
   pure STT-repair dialogue that were the least substantive material in it.

Run (from `server/`):

    MAMEMAI_TEST_LLM_QUALITY_GATE=1 uv run pytest tests/integration/test_worthiness_llm_quality_gate.py -s

Optional: `MAMEMAI_TEST_LLM_MODEL` (default `llama3.1:8b`, this machine's configured
model), `MAMEMAI_TEST_LLM_HOST`, `MAMEMAI_TEST_WORTHINESS_VOTES` (default 3).

A single run is one data point: this judgment is genuinely non-deterministic, which is why
each case is voted rather than asked once.
"""
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mamemai_server.domain.model import Conversation, Speaker, Turn
from mamemai_server.infrastructure.llm.ollama import OllamaWorthinessEvaluator

_ENABLED = os.environ.get("MAMEMAI_TEST_LLM_QUALITY_GATE") is not None
_MODEL = os.environ.get("MAMEMAI_TEST_LLM_MODEL", "llama3.1:8b")
_HOST = os.environ.get("MAMEMAI_TEST_LLM_HOST")
_VOTES = int(os.environ.get("MAMEMAI_TEST_WORTHINESS_VOTES", "3"))

pytestmark = pytest.mark.skipif(
    not _ENABLED, reason="manual quality gate — set MAMEMAI_TEST_LLM_QUALITY_GATE=1",
)

# Each case is (label, expected verdict, [(speaker, text), ...]). The French and English
# halves are deliberate translations of each other: the 2026-08-02 investigation began
# from a suspected language effect, and keeping the pair lets any future run re-measure it
# rather than re-argue it.
_CASES: list[tuple[str, bool, list[tuple[Speaker, str]]]] = [
    ("EN knowledge", True, [
        (Speaker.USER, "I'd like to discuss the northern lights. Can you explain the phenomenon?"),
        (Speaker.ASSISTANT, "Auroras occur when charged solar particles enter the atmosphere and "
                            "excite oxygen and nitrogen atoms."),
        (Speaker.USER, "And noctilucent clouds, at what latitude and season are they seen?"),
        (Speaker.ASSISTANT, "At high latitudes, generally between August and September in the "
                            "northern hemisphere, shortly after sunset."),
    ]),
    ("FR knowledge", True, [
        (Speaker.USER, "J'aimerais qu'on discute des aurores boréales. Peux-tu m'expliquer le phénomène ?"),
        (Speaker.ASSISTANT, "Les aurores boréales se produisent lorsque des particules solaires "
                            "chargées entrent dans l'atmosphère et excitent les atomes d'oxygène."),
        (Speaker.USER, "Et les nuages noctulescents, à quelle latitude et à quelle période les observe-t-on ?"),
        (Speaker.ASSISTANT, "Aux hautes latitudes, entre août et septembre dans l'hémisphère nord, "
                            "peu après le coucher du soleil."),
    ]),
    ("EN personal", True, [
        (Speaker.USER, "I went to Norway last weekend and finally saw the northern lights."),
        (Speaker.ASSISTANT, "How lucky! Was it near Tromsø?"),
        (Speaker.USER, "Yes, in Tromsø. I work as an engineer and I'm planning an observing trip next year."),
    ]),
    ("FR personal", True, [
        (Speaker.USER, "Je suis allé en Norvège le week-end dernier et j'ai enfin vu les aurores boréales."),
        (Speaker.ASSISTANT, "Quelle chance ! C'était près de Tromsø ?"),
        (Speaker.USER, "Oui, à Tromsø. Je travaille comme ingénieur et je prépare un voyage "
                       "d'observation astronomique pour l'année prochaine."),
    ]),
    ("EN smalltalk", False, [
        (Speaker.USER, "Hello, how are you today?"),
        (Speaker.ASSISTANT, "I'm well, thank you. How can I help?"),
        (Speaker.USER, "Nothing much, just saying hi."),
    ]),
    ("FR smalltalk", False, [
        (Speaker.USER, "Bonjour, comment vas-tu aujourd'hui ?"),
        (Speaker.ASSISTANT, "Très bien, merci. Comment puis-je vous aider ?"),
        (Speaker.USER, "Rien de spécial, je voulais juste dire bonjour."),
    ]),
    ("EN meta", False, [
        (Speaker.USER, "Your voice cut out again, the audio download seems broken."),
        (Speaker.ASSISTANT, "Sorry about that. It may be a TTS voice pack issue."),
        (Speaker.USER, "Can you check which model you're running?"),
    ]),
    ("FR meta", False, [
        (Speaker.USER, "Ta voix a encore coupé, le téléchargement audio semble cassé."),
        (Speaker.ASSISTANT, "Désolé. C'est peut-être un problème de pack de voix TTS."),
        (Speaker.USER, "Peux-tu vérifier quel modèle tu utilises ?"),
    ]),
]


def _conversation(pairs: list[tuple[Speaker, str]], repeat: int = 1) -> Conversation:
    now = datetime.now(UTC)
    turns = [Turn(timestamp=now, speaker=speaker, content=text) for _ in range(repeat) for speaker, text in pairs]
    return Conversation(id=None, started_at=now, persona_id=uuid4(), turns=turns)


def _majority(evaluator: OllamaWorthinessEvaluator, conversation: Conversation) -> tuple[int, bool]:
    votes = [evaluator.evaluate(conversation) for _ in range(_VOTES)]
    return sum(votes), sum(votes) * 2 > _VOTES


def test_worthiness_report_card() -> None:
    """Spec: FR-307 — every case correct on a majority of votes."""
    evaluator = OllamaWorthinessEvaluator(model=_MODEL, host=_HOST)
    failures = []
    print(f"\nworthiness report card — model={_MODEL}, {_VOTES} votes/case")
    for label, expected, pairs in _CASES:
        yes, verdict = _majority(evaluator, _conversation(pairs))
        ok = verdict == expected
        print(f"  {'ok  ' if ok else 'FAIL'} {label:14s} want={'YES' if expected else 'NO '} yes={yes}/{_VOTES}")
        if not ok:
            failures.append(label)
    assert not failures, f"worthiness judgment wrong for: {', '.join(failures)}"


def test_length_does_not_flip_a_worthy_conversation() -> None:
    """Spec: FR-307 — the windowing rule, stated as the property it exists to protect:
    padding a worthy conversation out to 90 turns must not make it unworthy. Judged whole,
    this model reversed itself on exactly that.
    """
    evaluator = OllamaWorthinessEvaluator(model=_MODEL, host=_HOST)
    _, _, pairs = _CASES[2]  # EN personal — a personal fact and a dated event
    short_yes, short_verdict = _majority(evaluator, _conversation(pairs))
    long_yes, long_verdict = _majority(evaluator, _conversation(pairs, repeat=30))
    print(f"\n  short ({len(pairs)} turns): yes={short_yes}/{_VOTES}   "
          f"long ({len(pairs) * 30} turns): yes={long_yes}/{_VOTES}")
    assert short_verdict is True
    assert long_verdict is True, "length alone flipped a worthy conversation to unworthy"
