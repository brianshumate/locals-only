"""Live LM Studio integration (WU-0.3 done-criteria). Skipped unless the
server is reachable. Run with: uv run pytest -m integration"""

import shutil

import pytest

from eval_pipeline.lmstudio import (loaded_models, model_session,
                                    server_reachable)

SMALL_MODEL = "lfm2.5-8b-a1b-mlx"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("lms") is None, reason="lms not installed"),
    pytest.mark.skipif(not server_reachable(), reason="LM Studio server down"),
]


def test_load_chat_json_unload():
    with model_session(SMALL_MODEL, temperature=0.0, max_tokens=200) as m:
        text, usage = m.chat([{"role": "user", "content": "Say OK."}])
        assert text.strip()
        assert usage.completion_tokens > 0

        schema = {"type": "object",
                  "properties": {"answer": {"type": "string"}},
                  "required": ["answer"], "additionalProperties": False}
        data, _ = m.chat_json(
            [{"role": "user",
              "content": 'Reply with JSON: {"answer": "yes"}'}], schema=schema)
        assert "answer" in data
        assert m.account.calls == 2

    assert SMALL_MODEL not in loaded_models(), "model not unloaded"
