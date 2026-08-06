# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
import json

import ollama

from ...domain.model import Concept, Conversation, Episode, Language, Procedure
from ...services.ports import ExtractionResult, MemoryItem, Message
from ._common import (
    drop_chat_dated_episodes,
    user_turns_only,
    OLLAMA_DEFAULT_NUM_CTX,
    WORTHINESS_SYSTEM_PROMPT,
    context_overflow,
    estimate_tokens,
    extraction_transcripts,
    is_affirmative,
    merge_extractions,
    usable_context,
    worthiness_transcripts,
    _conversation_language,
    _extraction_system_prompt,
    _parse_extraction,
)

# Every adapter below sends `num_ctx` explicitly rather than accepting Ollama's 4096
# default (TR-951/TR-952) — see OLLAMA_DEFAULT_NUM_CTX for what that default cost. The
# constructor default matches Ollama's own so that an adapter built without the setting
# behaves exactly as it did before this parameter existed; the composition root always
# passes the configured value.


# ---------------------------------------------------------------------------
# Live path — async streaming
# ---------------------------------------------------------------------------

class OllamaLLMService:
    def __init__(
        self, model: str = "llama3.3", host: str | None = None, num_ctx: int = OLLAMA_DEFAULT_NUM_CTX,
    ) -> None:
        self._client = ollama.AsyncClient(host=host)
        self._model = model
        self._num_ctx = num_ctx

    async def complete(self, messages: list[Message], system_prompt: str):
        ollama_messages = [{"role": "system", "content": system_prompt}]
        for m in messages:
            ollama_messages.append({"role": m.role, "content": m.content})
        # One unconditional line per turn, in the TR-311 instrumentation family: the live
        # context grows all session and the mechanism meant to bound it (TR-309's rolling
        # summary) doesn't fire until turn 50, so the operator needs the trend, not just
        # the alarm. Measured on a real 32-turn session: 2 526 tokens at turn 12, 4 141 at
        # 18 — i.e. past a 4096 window from the middle of the session onward, with the
        # oldest turns being dropped from every prompt after that and nothing saying so.
        tokens = estimate_tokens(system_prompt, *(m.content for m in messages))
        budget = usable_context(self._num_ctx)
        overflow = "  ** OVERFLOW — oldest turns are being dropped **" if tokens > budget else ""
        print(f"[llm] context: ~{tokens} tokens of {budget} usable (num_ctx={self._num_ctx}){overflow}")
        async for part in await self._client.chat(
            model=self._model,
            messages=ollama_messages,
            stream=True,
            keep_alive="30m",
            options={"num_ctx": self._num_ctx},
        ):
            if part.message.content:
                yield part.message.content


# ---------------------------------------------------------------------------
# Offline path — synchronous one-shot calls
# ---------------------------------------------------------------------------

class OllamaWorthinessEvaluator:
    def __init__(
        self, model: str = "llama3.3", host: str | None = None, num_ctx: int = OLLAMA_DEFAULT_NUM_CTX,
    ) -> None:
        self._client = ollama.Client(host=host)
        self._model = model
        self._num_ctx = num_ctx

    def evaluate(self, conversation: Conversation) -> bool:
        # Worthy if ANY window is (see worthiness_transcripts): a long conversation earns
        # its keep at either end, and a single unworthy stretch must not veto the rest.
        # Short-circuits, so a short conversation is still exactly one call.
        for transcript in worthiness_transcripts(conversation):
            # Warn but still ask, unlike extraction below: a YES/NO verdict on the part of
            # the window that survives truncation is degraded, not fabricated, and it is
            # the same partial-view bargain windowing already makes. Skipping instead would
            # let a long conversation be judged unworthy because no window could be read —
            # trading a visible degradation for the invisible discard this all exists to
            # stop.
            context_overflow("worthiness", self._num_ctx, WORTHINESS_SYSTEM_PROMPT, transcript)
            response = self._client.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": WORTHINESS_SYSTEM_PROMPT},
                    {"role": "user", "content": transcript},
                ],
                options={"num_ctx": self._num_ctx},
            )
            if is_affirmative(response.message.content):
                return True
        return False


class OllamaMemorySynthesizer:
    # No overflow check on the three calls below: their prompts are two ~300-word records,
    # bounded by construction at roughly 1/10th of even Ollama's own default window. The
    # check belongs where the input is a transcript that grows with the conversation.
    def __init__(
        self, model: str = "llama3.3", host: str | None = None, num_ctx: int = OLLAMA_DEFAULT_NUM_CTX,
    ) -> None:
        self._client = ollama.Client(host=host)
        self._model = model
        self._num_ctx = num_ctx

    def synthesize_episode(self, existing_summary: str, new_summary: str) -> str:
        response = self._client.chat(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Merge two summaries of the same event into one coherent, concise summary. "
                        "Preserve all factual details from both. Output only the merged summary, nothing else."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Summary A:\n{existing_summary}\n\nSummary B:\n{new_summary}",
                },
            ],
            options={"num_ctx": self._num_ctx},
        )
        return response.message.content.strip()

    def synthesize_concept(self, existing: Concept, new_description: str) -> str:
        response = self._client.chat(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Synthesize two descriptions of the same concept into one (~300 words max). "
                        f"Write in language '{existing.language.code}'. "
                        "Absorb all details — do not append, synthesize. Output only the merged description."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Concept: {existing.name}\n\n"
                        f"Existing description:\n{existing.description}\n\n"
                        f"New information:\n{new_description}"
                    ),
                },
            ],
            options={"num_ctx": self._num_ctx},
        )
        return response.message.content.strip()

    def synthesize_procedure(self, existing: Procedure, new_description: str, new_steps: list[str]) -> tuple[str, list[str]]:
        existing_steps = "\n".join(f"{i+1}. {s}" for i, s in enumerate(existing.steps)) or "(none)"
        new_steps_fmt = "\n".join(f"{i+1}. {s}" for i, s in enumerate(new_steps)) or "(none)"
        response = self._client.chat(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Synthesize two descriptions of the same procedure into one. "
                        f"Write in language '{existing.language.code}'. "
                        'Output JSON with "description" (string, ~300 words) and '
                        '"steps" (array of strings, empty array if no clear sequential steps).'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Procedure: {existing.name}\n\n"
                        f"Existing:\nDescription: {existing.description}\nSteps:\n{existing_steps}\n\n"
                        f"New:\nDescription: {new_description}\nSteps:\n{new_steps_fmt}"
                    ),
                },
            ],
            format="json",
            options={"num_ctx": self._num_ctx},
        )
        try:
            data = json.loads(response.message.content)
            return data.get("description", new_description).strip(), data.get("steps", new_steps)
        except (json.JSONDecodeError, AttributeError):
            return new_description, new_steps


