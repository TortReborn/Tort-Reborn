import os
from openai import OpenAI
from pydantic import BaseModel

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


class ApplicationParse(BaseModel):
    ign: str
    recruiter: str
    certainty: float


class ApplicationDetection(BaseModel):
    is_application: bool
    app_type: str  # "guild_member", "community_member", or "none"
    confidence: float


class IGNExtraction(BaseModel):
    ign: str
    confidence: float


def _strict_schema(model: type[BaseModel]) -> dict:
    """Return a JSON schema with additionalProperties: false (required by OpenAI)."""
    schema = model.model_json_schema()
    schema["additionalProperties"] = False
    return schema


def query(
    instructions: str,
    input_text: str,
    model: str = "gpt-4.1-nano",
    json_schema: type[BaseModel] | None = None,
    temperature: float = 0.0,
    max_tokens: int = 500,
) -> dict:
    client = _get_client()
    try:
        kwargs = {
            "model": model,
            "instructions": instructions,
            "input": input_text,
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if json_schema is not None:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": json_schema.__name__,
                    "schema": _strict_schema(json_schema),
                }
            }
        response = client.responses.create(**kwargs)
        text = response.output_text
        data = None
        if json_schema is not None:
            import json
            data = json.loads(text)
        return {"content": text, "data": data, "error": None}
    except Exception as e:
        return {"content": None, "data": None, "error": str(e)}



_PARSE_INSTRUCTIONS = """\
Extract the in-game name (IGN) and recruiter from this Wynncraft guild application.

The IGN is usually one of the first things mentioned, after "IGN:", "Username:", or similar.
A Wynncraft stats link like wynncraft.com/stats/player/NAME also contains the IGN.

The recruiter is the person who referred the applicant to the guild. Look for answers to
questions like "How did you learn about TAq?", "Who referred you?", "Reference for application",
or similar. The recruiter is typically another player's in-game name. If no recruiter is
mentioned or the applicant found the guild on their own (e.g. "guild list", "forums",
"I found it myself"), return an empty string for recruiter.

Return your certainty (0.0-1.0) for the overall extraction accuracy.
If you cannot find either field, return empty strings with certainty 0.0."""


def parse_application(message_text: str) -> dict:
    result = query(
        instructions=_PARSE_INSTRUCTIONS,
        input_text=message_text,
        json_schema=ApplicationParse,
        model="gpt-4.1-nano",
        temperature=0.0,
        max_tokens=200,
    )
    if result["error"]:
        return {"ign": "", "recruiter": "", "certainty": 0.0, "error": result["error"]}
    data = result["data"]
    return {
        "ign": data.get("ign", ""),
        "recruiter": data.get("recruiter", ""),
        "certainty": data.get("certainty", 0.0),
        "error": None,
    }


# ---------------------------------------------------------------------------
# Application detection & IGN extraction
# ---------------------------------------------------------------------------

_DETECT_INSTRUCTIONS = """\
You are analyzing a Discord message sent in a guild application ticket for The Aquarium,
a Wynncraft guild. Determine if this message is a response to either a Community Member
or Guild Member application questionnaire.

Community Member applications typically answer these questions:
- What is your IGN (in-game name)?
- What guild are you in?
- Why do you want to become a community member of TAq?
- What would you contribute to the community?
- Is there anything else you want to say?

Guild Member applications typically answer these questions:
- IGN (in-game name)
- Timezone (in relation to GMT)
- Link to stats page (wynncraft.com/stats)
- Age (optional)
- Estimated playtime per day
- Previous guild experience (name, rank, reason for leaving)
- Are you interested in warring? Experience?
- What do you know about TAq?
- What would you like to gain from joining TAq?
- What would you contribute to TAq?
- Anything else? (optional)
- How did you learn about TAq / reference for application

If the message answers several of these questions in a structured way, it IS an application.
If it is a short greeting, question, casual chat, or unrelated message, it is NOT an application.

Set app_type to "guild_member" if it matches the Guild Member format,
"community_member" if it matches the Community Member format,
or "none" if it is not an application.
Set confidence between 0.0 and 1.0."""


def detect_application(message_text: str) -> dict:
    """Determine if a message is an application response and what type."""
    result = query(
        instructions=_DETECT_INSTRUCTIONS,
        input_text=message_text,
        json_schema=ApplicationDetection,
        model="gpt-4.1-nano",
        temperature=0.0,
        max_tokens=200,
    )
    if result["error"]:
        return {"is_application": False, "app_type": "none", "confidence": 0.0, "error": result["error"]}
    data = result["data"]
    return {
        "is_application": data.get("is_application", False),
        "app_type": data.get("app_type", "none"),
        "confidence": data.get("confidence", 0.0),
        "error": None,
    }


_IGN_INSTRUCTIONS = """\
Extract the Minecraft in-game name (IGN) from this guild application text.
The IGN is usually one of the first things mentioned. It may appear after "IGN:",
"Username:", "My IGN is", or similar patterns. A Wynncraft stats link like
wynncraft.com/stats/player/NAME also contains the IGN as the last path segment.
Return the IGN string and your confidence (0.0-1.0).
If you cannot find an IGN, return an empty string with confidence 0.0."""


def extract_ign(application_text: str) -> dict:
    """Extract the IGN from application text."""
    result = query(
        instructions=_IGN_INSTRUCTIONS,
        input_text=application_text,
        json_schema=IGNExtraction,
        model="gpt-4.1-nano",
        temperature=0.0,
        max_tokens=100,
    )
    if result["error"]:
        return {"ign": "", "confidence": 0.0, "error": result["error"]}
    data = result["data"]
    return {
        "ign": data.get("ign", ""),
        "confidence": data.get("confidence", 0.0),
        "error": None,
    }
