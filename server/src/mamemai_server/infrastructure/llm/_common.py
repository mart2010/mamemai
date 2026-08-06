# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
import re
import unicodedata
from datetime import datetime, timedelta
from uuid import UUID

from ...domain.model import Concept, Conversation, Episode, Language, Speaker
from ...services.ports import ExtractionResult

# Ollama's context window when a request doesn't set `num_ctx`. Anything longer is
# silently truncated: no error, no warning, no field in the response saying so. Measured
# 2026-08-03 — this, and not any "length cliff" in the model's attention, is why
# extraction returned empty arrays for every transcript over ~14 000 characters (~4 400
# tokens of French) while the same transcript at num_ctx=16384 yielded 6-8 concepts. The
# model was answering about a transcript it had never been shown. Recorded here as a
# named constant because it is the value everything below defends against.
OLLAMA_DEFAULT_NUM_CTX = 4096

# `num_ctx` covers the prompt *and* the model's answer, so the prompt's budget is what is
# left after the answer. A flat reserve, not a per-call estimate: this whole facility is a
# canary, and the difference between a 200-token YES/NO answer and a 900-token extraction
# JSON is noise beside a prompt that overshoots by thousands.
_OUTPUT_RESERVE_TOKENS = 1024

# Characters per token, for a deliberate approximation rather than a real tokenizer.
# ~3 is conservative for the languages this runs in (English averages nearer 4, French and
# Spanish nearer 3.3 under a llama tokenizer, CJK far less) — it over-counts rather than
# under-counts, which is the right direction for a warning. A real tokenizer would mean
# either a `transformers` dependency or a per-model Ollama round trip, and the decision
# this feeds is "shout or don't", never "refuse work" (TR-951).
_CHARS_PER_TOKEN = 3


def estimate_tokens(*parts: str) -> int:
    """Approximate token count of everything that will be sent as one prompt."""
    return sum(len(part) for part in parts if part) // _CHARS_PER_TOKEN


def usable_context(num_ctx: int) -> int:
    """Tokens available to the prompt, once room for the answer is set aside."""
    return max(1, num_ctx - _OUTPUT_RESERVE_TOKENS)


def context_overflow(stage: str, num_ctx: int, *parts: str) -> bool:
    """True when a prompt won't fit `num_ctx` — and says so on stdout when it doesn't.

    The overflow itself is not the defect being fixed here; the *silence* is (TR-951). A
    truncated call produces a confident answer about content the model never saw, which
    every stage downstream reads as "nothing worth remembering": a 37-minute conversation
    was discarded twice, with no error in any log. One `[llm]` line converts an invisible
    data-loss bug into a visible one. Callers decide what to do with the answer — see the
    Ollama adapters, which skip an overflowing extraction window but still ask an
    overflowing worthiness question.
    """
    tokens = estimate_tokens(*parts)
    budget = usable_context(num_ctx)
    if tokens <= budget:
        return False
    print(
        f"[llm] CONTEXT OVERFLOW ({stage}): prompt ~{tokens} tokens exceeds the {budget} "
        f"usable of num_ctx={num_ctx} — Ollama truncates silently, so this answer would "
        "be about a prompt the model never saw"
    )
    return True


def _format_turns(turns) -> str:
    lines = []
    for turn in turns:
        role = "User" if turn.speaker == Speaker.USER else "Assistant"
        lines.append(f"{role}: {turn.content}")
    return "\n".join(lines)


# Measured on llama3.1:8b, 2026-08-02: its worthiness judgment degrades with transcript
# length, not with content. Slicing one real 72-turn conversation and asking 5 times per
# slice — first 12 turns 5/5 YES, first 24 5/5, last 24 4/5, and even the 18 turns of pure
# STT-repair dialogue (the least substantive material in it) 5/5 — while every long slice
# collapsed to 0/5: first 48, the 54 turns left after removing all repair dialogue, and
# the whole 72. Perverse exactly backwards: the longer and richer the session, the more
# certain it was to be thrown away.
# So worthiness is judged on bounded windows rather than the whole transcript. Placeholder
# value pending tuning on other models, same posture as the thresholds in services/.
WORTHINESS_WINDOW_TURNS = 24


