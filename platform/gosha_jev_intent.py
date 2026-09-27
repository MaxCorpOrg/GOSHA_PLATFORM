"""Fast model interpretation before English-only JEV classification.

Luna has no device tools. Its output is advisory and is checked against the
original transcript again after JEV chooses a route.
"""
import http.client
import json
import ssl
import time

from gosha_jev_language import validate_english

MODEL = "gpt-6-luna"
KINDS = ("action", "conversation", "cancel", "clarify")
SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(KINDS)},
        "english": {"type": "string"},
    },
    "required": ["kind", "english"],
    "additionalProperties": False,
}
INSTRUCTIONS = (
    "Interpret one completed Russian or English utterance addressed to Gosha. "
    "Return a faithful plain-ASCII English rendering, preserving every negation, "
    "correction, question, direction, limb, exact count and quoted-command context. "
    "Do not invent a command. 'action' includes direct requests to read status or battery; "
    "'conversation' is discussion or a capability question; 'cancel' means the speaker "
    "withdrew or prohibited an action; 'clarify' means ambiguous or incomplete. "
    "You have no tools and cannot act on the robot."
)


class IntentError(RuntimeError):
    """Only fixed failure codes, with no provider content or source text."""


def request_payload(source):
    if not isinstance(source, str) or not source.strip() or len(source) > 2000:
        raise IntentError("invalid_source")
    return {
        "model": MODEL,
        "reasoning": {"effort": "none"},
        "instructions": INSTRUCTIONS,
        "input": source,
        "text": {"format": {"type": "json_schema", "name": "gosha_voice_intent_v1",
                            "strict": True, "schema": SCHEMA}},
        "max_output_tokens": 160,
        "store": False,
    }


def parse_response(response):
    if not isinstance(response, dict) or response.get("status") != "completed":
        raise IntentError("response_incomplete")
    if not str(response.get("model", "")).startswith(MODEL):
        raise IntentError("unexpected_model")
    contents = [content for item in response.get("output", []) if item.get("type") == "message"
                for content in item.get("content", []) if content.get("type") == "output_text"]
    if len(contents) != 1 or not isinstance(contents[0].get("text"), str):
        raise IntentError("missing_output")
    try:
        value = json.loads(contents[0]["text"])
        if not isinstance(value, dict) or set(value) != {"kind", "english"}:
            raise ValueError
        if value["kind"] not in KINDS:
            raise ValueError
        value["english"] = validate_english(value["english"])
    except (ValueError, TypeError):
        raise IntentError("invalid_output") from None
    return value


class LunaIntentClient:
    def __init__(self, key, *, max_calls=40):
        if not isinstance(key, str) or not key:
            raise IntentError("api_key_missing")
        self.key = key
        self.max_calls = min(max_calls, 40)
        self.calls = 0
        self.connection = http.client.HTTPSConnection(
            "api.openai.com", timeout=6, context=ssl.create_default_context())

    def evaluate(self, source):
        payload = json.dumps(request_payload(source), ensure_ascii=False).encode()
        if self.calls >= self.max_calls:
            raise IntentError("trial_budget_exceeded")
        self.calls += 1
        start = time.monotonic()
        try:
            self.connection.request("POST", "/v1/responses", payload, {
                "Authorization": "Bearer " + self.key, "Content-Type": "application/json",
            })
            reply = self.connection.getresponse()
            if reply.status != 200:
                status = reply.status
                self.close()
                raise IntentError(f"http_{status}")
            raw = reply.read(32769)
            if len(raw) > 32768:
                raise IntentError("response_too_large")
            value = parse_response(json.loads(raw))
            return value, round((time.monotonic() - start) * 1000, 2)
        except IntentError:
            self.close()
            raise
        except (OSError, http.client.HTTPException, ValueError):
            self.close()
            raise IntentError("provider_error_no_retry") from None

    def close(self):
        self.connection.close()
