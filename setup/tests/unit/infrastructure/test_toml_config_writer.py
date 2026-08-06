import stat
import tomllib
from pathlib import Path

from mamemai_setup.domain.plan import InstallationPlan
from mamemai_setup.infrastructure.config_writer import TomlConfigWriter


def _write_and_read(tmp_path: Path, plan: InstallationPlan) -> dict:
    path = tmp_path / "mamemai.toml"
    TomlConfigWriter(path).write_server_config(plan)
    with open(path, "rb") as f:
        return tomllib.load(f)


def test_writes_cpu_device_and_int8_compute_type_when_plan_says_cpu(tmp_path: Path):
    plan = InstallationPlan(compute_device="cpu")

    config = _write_and_read(tmp_path, plan)

    assert config["stt"]["device"] == "cpu"
    assert config["stt"]["compute_type"] == "int8"


def test_writes_cuda_device_and_float16_compute_type_when_plan_says_cuda(tmp_path: Path):
    plan = InstallationPlan(compute_device="cuda")

    config = _write_and_read(tmp_path, plan)

    assert config["stt"]["device"] == "cuda"
    assert config["stt"]["compute_type"] == "float16"


def test_writes_tts_section_with_matching_device(tmp_path: Path):
    plan = InstallationPlan(compute_device="cpu")

    config = _write_and_read(tmp_path, plan)

    assert config["tts"]["device"] == "cpu"


def test_writes_selected_languages_as_installed_languages(tmp_path: Path):
    """Spec: FR-705 — the wizard's language selection is the installed-languages
    contract the server reads back for onboarding and response mirroring."""
    plan = InstallationPlan(languages=["en", "fr", "es"])

    config = _write_and_read(tmp_path, plan)

    assert config["languages"]["installed"] == ["en", "fr", "es"]


def test_ollama_provider_writes_no_remote_endpoint_keys(tmp_path: Path):
    """Spec: FR-707 — provider/base_url/remote_model/api_key are omitted entirely in the
    common case; [llm] carries only what a local install needs."""
    plan = InstallationPlan(llm_model_id="aya-expanse", llm_provider="ollama")

    config = _write_and_read(tmp_path, plan)

    assert config["llm"] == {"model": "aya-expanse", "num_ctx": 16384}


def test_writes_num_ctx_explicitly(tmp_path: Path):
    """Spec: TR-951, TR-952 — leaving it out is not neutral: Ollama then uses 4096 and
    truncates longer prompts silently, which is what discarded whole conversations from
    long-term memory before 2026-08-03. Written even though the server defaults to the
    same value, so the knob is visible in the generated file."""
    plan = InstallationPlan(llm_model_id="aya-expanse")

    config = _write_and_read(tmp_path, plan)

    assert config["llm"]["num_ctx"] == 16384


def test_a_num_ctx_carried_from_an_existing_install_is_preserved(tmp_path: Path):
    """Spec: TR-951, FR-706 — a re-run must not reset a value the operator tuned for
    their own VRAM back to the default."""
    plan = InstallationPlan(llm_model_id="aya-expanse", llm_num_ctx=32768)

    config = _write_and_read(tmp_path, plan)

    assert config["llm"]["num_ctx"] == 32768


def test_openai_compatible_provider_writes_provider_base_url_and_remote_model(tmp_path: Path):
    """Spec: FR-707"""
    plan = InstallationPlan(
        llm_model_id="aya-expanse",
        llm_provider="openai_compatible",
        llm_base_url="https://openrouter.ai/api/v1",
        llm_remote_model="meta-llama/llama-3.3-70b-instruct",
    )

    config = _write_and_read(tmp_path, plan)

    assert config["llm"]["model"] == "aya-expanse"
    assert config["llm"]["provider"] == "openai_compatible"
    assert config["llm"]["base_url"] == "https://openrouter.ai/api/v1"
    assert config["llm"]["remote_model"] == "meta-llama/llama-3.3-70b-instruct"
    assert "api_key" not in config["llm"]


def test_openai_compatible_provider_writes_api_key_when_present(tmp_path: Path):
    """Spec: FR-707"""
    plan = InstallationPlan(
        llm_provider="openai_compatible",
        llm_base_url="https://openrouter.ai/api/v1",
        llm_remote_model="some-model",
        llm_api_key="sk-example",
    )

    config = _write_and_read(tmp_path, plan)

    assert config["llm"]["api_key"] == "sk-example"


def test_writes_recorded_schema(tmp_path: Path):
    """Spec: TR-5xx"""
    plan = InstallationPlan(database_schema="alice")

    config = _write_and_read(tmp_path, plan)

    assert config["database"]["schema"] == "alice"


def test_falls_back_to_public_schema_when_plan_has_none(tmp_path: Path):
    """Spec: TR-5xx — a plan that never ran ConfigureUserIdentity (e.g. a
    programmatically-built InstallationPlan in a test) still writes a valid config."""
    plan = InstallationPlan()

    config = _write_and_read(tmp_path, plan)

    assert config["database"]["schema"] == "public"


def test_writes_user_name_seed_when_given(tmp_path: Path):
    """Spec: TR-5xx"""
    plan = InstallationPlan(user_display_name="Alice")

    config = _write_and_read(tmp_path, plan)

    assert config["user"]["name"] == "Alice"


def test_omits_user_section_when_no_display_name_was_collected(tmp_path: Path):
    """Spec: TR-5xx — a re-run that no-op'd ConfigureUserIdentity has nothing new to seed."""
    plan = InstallationPlan()

    config = _write_and_read(tmp_path, plan)

    assert "user" not in config


def test_written_config_is_only_readable_by_owner(tmp_path: Path):
    # database.url may carry a plaintext password (password-auth fallback) —
    # this file must never be group/world-readable.
    path = tmp_path / "mamemai.toml"
    TomlConfigWriter(path).write_server_config(InstallationPlan())

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_permissions_tightened_even_on_a_pre_existing_looser_file(tmp_path: Path):
    path = tmp_path / "mamemai.toml"
    path.write_text("")
    path.chmod(0o644)

    TomlConfigWriter(path).write_server_config(InstallationPlan())

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
