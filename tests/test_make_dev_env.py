from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("has_env_file", [False, True])
def test_make_dev_passes_local_env_file_only_to_api(
    tmp_path: Path, has_env_file: bool
) -> None:
    shutil.copyfile(Path(__file__).resolve().parents[1] / "Makefile", tmp_path / "Makefile")
    if has_env_file:
        (tmp_path / ".env").write_text(
            "AGENTIC_AUTO_MONITOR_ENABLED=false\n"
            "AGENTIC_AUTO_MONITOR_CADENCE_SECONDS=17\n",
            encoding="utf-8",
        )

    result = subprocess.run(
        ["make", "--no-print-directory", "--dry-run", "dev"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    api_launch, ui_launch = result.stdout.split("api_pid=$!", maxsplit=1)

    assert ("--env-file .env" in api_launch) is has_env_file
    assert "AGENTIC_AUTO_MONITOR_ENABLED=" not in api_launch
    assert "AGENTIC_AUTO_MONITOR_CADENCE_SECONDS=" not in api_launch
    assert "--env-file" not in ui_launch
    assert 'MONEYGRAPH_API_URL="http://127.0.0.1:8000"' in ui_launch
