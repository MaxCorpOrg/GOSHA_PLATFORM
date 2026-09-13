#!/usr/bin/env python3
"""Contract and local WebSocket regression tests; no external API or robot."""
import asyncio
import base64
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
import threading
import urllib.request
from unittest.mock import patch

_runtime = tempfile.TemporaryDirectory(prefix="gosha-live-tests-")
os.environ["APP_ROOT"] = _runtime.name

from websockets.legacy.client import connect
from websockets.legacy.server import serve
import gosha_agent_store as providers
import gosha_assistant_store as assistants
import selfhost_xiaozhi_common as claims
from gosha_live_audio import AUDIO_PARAMS, PCM_BYTES, SILENCE, SAMPLES, OpusCodec, audible, pack_audio, unpack_audio
from gosha_live_protocol import LIVE_URL, LiveSession, session_config
from gosha_voice_router import RobotLiveBridge, VoiceRouter, authenticate

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from configure_gpt_live import apply_profiles, proposed_profiles

TONE = struct.pack("<%dh" % SAMPLES, *[int(8000 * math.sin(i * 2 * math.pi * 440 / 16000)) for i in range(SAMPLES)])
PROVIDER = {"base_url": "https://api.openai.com/v1", "model": "gpt-5.5"}


class AudioTests(unittest.TestCase):
    def test_real_opus_roundtrip_and_wire_versions(self):
        codec = OpusCodec()
        try:
            opus = codec.encode(TONE)
            for version in (1, 2, 3):
                self.assertEqual(unpack_audio(pack_audio(opus, version), version), opus)
            pcm = codec.decode(opus)
            self.assertEqual(len(pcm), PCM_BYTES)
            self.assertTrue(audible(pcm))
            self.assertFalse(audible(SILENCE))
        finally:
            codec.close()
            codec.close()

    def test_invalid_packet_bounds(self):
        for packet, version in ((b"", 1), (b"abc", 2), (b"abc", 3), (b"abc", 4),
                                (struct.pack("!BBH", 0, 0, 8) + b"x", 3)):
            with self.assertRaises(ValueError):
                unpack_audio(packet, version)

    def test_exact_model_contract(self):
        config = session_config({}, PROVIDER)
        self.assertEqual(config["model"], "gpt-live-1")
        self.assertEqual(config["delegation"]["responses"]["model"], "gpt-5.5")
        self.assertEqual(config["audio"]["format"], {"type": "audio/pcm", "rate": 16000})
        self.assertFalse(config["store"])
        self.assertEqual(config["delegation"]["responses"]["tools"], [])
        for provider in ({**PROVIDER, "model": "deepseek-v4-flash"}, {**PROVIDER, "base_url": "https://example.invalid/v1"}):
            with self.assertRaises(ValueError):
                session_config({}, provider)


