import os
from openai import OpenAI
from pydantic import BaseModel

from Helpers.logger import log, INFO, ERROR

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
    is_old_member: bool


class ApplicationDetection(BaseModel):
    is_application: bool
    app_type: str  # "guild_member", "community_member", or "none"
    confidence: float


class RejoinDetection(BaseModel):
    is_application: bool
    app_type: str  # "guild_member", "community_member", or "none"
    confidence: float


class ApplicationCompleteness(BaseModel):
    has_ign: bool
    has_timezone: bool
    has_stats_link: bool
    has_playtime: bool
    has_guild_experience: bool
    has_warring_interest: bool
    has_know_about_taq: bool
    has_gain_from_taq: bool
    has_contribute_to_taq: bool
    has_how_learned: bool
    missing_fields: list[str]
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
                    "strict": True,
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
You are parsing a Wynncraft guild application for The Aquarium [TAq].

Extract the following from the application text:

1. **IGN (in-game name)**: Usually one of the first things mentioned, after "IGN:", \
"Username:", or similar. A Wynncraft stats link like wynncraft.com/stats/player/NAME \
also contains the IGN as the last path segment.

2. **Recruiter**: The person who referred the applicant to the guild. Look for answers \
to questions like "How did you learn about TAq?", "Who referred you?", "Reference for \
application", or similar.
   - If the applicant was referred by a specific player, return that player's in-game name.
   - If multiple players are mentioned as recruiters, return them comma-separated \
(e.g. "Player1, Player2").
   - If the applicant found the guild via a general source (e.g. "server list", "forums", \
"guild list", "I found it myself", "Google"), return that source name as the recruiter \
(e.g. "server list", "forums").
   - If no referral source is mentioned at all, return an empty string.

3. **Certainty**: Your confidence (0.0-1.0) for the overall extraction accuracy. \
If you cannot find either field, return empty strings with certainty 0.0.

4. **is_old_member**: Whether the applicant indicates they were previously in the guild. \
Look for language like "I was in the guild before", "returning member", "rejoin", \
"was kicked for inactivity", "coming back", "I used to be in TAq", "reapplying", \
"I left and want to come back", or other prior membership indicators. \
Set to true if any such language is present, false otherwise."""


class RecruiterMatch(BaseModel):
    matched_name: str
    confidence: float


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
        return {"ign": "", "recruiter": "", "certainty": 0.0, "is_old_member": False, "error": result["error"]}
    data = result["data"]
    return {
        "ign": data.get("ign", ""),
        "recruiter": data.get("recruiter", ""),
        "certainty": data.get("certainty", 0.0),
        "is_old_member": data.get("is_old_member", False),
        "error": None,
    }


def match_recruiter_name(recruiter_input: str, member_names: list[str]) -> dict:
    """Use OpenAI to fuzzy-match a recruiter name against guild member names."""
    names_text = "\n".join(member_names)
    input_text = f"Recruiter from application: {recruiter_input}\n\nGuild member names:\n{names_text}"
    result = query(
        instructions=_RECRUITER_MATCH_INSTRUCTIONS,
        input_text=input_text,
        json_schema=RecruiterMatch,
        model="gpt-4.1-nano",
        temperature=0.0,
        max_tokens=100,
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
    preview = message_text[:100].replace('\n', ' ')
    result = query(
        instructions=_DETECT_INSTRUCTIONS,
        input_text=message_text,
        json_schema=ApplicationDetection,
        model="gpt-4.1-nano",
        temperature=0.0,
        max_tokens=200,
    )
    if result["error"]:
        log(ERROR, f"\"{preview}\" -> error: {result['error']}", context="openai")
        return {"is_application": False, "app_type": "none", "confidence": 0.0, "error": result["error"]}
    data = result["data"]
    log(INFO, f"\"{preview}\" -> {data.get('app_type')} (confidence: {data.get('confidence')})", context="openai")
    return {
        "is_application": data.get("is_application", False),
        "app_type": data.get("app_type", "none"),
        "confidence": data.get("confidence", 0.0),
        "error": None,
    }


_REJOIN_DETECT_INSTRUCTIONS = """\
You are analyzing a Discord message sent in a guild application ticket for The Aquarium,
a Wynncraft guild. The sender is a KNOWN EX-MEMBER who was previously in the guild.

Determine if this message expresses intent to rejoin or reapply to the guild.
Be LENIENT — ex-members often write casually, such as:
- "Hey, I'd like to rejoin if possible"
- "Hi, sorry about being kicked for inactivity, can I come back?"
- "I want to apply again"
- "Is it possible to rejoin?"
- Any message expressing desire to return, reapply, rejoin, or come back

This does NOT need to follow a structured application format. Any indication
of wanting to rejoin or reapply counts.

