"""Guard against translation-induced physical actions."""
import unittest
import json

from gosha_jev_language import LanguageError, guard_physical_choice, is_priority_stop, validate_english
from gosha_jev_trial import CRITERIA, JevTrialClient, SimulatedRobot, TrialDispatcher, TrialError, make_request
from unittest.mock import patch


def response(choice):
    probabilities = dict.fromkeys(CRITERIA, 0.0)
    probabilities[choice] = 1.0
    return {"model": "jev-1.13.0", "answers": {"skill": {
        "type": "choice", "choice": choice, "confidence": 1.0,
        "probabilities": probabilities}}, "usage": {"input_tokens": 1, "output_tokens": 1}}


class LanguageGuardTests(unittest.TestCase):
    def test_only_english_reaches_jev(self):
        self.assertEqual(validate_english("Gosha, wave your hand."), "Gosha, wave your hand.")
        for text in ("Гоша, помаши", "Gosha, помаши", "", "x" * 2001):
            with self.subTest(text=text[:20]), self.assertRaises(LanguageError):
                validate_english(text)

    def test_complete_jev_payload_contains_no_cyrillic(self):
        payload = json.dumps(make_request("Gosha, wave your hand."), ensure_ascii=False)
        self.assertTrue(payload.isascii())
        with self.assertRaisesRegex(TrialError, "english_translation_required"):
            make_request("Гоша, помаши рукой.")

    def test_lost_cancellation_never_becomes_wave(self):
        source = "Гоша, помаши рукой. Не надо."
        translated = "Gosha, wave your hand."  # Observed Argos mistranslation.
        self.assertEqual(guard_physical_choice(source, translated, "builtin/hand_wave"), "no_action")

    def test_motion_requires_corresponding_source_command(self):
        cases = (
            ("Гоша, помаши рукой.", "Gosha, wave your hand.", "builtin/hand_wave", "builtin/hand_wave"),
            ("Гоша, сколько заряда?", "Gosha, walk forward.", "builtin/walk_forward", "clarify"),
            ("Гоша, иди назад.", "Gosha, walk forward.", "builtin/walk_forward", "clarify"),
            ("Гоша, иди вперёд.", "Gosha, walk forward.", "builtin/walk_forward", "builtin/walk_forward"),
            ("Гоша, иди назад.", "Gosha, walk backward.", "builtin/walk_backward", "builtin/walk_backward"),
            ("Гоша, помаши левой рукой.", "Gosha, wave your left hand.", "builtin/hand_wave", "clarify"),
            ("Гоша, сделай два шага вперёд.", "Gosha, walk forward twice.", "builtin/walk_forward", "clarify"),
            ("Как перевести фразу помаши рукой?", "How to translate wave your hand?", "builtin/hand_wave", "clarify"),
            ("Ты можешь помаши рукой?", "Can you wave your hand?", "builtin/hand_wave", "clarify"),
            ("ты можешь помаши рукой", "can you wave your hand", "builtin/hand_wave", "clarify"),
            ("Гоша, выключи питание.", "Gosha, stop.", "stop", "clarify"),
        )
        for source, translated, choice, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(guard_physical_choice(source, translated, choice), expected)

    def test_missing_source_and_non_english_translation_fail_closed(self):
        self.assertEqual(guard_physical_choice("", "Gosha, wave your hand.", "builtin/hand_wave"), "clarify")
        with self.assertRaises(LanguageError):
            guard_physical_choice("Гоша, помаши", "Гоша, помаши", "builtin/hand_wave")

    def test_priority_stop_is_direct_only(self):
        self.assertTrue(is_priority_stop("Гоша, останови движение."))
        for phrase in ("Не останавливайся", "Как сказать стоп?", "Он сказал стоп",
                       "ты можешь остановить движение",
                       "Помаши рукой и остановись"):
            with self.subTest(phrase=phrase):
                self.assertFalse(is_priority_stop(phrase))

    def test_english_only_client_blocks_cyrillic_before_network(self):
        with patch("gosha_jev_trial.http.client.HTTPSConnection") as connection:
            client = JevTrialClient("unit-only")
            with self.assertRaisesRegex(TrialError, "english_translation_required"):
                client.evaluate("Гоша, помаши")
            connection.return_value.request.assert_not_called()


class GuardedDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.robot = SimulatedRobot()
        await self.robot.device.discover(retry_delays=())
        self.dispatcher = TrialDispatcher(self.robot.device, require_source=True)

    async def test_observed_translation_loss_never_calls_robot(self):
        result = await self.dispatcher.dispatch(
            "cancelled", response("builtin/hand_wave"),
            source_text="Гоша, помаши рукой. Не надо.", english_text="Gosha, wave your hand.")
        self.assertEqual(result["route"], "no_action")
        self.assertEqual(self.robot.calls, [])
        duplicate = await self.dispatcher.dispatch(
            "cancelled", response("builtin/hand_wave"),
            source_text="Гоша, помаши рукой.", english_text="Gosha, wave your hand.")
        self.assertEqual(duplicate["reason"], "duplicate_utterance")

    async def test_verified_source_reaches_existing_device_tools_once(self):
        result = await self.dispatcher.dispatch(
            "wave", response("builtin/hand_wave"),
            source_text="Гоша, помаши рукой.", english_text="Gosha, wave your hand.")
        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(len(self.robot.calls), 1)

    async def test_source_is_mandatory_in_physical_mode(self):
        result = await self.dispatcher.dispatch("missing", response("builtin/hand_wave"))
        self.assertEqual(result["route"], "clarify")
        self.assertEqual(self.robot.calls, [])


if __name__ == "__main__":
    unittest.main()