class StoreTests(unittest.TestCase):
    def test_selecting_live_and_returning_to_legacy_does_not_restart_backend(self):
        import gui_panel
        robot_id = "binding-robot"
        (Path(_runtime.name) / "robots" / robot_id).mkdir(parents=True, exist_ok=True)
        provider, assistant = proposed_profiles(robot_id)
        providers.save_agent_profile(provider["profile_id"], provider)
        assistants.save_assistant_profile(assistant["profile_id"], assistant)
        assistants.save_assistant_profile("legacy-binding-fixture", {"display_name": "Legacy"})
        with patch.object(gui_panel, "refresh_backend_runtime", side_effect=AssertionError("must not restart")) as refresh, patch.object(gui_panel, "agent_gateway_status", return_value={}):
            for profile_id in (assistant["profile_id"], "legacy-binding-fixture"):
                result = gui_panel.save_robot_assistant_config(robot_id, {"assistant_profile_id": profile_id})
                self.assertEqual(result["apply"]["activation"], "next_voice_connection")
            refresh.assert_not_called()

    def test_operator_api_round_trips_live_settings(self):
        import gui_panel
        provider, assistant = proposed_profiles("http-robot", "quartz", "high")
        providers.save_agent_profile(provider["profile_id"], provider)
        server = gui_panel.ThreadingHTTPServer(("127.0.0.1", 0), gui_panel.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/operator/assistant-profiles",
                data=json.dumps(assistant).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with patch.object(gui_panel, "refresh_backend_runtime", side_effect=AssertionError("Live profile must not restart legacy backend")) as refresh:
                with urllib.request.urlopen(request, timeout=3) as response:
                    payload = json.load(response)
                refresh.assert_not_called()
            self.assertTrue(payload["ok"])
            self.assertFalse(payload["apply"]["runtime_verified"])
            saved = assistants.get_assistant_profile(assistant["profile_id"])
            self.assertEqual(saved["live_voice"], "quartz")
            self.assertEqual(saved["live_reasoning_effort"], "high")
            self.assertEqual(saved["voice_engine"], "openai_live")
        finally:
            server.shutdown()
            thread.join(3)
            server.server_close()

    def test_profile_defaults_and_persistence(self):
        provider, assistant = proposed_profiles("fixture-robot")
        providers.save_agent_profile(provider["profile_id"], provider)
        saved = assistants.save_assistant_profile(assistant["profile_id"], assistant)
        self.assertEqual(saved["voice_engine"], "openai_live")
        self.assertEqual(saved["live_reasoning_effort"], "medium")
        legacy = assistants.normalize_assistant_profile("legacy-test", {})
        self.assertEqual(legacy["voice_engine"], "chained")
        with self.assertRaises(ValueError):
            assistants.normalize_assistant_profile("bad-test", {"voice_engine": "unknown"})

    def test_device_auth_checks_token_not_only_identity(self):
        claim = {"robot_id": "fixture-robot", "websocket_token": "test-only-token"}
        with patch.object(claims, "find_claim_by_device", return_value=claim):
            self.assertIsNone(authenticate({"Device-Id": "fixture-device"}))
            self.assertIsNone(authenticate({"Device-Id": "fixture-device", "Authorization": "Bearer wrong"}))
            self.assertEqual(authenticate({"Device-Id": "fixture-device", "Authorization": "Bearer test-only-token"}), claim)

    def test_apply_preserves_other_binding_fields_and_backup(self):
        provider, assistant = proposed_profiles("apply-robot")
        before = assistants.load_robot_binding("apply-robot")
        with patch("configure_gpt_live.find_claim_by_robot", return_value={"robot_id": "apply-robot"}), patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-only"}):
            backup = apply_profiles("apply-robot", provider, assistant)
        binding = assistants.load_robot_binding("apply-robot")
        self.assertEqual(binding["assistant_profile_id"], assistant["profile_id"])
        self.assertEqual(binding["screen_profile_id"], before["screen_profile_id"])
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        manifest = json.loads((backup / "manifest.json").read_text())
        self.assertEqual(len(manifest), 3)
        self.assertNotIn("unit-test-only", "".join(p.read_text() for p in backup.iterdir()))


class FakeSocket:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def send(self, raw):
        event = json.loads(raw)
        self.sent.append(event)
        if event["type"] == "session.start":
            await self.queue.put(json.dumps({"type": "session.started", "session": {"id": "fixture"}}))
        elif event["type"] == "session.close":
            await self.queue.put(json.dumps({"type": "session.closed", "usage": {"seconds": 1}, "reason": "client_requested"}))

    async def recv(self):
        return await self.queue.get()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.recv()

    async def close(self):
        self.closed = True


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_audio_and_graceful_final_usage(self):
        socket = FakeSocket()
        calls = []
        async def connector(url, **kwargs):
            calls.append((url, kwargs))
            return socket
        session = LiveSession(session_config({}, PROVIDER), "unit-only-secret", connector=connector)
        await session.start()
        self.assertEqual(calls[0][0], LIVE_URL)
        self.assertEqual(calls[0][1]["extra_headers"], {"Authorization": "Bearer unit-only-secret"})
        self.assertEqual(socket.sent[0]["type"], "session.start")
        await session.send_audio(SILENCE)
        self.assertEqual(socket.sent[1]["type"], "session.input_audio.append")
        self.assertEqual(base64.b64decode(socket.sent[1]["audio"]), SILENCE)
        async def receive():
            async for _ in session.events():
                pass
        reader = asyncio.create_task(receive())
        await session.close()
        await reader
        self.assertTrue(session.finalized.is_set())
        self.assertEqual(session.usage, {"seconds": 1})
        self.assertTrue(socket.closed)

    async def test_missing_key_never_connects(self):
        async def forbidden(*args, **kwargs):
            self.fail("must not connect without key")
        with self.assertRaises(ValueError):
            await LiveSession(session_config({}, PROVIDER), "", connector=forbidden).start()

    async def test_provider_error_is_redacted(self):
        socket = FakeSocket()
        session = LiveSession({}, "unit-only-secret")
        session.ws = socket
        await socket.queue.put(json.dumps({"type": "error", "error": {"message": "unit-only-secret private transcript"}}))
        with self.assertRaisesRegex(RuntimeError, "^live_command_rejected$"):
            async for _ in session.events():
                pass


class FakeLive:
    instances = []

    def __init__(self, config, key):
        self.config, self.key = config, key
        self.queue = asyncio.Queue()
        self.audio = []
        self.finalized = asyncio.Event()
        self.usage = None
        self.ws = self
        self.__class__.instances.append(self)

    async def start(self):
        return {"type": "session.started"}

    async def send_audio(self, pcm):
        self.audio.append((time.monotonic(), pcm))

    async def events(self):
        while True:
            event = await self.queue.get()
            if event["type"] == "session.closed":
                self.finalized.set()
                self.usage = {"seconds": 1}
                yield event
                return
            yield event

    async def close(self):
        await self.queue.put({"type": "session.closed"})
        await asyncio.wait_for(self.finalized.wait(), 1)


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeLive.instances = []
        self.bridges = []
        self.tasks = set()
        async def handler(ws, path):
            task = asyncio.current_task()
            self.tasks.add(task)
            bridge = RobotLiveBridge(ws, session_config({}, PROVIDER), "unit-only", session_factory=FakeLive, idle_seconds=3)
            self.bridges.append(bridge)
            try:
                await bridge.run()
            finally:
                self.tasks.discard(task)
        self.server = await serve(handler, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def open_robot(self, version=1):
        ws = await connect(f"ws://127.0.0.1:{self.port}/xiaozhi/v1/")
        await ws.send(json.dumps({"type": "hello", "version": version, "transport": "websocket", "audio_params": AUDIO_PARAMS}))
        hello = json.loads(await asyncio.wait_for(ws.recv(), 2))
        self.assertEqual(hello["audio_params"], AUDIO_PARAMS)
        await ws.send(json.dumps({"type": "listen", "state": "start", "mode": "auto"}))
        return ws

    async def test_local_socket_audio_pacing_and_speech_stop(self):
        ws = await self.open_robot(version=3)
        codec = OpusCodec()
        try:
            await ws.send(pack_audio(codec.encode(TONE), 3))
            await asyncio.sleep(0.4)
            live = FakeLive.instances[0]
            self.assertTrue(any(audible(pcm) for _, pcm in live.audio))
            self.assertTrue(all(b[0] - a[0] >= 0.045 for a, b in zip(live.audio, live.audio[1:])))
            await live.queue.put({"type": "session.output_audio.delta", "delta": base64.b64encode(TONE).decode()})
            self.assertEqual(json.loads(await asyncio.wait_for(ws.recv(), 2))["state"], "start")
            await asyncio.wait_for(ws.recv(), 2)  # Silent decoder warmup frame.
            audio = await asyncio.wait_for(ws.recv(), 2)
            self.assertTrue(audible(codec.decode(unpack_audio(audio, 3))))
            async with asyncio.timeout(3):
                while True:
                    frame = await ws.recv()
                    if isinstance(frame, str) and json.loads(frame).get("state") == "stop":
                        break
            await ws.send(json.dumps({"type": "goodbye"}))
            await asyncio.sleep(0.1)
            self.assertTrue(live.finalized.is_set())
        finally:
            codec.close()
            await ws.close()

    async def test_abort_retires_previous_session(self):
        ws = await self.open_robot()
        try:
            old = FakeLive.instances[0]
            self.bridges[0].remember("user", "Первый вопрос")
            self.bridges[0].remember("assistant", "Неуслышанный ответ")
            for _ in range(10):
                self.bridges[0].input.put_nowait(TONE)
            await ws.send(json.dumps({"type": "abort", "reason": "wake_word_detected"}))
            async with asyncio.timeout(2):
                while len(FakeLive.instances) < 2:
                    await asyncio.sleep(0.01)
            await old.queue.put({"type": "session.output_audio.delta", "delta": base64.b64encode(TONE).decode()})
            await asyncio.sleep(0.15)
            self.assertFalse(self.bridges[0].speaking)
            self.assertTrue(old.finalized.is_set())
            self.assertTrue(all(pcm == SILENCE for _, pcm in FakeLive.instances[1].audio))
            self.assertNotIn("Неуслышанный", json.dumps(FakeLive.instances[1].config, ensure_ascii=False))
            await ws.send(json.dumps({"type": "goodbye"}))
        finally:
            await ws.close()

    async def test_router_rejects_missing_auth_before_provider(self):
        class Robot:
            request_headers = {}
            closed = None
            async def close(self, code, reason):
                self.closed = (code, reason)
        robot = Robot()
        router = VoiceRouter("ws://127.0.0.1:18081")
        with patch.object(claims, "find_claim_by_device", return_value={"robot_id": "fixture"}), patch("gosha_voice_router.authenticate", return_value=None), patch("gosha_voice_router.live_settings", side_effect=AssertionError("no provider call")):
            await router.handle(robot, "/xiaozhi/v1/")
        self.assertEqual(robot.closed[0], 1008)

    async def test_legacy_proxy_preserves_frames_and_headers(self):
        seen = []
        async def echo(ws, path):
            seen.append((path, ws.request_headers.get("Device-Id")))
            async for raw in ws:
                await ws.send(raw)
        backend = await serve(echo, "127.0.0.1", 0)
        backend_port = backend.sockets[0].getsockname()[1]
        router = VoiceRouter(f"ws://127.0.0.1:{backend_port}")
        server = await serve(router.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            async with connect(f"ws://127.0.0.1:{port}/xiaozhi/v1/", extra_headers={"Device-Id": "unclaimed-fixture"}) as ws:
                for raw in ('{"type":"hello"}', b"opus-fixture"):
                    await ws.send(raw)
                    self.assertEqual(await asyncio.wait_for(ws.recv(), 2), raw)
            self.assertEqual(seen, [("/xiaozhi/v1/", "unclaimed-fixture")])
        finally:
            server.close()
            backend.close()
            await server.wait_closed()
            await backend.wait_closed()

    async def test_long_delta_uses_backpressure_and_disconnect_finalizes(self):
        ws = await self.open_robot()
        live = FakeLive.instances[0]
        await live.queue.put({"type": "session.output_audio.delta", "delta": base64.b64encode(TONE * 80).decode()})
        self.assertEqual(json.loads(await asyncio.wait_for(ws.recv(), 2))["state"], "start")
        await asyncio.sleep(0.1)
        self.assertTrue(self.tasks)
        await ws.close()
        async with asyncio.timeout(2):
            while not live.finalized.is_set():
                await asyncio.sleep(0.01)

    async def test_idle_voice_connection_closes(self):
        ws = await self.open_robot()
        live = FakeLive.instances[0]
        self.bridges[0].idle_seconds = 0.1
        await asyncio.sleep(0.3)
        self.assertTrue(live.finalized.is_set())
        await ws.close()


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_requests_voice_separately_from_reasoning(self):
        from probe_gpt_live import probe
        sent = []
        class ProbeLive(FakeLive):
            async def send(self, kind, **fields):
                sent.append((kind, fields))
                if kind == "session.instructions.append":
                    await self.queue.put({"type": "session.instructions.appended", "client_event_id": fields["event_id"]})
                elif kind == "session.commentary.append":
                    await self.queue.put({"type": "session.output_audio.delta", "delta": base64.b64encode(TONE).decode()})
                elif kind == "response.create":
                    await self.queue.put({"type": "response.event", "event": {"type": "response.completed", "response": {"status": "completed", "model": "gpt-5.5"}}})
        with patch("probe_gpt_live.LiveSession", ProbeLive), patch("probe_gpt_live.check_models"):
            result = await probe(session_config({}, PROVIDER), "unit-only", seconds=1)
        self.assertTrue(result["ok"], result)
        self.assertEqual([kind for kind, _ in sent], ["session.instructions.append", "session.commentary.append", "response.item.create", "response.create"])
        self.assertIsNone(sent[0][1]["delegation_id"])
        self.assertTrue(result["finalized"])


if __name__ == "__main__":
    unittest.main()
