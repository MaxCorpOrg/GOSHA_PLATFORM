#!/usr/bin/env python3
"""Per-assistant Live voice routing; other sessions retain the compatible backend."""
import asyncio
import base64
import contextlib
import http
import json
import logging
import os
import secrets
import time
import uuid
from collections import deque
from urllib.parse import urlsplit

from websockets.legacy.client import connect
from websockets.legacy.server import serve

import gosha_agent_store as providers
import gosha_assistant_store as assistants
import selfhost_xiaozhi_common as claims
from gosha_live_audio import AUDIO_PARAMS, FRAME_MS, PCM_BYTES, SILENCE, OpusCodec, audible, pack_audio, unpack_audio
from gosha_live_protocol import LiveSession, session_config

LOG = logging.getLogger("gosha.voice")
PERIOD = FRAME_MS / 1000


def authenticate(headers):
    claim = claims.find_claim_by_device(headers.get("Device-Id", ""))
    auth = headers.get("Authorization", "")
    supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    expected = (claim or {}).get("websocket_token", "")
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        return None
    return claim


def live_settings(claim):
    effective = assistants.effective_robot_assistant_config(claim["robot_id"])
    assistant = effective.get("assistant_profile") or {}
    if assistant.get("voice_engine", "chained") != "openai_live":
        return None
    public_provider = effective.get("provider_profile") or {}
    provider = providers.get_agent_profile(public_provider.get("profile_id", ""))
    if not provider or not provider.get("enabled"):
        raise ValueError("live_provider_missing")
    key = providers.resolve_api_key(provider)
    if not key:
        raise ValueError("openai_api_key_missing")
    return session_config(assistant, provider), key