# Re-measured 2026-08-03 at an explicit num_ctx=16384, which is the only measurement of
# this constant worth trusting: every earlier one was taken under Ollama's silent 4096
# truncation and therefore scored how well a window size avoided overflowing, not how well
# it extracted. 4 real conversations (32-72 turns), sizes 8/16/24/whole, 3 repeats each,
# scored on concepts the *user themselves named* (services/upsert.py's FR-310 provenance
# rule — an ungrounded concept is discarded downstream anyway) rather than on how many
# came back:
#
#   size    concepts  grounded  subjects  seconds
#   w=8        74.7      18.3      66.7     251
#   w=16       40.0      11.3      36.3     163
#   w=24       36.0       8.7      34.7     131
#   whole      24.0       7.0      23.7      77
#
# Two findings. **Windowing is not redundant now that the context is right**: every
# transcript fit 16384 with room to spare, yet one whole-conversation call found a third
# of the concepts and half the episodes of w=8 — a real length effect on extraction,
# distinct from the truncation bug that was masking it. And **precision is flat across
# sizes** (~25% grounded everywhere): a smaller window buys recall, not accuracy, so this
# is a volume knob and nothing more.
#
# 16 is the chosen point on that trade-off: ~30% more grounded concepts than the 24 it
# replaces, at half the near-duplicate fragmentation of w=8 and 1.25x the offline cost —
# deliberately not the recall-maximising value, keeping the 2026-07-18 false-positive
# posture. Worthiness keeps its own 24 (worthiness_transcripts): it samples, extraction
# covers, and their sizes were never the same question.
EXTRACTION_WINDOW_TURNS = 16


def _windows(turns: list, size: int) -> list[list]:
    """Consecutive, non-overlapping windows. A trailing remnant shorter than half a window
    is folded into its predecessor rather than asked about on its own — two stray turns
    are not a conversation, and asking would spend a call to be told so.

    Non-overlapping is a deliberate simplification with a known cost: a subject introduced
    just before a boundary and developed just after it is seen by neither window as a
    whole. Overlap would blur that at the price of more calls against a local model that
    serialises; revisit if extraction misses turn out to cluster on boundaries.
    """
    if len(turns) <= size:
        return [turns]
    windows = [turns[i:i + size] for i in range(0, len(turns), size)]
    if len(windows) > 1 and len(windows[-1]) < size // 2:
        # Pop first, then extend the new last window. Writing this as
        # `windows[-2] = windows[-2] + windows.pop()` looks equivalent and is not: Python
        # resolves the assignment target *after* the right-hand side, so `-2` indexes the
        # already-shortened list and the fold lands on the wrong window — silently
        # discarding the first 24 turns. Caught by test_trailing_remnant..., which is why
        # that test asserts on coverage rather than on the window count alone.
        tail = windows.pop()
        windows[-1] = windows[-1] + tail
    return windows


def extraction_transcripts(conversation: Conversation) -> list[str]:
    """One transcript per window; a short conversation stays a single call."""
    return [_format_turns(w) for w in _windows(conversation.turns, EXTRACTION_WINDOW_TURNS)]


# How far outside a conversation's own span an episode may be dated before it stops
# counting as "during this chat". One minute, because the observed failure needs only
# sub-second slack: a stored episode dated 15:43:20 against a conversation starting
# 15:43:20.021288 is the same instant with the microseconds truncated away. A legitimate
# life episode dated inside the conversation would have to have happened while the user
# was talking to the assistant about it, which is not the shape of a life episode.
_EPISODE_CHAT_TOLERANCE = timedelta(seconds=60)


