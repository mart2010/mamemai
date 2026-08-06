# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
from math import gcd

import numpy as np
from scipy.signal import resample_poly

_KOKORO_SAMPLE_RATE = 24_000
_CLIENT_SAMPLE_RATE = 16_000
_g = gcd(_KOKORO_SAMPLE_RATE, _CLIENT_SAMPLE_RATE)  # 8000
_RESAMPLE_UP = _CLIENT_SAMPLE_RATE // _g    # 2
_RESAMPLE_DOWN = _KOKORO_SAMPLE_RATE // _g  # 3

# Maps Kokoro voice prefix to the lang_code argument KPipeline expects.
# Prefix is the first character of the voice name (e.g. "af_heart" → "a").
# No Korean entry: the installed Kokoro package (hexgrad/Kokoro-82M) has no Korean
# pipeline at all — confirmed live 2026-07-08 (KPipeline(lang_code='ko') raises; its
# only valid codes are a/b/e/f/h/i/p/j/z). "ko" was removed from SUPPORTED_LANGUAGES
# for the same reason — see docs/PLAN.md Phase 3 findings.
_PREFIX_TO_LANG: dict[str, str] = {
    "a": "a",   # American English
    "b": "b",   # British English
    "e": "e",   # Spanish
    "f": "f",   # French
    "i": "i",   # Italian
    "j": "j",   # Japanese
    "p": "p",   # Portuguese
    "z": "z",   # Mandarin Chinese
}

# Default Kokoro voice per language code (IETF tag used in Language.code).
# Verify names against the installed kokoro package — they ship with each release.
KOKORO_DEFAULT_VOICES: dict[str, str] = {
    "en":    "af_heart",
    "fr":    "ff_siwis",
    "es":    "ef_dora",
    "it":    "if_sara",
    "pt":    "pf_dora",
    "ja":    "jf_alpha",
    "zh-cn": "zf_xiaobei",
}


def _neutralise_lstm_dropout(model) -> int:
    """Zero the `dropout` argument on every LSTM in a loaded Kokoro model.

    Required to run Kokoro on ROCm, and free everywhere else. Kokoro's DurationEncoder
    builds each LSTM as `nn.LSTM(..., num_layers=1, dropout=0.1)`, which PyTorch itself
    warns about: with a single layer there is no inter-layer gap for dropout to apply to,
    so the argument never fires — in training or inference, on any backend. It is dead
    configuration.

    MIOpen, however, builds its RNN descriptor from the argument rather than from whether
    it can ever fire, and so JIT-compiles a dropout kernel whose source includes
    `<rocrand/rocrand_xorwow.h>` — a header the PyTorch ROCm wheel does not ship. The
    compile fails and the whole synthesis dies with a bare `miopenStatusUnknownError`
    (measured 2026-08-04, torch 2.12.1+rocm7.2, gfx1151). Zeroing it takes that kernel out
    of the picture without touching any other MIOpen path, unlike disabling the backend
    wholesale, which would also cost the convolutions.

    Applied unconditionally rather than only on GPU: verified numerically identical on CPU
    (max abs difference 0.0 between `dropout=0.1` and `dropout=0.0` on the same weights and
    input), so a device-conditional branch would buy nothing but a second code path.
    Returns how many modules were changed, so a silent no-op is detectable if Kokoro's
    architecture changes under us.
    """
    from torch.nn import LSTM

    patched = 0
    for module in model.modules():
        if isinstance(module, LSTM) and module.dropout:
            module.dropout = 0.0
            patched += 1
    return patched


class KokoroTTSService:
    """Kokoro TTS adapter.

    Lazily initialises one KPipeline per language prefix and resamples output
    from Kokoro's native 24 kHz to the client's expected 16 kHz.
    """

    def __init__(self, device: str | None = None) -> None:
        self._pipelines: dict[str, object] = {}
        self._device = device

    def _pipeline(self, voice: str):
        prefix = voice[0]
        if prefix not in self._pipelines:
            from kokoro import KPipeline
            lang_code = _PREFIX_TO_LANG.get(prefix, prefix)
            pipeline = KPipeline(lang_code=lang_code, device=self._device)
            # KPipeline.model is None when it was constructed without one (the caller
            # supplies output externally); nothing to patch in that case.
            if pipeline.model is not None:
                _neutralise_lstm_dropout(pipeline.model)
            self._pipelines[prefix] = pipeline
        return self._pipelines[prefix]

    def synthesise(self, text: str, voice: str, speed: float = 1.0) -> bytes:
        pipeline = self._pipeline(voice)
        chunks = [audio for _, _, audio in pipeline(text, voice=voice, speed=speed)]
        if not chunks:
            return b""
        combined = np.concatenate(chunks)
        resampled = resample_poly(combined, _RESAMPLE_UP, _RESAMPLE_DOWN).astype(np.float32)
        return resampled.tobytes()
