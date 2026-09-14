#!/usr/bin/env python3
"""Bounded paid API probe: models, delegated reasoning, output audio and graceful close."""
import asyncio
import base64
import contextlib
import json
import os
from pathlib import Path
import sys
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform"))
from gosha_live_audio import SILENCE, audible
from gosha_live_protocol import LiveSession, session_config


def check_models(key, reasoning_model):
    for model in ("gpt-live-1", reasoning_model):
        request = urllib.request.Request("https://api.openai.com/v1/models/" + model,
                                         headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(request, timeout=12) as response:
            if json.load(response).get("id") != model:
                raise RuntimeError("model_probe_mismatch")


async def probe(config, key, *, seconds=25):
    result = {"ok": False, "models_available": False, "session_started": False,
              "voice_instructions_acknowledged": False,
              "reasoning_completed": False, "answer_correct": False, "audio_received": False, "audio_audible": False,
              "finalized": False, "robot_contacted": False}
    if not key:
        result["error"] = "openai_api_key_missing"
        return result
    reasoning_model = config["delegation"]["responses"]["model"]
    result["reasoning_model"] = reasoning_model
    session = LiveSession(config, key)
    reader = sender = None
    try:
        await asyncio.to_thread(check_models, key, reasoning_model)
        result["models_available"] = True
        await session.start()
        result["session_started"] = True
        done = asyncio.Event()
        instructions_ack = asyncio.Event()
        instructions_id = uuid.uuid4().hex
        answer = ""

        async def receive():
            nonlocal answer
            async for event in session.events():
                if event.get("type") == "session.instructions.appended" and event.get("client_event_id") == instructions_id:
                    result["voice_instructions_acknowledged"] = True
                    instructions_ack.set()
                elif event.get("type") == "session.output_audio.delta":
                    pcm = base64.b64decode(event["delta"], validate=True)
                    result["audio_received"] |= bool(pcm)
                    result["audio_audible"] |= audible(pcm)
                elif event.get("type") == "response.event":
                    inner = event.get("event", {})
                    if inner.get("type") == "response.output_text.delta":
                        answer += inner.get("delta", "")
                    if inner.get("type") == "response.completed":
                        response = inner.get("response", {})
                        model = response.get("model", "")
                        result["reasoning_completed"] = response.get("status") == "completed" and (
                            model == reasoning_model or model.startswith(reasoning_model + "-"))
                        result["answer_correct"] = "391" in answer
                if result["reasoning_completed"] and result["audio_audible"]:
                    done.set()

        async def send_silence():
            while True:
                tick = asyncio.get_running_loop().time()
                await session.send_audio(SILENCE)
                await asyncio.sleep(max(0, tick + 0.06 - asyncio.get_running_loop().time()))

        reader = asyncio.create_task(receive())
        sender = asyncio.create_task(send_silence())
        # Voice output is a separate check from response.create (which only runs the backend).
        await session.send("session.instructions.append", event_id=instructions_id, delegation_id=None,
                           content="Сейчас сразу скажи по-русски: «Проверка голоса Гоши». Не жди речи пользователя. Затем слушай.")
        await asyncio.wait_for(instructions_ack.wait(), 8)
        await session.send("session.commentary.append", delegation_id=None,
                           content="Начни сейчас, следуя только что переданной инструкции приветствия.")
        await session.send("response.item.create", item={"type": "message", "role": "user", "content": [{
            "type": "input_text", "text": "Вычисли 17 умножить на 23 и сообщи результат одной короткой фразой по-русски.",
        }]})
        await session.send("response.create")
        waiter = asyncio.create_task(done.wait())
        try:
            completed, _ = await asyncio.wait([waiter, reader, sender], timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
            for task in completed:
                task.result()
            if not done.is_set():
                result["error"] = "reasoning_or_audio_unconfirmed"
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
    except Exception as exc:
        result["error_category"] = type(exc).__name__
    finally:
        if sender:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        if session.ws:
            await session.close()
        if reader:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        result["finalized"] = session.finalized.is_set()
        if isinstance(session.usage, dict):
            result["voice_seconds"] = session.usage.get("seconds")
    result["ok"] = all(result[key] for key in ("models_available", "session_started", "voice_instructions_acknowledged", "reasoning_completed", "answer_correct", "audio_audible", "finalized"))
    return result


if __name__ == "__main__":
    config = session_config({}, {"base_url": "https://api.openai.com/v1", "model": "gpt-5.5"})
    result = asyncio.run(probe(config, os.environ.get("OPENAI_API_KEY", "")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
