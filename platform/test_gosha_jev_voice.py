"""Model transcript, Luna and JEV must reach the existing executor once."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gosha_jev_trial import CRITERIA, SimulatedRobot, TrialDispatcher
from gosha_jev_voice import JevRobotBridge
from gosha_live_audio import AUDIO_PARAMS


def response(choice):
    probabilities = dict.fromkeys(CRITERIA, 0.0)
    probabilities[choice] = 1.0
    return {"model": "jev-1.13.0", "answers": {"skill": {
        "type": "choice", "choice": choice, "confidence": 1.0,
        "probabilities": probabilities}}, "usage": {"input_tokens": 1, "output_tokens": 1}}


class FakeLuna:
    def evaluate(self, source):
        return {"kind": "action", "english": "Gosha, wave your hand."}, 1

    def close(self):
        pass


class FakeJev:
    def evaluate(self, english):
        return response("builtin/hand_wave"), 1

    def close(self):
        pass


class VoiceDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.robot = SimulatedRobot()
        await self.robot.device.discover(retry_delays=())
        bridge = JevRobotBridge.__new__(JevRobotBridge)
        bridge.worker = ThreadPoolExecutor(max_workers=2)
        bridge.device_tools = self.robot.device
        bridge.dispatcher = TrialDispatcher(self.robot.device, require_source=True)
        bridge.stopped = asyncio.Event()
        bridge.generation = 1
        bridge.luna = FakeLuna()
        bridge.jev = FakeJev()
        self.bridge = bridge

    async def asyncTearDown(self):
        self.bridge.worker.shutdown(wait=True)
        self.robot.device.close()

    async def test_completed_wave_dispatches_once(self):
        await self.bridge.process_transcript(1, "item-1", "Гоша, помаши рукой.")
        self.assertEqual(len(self.robot.calls), 1)
        self.assertEqual(self.robot.calls[0]["name"], "self.motion.play")

    async def test_luna_action_cannot_override_cancellation(self):
        await self.bridge.process_transcript(1, "item-2", "Гоша, помаши рукой. Нет, не надо.")
        self.assertEqual(self.robot.calls, [])

    async def test_stop_bypasses_luna_and_jev(self):
        self.bridge.luna.evaluate = lambda source: self.fail("Luna must not delay stop")
        await self.bridge.process_transcript(1, "item-3", "Гоша, останови движение.")
        self.assertEqual(self.robot.calls[0]["name"], "self.motion.stop")

    async def test_late_luna_result_is_discarded(self):
        def late(source):
            self.bridge.generation += 1
            return {"kind": "action", "english": "Gosha, wave your hand."}, 1
        self.bridge.luna.evaluate = late
        await self.bridge.process_transcript(1, "item-4", "Гоша, помаши рукой.")
        self.assertEqual(self.robot.calls, [])


class FakeRobotSocket:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.sent_calls = []
        self.play_sent = asyncio.Event()
        self.queue.put_nowait(json.dumps({"type": "hello", "transport": "websocket",
                                          "version": 2, "audio_params": AUDIO_PARAMS,
                                          "features": {"mcp": True, "live_duplex": False}}))

    async def recv(self):
        return await self.queue.get()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.recv()

    async def send(self, raw):
        event = json.loads(raw)
        if event["type"] == "hello":
            await self.queue.put(json.dumps({"type": "listen", "state": "start"}))
            return
        if event["type"] != "mcp":
            return
        request = event["payload"]
        method = request["method"]
        if method == "notifications/initialized" or "id" not in request:
            return
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05"}
        elif method == "tools/list":
            result = {"tools": [{"name": "self.motion.play"}, {"name": "self.motion.stop"}]}
        elif method == "tools/call":
            self.sent_calls.append(request["params"])
            result = {"content": [{"type": "text", "text": json.dumps({
                "status": "in_progress", "duration_ms": 3000,
            })}]}
            self.play_sent.set()
        else:
            raise AssertionError(method)
        await self.queue.put(json.dumps({"type": "mcp", "payload": {
            "jsonrpc": "2.0", "id": request["id"], "result": result}}))


class FakeTranscriber:
    def __init__(self, key):
        self.events_queue = asyncio.Queue()
        self.closed = False
        self.audio_frames = 0

    async def start(self):
        for event in (
            {"type": "input_audio_buffer.speech_started"},
            {"type": "input_audio_buffer.speech_stopped", "item_id": "item-voice"},
            {"type": "conversation.item.input_audio_transcription.completed",
             "item_id": "item-voice", "transcript": "Гоша, помаши рукой."},
        ):
            await self.events_queue.put(event)

    async def send_pcm(self, pcm):
        self.audio_frames += 1

    async def events(self):
        while True:
            yield await self.events_queue.get()

    async def close(self):
        self.closed = True


class FullVoiceRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_robot_wire_reaches_one_existing_mcp_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            key_file = Path(temporary) / "key"
            key_file.write_text("unit-only")
            robot = FakeRobotSocket()
            with patch("gosha_jev_voice.JevTrialClient", lambda key: FakeJev()), \
                 patch("gosha_jev_voice.LunaIntentClient", lambda key: FakeLuna()):
                bridge = JevRobotBridge(robot, key_file=str(key_file), openai_key="unit-only",
                                        transcriber_factory=FakeTranscriber)
                runner = asyncio.create_task(bridge.run())
                try:
                    await asyncio.wait_for(robot.play_sent.wait(), 3)
                    self.assertEqual(len([call for call in robot.sent_calls
                                          if call["name"] == "self.motion.play"]), 1)
                finally:
                    await robot.queue.put(json.dumps({"type": "goodbye"}))
                    await asyncio.wait_for(runner, 3)


if __name__ == "__main__":
    unittest.main()