For app_type:
- If the message mentions wanting to be a community member (including abbreviations like
  "comm member", "community", "comm", "cm"), set app_type to "community_member".
- Otherwise, default to "guild_member".

Set confidence between 0.0 and 1.0 based on how clearly the message expresses
rejoin intent. Even a casual "can I come back?" should get high confidence."""


def detect_rejoin_intent(message_text: str) -> dict:
    """Determine if an ex-member's message expresses intent to rejoin."""
    preview = message_text[:100].replace('\n', ' ')
    result = query(
        instructions=_REJOIN_DETECT_INSTRUCTIONS,
        input_text=message_text,
        json_schema=RejoinDetection,
        model="gpt-4.1-nano",
        temperature=0.0,
        max_tokens=200,
    )
    if result["error"]:
        log(ERROR, f"\"{preview}\" -> error: {result['error']}", context="openai")
        return {"is_application": False, "app_type": "none", "confidence": 0.0, "error": result["error"]}
    data = result["data"]
    log(INFO, f"\"{preview}\" -> {data.get('app_type')} (confidence: {data.get('confidence')})", context="openai")
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


# ---------------------------------------------------------------------------
# Application completeness validation
# ---------------------------------------------------------------------------

_VALIDATE_INSTRUCTIONS = """\
You are analyzing a guild application message for The Aquarium, a Wynncraft guild.
Determine which of the following required fields have been answered in the application text.
People write informally — a field is "present" if the applicant has provided ANY answer to it,
even if brief, informal, or not labeled with the exact field name.

Required fields to check:
1. IGN (in-game name) — look for "IGN:", a username, or a wynncraft.com/stats link
2. Timezone — any mention of timezone, GMT offset, region, or time zone name (e.g. "EST", "GMT+2", "UK")
3. Stats link — a wynncraft.com/stats link or similar URL
4. Estimated playtime per day — any mention of how much they play per day/week
5. Previous guild experience — any mention of past guilds, "no guild experience", or similar
6. Warring interest — any answer about warring, wars, or war experience
7. What they know about TAq — any answer about knowledge of The Aquarium
8. What they'd like to gain from TAq — any answer about goals or expectations from joining
9. What they'd contribute to TAq — any answer about contributions
10. How they learned about TAq — any answer about how they found/heard about TAq (e.g. a referral, friend, guild list)

For missing_fields, return a human-readable list of field names that are NOT present.
Use these exact names for missing fields:
- "IGN"
- "Timezone"
- "Stats link"
- "Estimated playtime per day"
- "Previous guild experience"
- "Warring interest"
- "What you know about TAq"
- "What you'd like to gain from TAq"
- "What you'd contribute to TAq"
- "How you learned about TAq"

Set confidence between 0.0 and 1.0 for your overall assessment accuracy."""


def validate_application_completeness(message_text: str) -> dict:
    """Check which required application fields are present in the message text."""
    preview = message_text[:100].replace('\n', ' ')
    result = query(
        instructions=_VALIDATE_INSTRUCTIONS,
        input_text=message_text,
        json_schema=ApplicationCompleteness,
        model="gpt-4.1-mini",
        temperature=0.0,
        max_tokens=400,
    )
    if result["error"]:
        log(ERROR, f"\"{preview}\" -> error: {result['error']}", context="openai")
        return {"complete": False, "fields": {}, "missing_fields": [], "error": result["error"]}
    data = result["data"]

    all_fields = [
        "has_ign", "has_timezone", "has_stats_link", "has_playtime",
        "has_guild_experience", "has_warring_interest", "has_know_about_taq",
        "has_gain_from_taq", "has_contribute_to_taq", "has_how_learned",
    ]
    is_complete = all(data.get(f, False) for f in all_fields)

    present = [f for f in all_fields if data.get(f, False)]
    missing = data.get("missing_fields", [])
    log(INFO, f"\"{preview}\" -> complete={is_complete}, present={len(present)}/10, missing={missing}", context="openai")

    return {
        "complete": is_complete,
        "fields": data,
        "missing_fields": missing,
        "error": None,
    }


def validate_exmember_completeness(message_text: str) -> dict:
    """Check required fields for an ex-member rejoin application.

    Ex-members only need: IGN, Timezone, Stats link.
    """
    result = validate_application_completeness(message_text)
    if result.get("error"):
        return result

    fields = result.get("fields", {})
    required = ["has_ign", "has_timezone", "has_stats_link"]
    is_complete = all(fields.get(f, False) for f in required)

    exmember_field_names = {"IGN", "Timezone", "Stats link"}
    filtered_missing = [
        f for f in result.get("missing_fields", [])
        if any(name.lower() in f.lower() for name in exmember_field_names)
    ]

    return {
        "complete": is_complete,
        "fields": fields,
        "missing_fields": filtered_missing,
        "error": None,
    }
