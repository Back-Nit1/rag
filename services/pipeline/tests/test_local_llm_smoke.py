"""REAL-MODEL smoke test. Opt-in: needs a running Ollama and an installed model.

METIS_LOCAL_SMOKE=1 METIS_LOCAL_LLM_MODEL=<installed model> pytest services/pipeline/tests/test_local_llm_smoke.py
"""

from __future__ import annotations

import os

import pytest
from metis_pipeline.local_llm import LocalInferenceClient

pytestmark = pytest.mark.skipif(not os.environ.get("METIS_LOCAL_SMOKE"), reason="set METIS_LOCAL_SMOKE=1")


def test_real_model_returns_schema_shaped_json():
    c = LocalInferenceClient()
    ok, detail = c.health()
    assert ok, detail
    payload, usage = c.complete_json(
        prompt_version="smoke",
        system="Return JSON.",
        user_content="Say hi.",
        json_schema={
            "type": "object",
            "properties": {"greeting": {"type": "string"}},
            "required": ["greeting"],
        },
        max_input_tokens=500,
        max_output_tokens=100,
        timeout_seconds=120,
    )
    assert isinstance(payload.get("greeting"), str) and usage["adapter_mode"] == "local"