def user_turns_only(conversation: Conversation) -> Conversation:
    """The same conversation with the assistant's turns removed (TR-961).

    Episodes are extracted from this, concepts from the full transcript. The fabrications
    that motivated it are summaries of *assistant* content — measured 2026-08-04, the full
    transcript of a 32-turn astronomy conversation produced "Hier soir, j'ai observé la
    nébuleuse de l'Oiseau avec mon télescope" and "Le week-end dernier, je suis allé au
    parc", two dated first-person life events that never happened. On a transcript holding
    only what the user actually said, those are not discouraged but unconstructible: the
    same withhold-the-signal posture as FR-114, a filter rather than a plea. Same
    conversation produced 0 episodes this way.

    Everything but the turns is preserved, so the extractor sees an otherwise identical
    object and only the transcript differs.
    """
    return Conversation(
        id=conversation.id,
        started_at=conversation.started_at,
        persona_id=conversation.persona_id,
        turns=[t for t in conversation.turns if t.speaker == Speaker.USER],
        ended_at=conversation.ended_at,
        worthiness=conversation.worthiness,
        summary=conversation.summary,
        consolidated=conversation.consolidated,
    )


def drop_chat_dated_episodes(episodes: list[Episode], conversation: Conversation) -> list[Episode]:
    """Drop episodes dated inside the conversation that produced them (TR-961).

    The extraction prompt already demands "a genuine, identifiable time or place ...
    distinct from 'during this chat'", and cannot enforce it: a model that files the
    conversation itself as a life event dates it with the conversation's own timestamp.
    Measured on the real database — **8 of 9 stored episodes fall inside their own
    conversation's span**, and the ninth misses by 0.02s because its timestamp was
    truncated to whole seconds. All nine are of the form "Discussion sur ...", "User
    inquired about ...": records of the chat, not of the user's life (INV-10).

    Complementary to `user_turns_only`, not redundant with it: that removes episodes
    invented from assistant content, this removes episodes about the conversation itself,
    and the measured examples of each survive the other.
    """
    if conversation.started_at is None:
        return episodes
    end = conversation.ended_at or conversation.started_at
    low = conversation.started_at - _EPISODE_CHAT_TOLERANCE
    high = end + _EPISODE_CHAT_TOLERANCE
    kept = []
    for episode in episodes:
        if episode.happened_at is not None and low <= episode.happened_at <= high:
            print(f"[extraction] dropped episode dated during the conversation itself: "
                  f"{episode.summary[:60]!r}")
            continue
        kept.append(episode)
    return kept


_GROUNDING_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize_for_grounding(text: str) -> str:
    """Casefold, strip accents/punctuation, collapse whitespace. Enough slack to match a
    verbatim quote against its source past minor STT/formatting noise, without being loose
    enough to accept a paraphrase instead of an actual quote."""
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _GROUNDING_PUNCT_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_grounded(evidence: str | None, transcript: str) -> bool:
    """Does the claimed verbatim quote `evidence` actually occur in `transcript`?"""
    if not evidence:
        return False
    normalized_evidence = _normalize_for_grounding(evidence)
    if not normalized_evidence:
        return False
    return normalized_evidence in _normalize_for_grounding(transcript)


def drop_ungrounded_episode_entries(entries: list[dict], transcript: str) -> list[dict]:
    """Drop episode entries whose claimed "evidence" quote does not actually occur in the
    transcript that produced them (TR-961, third filter).

    `user_turns_only` stops episodes fabricated by generalising from the *assistant's*
    turns, and `drop_chat_dated_episodes` stops the conversation itself being filed as a
    life event — but measured live 2026-08-06: a session where the user asked nothing but
    general astronomy questions (no assistant fabrication involved, no in-chat timestamp
    for the other filter to catch) still produced a stored episode reading, in French,
    "the user saw the northern lights in northern France" — invented purely from the topic
    word "aurores boréales" appearing in the user's own turns, with nothing in those turns
    actually claiming it happened to them.

    The extraction prompt now asks for an "evidence" field alongside each episode: a
    verbatim quote from the user's own words that states it. A hallucinated episode's
    supporting quote is either missing or itself invented, and an invented quote does not
    literally occur in the transcript it claims to come from — while a genuine one
    trivially does, since copying a snippet of text already in front of the model is not a
    hard task. Same withhold-the-signal posture as the other two filters: this does not
    ask the model to not invent, it makes an invented answer unable to pass.
    """
    kept = []
    for entry in entries:
        evidence = entry.get("evidence")
        if not _is_grounded(evidence if isinstance(evidence, str) else None, transcript):
            summary_preview = entry.get("summary")
            print(f"[extraction] dropped ungrounded episode (no matching quote in the "
                  f"user's own words): {str(summary_preview)[:60]!r}")
            continue
        kept.append(entry)
    return kept


