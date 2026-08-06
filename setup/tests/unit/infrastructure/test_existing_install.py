from pathlib import Path

from mamemai_setup.domain.plan import Topology
from mamemai_setup.infrastructure.existing_install import FileExistingInstallDetector

_FULL_CONFIG = """
[server]
ws_port = 8765
log_dir = "logs/sessions"

[database]
url = "postgresql://mamemai:s3cret@localhost:5432/mamemai"

[stt]
model_path = "small"
device = "cuda"
compute_type = "float16"

[tts]
device = "cuda"

[llm]
model = "aya-expanse"

[languages]
installed = ["en", "fr"]
"""


def _detector(tmp_path: Path, content: str | None) -> FileExistingInstallDetector:
    path = tmp_path / "mamemai.toml"
    if content is not None:
        path.write_text(content)
    return FileExistingInstallDetector(path)


def test_returns_none_when_no_config_exists(tmp_path: Path):
    assert _detector(tmp_path, None).load_existing_plan() is None


def test_prefills_plan_from_existing_config(tmp_path: Path):
    """FR-706 — re-runs start from the recorded state."""
    plan = _detector(tmp_path, _FULL_CONFIG).load_existing_plan()

    assert plan is not None
    assert plan.from_existing_install is True
    assert plan.llm_model_id == "aya-expanse"
    assert plan.languages == ["en", "fr"]
    assert plan.whisper_model == "small"
    assert plan.compute_device == "cuda"
    assert plan.database_url == "postgresql://mamemai:s3cret@localhost:5432/mamemai"
    # Server-side configs of single- and split-host installs are
    # indistinguishable — topology is asked again rather than guessed.
    assert plan.topology is None


def test_ssh_host_marks_split_host_topology(tmp_path: Path):
    plan = _detector(tmp_path, '[server]\nws_port = 8765\nssh_host = "gpu-box"\n').load_existing_plan()

    assert plan is not None
    assert plan.topology is Topology.SPLIT_HOST


def test_partial_config_prefills_only_present_fields(tmp_path: Path):
    plan = _detector(tmp_path, '[llm]\nmodel = "gemma3:4b"\n').load_existing_plan()

    assert plan is not None
    assert plan.llm_model_id == "gemma3:4b"
    assert plan.languages == []
    assert plan.whisper_model is None


def test_malformed_config_degrades_to_fresh_run(tmp_path: Path):
    assert _detector(tmp_path, "[server\nnot toml at all").load_existing_plan() is None


def test_prefills_default_ollama_provider_when_llm_provider_key_absent(tmp_path: Path):
    """Spec: FR-707 — configs written before this setting existed prefill the
    plan's own "ollama" default, not None."""
    plan = _detector(tmp_path, _FULL_CONFIG).load_existing_plan()

    assert plan is not None
    assert plan.llm_provider == "ollama"
    assert plan.llm_base_url is None


def test_prefills_openai_compatible_provider_and_remote_fields(tmp_path: Path):
    """Spec: FR-707"""
    content = """
    [llm]
    model = "aya-expanse"
    provider = "openai_compatible"
    base_url = "https://openrouter.ai/api/v1"
    remote_model = "meta-llama/llama-3.3-70b-instruct"
    api_key = "sk-example"
    """
    plan = _detector(tmp_path, content).load_existing_plan()

    assert plan is not None
    assert plan.llm_provider == "openai_compatible"
    assert plan.llm_base_url == "https://openrouter.ai/api/v1"
    assert plan.llm_remote_model == "meta-llama/llama-3.3-70b-instruct"
    assert plan.llm_api_key == "sk-example"


def test_prefills_num_ctx_from_an_existing_config(tmp_path: Path):
    """Spec: TR-951, FR-706 — the usable context is bounded by the model's ceiling and by
    the machine's VRAM, so a hand-tuned value must survive a wizard re-run."""
    plan = _detector(tmp_path, '[llm]\nmodel = "gemma3:4b"\nnum_ctx = 32768\n').load_existing_plan()

    assert plan is not None
    assert plan.llm_num_ctx == 32768


def test_num_ctx_absent_leaves_the_plan_on_the_default(tmp_path: Path):
    """Spec: TR-951 — a config written before this key existed gets the current default
    written on the re-run, not Ollama's silently-truncating 4096."""
    plan = _detector(tmp_path, '[llm]\nmodel = "gemma3:4b"\n').load_existing_plan()

    assert plan is not None
    assert plan.llm_num_ctx is None
