from pathlib import Path

import pytest

from mamemai_server.infrastructure.config import (
    DEFAULT_CPU_THREADS,
    DEFAULT_MIN_UTTERANCE_MS,
    DEFAULT_NUM_CTX,
    load_config,
)
from mamemai_server.infrastructure.llm._common import OLLAMA_DEFAULT_NUM_CTX


def _write_toml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "mamemai.toml"
    path.write_text(content)
    return path


class TestLoadConfigComputeDevice:
    def test_tts_device_defaults_to_none_when_tts_section_absent(self, tmp_path: Path):
        """Spec: TR-951"""
        path = _write_toml(
            tmp_path,
            """
            [stt]
            device = "cpu"
            compute_type = "int8"
            [llm]
            model = "aya-expanse"
            """,
        )

        cfg = load_config(path)

        assert cfg.tts_device is None

    def test_tts_device_read_from_config_when_present(self, tmp_path: Path):
        """Spec: TR-951"""
        path = _write_toml(
            tmp_path,
            """
            [tts]
            device = "cpu"
            """,
        )

        cfg = load_config(path)

        assert cfg.tts_device == "cpu"

    def test_embedding_device_defaults_to_none_when_section_absent(self, tmp_path: Path):
        """Spec: TR-951, TR-960 — None means "let sentence-transformers auto-detect",
        which is what this adapter did before the setting existed."""
        path = _write_toml(tmp_path, "")

        cfg = load_config(path)

        assert cfg.embedding_device is None

    def test_embedding_device_read_from_config_when_present(self, tmp_path: Path):
        """Spec: TR-951, TR-960"""
        path = _write_toml(tmp_path, '[embedding]\ndevice = "cuda"\n')

        cfg = load_config(path)

        assert cfg.embedding_device == "cuda"

    def test_embedding_and_tts_devices_are_independent(self, tmp_path: Path):
        """Spec: TR-960 — the measured result differs per model on the *same* GPU (e5
        gains, Kokoro loses 7x), so one shared "gpu device" setting would force a wrong
        answer on one of them."""
        path = _write_toml(tmp_path, '[tts]\ndevice = "cpu"\n\n[embedding]\ndevice = "cuda"\n')

        cfg = load_config(path)

        assert (cfg.tts_device, cfg.embedding_device) == ("cpu", "cuda")

    def test_stt_device_and_compute_type_default_to_cpu_safe_values_when_section_absent(self, tmp_path: Path):
        """Spec: TR-951"""
        path = _write_toml(tmp_path, "")

        cfg = load_config(path)

        assert cfg.stt_device == "cpu"
        assert cfg.stt_compute_type == "int8"

    def test_stt_cpu_threads_defaults_to_the_measured_value(self, tmp_path: Path):
        """Spec: TR-951, TR-956 — a measured constant, not CTranslate2's own default and
        deliberately not `nproc`: on this machine medium/int8 peaks at 8 threads and is
        *slower* at 16 and 24 (2026-08-02). Left unset, CTranslate2 picks 4 and leaves a
        24-core box running STT on a sixth of itself."""
        path = _write_toml(tmp_path, "")

        cfg = load_config(path)

        assert cfg.stt_cpu_threads == DEFAULT_CPU_THREADS

    def test_stt_cpu_threads_read_from_config_when_present(self, tmp_path: Path):
        """Spec: TR-951 — overridable, since the sweet spot is hardware-specific."""
        path = _write_toml(
            tmp_path,
            """
            [stt]
            cpu_threads = 16
            """,
        )

        cfg = load_config(path)

        assert cfg.stt_cpu_threads == 16

    def test_min_utterance_ms_defaults_when_absent(self, tmp_path: Path):
        """Spec: TR-951, TR-958 — an absent key is a config written before the floor
        existed, and must still get the floor: those are exactly the installs whose
        clients predate the onset gate and so send the most noise."""
        path = _write_toml(tmp_path, "")

        cfg = load_config(path)

        assert cfg.stt_min_utterance_ms == DEFAULT_MIN_UTTERANCE_MS

    def test_min_utterance_ms_read_from_config_when_present(self, tmp_path: Path):
        """Spec: TR-951, TR-958 — overridable per room, same as cpu_threads is per machine."""
        path = _write_toml(
            tmp_path,
            """
            [stt]
            min_utterance_ms = 500
            """,
        )

        cfg = load_config(path)

        assert cfg.stt_min_utterance_ms == 500

    def test_min_utterance_ms_of_zero_disables_the_floor(self, tmp_path: Path):
        """Spec: TR-958 — 0 is the escape hatch, and must not be swallowed by an
        `or DEFAULT` idiom: a user who suspects the floor is eating real turns needs a way
        to take it out of the picture without reinstalling."""
        path = _write_toml(tmp_path, "[stt]\nmin_utterance_ms = 0\n")

        cfg = load_config(path)

        assert cfg.stt_min_utterance_ms == 0


