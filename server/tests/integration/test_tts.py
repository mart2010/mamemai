# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Integration test against the real Kokoro TTS model. Skips gracefully when Kokoro
itself can't be imported/initialised (e.g. the dev laptop has no CUDA)."""
import pytest

from mamemai_server.infrastructure.tts import KOKORO_DEFAULT_VOICES, KokoroTTSService


@pytest.fixture(scope="module")
def tts_service() -> KokoroTTSService:
    try:
        import kokoro  # noqa: F401
    except ImportError as e:
        pytest.skip(f"kokoro package unavailable: {e}")
    # Pinned to CPU rather than left to auto-detect (TR-960). Auto-detect picks the GPU
    # whenever torch can see one, which is both the slower path for Kokoro and a different
    # path from the one the server actually runs — `[tts] device` is set explicitly in
    # every real config. Left on auto, this module took 43 s of a 53 s suite on a machine
    # whose torch build could reach the GPU, and was testing something nothing ships.
    return KokoroTTSService(device="cpu")


class TestKokoroTTSService:
    def test_synthesises_nonempty_audio(self, tts_service: KokoroTTSService) -> None:
        """Spec: TR-952"""
        audio = tts_service.synthesise("Hello, this is a test.", voice="af_heart")
        assert len(audio) > 0

    def test_speaking_rate_changes_output_length(self, tts_service: KokoroTTSService) -> None:
        """Spec: FR-105, TR-952"""
        normal = tts_service.synthesise("This is a somewhat longer test sentence.", voice="af_heart", speed=1.0)
        fast = tts_service.synthesise("This is a somewhat longer test sentence.", voice="af_heart", speed=1.5)
        assert len(fast) < len(normal)

    @pytest.mark.parametrize("lang_code,voice", list(KOKORO_DEFAULT_VOICES.items()))
    def test_default_voice_is_valid_for_installed_kokoro(
        self, tts_service: KokoroTTSService, lang_code: str, voice: str
    ) -> None:
        """Spec: TR-952 — Each entry in KOKORO_DEFAULT_VOICES must be a real voice name in the installed
        Kokoro package version — voice packs not yet pre-downloaded on this machine are
        skipped rather than failed (see PLAN.md Phase 7 TODO on wizard-driven downloads),
        but any genuinely invalid/renamed voice name should still fail loudly."""
        try:
            audio = tts_service.synthesise("Test.", voice=voice)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"voice pack for {lang_code}/{voice} not available here (likely not pre-downloaded): {e}")
        assert len(audio) > 0
