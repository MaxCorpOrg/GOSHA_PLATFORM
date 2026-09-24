"""Luna may normalize language but must not become a direct executor."""
import json
import unittest

from gosha_jev_intent import IntentError, parse_response, request_payload


def result(kind, english):
    return {"status": "completed", "model": "gpt-6-luna-2026-09-22", "output": [{
        "type": "message", "content": [{"type": "output_text", "text": json.dumps({
            "kind": kind, "english": english,
        })}],
    }]}


class LunaIntentTests(unittest.TestCase):
    def test_request_has_no_tools_and_does_not_store_text(self):
        payload = request_payload("Гоша, помаши рукой")
        self.assertEqual(payload["model"], "gpt-6-luna")
        self.assertEqual(payload["reasoning"], {"effort": "none"})
        self.assertFalse(payload["store"])
        self.assertNotIn("tools", payload)

    def test_english_action_is_validated(self):
        self.assertEqual(parse_response(result("action", "Gosha, wave your hand."))["kind"],
                         "action")

    def test_cyrillic_cannot_reach_jev(self):
        with self.assertRaises(IntentError):
            parse_response(result("action", "Гоша, помаши рукой"))

    def test_refusal_or_incomplete_response_fails_closed(self):
        with self.assertRaises(IntentError):
            parse_response({"status": "incomplete", "model": "gpt-6-luna", "output": []})


if __name__ == "__main__":
    unittest.main()