class TestLoadConfigLLMProvider:
    def test_defaults_to_ollama_when_llm_section_absent(self, tmp_path: Path):
        """Spec: FR-707, TR-955 — fully backward compatible with configs that
        predate this setting."""
        path = _write_toml(tmp_path, "")

        cfg = load_config(path)

        assert cfg.llm_provider == "ollama"
        assert cfg.llm_base_url is None
        assert cfg.llm_remote_model is None
        assert cfg.llm_api_key is None

    def test_ollama_provider_does_not_require_base_url_or_remote_model(self, tmp_path: Path):
        """Spec: FR-707 — provider="ollama" (default or explicit) keeps using
        llm_model/llm_ollama_host for the live path too, same as before this
        setting existed."""
        path = _write_toml(
            tmp_path,
            """
            [llm]
            provider = "ollama"
            model = "aya-expanse"
            """,
        )

        cfg = load_config(path)

        assert cfg.llm_provider == "ollama"
        assert cfg.llm_model == "aya-expanse"

    def test_openai_compatible_provider_reads_base_url_model_and_key(self, tmp_path: Path):
        """Spec: FR-707, TR-955"""
        path = _write_toml(
            tmp_path,
            """
            [llm]
            provider = "openai_compatible"
            base_url = "https://openrouter.ai/api/v1"
            remote_model = "meta-llama/llama-3.3-70b-instruct"
            api_key = "sk-example"
            """,
        )

        cfg = load_config(path)

        assert cfg.llm_provider == "openai_compatible"
        assert cfg.llm_base_url == "https://openrouter.ai/api/v1"
        assert cfg.llm_remote_model == "meta-llama/llama-3.3-70b-instruct"
        assert cfg.llm_api_key == "sk-example"

    def test_openai_compatible_provider_allows_missing_api_key(self, tmp_path: Path):
        """Spec: FR-707 — the key is optional even for the remote provider (some
        self-hosted OpenAI-compatible endpoints don't require one)."""
        path = _write_toml(
            tmp_path,
            """
            [llm]
            provider = "openai_compatible"
            base_url = "http://localhost:8080/v1"
            remote_model = "local-model"
            """,
        )

        cfg = load_config(path)

        assert cfg.llm_api_key is None

    def test_openai_compatible_provider_without_base_url_raises(self, tmp_path: Path):
        """Spec: FR-707 — fail fast at config-load time rather than at first live turn."""
        path = _write_toml(
            tmp_path,
            """
            [llm]
            provider = "openai_compatible"
            remote_model = "some-model"
            """,
        )

        with pytest.raises(RuntimeError, match="base_url"):
            load_config(path)

    def test_openai_compatible_provider_without_remote_model_raises(self, tmp_path: Path):
        """Spec: FR-707"""
        path = _write_toml(
            tmp_path,
            """
            [llm]
            provider = "openai_compatible"
            base_url = "https://openrouter.ai/api/v1"
            """,
        )

        with pytest.raises(RuntimeError, match="remote_model"):
            load_config(path)

    def test_invalid_provider_value_raises(self, tmp_path: Path):
        """Spec: FR-707"""
        path = _write_toml(
            tmp_path,
            """
            [llm]
            provider = "anthropic-direct"
            """,
        )

        with pytest.raises(RuntimeError, match="ollama.*openai_compatible"):
            load_config(path)


class TestLoadConfigInstalledLanguages:
    def test_installed_languages_read_from_languages_section(self, tmp_path: Path):
        """Spec: TR-951, FR-705"""
        path = _write_toml(
            tmp_path,
            """
            [languages]
            installed = ["en", "fr"]
            """,
        )

        cfg = load_config(path)

        assert cfg.installed_languages == ("en", "fr")

    def test_installed_languages_empty_when_section_absent(self, tmp_path: Path):
        """Spec: TR-951 — configs written before the key existed: empty tuple, the
        composition root then treats all of SUPPORTED_LANGUAGES as installed."""
        path = _write_toml(tmp_path, "")

        cfg = load_config(path)

        assert cfg.installed_languages == ()


class TestLoadConfigSessionTailTurns:
    def test_defaults_to_ten_when_absent(self, tmp_path: Path):
        """Spec: TR-951, FR-109"""
        path = _write_toml(tmp_path, "")

        cfg = load_config(path)

        assert cfg.session_tail_turns == 10

    def test_read_from_server_section(self, tmp_path: Path):
        """Spec: TR-951, FR-109 — 0 disables session-tail injection entirely."""
        path = _write_toml(tmp_path, "[server]\nsession_tail_turns = 0\n")

        cfg = load_config(path)

        assert cfg.session_tail_turns == 0


class TestLoadConfigNumCtx:
    def test_defaults_to_the_measured_value_not_ollamas(self, tmp_path: Path):
        """Spec: TR-951, TR-952 — a config written before this key existed must not
        inherit Ollama's silently-truncating 4096: that default is what returned empty
        extractions for every transcript over ~14 000 characters."""
        path = _write_toml(tmp_path, "")

        cfg = load_config(path)

        assert cfg.llm_num_ctx == DEFAULT_NUM_CTX
        assert cfg.llm_num_ctx > OLLAMA_DEFAULT_NUM_CTX

    def test_read_from_llm_section(self, tmp_path: Path):
        """Spec: TR-951 — overridable, since the usable value is bounded by the model's
        ceiling and by VRAM, both machine-specific."""
        path = _write_toml(tmp_path, "[llm]\nnum_ctx = 32768\n")

        cfg = load_config(path)

        assert cfg.llm_num_ctx == 32768

    def test_read_even_when_the_live_provider_is_remote(self, tmp_path: Path):
        """Spec: TR-951, TR-955 — FR-707 moves only the live reply off-machine; the
        offline pipeline stays on local Ollama, which is exactly where the truncation
        cliff was found, so the knob is needed whatever the provider is."""
        path = _write_toml(
            tmp_path,
            """
            [llm]
            provider = "openai_compatible"
            base_url = "http://localhost:8080/v1"
            remote_model = "some-model"
            num_ctx = 8192
            """,
        )

        cfg = load_config(path)

        assert cfg.llm_num_ctx == 8192