def merge_extractions(results: list[ExtractionResult]) -> ExtractionResult:
    """Fold per-window results into one, first occurrence winning.

    Concept de-duplication by name is **required for correctness**, not tidiness. Two
    windows of one conversation proposing the same concept would be upserted twice; the
    second call would match the row the first just wrote and promote its engagement level
    (`MemoryUpserter.upsert_concept`) — manufacturing exactly the "repetition inside one
    sitting" that FR-310 rules out as evidence. Windowing must not quietly reintroduce it.

    Episodes are deduplicated only on an exact summary/time match, which is the honest
    limit of a string comparison: two windows describing one event in different words fall
    through to `upsert_episode`, whose embedding-similarity merge is designed for precisely
    that and handles it properly.

    Relies on `_parse_extraction` having dropped items with no usable name or summary —
    the keys below are built from those fields, and a null one used to raise out of here
    and take every window's results with it. Guarded there rather than here so the bad
    item dies alone instead of costing its window.
    """
    concepts: dict[str, Concept] = {}
    episodes: list[Episode] = []
    seen_episodes: set[tuple] = set()
    for result in results:
        for concept in result.concepts:
            concepts.setdefault(concept.name.strip().lower(), concept)
        for episode in result.episodes:
            key = (episode.happened_at, episode.summary.strip().lower())
            if key not in seen_episodes:
                seen_episodes.add(key)
                episodes.append(episode)
    return ExtractionResult(episodes=episodes, concepts=list(concepts.values()))


def worthiness_transcripts(conversation: Conversation) -> list[str]:
    """One transcript for a short conversation; the opening and closing windows for a long
    one, to be judged independently — worthy if *either* is (see the evaluators).

    Two windows rather than one because a long session earns its keep at either end: the
    subject that opened it, or whatever it turned into by the close. Nothing in the middle
    is sampled, which is a real gap and a deliberate one — a third call per conversation
    buys little when both ends are already representative, and this runs offline on a
    local model that serialises.
    """
    turns = conversation.turns
    if len(turns) <= WORTHINESS_WINDOW_TURNS:
        return [_format_turns(turns)]
    return [
        _format_turns(turns[:WORTHINESS_WINDOW_TURNS]),
        _format_turns(turns[-WORTHINESS_WINDOW_TURNS:]),
    ]


def _conversation_language(conversation: Conversation) -> Language:
    for turn in conversation.turns:
        if turn.language:
            return turn.language
    return Language("en")


# Shared by the Ollama and OpenRouter worthiness evaluators so the criteria cannot
# drift between the two. The 2026-07-18 live-testing review found the LLM correctly
# judging a substantial-but-meta conversation (debugging a TTS voice-download bug) as
# "worth storing," then extraction turned that debugging session into a fabricated
# personal-event episode — a content-category failure, not a threshold one, so this
# prompt explicitly excludes conversation-about-itself/the-assistant's-own-operation
# from what counts as worth storing, rather than just raising a bar.
#
# Rebalanced 2026-08-02 after a measured NO bias. The previous wording stated the YES
# case in one clause and the NO case in five vivid ones, and llama3.1:8b duly said NO to
# almost everything: over a labelled probe set it scored 14-15/24, getting every negative
# case right (12/12) and failing the positives — including "I went to Norway last weekend
# and finally saw the northern lights, I work as an engineer", which is a personal fact
# and a dated event in plain English. Live, every French conversation in the database had
# been judged unworthy (0/4, against 9/18 for English), and a 37-minute astronomy session
# was discarded whole. Giving the YES criteria their own enumerated list, and narrowing
# NO to "nothing but", scores 24/24 on the same set with no other change; adding a
# language-neutrality clause or few-shot examples on top measured identically, so neither
# is here (see the FR-307 quality gate).
#
# "in English" is load-bearing, not decoration: the reply is parsed by `is_affirmative`,
# and a French transcript did draw a bare "OUI" from this model at least once.
WORTHINESS_SYSTEM_PROMPT = (
    "You decide whether a conversation is worth extracting into the user's long-term memory.\n"
    "\n"
    "Answer YES if it contains any of:\n"
    "- a fact about the user: their life, work, plans, preferences, relationships\n"
    "- a real event with a time or a place\n"
    "- a subject they discussed or learned about with any substance\n"
    "\n"
    "Answer NO only if it is nothing but:\n"
    "- greetings, small talk, or a trivial exchange\n"
    "- about the assistant itself or this session: bugs, errors, testing, debugging, or "
    "questions about its own capabilities, configuration or model\n"
    "\n"
    "Reply with exactly one word, in English: YES or NO."
)

