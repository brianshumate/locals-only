"""Judge runner with a fake client — schema retry + failure recording."""

import jsonschema

from eval_pipeline.judge import judge_document
from eval_pipeline.lmstudio import LMStudioError
from eval_pipeline.skills import load_skill


class FakeClient:
    """Returns queued responses; raises if queue holds an exception."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat_json(self, messages, schema=None, **kw):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r, None


GOOD = {"score": 8.0, "confidence": 0.9, "violations": [], "summary": "fine"}


def test_valid_first_try():
    client = FakeClient([GOOD])
    data, latency, err = judge_document(client, load_skill("style-guide"), "doc")
    assert data == GOOD and err == "" and client.calls == 1


def test_repair_retry_succeeds():
    client = FakeClient([{"score": 42}, GOOD])
    data, _, err = judge_document(client, load_skill("style-guide"), "doc")
    assert data == GOOD and client.calls == 2


def test_two_failures_recorded_not_coerced():
    client = FakeClient([LMStudioError("bad json"), {"nope": 1}])
    data, _, err = judge_document(client, load_skill("style-guide"), "doc")
    assert data is None and err != "" and client.calls == 2
