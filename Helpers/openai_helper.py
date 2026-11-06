import json
import os
import time

import requests

from Helpers.logger import log, ERROR

_RESPONSES_URL = "https://api.openai.com/v1/responses"
_TIMEOUT_SECONDS = 60
_MAX_ATTEMPTS = 3
_RETRY_STATUSES = {408, 409, 429, 500, 502, 503, 504}


def _object_schema(name: str, **properties: str) -> dict:
    """Return a named strict JSON schema, as the Responses API requires."""
    return {
        "name": name,
        "schema": {
            "type": "object",
            "properties": {key: {"type": kind} for key, kind in properties.items()},
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def _post_with_retries(payload: dict) -> requests.Response:
    headers = {"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"}
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = requests.post(_RESPONSES_URL, json=payload, headers=headers, timeout=_TIMEOUT_SECONDS)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == _MAX_ATTEMPTS:
                raise
        else:
            if response.status_code not in _RETRY_STATUSES or attempt == _MAX_ATTEMPTS:
                return response
        time.sleep(2 ** (attempt - 1))


def _output_text(body: dict) -> str:
    return "".join(
        part.get("text", "")
        for item in body.get("output") or []
        if item.get("type") == "message"
        for part in item.get("content") or []
        if part.get("type") == "output_text"
    )


def query(
    instructions: str,
    input_text: str,
    model: str = "gpt-5-nano",
    json_schema: dict | None = None,
    temperature: float | None = None,
    max_tokens: int = 500,
    reasoning_effort: str | None = None,
) -> dict:
    try:
        payload = {
            "model": model,
            "instructions": instructions,
            "input": input_text,
            "max_output_tokens": max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if reasoning_effort is not None:
            # gpt-5 models spend max_output_tokens on hidden reasoning before the
            # visible answer. "minimal" keeps that from eating the whole budget.
            payload["reasoning"] = {"effort": reasoning_effort}
        if json_schema is not None:
            payload["text"] = {"format": {"type": "json_schema", "strict": True, **json_schema}}
        response = _post_with_retries(payload)
        body = response.json()
        if not response.ok:
            message = (body.get("error") or {}).get("message", "request failed")
            return {"content": None, "data": None, "error": f"OpenAI HTTP {response.status_code}: {message}"}
        text = _output_text(body)
        if not text:
            # json.loads("") below just says "Expecting value", log the real reason.
            schema_name = json_schema["name"] if json_schema is not None else None
            log(
                ERROR,
                f"Empty output text from model={model} schema={schema_name} "
                f"status={body.get('status')} "
                f"incomplete_details={body.get('incomplete_details')} "
                f"usage={body.get('usage')}",
                context="openai_helper",
            )
        data = json.loads(text) if json_schema is not None else None
        return {"content": text, "data": data, "error": None}
    except Exception as e:
        return {"content": None, "data": None, "error": str(e)}


_RECRUITER_SOURCE = _object_schema("RecruiterSource", recruiter="string", certainty="number")


_PARSE_INSTRUCTIONS = """\
You are extracting the recruiter or referral source from a Wynncraft guild application \
for The Aquarium [TAq].

The input is the applicant's answer to the question: "How did you learn about TAq / \
reference for application? If recruited via party finder, include the recruiter's IGN."

Extract the recruiter or source:
- If the applicant was referred by a specific player, return that player's in-game name \
(strip surrounding text like "my friend X told me" → just return "X").
- If multiple players are mentioned, return them comma-separated (e.g. "Player1, Player2").
- If the source is a general channel or platform, normalize it:
  - "wynncraft discord", "wynn discord", "the wynncraft discord server" → "wynncord"
  - "server list", "guild list" → "server list"
  - "forums", "wynncraft forums" → "forums"
  - "party finder", "found in party" → "party finder"
  - Other general sources (e.g. "Google", "YouTube", "Reddit") → return as-is in lowercase.
- If the answer is empty or you cannot determine a source, return an empty string \
with certainty 0.0.

Set certainty (0.0-1.0) based on how confident you are in the extraction."""


_RECRUITER_MATCH = _object_schema("RecruiterMatch", matched_name="string", confidence="number")


_RECRUITER_MATCH_INSTRUCTIONS = """\
You are matching a recruiter name from a guild application to the correct member in \
the guild member list. The recruiter name may be misspelled, abbreviated, or a partial match.

Given the recruiter input and a list of guild member names, find the best match.

Rules:
- If a name clearly matches (exact, case-insensitive, or obvious typo), return it with \
high confidence (0.9-1.0).
- If a name partially matches but is ambiguous, return the best guess with lower confidence.
- If no reasonable match exists, return an empty string with confidence 0.0.
- Only return one name, even if multiple partial matches exist — pick the best one."""


def parse_recruiter_source(reference_text: str) -> dict:
    """Extract and normalize recruiter/source from the reference field."""
    result = query(
        instructions=_PARSE_INSTRUCTIONS,
        input_text=reference_text,
        json_schema=_RECRUITER_SOURCE,
        model="gpt-5-nano",
        max_tokens=500,
        reasoning_effort="minimal",
    )
    if result["error"]:
        return {"recruiter": "", "certainty": 0.0, "error": result["error"]}
    data = result["data"]
    return {
        "recruiter": data.get("recruiter", ""),
        "certainty": data.get("certainty", 0.0),
        "error": None,
    }


def match_recruiter_name(recruiter_input: str, member_names: list[str]) -> dict:
    """Use OpenAI to fuzzy-match a recruiter name against guild member names."""
    names_text = "\n".join(member_names)
    input_text = f"Recruiter from application: {recruiter_input}\n\nGuild member names:\n{names_text}"
    result = query(
        instructions=_RECRUITER_MATCH_INSTRUCTIONS,
        input_text=input_text,
        json_schema=_RECRUITER_MATCH,
        model="gpt-5-nano",
        max_tokens=400,
        reasoning_effort="minimal",
    )
    if result["error"]:
        return {"matched_name": "", "confidence": 0.0, "error": result["error"]}
    data = result["data"]
    return {
        "matched_name": data.get("matched_name", ""),
        "confidence": data.get("confidence", 0.0),
        "error": None,
    }


# ---------------------------------------------------------------------------
# Application detection & IGN extraction
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Application completeness validation
# ---------------------------------------------------------------------------