# A one-word YES/NO answer is asked for in English, but a model reading a French or
# Japanese transcript does not always comply — a bare "OUI" was observed from
# llama3.1:8b. The previous `"yes" in reply.lower()` read that as NO, silently, which is
# the worst possible failure mode for a gate: indistinguishable from a real verdict.
# Shared by both providers and by both judgments (worthiness, disambiguation) so the four
# call sites cannot drift.
_AFFIRMATIVE = {"yes", "y", "true", "oui", "si", "sí", "sim", "ja", "はい", "是", "是的", "对"}
_NEGATIVE = {"no", "n", "false", "non", "nao", "não", "nein", "いいえ", "不", "不是"}
_WORDS = re.compile(r"\w+", re.UNICODE)


def is_affirmative(reply: str | None) -> bool:
    """True when a one-word YES/NO answer means yes, in any supported language.

    Decided on the *first* word, so a model that explains itself ("No — this is only
    small talk, yes it is trivial") is read by its verdict rather than by whichever token
    happens to appear in the sentence. An unrecognised first word falls back to scanning
    the remaining *words* — never substrings, or "si" would match "consider" — and then
    to False: a gate that cannot read the answer must not invent a yes.
    """
    if not reply:
        return False
    words = _WORDS.findall(reply.lower())
    if not words:
        return False
    if words[0] in _NEGATIVE:
        return False
    if words[0] in _AFFIRMATIVE:
        return True
    return any(word in _AFFIRMATIVE for word in words)


def _extraction_system_prompt(
    conversation: Conversation,
    primary_language: Language | None,
    extract_episodes: bool = True,
    extract_concepts: bool = True,
) -> str:
    """Shared by the Ollama and OpenRouter extractors so the extraction contract
    (JSON shape, episode-language rule) cannot drift between the two.

    `extract_episodes=False` (set by ConsolidateMemory from the persona's own
    `extracts_episodes` declaration, FR-407) omits the "episodes" array from the
    requested schema entirely, rather than asking then discarding: rehearsed material —
    drills, role-play, exercises — is not a record of real events, and asking a small
    local model to judge genuine-story-vs-rehearsal after the fact was tried and is not
    reliable — better not to ask at all. This function stays persona-agnostic either
    way: it only ever sees a plain bool, never who set it or why.

    Procedures are never requested here, for any persona: how-to knowledge belongs to
    authoring expertise (bundles), not something live conversation organically produces
    (FR-307) — simplifies the LLM's job to episodes (when asked) and concepts only.
    """
    # Episodes are persona-independent and carry no language field — summaries are always
    # written in the user's primary language regardless of conversation language, so months
    # of conversing with one persona don't rewrite the user's life story into its language.
    episode_language_rule = (
        f" Write every episode summary in the language with IETF code '{primary_language.code}', "
        "regardless of the language the conversation was held in."
        if primary_language
        else ""
    )
    episodes_section = (
        '- "episodes": real personal events from the user\'s own life — NOT this '
        "conversation itself, and NOT anything about the assistant's own operation "
        "(bugs, errors, testing, debugging, capability or configuration questions). "
        "Only include an episode if it has a genuine, identifiable time or place "
        '(e.g. "yesterday", "last weekend", "at work", "in Paris") distinct from '
        "\"during this chat\" — if you can't name when or where it happened, don't "
        'extract it (each: {"summary": str, "happened_at": ISO8601 datetime of when it '
        'happened, or null only if genuinely unknown, "evidence": a short quote copied '
        "word-for-word from the user's own turns above proving they said this — if you "
        'cannot quote their own words stating it, do not include the episode}).'
        f"{episode_language_rule}\n"
        if extract_episodes
        else ""
    )
    # `extract_concepts=False` is the mirror of the flag above and exists for the
    # episode-only pass (TR-961): episodes are read from a transcript of the user's turns
    # alone, where asking for concepts as well would be actively harmful — the assistant's
    # side is where correct terminology lives, so concepts must keep the full transcript.
    concepts_section = (
        '- "concepts": facts or knowledge the user learned or discussed '
        '(each: {"name": str, "description": str, "language": IETF code, '
        '"category": short lowercase classification label or null})\n'
        if extract_concepts
        else ""
    )
    return (
        "Extract structured memories from this conversation. "
        f"The conversation took place around {conversation.started_at.isoformat()}.\n"
        "Return JSON with these arrays:\n"
        f"{episodes_section}"
        f"{concepts_section}"
        "Be selective — only include what is genuinely informative. "
        "Leave arrays empty when nothing qualifies."
    )


