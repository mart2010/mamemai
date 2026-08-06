# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
from __future__ import annotations

# How close a stored memory must be to the question before it counts as a recall
# (FR-309). Measured 2026-08-03 against the real database (64 concepts, 13 procedures,
# 9 episodes) and the real embedding model, 26 probe questions in French, English and
# Italian — half about subjects genuinely in the database, half about subjects genuinely
# absent, each scored by its best hit:
#
#   absent  0.779 … 0.844   (n=14)
#   present 0.852 … 0.942   (n=12)
#
# That gave 0 errors *at the time*, on a ~0.008 margin. Re-measured 2026-08-04 on the
# same database it no longer does — 1 false recall and 1 lost recall — which is what a
# margin that thin looks like once a few more rows exist. The cause was the missing
# `query:`/`passage:` prefixes (TR-509), now added, and the floor is re-derived on the
# scale that produced:
#
#   absent  0.769 … 0.834   (n=15)
#   present 0.833 … 0.903   (n=16)
#
# **0.84**, chosen off the error curve rather than the range. Zero false recalls begins
# at 0.8342, and 0.835 through 0.840 all give the same result (0 false, 1 lost), so 0.84
# sits mid-plateau instead of balanced on the boundary. Below it the false recalls come
# back fast (2 at 0.830, 4 at 0.825); above it real recalls fall away (2 lost at 0.845,
# 6 at 0.855).
#
# The asymmetry is deliberate and is why the floor is not placed to minimise total
# errors: a false recall makes the assistant claim to remember something that never
# happened — the failure FR-309's honesty branch exists to prevent, and the one observed
# live on 2026-08-03. A lost recall merely means it answers without mentioning memory.
#
# Still one database, and still worth re-deriving as it grows: the per-language margins
# are +0.018 (fr), +0.027 (en), +0.017 (it), all positive, but a *single* global floor
# only works because those bands happen to overlap. A language-aware floor is the
# principled fix and is recorded as follow-up — see docs/PLAN.md.
MIN_RECALL_SIMILARITY = 0.84


class DefaultRecallGate:
    """The RecallGate every persona gets unless it registers its own (FR-309/TR-314).
    Two independent short-circuits, in order:

    1. should_embed: skip anything that isn't a real, substantial question — not
       worth an embedding call or a DB round trip. Memory recall answers questions
       ("what did I tell you about X", "do you remember Y") — a plain statement isn't
       asking to be answered from memory, so it's excluded outright now, not just
       short ones (2026-07-30: previously any sufficiently long utterance, statement
       or not, could trigger recall — narrowed after review found that too permissive
       for the CPU cost). Requires a literal "?" in the text — faster-whisper doesn't
       strip punctuation anywhere in this codebase, so a genuinely spoken question
       normally comes through with one; not perfectly reliable (STT punctuation is
       inferred from intonation/syntax, not guaranteed) but a cheap, deterministic
       proxy, same posture as `min_words` itself. `min_words` is a word-count floor,
       not a character count (tracks "is there enough content here" across languages
       better than length), raised from 3 to 6 alongside the "?" requirement
       specifically to also exclude short reflexive questions ("How are you?", "What's
       up?") that aren't real memory-recall requests.
    2. is_relevant: given the DB answered, decide whether a returned item is close
       enough to count as a recall at all. `MemoryRepository.search` is
       `ORDER BY distance LIMIT top_n` with no floor of its own — with a non-empty
       database it *always* returns `top_n` rows, however unrelated, which is what made
       "I checked and found nothing" unreachable: `recalled_memories` was never empty, so
       every recall-eligible turn took the "briefly acknowledge you're drawing on a past
       conversation" branch. Observed live 2026-08-03 — asked whether zodiacal light had
       come up before, a subject absent from the database, the reply opened "Oui, nous en
       avons déjà parlé". The floor makes "nothing close enough" representable, so honesty
       stops depending on the model overriding a contrary instruction — the same
       deterministic-filter-over-plea posture as FR-114 and `matches_trigger`.
    3. should_search: given an embedding was computed, skip the DB round trip when
       it's nearly identical to *any* prior search this session (not just the most
       recent one) — the cached results from that search are still the best available
       answer, so paying for another lookup buys nothing. Comparing against the whole
       history rather than only the last entry matters because nothing new can enter
       long-term memory mid-session (INV-1): the searchable set is frozen for the
       whole conversation, so a repeat of any earlier query, not only the immediately
       preceding one, would return the same thing again. `dedup_threshold` reuses this
       project's existing 0.93 "treat as the same thing" bar (ConsolidateMemory's
       merge_threshold, [memory].merge_threshold) rather than inventing a new
       placeholder constant."""

    def __init__(
        self, min_words: int = 6, dedup_threshold: float = 0.93, min_similarity: float = MIN_RECALL_SIMILARITY,
    ) -> None:
        self._min_words = min_words
        self._dedup_threshold = dedup_threshold
        self._min_similarity = min_similarity

    def should_embed(self, text: str) -> bool:
        return "?" in text and len(text.split()) >= self._min_words

    def is_relevant(self, similarity: float) -> bool:
        return similarity >= self._min_similarity

    def should_search(self, max_similarity_to_prior_searches: float | None) -> bool:
        if max_similarity_to_prior_searches is None:
            return True
        return max_similarity_to_prior_searches < self._dedup_threshold
