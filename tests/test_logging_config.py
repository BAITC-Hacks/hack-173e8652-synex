from __future__ import annotations

import io
import json
import logging

from moneygraph.logging_config import JsonFormatter


def test_json_formatter_emits_structured_context_without_secrets() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("moneygraph.test.structured")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("analysis completed", extra={"run_id": "run-1", "duration_seconds": 1.25})

    payload = json.loads(stream.getvalue())
    assert payload["level"] == "INFO"
    assert payload["message"] == "analysis completed"
    assert payload["run_id"] == "run-1"
    assert payload["duration_seconds"] == 1.25
    assert "api_key" not in payload