class OllamaDisambiguationEvaluator:
    # Same bounded-by-construction prompt as the synthesizer above, hence no overflow check.
    def __init__(
        self, model: str = "llama3.3", host: str | None = None, num_ctx: int = OLLAMA_DEFAULT_NUM_CTX,
    ) -> None:
        self._client = ollama.Client(host=host)
        self._model = model
        self._num_ctx = num_ctx

    def is_same(self, existing: MemoryItem, candidate: MemoryItem) -> bool:
        def _fmt(item: MemoryItem) -> str:
            if isinstance(item, Episode):
                return f"Episode: {item.summary}"
            if isinstance(item, Concept):
                return f"Concept '{item.name}': {item.description}"
            return f"Procedure '{item.name}': {item.description}"

        response = self._client.chat(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Decide whether two memory records refer to the same real-world entity, event, or topic. "
                        "Reply with exactly one word: YES or NO."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Record A:\n{_fmt(existing)}\n\nRecord B:\n{_fmt(candidate)}",
                },
            ],
            options={"num_ctx": self._num_ctx},
        )
        return is_affirmative(response.message.content)


class OllamaConsolidationExtractor:
    def __init__(
        self, model: str = "llama3.3", host: str | None = None, num_ctx: int = OLLAMA_DEFAULT_NUM_CTX,
    ) -> None:
        self._client = ollama.Client(host=host)
        self._model = model
        self._num_ctx = num_ctx

    def extract(
        self,
        conversation: Conversation,
        primary_language: Language | None = None,
        extract_episodes: bool = True,
    ) -> ExtractionResult:
        persona_id = conversation.persona_id
        lang = _conversation_language(conversation)

        # Two passes with different transcripts (TR-961), not one pass over both.
        # Concepts need the assistant's turns — that is where correct terminology lives,
        # against STT-mangled user turns. Episodes must NOT see them, because the
        # fabrications are summaries of assistant content.
        concepts = self._extract_pass(
            conversation, conversation, persona_id, lang,
            _extraction_system_prompt(conversation, primary_language,
                                      extract_episodes=False, extract_concepts=True),
        )
        if not extract_episodes:
            return concepts

        trimmed = user_turns_only(conversation)
        episodes = self._extract_pass(
            conversation, trimmed, persona_id, lang,
            _extraction_system_prompt(conversation, primary_language,
                                      extract_episodes=True, extract_concepts=False),
        )
        return ExtractionResult(
            # An episode dated inside the conversation is a record of the chat, not of the
            # user's life — dropped here rather than asked-for-and-hoped-for in the prompt.
            episodes=drop_chat_dated_episodes(episodes.episodes, conversation),
            concepts=concepts.concepts,
        )

    def _extract_pass(
        self,
        conversation: Conversation,
        source: Conversation,
        persona_id,
        lang,
        system_prompt: str,
    ) -> ExtractionResult:
        """One windowed pass over `source`'s turns, parsed against `conversation`.

        `source` differs from `conversation` for the episode pass (user turns only); the
        parse still resolves relative dates and persona against the real conversation.
        """
        # One call per window (see extraction_transcripts) — a short conversation is still
        # exactly one. A window whose answer won't parse is skipped rather than failing the
        # whole conversation: before windowing there was only one answer, so an unparseable
        # one meant an empty result by necessity; now the others still stand.
        results = []
        for transcript in extraction_transcripts(source):
            # A window that cannot fit is skipped, loudly, rather than asked and believed.
            # Measured 2026-08-03: a truncated extraction call returns valid JSON with
            # empty arrays, every time, which is indistinguishable from "nothing here" to
            # everything downstream — the failure that discarded a 37-minute conversation
            # twice. Spending 30s to be lied to buys nothing; the printed line is the
            # result. Worthiness (above) makes the opposite call, for the reason given
            # there.
            if context_overflow("extraction window", self._num_ctx, system_prompt, transcript):
                continue
            response = self._client.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": transcript},
                ],
                format="json",
                options={"num_ctx": self._num_ctx},
            )
            try:
                data = json.loads(response.message.content)
            except (json.JSONDecodeError, AttributeError):
                continue
            results.append(_parse_extraction(data, conversation, persona_id, lang))

        return merge_extractions(results)
