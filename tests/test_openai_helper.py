"""Tests for the direct OpenAI Responses API calls."""

import sys
from types import SimpleNamespace

import pytest
import requests

from Helpers import openai_helper as oh


def _response(status=200, body=None):
    return SimpleNamespace(status_code=status, ok=status < 400, json=lambda: body or {})


def _answer(text):
    return {"output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": text}]},
    ]}


@pytest.fixture
def calls(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(oh.time, "sleep", lambda _: None)
    recorded = []

    def install(*results):
        queue = list(results)

        def post(url, json, headers, timeout):
            recorded.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
            result = queue.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(oh.requests, "post", post)
        return recorded

    return install


def test_does_not_import_the_sdk():
    assert "openai" not in sys.modules or not hasattr(oh, "OpenAI")
    assert not hasattr(oh, "BaseModel")


def test_parse_sends_the_strict_schema_and_reads_the_message_text(calls):
    recorded = calls(_response(body=_answer('{"recruiter": "Bob", "certainty": 0.9}')))

    assert oh.parse_recruiter_source("my friend Bob") == {"recruiter": "Bob", "certainty": 0.9, "error": None}

    sent = recorded[0]
    assert sent["url"] == "https://api.openai.com/v1/responses"
    assert sent["headers"]["Authorization"] == "Bearer test-key"
    assert sent["json"]["model"] == "gpt-5-nano"
    assert sent["json"]["reasoning"] == {"effort": "minimal"}
    assert sent["json"]["input"] == "my friend Bob"
    assert sent["json"]["text"]["format"] == {
        "type": "json_schema",
        "strict": True,
        "name": "RecruiterSource",
        "schema": {
            "type": "object",
            "properties": {"recruiter": {"type": "string"}, "certainty": {"type": "number"}},
            "required": ["recruiter", "certainty"],
            "additionalProperties": False,
        },
    }


def test_match_returns_the_name_and_confidence(calls):
    calls(_response(body=_answer('{"matched_name": "Bob", "confidence": 0.95}')))
    assert oh.match_recruiter_name("bob", ["Bob", "Alice"]) == {"matched_name": "Bob", "confidence": 0.95, "error": None}


def test_retries_a_rate_limit_then_succeeds(calls):
    recorded = calls(_response(429, {"error": {"message": "slow down"}}), _response(body=_answer('{"recruiter": "", "certainty": 0}')))
    assert oh.parse_recruiter_source("x")["error"] is None
    assert len(recorded) == 2


def test_retries_a_connection_error_then_succeeds(calls):
    recorded = calls(requests.ConnectionError("reset"), _response(body=_answer('{"recruiter": "", "certainty": 0}')))
    assert oh.parse_recruiter_source("x")["error"] is None
    assert len(recorded) == 2


def test_does_not_retry_a_bad_key_and_reports_it(calls):
    recorded = calls(_response(401, {"error": {"message": "bad key"}}))
    result = oh.parse_recruiter_source("x")
    assert result == {"recruiter": "", "certainty": 0.0, "error": "OpenAI HTTP 401: bad key"}
    assert len(recorded) == 1


def test_gives_up_after_three_failures(calls):
    recorded = calls(*[_response(500, {"error": {"message": "down"}})] * 3)
    assert oh.parse_recruiter_source("x")["error"] == "OpenAI HTTP 500: down"
    assert len(recorded) == 3


def test_lasting_connection_failure_becomes_an_error(calls):
    recorded = calls(*[requests.Timeout("slow")] * 3)
    assert oh.parse_recruiter_source("x")["error"] == "slow"
    assert len(recorded) == 3


def test_empty_output_becomes_an_error_not_a_crash(calls):
    calls(_response(body={"status": "incomplete", "output": []}))
    assert oh.parse_recruiter_source("x")["error"]