async def stop_tasks(tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


class RobotLiveBridge:
    def __init__(self, robot, config, key, *, session_factory=LiveSession, max_seconds=300, idle_seconds=60):
        self.robot, self.config, self.key = robot, config, key
        self.session_factory = session_factory
        self.max_seconds, self.idle_seconds = max_seconds, idle_seconds
        self.codec = None
        self.version = 1
        self.session_id = uuid.uuid4().hex
        self.input = asyncio.Queue(maxsize=50)  # Startup/reconnect buffer, never replayed as a burst.
        self.output = asyncio.Queue(maxsize=50)
        self.output_tail = bytearray()
        self.speaking = False
        self.listening = False
        self.realtime = False
        self.started_at = self.last_activity = time.monotonic()
        self.restart = asyncio.Event()
        self.stopped = asyncio.Event()
        self.history = deque(maxlen=16)
        self.accept_output = True
        self.last_output_received = 0.0
        self.dropped_input_frames = 0
        self.last_user_audio = 0.0
        self.audio_flow = dict.fromkeys(("robot_frames", "decoded_frames", "decoded_audible",
                                        "input_sent", "input_sent_audible", "listen_starts", "listen_stops",
                                        "ignored_not_listening", "ignored_speaking", "transcript_events",
                                        "backend_completed", "output_audible", "tts_starts", "tts_stops"), 0)
        self.last_flow_log = self.started_at

    def log_audio_flow(self):
        # Fixed numeric counters only: never audio, text, identity or credentials.
        LOG.info("live_audio_flow %s", json.dumps(self.audio_flow, sort_keys=True))

    async def send_json(self, kind, **fields):
        await self.robot.send(json.dumps({"type": kind, "session_id": self.session_id, **fields}, ensure_ascii=False))

    async def hello(self):
        raw = await asyncio.wait_for(self.robot.recv(), 8)
        hello = json.loads(raw) if isinstance(raw, str) else {}
        if hello.get("type") != "hello" or hello.get("transport") != "websocket":
            raise ValueError("invalid_robot_hello")
        self.version = hello.get("version", 1)
        params = hello.get("audio_params")
        # Current firmware also announces hardware input/output rates. They are
        # metadata: its encoded uplink remains 16 kHz and playback resamples the
        # negotiated downlink. Validate wire fields without rejecting extensions.
        if (type(self.version) is not int or self.version not in (1, 2, 3)
                or not isinstance(params, dict)
                or any(isinstance(params.get(field), bool) or params.get(field) != value
                       for field, value in AUDIO_PARAMS.items())
                or params.get("uplink_sample_rate", AUDIO_PARAMS["sample_rate"]) != AUDIO_PARAMS["sample_rate"]):
            raise ValueError("unsupported_robot_audio")
        LOG.info("robot_hello_accepted version=%s", self.version)

    def remember(self, role, delta):
        if not isinstance(delta, str) or not delta:
            return
        # Text remains in memory only, for restart after an explicit interruption.
        if self.history and self.history[-1]["role"] == role:
            self.history[-1]["text"] = (self.history[-1]["text"] + delta)[-2000:]
        else:
            self.history.append({"role": role, "text": delta[-2000:]})
        while sum(len(item["text"]) for item in self.history) > 6000:
            self.history.popleft()

    def fresh_config(self):
        history = [
            {"type": "message", "role": item["role"], "content": [{
                "type": "input_text", "text": item["text"],
            }]} for item in self.history if item["role"] == "user"
        ]
        if self.history:
            history.append({"type": "message", "role": "developer", "content": [{
                "type": "input_text", "text": "Предыдущая речь ассистента была прервана. "
                "Пользователь мог её не услышать. Не считай старые запросы новыми поручениями; слушай новый запрос.",
            }]})
        return {**self.config, "input": history}

    async def read_robot(self):
        try:
            async for raw in self.robot:
                if isinstance(raw, bytes):
                    self.audio_flow["robot_frames"] += 1
                    if self.listening and (self.realtime or not self.speaking):
                        pcm = self.codec.decode(unpack_audio(raw, self.version))
                        self.audio_flow["decoded_frames"] += 1
                        if audible(pcm):
                            self.audio_flow["decoded_audible"] += 1
                            self.last_activity = time.monotonic()
                            self.last_user_audio = self.last_activity
                        if self.input.full():
                            self.input.get_nowait()
                            self.dropped_input_frames += 1
                        self.input.put_nowait(pcm)
                    else:
                        self.audio_flow["ignored_not_listening" if not self.listening else "ignored_speaking"] += 1
                    continue
                message = json.loads(raw)
                kind = message.get("type")
                if kind == "listen":
                    state = message.get("state")
                    if state == "start":
                        self.audio_flow["listen_starts"] += 1
                        self.realtime = message.get("mode") == "realtime"
                        self.listening = True
                        self.last_activity = time.monotonic()
                    elif state == "stop":
                        self.audio_flow["listen_stops"] += 1
                        self.listening = False
                    # Wake-word 'detect' text isn't sent as a fabricated user utterance.
                elif kind == "abort":
                    self.accept_output = False
                    while not self.input.empty():
                        self.input.get_nowait()
                    self.restart.set()
                    self.last_activity = time.monotonic()
                elif kind == "goodbye":
                    return
                # No MCP, IoT, motion, OTA or control tools are exposed on Live.
        finally:
            self.stopped.set()

    async def input_loop(self, session):
        while True:
            now = time.monotonic()
            if now - self.started_at >= self.max_seconds or now - self.last_activity >= self.idle_seconds:
                self.stopped.set()
                return
            try:
                pcm = self.input.get_nowait()
            except asyncio.QueueEmpty:
                pcm = SILENCE
            await session.send_audio(pcm)
            self.audio_flow["input_sent"] += 1
            self.audio_flow["input_sent_audible"] += int(audible(pcm))
            if os.environ.get("GOSHA_VOICE_DIAGNOSTICS") == "1" and now - self.last_flow_log >= 5:
                self.log_audio_flow()
                self.last_flow_log = now
            # Never send catch-up bursts after network stalls. Fill half-duplex gaps with silence.
            await asyncio.sleep(max(0, now + PERIOD - time.monotonic()))

    async def read_live(self, session):
        async for event in session.events():
            kind = event.get("type")
            if not self.accept_output:
                continue
            if kind == "session.output_audio.delta":
                pcm = base64.b64decode(event["delta"], validate=True)
                if len(pcm) % 2 or len(pcm) > PCM_BYTES * 500:
                    raise ValueError("invalid_live_audio")
                self.last_output_received = time.monotonic()
                self.output_tail.extend(pcm)
                while len(self.output_tail) >= PCM_BYTES:
                    await self.output.put(bytes(self.output_tail[:PCM_BYTES]))
                    del self.output_tail[:PCM_BYTES]
            elif kind in {"session.input_transcript.delta", "session.output_transcript.delta"}:
                role = "user" if kind == "session.input_transcript.delta" else "assistant"
                self.remember(role, event.get("delta", ""))
                if role == "user":
                    self.audio_flow["transcript_events"] += 1
                    self.last_activity = time.monotonic()
            elif kind == "response.event":
                backend = event.get("event", {})
                if backend.get("type") == "response.completed":
                    self.audio_flow["backend_completed"] += 1
                if backend.get("type") in {"response.failed", "response.incomplete", "error"}:
                    raise RuntimeError("live_reasoning_failed")
            elif kind == "session.closed":
                return

    async def stop_speech(self):
        if self.speaking:
            self.speaking = False
            self.audio_flow["tts_stops"] += 1
            await self.send_json("tts", state="stop")

    async def output_loop(self):
        silent_frames = 0
        while True:
            tick = time.monotonic()
            try:
                pcm = self.output.get_nowait()
            except asyncio.QueueEmpty:
                if self.output_tail and tick - self.last_output_received >= 0.2:
                    pcm = bytes(self.output_tail).ljust(PCM_BYTES, b"\0")
                    self.output_tail.clear()
                else:
                    pcm = SILENCE
            if not self.realtime and not self.speaking and tick - self.last_user_audio < 0.35:
                # A Live backchannel must not turn off a half-duplex robot's microphone mid-utterance.
                await asyncio.sleep(PERIOD)
                continue
            if audible(pcm):
                self.audio_flow["output_audible"] += 1
                silent_frames = 0
                self.last_activity = time.monotonic()
                if not self.speaking:
                    self.speaking = True
                    self.audio_flow["tts_starts"] += 1
                    await self.send_json("tts", state="start")
                    # Firmware schedules Speaking on its main task before it can accept binary audio.
                    await asyncio.sleep(PERIOD)
                    if self.accept_output:
                        await self.robot.send(pack_audio(self.codec.encode(SILENCE), self.version))
                    await asyncio.sleep(PERIOD)
                    tick = time.monotonic()
            else:
                silent_frames += 1
            if self.speaking and self.accept_output:
                await self.robot.send(pack_audio(self.codec.encode(pcm), self.version))
            await asyncio.sleep(max(0, tick + PERIOD - time.monotonic()))
            if self.speaking and silent_frames >= 12:
                # Live keeps streaming silence. Waiting for an empty queue can pin the
                # half-duplex robot in Speaking forever. Use consumed quiet frames;
                # later audible frames will start a new playback segment normally.
                await self.stop_speech()

    async def retire(self, session, reader):
        try:
            await session.close()
        finally:
            await stop_tasks([reader])
            LOG.info("live_session_closed finalized=%s", session.finalized.is_set())
            if isinstance(session.usage, dict) and isinstance(session.usage.get("seconds"), (int, float)):
                LOG.info("live_voice_usage seconds=%s", session.usage["seconds"])

    async def run(self):
        self.codec = OpusCodec()
        robot_reader = None
        retirees = set()
        try:
            await self.hello()
            initial = True
            restart_count = 0
            while not self.stopped.is_set():
                self.restart.clear()
                session = self.session_factory(self.fresh_config(), self.key)
                await asyncio.wait_for(session.start(), 8)
                self.accept_output = True
                if initial:
                    await self.send_json("hello", version=self.version, transport="websocket", audio_params=AUDIO_PARAMS)
                    robot_reader = asyncio.create_task(self.read_robot())
                    initial = False
                reader = asyncio.create_task(self.read_live(session))
                sender = asyncio.create_task(self.input_loop(session))
                playback = asyncio.create_task(self.output_loop())
                restart = asyncio.create_task(self.restart.wait())
                stopped = asyncio.create_task(self.stopped.wait())
                try:
                    done, _ = await asyncio.wait(
                        [reader, sender, playback, restart, stopped, robot_reader],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        task.result()
                finally:
                    self.accept_output = False
                    await stop_tasks([sender, playback, restart, stopped])
                    with contextlib.suppress(Exception):
                        await self.stop_speech()
                    self.output_tail.clear()
                    while not self.output.empty():
                        self.output.get_nowait()
                    # Retired readers must not publish old audio into the new session.
                    await stop_tasks([reader])
                    drain = asyncio.create_task(self.drain_retired(session))
                    retirees.add(drain)
                    drain.add_done_callback(retirees.discard)
                if not self.restart.is_set():
                    break
                restart_count += 1
                if restart_count >= 5:
                    break
                # Explicit abort starts a fresh provider session; stale responses cannot resume.
        finally:
            if robot_reader:
                await stop_tasks([robot_reader])
            if retirees:
                await asyncio.gather(*retirees, return_exceptions=True)
            self.codec.close()
            self.log_audio_flow()
            if self.dropped_input_frames:
                LOG.info("live_input_dropped frames=%s", self.dropped_input_frames)

    async def drain_retired(self, session):
        async def drain():
            async for _ in session.events():
                pass
        reader = asyncio.create_task(drain())
        await self.retire(session, reader)


class VoiceRouter:
    def __init__(self, legacy_url, *, max_sessions=4, bridge_factory=RobotLiveBridge):
        parsed = urlsplit(legacy_url)
        if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.query:
            raise ValueError("legacy_backend_must_be_loopback")
        self.legacy_url = legacy_url.rstrip("/")
        self.max_sessions = max_sessions
        self.active = set()
        self.bridge_factory = bridge_factory

    async def proxy(self, robot, path):
        # Preserve only application headers; never forward WebSocket handshake headers twice.
        headers = {name: robot.request_headers[name] for name in (
            "Authorization", "Device-Id", "Client-Id", "Protocol-Version",
        ) if name in robot.request_headers}
        async with connect(self.legacy_url + path, extra_headers=headers, max_size=2**20, max_queue=16, open_timeout=8) as backend:
            async def pipe(source, target):
                async for message in source:
                    await target.send(message)
            tasks = [asyncio.create_task(pipe(robot, backend)), asyncio.create_task(pipe(backend, robot))]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                await stop_tasks(tasks)

    async def handle(self, robot, path):
        robot_id = None
        try:
            # Compatible MCP/other paths stay with the original backend. Live voice is exact-path only.
            if urlsplit(path).path.rstrip("/") != "/xiaozhi/v1":
                await self.proxy(robot, path)
                return
            if not claims.find_claim_by_device(robot.request_headers.get("Device-Id", "")):
                # Preserve the legacy server's onboarding/auth behavior for unclaimed devices.
                await self.proxy(robot, path)
                return
            claim = authenticate(robot.request_headers)
            if not claim:
                await robot.close(1008, "device_auth_required")
                return
            settings = live_settings(claim)
            if settings is None:
                await self.proxy(robot, path)
                return
            if claim["robot_id"] in self.active or len(self.active) >= self.max_sessions:
                await robot.close(1013, "voice_session_busy")
                return
            robot_id = claim["robot_id"]
            self.active.add(robot_id)
            config, key = settings
            await self.bridge_factory(robot, config, key).run()
            await robot.close(1000, "voice_session_ended")
        except Exception as exc:
            # Exception messages, URLs, device IDs, transcripts and keys never enter logs.
            LOG.warning("voice_session_failed category=%s", type(exc).__name__)
            with contextlib.suppress(Exception):
                await robot.close(1011, "voice_service_unavailable")
        finally:
            if robot_id:
                self.active.discard(robot_id)


async def health(path, headers):
    if path == "/healthz":
        body = b'{"ok":true,"service":"gosha-voice-router"}\n'
        return http.HTTPStatus.OK, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))], body


async def main():
    host = os.environ.get("GOSHA_VOICE_HOST", "127.0.0.1")
    port = int(os.environ.get("GOSHA_VOICE_PORT", "18084"))
    legacy = os.environ.get("GOSHA_VOICE_LEGACY_URL", "ws://127.0.0.1:18080")
    if urlsplit(legacy).port == port:
        raise ValueError("voice_router_loop")
    router = VoiceRouter(legacy, max_sessions=int(os.environ.get("GOSHA_VOICE_MAX_SESSIONS", "4")))
    async with serve(router.handle, host, port, process_request=health, max_size=65536, max_queue=16):
        LOG.info("voice_router_ready")
        await asyncio.Future()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    # Library tracebacks can carry credential-bearing handshake objects.
    logging.getLogger("websockets").setLevel(logging.CRITICAL)
    asyncio.run(main())
