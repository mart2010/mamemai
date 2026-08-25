# Tasks

## Phase 13 — installed languages

- [ ] Fresh onboarding shows only installed languages.
- [ ] Speak a second installed language to GA — reply mirrors, voice switches.
- [ ] Speak an uninstalled language — primary-language reminder names
      `mamemai-setup`.
- [ ] Tutor lesson: confirm `[lang:]` tags appear in the composed context
      (`MAMEMAI_TEST_PERSONA_DEBUG=1`, prints `[persona-debug]` lines), the
      tutor treats a wrong-language attempt gently, and no tag is ever spoken.
- [ ] Re-run `mamemai-setup` and confirm the existing-install banner +
      pre-checked languages.
- [ ] Workstation config catch-up: add `[languages] installed` to its
      `mamemai.toml` (or leave absent for all-supported behaviour).

## Phase 12 — tutor flow with real audio hardware

- [ ] Confirm the persona switch, the two-teacher cast audibly switching
      voices, HVPT rotation across sessions, and the overall acoustic
      experience.

Context: the tutor flow is already verified via the plain-text LLM quality
gate (`server/tests/integration/test_tutor_llm_quality_gate.py`, 5/5 against
`aya-expanse`) and scripted-WebSocket runs, but never with real audio hardware.
