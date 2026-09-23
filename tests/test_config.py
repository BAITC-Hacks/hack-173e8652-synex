from __future__ import annotations

from pathlib import Path

import pytest

from moneygraph.config import Settings, load_analysis_config


def test_settings_without_ai_credentials_remain_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("AI_ENABLED", "false")

    settings = Settings()

    assert settings.ai_enabled is False
    assert settings.cors_origins == ("http://127.0.0.1:8501", "http://localhost:8501")


def test_analysis_config_rejects_role_weights_that_do_not_sum_to_one(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text(
        "random_seed: 42\nrole_threshold: 0.4\nroles:\n  transit:\n    first: 0.8\n    second: 0.8\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sum to 1"):
        load_analysis_config(config_path)


def test_default_analysis_config_is_deterministic() -> None:
    config = load_analysis_config(Path("config/default.yaml"))

    assert config.random_seed == 42
    assert config.top_n == 50
    assert config.role_threshold == pytest.approx(0.40)

