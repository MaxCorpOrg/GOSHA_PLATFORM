#!/usr/bin/env python3
"""Bounded paid API probe of idle farewell with an in-memory robot transport."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform"))
from gosha_live_audio import AUDIO_PARAMS, OpusCodec, audible
from gosha_live_protocol import LiveSession, session_config
from gosha_voice_router import RobotLiveBridge


class SyntheticRobot:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.codec = OpusCodec()
        self.audible_frames = 0
        self.states = []
        self.incoming.put_nowait(json.dumps({"type": "hello", "version": 1,
                                            "transport": "websocket", "audio_params": AUDIO_PARAMS}))
        self.incoming.put_nowait(json.dumps({"type": "listen", "state": "start", "mode": "auto"}))

    async def recv(self):
        return await self.incoming.get()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.incoming.get()

    async def send(self, value):
        if isinstance(value, bytes):
            self.audible_frames += int(audible(self.codec.decode(value)))
        else:
            event = json.loads(value)
            if event.get("type") == "tts":
                self.states.append(event.get("state"))


async def probe(config, key):
    robot = SyntheticRobot()
    sessions = []
    def create_session(config, key):
        session = LiveSession(config, key)
        sessions.append(session)
        return session
    # Only the test instance accelerates waiting; production remains 120 seconds.
    bridge = RobotLiveBridge(robot, config, key, session_factory=create_session, idle_seconds=0.3)
    result = {"ok": False, "robot_contacted": False, "idle_seconds_in_probe": 0.3}
    started = time.monotonic()
    try:
        await asyncio.wait_for(bridge.run(), 40)
        text = " ".join(item["text"] for item in bridge.history if item["role"] == "assistant").lower()
        result.update(reason=bridge.stop_reason, audible_frames=robot.audible_frames,
                      playback_states=robot.states, mentions_wake_name="гоша" in text,
                      mentions_voice_mode="голос" in text,
                      finalized=bool(sessions) and all(s.finalized.is_set() for s in sessions),
                      tool_calls=bridge.device_tools.counts["requested"],
                      elapsed_seconds=round(time.monotonic()-started, 2))
        result["ok"] = (result["reason"] == "idle_farewell_finished"
                        and robot.audible_frames >= 10 and robot.states[-1:] == ["stop"]
                        and result["mentions_wake_name"] and result["mentions_voice_mode"]
                        and result["finalized"] and result["tool_calls"] == 0)
        result["voice_seconds"] = [s.usage.get("seconds") for s in sessions if isinstance(s.usage, dict)]
    except Exception as exc:
        result["error_category"] = type(exc).__name__
    finally:
        robot.codec.close()
    return result


if __name__ == "__main__":
    config = session_config({"live_voice": "meridian", "live_reasoning_effort": "low"},
                            {"base_url": "https://api.openai.com/v1", "model": "gpt-5.6-luna"})
    result = asyncio.run(probe(config, os.environ.get("OPENAI_API_KEY", "")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
