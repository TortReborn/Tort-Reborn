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


_APPLICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "ign": {
            "type": "string",
            "description": "The in-game name or username extracted from the Discord message."
        },
        "recruiter": {
            "type": "string",
            "description": "The name of the recruiter extracted from the Discord message."
        },
        "certainty": {
            "type": "number",
            "description": "A decimal between 0 and 1, where 1 means 100% certainty regarding the extraction accuracy.",
            "minimum": 0,
            "maximum": 1
        }
    },
    "required": ["ign", "recruiter", "certainty"],
    "additionalProperties": False
}


def parse_application(message_text: str) -> dict:
    try:
        result = _get_client().responses.create(
            prompt={
                "id": "pmpt_6986ef7597b48197ad1c047c1ce9763c004dcce51be737f2",
                "version": "1",
            },
            input=message_text,
            text={
                "format": {
                    "type": "json_schema",
                    "strict": True,
                    "name": "discord_message_extraction",
                    "schema": _APPLICATION_SCHEMA,
                }
            },
        )
        import json
        data = json.loads(result.output_text)
        return {
            "ign": data.get("ign", ""),
            "recruiter": data.get("recruiter", ""),
            "certainty": data.get("certainty", 0.0),
            "error": None,
        }
    except Exception as e:
        return {"ign": "", "recruiter": "", "certainty": 0.0, "error": str(e)}
