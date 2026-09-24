"""Offline checks for JEV decisions through the unchanged device executor."""
import asyncio
import copy
import unittest
from unittest.mock import patch

from gosha_jev_trial import (
    CRITERIA, MODEL, JevTrialClient, SimulatedRobot, TrialDispatcher,
    TrialError, make_request, validate_response,
)


def response(choice, value=1.0):
    probabilities = dict.fromkeys(CRITERIA, 0.0)
    probabilities[choice] = value
    probabilities["clarify" if choice != "clarify" else "no_action"] += 1 - value
    return {"model": MODEL, "answers": {"skill": {
        "type": "choice", "choice": choice, "confidence": value,
        "probabilities": probabilities}}, "usage": {"input_tokens": 100, "output_tokens": 20}}


class TrialTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.robot = SimulatedRobot()
        await self.robot.device.discover(retry_delays=())
        self.dispatcher = TrialDispatcher(self.robot.device)

    async def test_wave_uses_existing_mcp_and_does_not_claim_completion(self):
        result = await self.dispatcher.dispatch("one", response("builtin/hand_wave"))
        self.assertEqual(result["status"], "in_progress")
        self.assertFalse(result["device_result"]["completion_confirmed"])
        self.assertEqual(len(self.robot.calls), 1)
        call = self.robot.calls[0]
        self.assertEqual(call["name"], "self.motion.play")
        self.assertEqual(call["arguments"]["motion_id"], "builtin/hand_wave")
        self.assertEqual(set(call["arguments"]), {"motion_id", "request_id"})

    async def test_duplicate_delivery_does_not_repeat_but_new_utterance_can(self):
        await asyncio.gather(*(self.dispatcher.dispatch("same", response("builtin/walk_forward"))
                               for _ in range(3)))
        self.assertEqual(len(self.robot.calls), 1)
        await self.dispatcher.dispatch("new", response("builtin/walk_forward"))
        self.assertEqual(len(self.robot.calls), 2)

    async def test_no_action_conversation_clarify_and_low_probability_do_not_call(self):
        for choice in ("no_action", "conversation", "clarify"):
            await self.dispatcher.dispatch(choice, response(choice))
        result = await self.dispatcher.dispatch("uncertain", response("builtin/hand_wave", .7))
        self.assertEqual(result["route"], "clarify")
        self.assertEqual(self.robot.calls, [])

    async def test_missing_capability_uses_existing_rejection(self):
        self.robot.device.available.pop("robot_play_movement")
        result = await self.dispatcher.dispatch("one", response("builtin/hand_wave"))
        self.assertEqual(result, {"status": "rejected", "reason": "operation_unavailable"})
        self.assertEqual(self.robot.calls, [])

    async def test_lost_ack_unknown_and_duplicate_cannot_replay(self):
        self.robot.drop_next_reply = True
        result = await self.dispatcher.dispatch("one", response("builtin/hand_wave"))
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["retry"])
        await self.dispatcher.dispatch("one", response("builtin/hand_wave"))
        self.assertEqual(len(self.robot.calls), 1)

    async def test_stop_reuses_existing_stop_operation(self):
        await self.dispatcher.dispatch("one", response("builtin/hand_wave"))
        result = await self.dispatcher.dispatch("two", response("stop"))
        self.assertEqual(result["status"], "stopped")
        self.assertIsNone(self.robot.active_motion)
        self.assertEqual(self.robot.calls[-1], {"name": "self.motion.stop", "arguments": {}})

    async def test_device_sessions_do_not_share_deduplication(self):
        other = SimulatedRobot()
        await other.device.discover(retry_delays=())
        await self.dispatcher.dispatch("one", response("battery"))
        await TrialDispatcher(other.device).dispatch("one", response("battery"))
        self.assertEqual((len(self.robot.calls), len(other.calls)), (1, 1))

    async def test_malformed_provider_response_never_calls_device(self):
        original = response("builtin/hand_wave")
        malformed = [[], {**original, "model": "different"}, {**original, "answers": {}},
                     {**original, "usage": {"input_tokens": True, "output_tokens": 1}}]
        for key, value in [("choice", "self.reboot"), ("choice", []),
                           ("confidence", float("nan")), ("probabilities", {"bad": 1}),
                           ("probabilities", dict.fromkeys(CRITERIA, .9))]:
            candidate = copy.deepcopy(original)
            candidate["answers"]["skill"][key] = value
            malformed.append(candidate)
        for index, candidate in enumerate(malformed):
            with self.assertRaises(TrialError):
                await self.dispatcher.dispatch(str(index), candidate)
        self.assertEqual(self.robot.calls, [])

    def test_input_and_numeric_validation(self):
        for text in (None, "", " " * 3, "x" * 2001):
            with self.assertRaises(TrialError):
                make_request(text)
        candidate = response("builtin/hand_wave")
        candidate["answers"]["skill"]["probabilities"]["builtin/hand_wave"] = True
        with self.assertRaises(TrialError):
            validate_response(candidate)

    def test_transport_failure_does_not_retry(self):
        with patch("gosha_jev_trial.http.client.HTTPSConnection") as transport:
            transport.return_value.request.side_effect = OSError("private provider message")
            client = JevTrialClient("unit-test-only")
            with self.assertRaisesRegex(TrialError, "provider_error_no_retry"):
                client.evaluate("Wave your hand.")
            self.assertEqual(transport.return_value.request.call_count, 1)

    def test_budget_blocks_network_before_call(self):
        with patch("gosha_jev_trial.http.client.HTTPSConnection") as transport:
            client = JevTrialClient("unit-test-only", max_calls=0)
            with self.assertRaisesRegex(TrialError, "budget"):
                client.evaluate("Wave your hand.")
            transport.return_value.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