def _text(item: dict, key: str) -> str | None:
    """A required text field, or None when the model didn't really supply one.

    Well-formed JSON is not well-formed *content*: ~2% of measured extraction runs came
    back with a syntactically perfect object whose `name` was `null`, and one of those
    used to raise `AttributeError` out of `merge_extractions` and discard every window's
    results for the conversation — the whole point of per-window skipping, defeated by a
    field the JSON decoder was perfectly happy with. Checked here, at the boundary where
    the model's answer becomes domain objects, so only the bad *item* is dropped and the
    good ones beside it in the same window survive. `null`, a missing key, whitespace and
    a non-string (a number, a nested object) are all the same answer: no value.
    """
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _entries(data: dict, key: str) -> list[dict]:
    """The named array, tolerating the shapes a model reaches for instead of one.

    `"concepts": null` is not an empty array to `dict.get`, and a bare string where an
    object was asked for has no `.get` — both used to be an exception, i.e. the whole
    conversation lost. Same posture as everything else here: an item that can't be read
    is not an item.
    """
    entries = data.get(key)
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _parse_extraction(
    data: dict, conversation: Conversation, persona_id: UUID, lang: Language,
    episode_transcript: str | None = None,
) -> ExtractionResult:
    """`episode_transcript` is the transcript the model actually saw for this call — when
    given, each episode entry's "evidence" quote is required to actually occur in it
    (`drop_ungrounded_episode_entries`, TR-961's third filter) before being kept."""
    origin_id = conversation.id or 0

    episode_entries = _entries(data, "episodes")
    if episode_transcript is not None:
        episode_entries = drop_ungrounded_episode_entries(episode_entries, episode_transcript)

    episodes = []
    for e in episode_entries:
        summary = _text(e, "summary")
        if summary is None:
            continue
        # No genuine time grounding (missing/unparseable happened_at) means the model
        # couldn't name a real when/where — treat as not a real episode rather than
        # silently backdating it to the conversation's own timestamp, which previously
        # let conversation-about-itself content masquerade as a dated personal event.
        happened_at_raw = _text(e, "happened_at")
        if not happened_at_raw:
            continue
        try:
            happened_at = datetime.fromisoformat(happened_at_raw)
        except ValueError:
            continue
        episodes.append(Episode(
            id=None,
            summary=summary,
            happened_at=happened_at,
            origin_conversation_id=origin_id,
        ))

    concepts = []
    for c in _entries(data, "concepts"):
        # A concept with no name is not a concept, and one with no description carries
        # nothing to remember: both are dropped rather than stored empty, since the merge
        # below keys on the name and every consumer downstream reads the description.
        name = _text(c, "name")
        description = _text(c, "description")
        if name is None or description is None:
            continue
        try:
            entry_lang = Language(_text(c, "language") or lang.code)
        except ValueError:
            entry_lang = lang
        concepts.append(Concept(
            id=None,
            persona_id=persona_id,
            name=name,
            description=description,
            language=entry_lang,
            category=_text(c, "category"),
            origin="organic",
        ))

    return ExtractionResult(episodes=episodes, concepts=concepts)
