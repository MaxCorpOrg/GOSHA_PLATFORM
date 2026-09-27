"""Opt-in Gosha voice trial: model transcript -> Luna -> JEV -> DeviceTools.

The ordinary GPT-Live route remains the default. This experiment has no
spoken assistant response yet and never gives either model physical tools.
"""
import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gosha_jev_intent import LunaIntentClient
from gosha_jev_language import is_priority_stop
from gosha_jev_transcribe import ModelTranscriber
from gosha_jev_trial import JevTrialClient, TrialDispatcher, validate_response
from gosha_live_audio import AUDIO_PARAMS, OpusCodec, SILENCE
from gosha_voice_router import RobotLiveBridge, stop_tasks

LOG = logging.getLogger("gosha.voice")


class JevRobotBridge(RobotLiveBridge):
    def __init__(self, robot, *, key_file, openai_key, transcriber_factory=ModelTranscriber):
        super().__init__(robot, {}, "")
        key = Path(key_file).read_text(encoding="utf-8").strip()
        self.jev = JevTrialClient(key)
        self.luna = LunaIntentClient(openai_key)
        self.transcriber = transcriber_factory(openai_key)
        self.dispatcher = TrialDispatcher(self.device_tools, require_source=True)
        self.worker = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gosha-jev")
        self.generation = 0
        self.turn_generations = {}
        self.tasks = set()

    async def process_transcript(self, generation, item_id, source):
        if not isinstance(source, str) or not source.strip() or len(source) > 2000:
            return
        loop = asyncio.get_running_loop()
        try:
            if is_priority_stop(source):
                if generation != self.generation or self.stopped.is_set():
                    return
                await self.device_tools.ready.wait()
                if generation != self.generation or self.stopped.is_set():
                    return
                result = await self.device_tools.call("robot_stop_motion", "{}")
                LOG.info("jev_trial_action kind=stop status=%s", result.get("status"))
                return
            intent, luna_ms = await asyncio.wait_for(
                loop.run_in_executor(self.worker, self.luna.evaluate, source), 7)
            if generation != self.generation or self.stopped.is_set():
                return
            if intent["kind"] != "action":
                LOG.info("jev_trial_intent route=%s latency_ms=%s", intent["kind"], luna_ms)
                return
            english = intent["english"]
            response, jev_ms = await asyncio.wait_for(
                loop.run_in_executor(self.worker, self.jev.evaluate, english), 6)
            if generation != self.generation or self.stopped.is_set():
                return
            choice, probability = validate_response(response)
            await self.device_tools.ready.wait()
            if generation != self.generation or self.stopped.is_set():
                return
            result = await self.dispatcher.dispatch(item_id, response,
                                                    source_text=source, english_text=english)
            LOG.info("jev_trial_decision status=%s route=%s reason=%s choice=%s probability=%.3f luna_ms=%s jev_ms=%s",
                     result.get("status"), result.get("route", "tool"),
                     result.get("reason", "none"), choice, probability, luna_ms, jev_ms)
        except Exception as exc:
            # No provider payload, transcript, audio or credentials in normal logs.
            LOG.info("jev_trial_skipped category=%s", type(exc).__name__)

    async def stream_audio(self):
        try:
            while not self.stopped.is_set():
                try:
                    pcm = await asyncio.wait_for(self.input.get(), 0.06)
                except asyncio.TimeoutError:
                    pcm = SILENCE
                await self.transcriber.send_pcm(pcm)
        except Exception as exc:
            LOG.info("jev_trial_audio_stopped category=%s", type(exc).__name__)
        finally:
            self.stopped.set()

    async def receive_transcripts(self):
        try:
            async for event in self.transcriber.events():
                kind = event.get("type")
                if kind == "input_audio_buffer.speech_started":
                    self.generation += 1
                elif kind in {"input_audio_buffer.speech_stopped", "input_audio_buffer.committed"}:
                    item_id = event.get("item_id")
                    if isinstance(item_id, str) and item_id and len(self.turn_generations) < 64:
                        self.turn_generations.setdefault(item_id, self.generation)
                elif kind == "conversation.item.input_audio_transcription.completed":
                    item_id = event.get("item_id")
                    generation = self.turn_generations.pop(item_id, None)
                    if (generation is None or generation != self.generation
                            or not isinstance(event.get("transcript"), str)):
                        LOG.info("jev_trial_transcript skipped=stale_or_unmatched")
                        continue
                    LOG.info("jev_trial_transcript status=completed chars=%s",
                             len(event["transcript"]))
                    task = asyncio.create_task(
                        self.process_transcript(generation, item_id, event["transcript"]))
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
                elif kind == "conversation.item.input_audio_transcription.failed":
                    self.turn_generations.pop(event.get("item_id"), None)
                    LOG.info("jev_trial_transcript status=failed")
        except Exception as exc:
            LOG.info("jev_trial_transcription_stopped category=%s", type(exc).__name__)
        finally:
            self.stopped.set()

    async def keepalive(self):
        while not self.stopped.is_set():
            await asyncio.sleep(30)
            if self.mcp_supported and not self.stopped.is_set():
                await self.send_json("mcp", payload={"jsonrpc": "2.0",
                                                     "method": "notifications/gosha/keepalive", "params": {}})

    async def run(self):
        self.codec = OpusCodec()
        runner = discovery = keepalive = audio = transcripts = None
        try:
            await self.hello()
            if not self.mcp_supported:
                raise RuntimeError("jev_trial_mcp_unavailable")
            if self.echo:
                self.echo.close()
                self.echo = None
            if self.reference_codec:
                self.reference_codec.close()
                self.reference_codec = None
            self.duplex = False
            await self.send_json("hello", version=self.version, transport="websocket",
                                 audio_params=AUDIO_PARAMS,
                                 features={"live_duplex": False, "aec": "off"})
            runner = asyncio.create_task(self.read_robot())
            discovery = asyncio.create_task(self.discover_tools())
            keepalive = asyncio.create_task(self.keepalive())
            await self.transcriber.start()
            # Audio captured during the model handshake is stale by definition.
            while not self.input.empty():
                self.input.get_nowait()
            audio = asyncio.create_task(self.stream_audio())
            transcripts = asyncio.create_task(self.receive_transcripts())
            LOG.info("jev_trial_route ready=true")
            await self.stopped.wait()
        finally:
            self.generation += 1
            await stop_tasks([task for task in (runner, discovery, keepalive, audio, transcripts) if task])
            await stop_tasks(self.tasks)
            await self.device_tools.stop_movement()
            self.device_tools.close()
            self.worker.shutdown(wait=False, cancel_futures=True)
            await self.transcriber.close()
            self.luna.close()
            self.jev.close()
            self.codec.close()
            LOG.info("jev_trial_voice_ended elapsed_seconds=%.1f", time.monotonic() - self.started_at)
