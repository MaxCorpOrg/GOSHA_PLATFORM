#!/usr/bin/env python3
"""Bounded paid Live function-call probe with a synthetic battery; no robot I/O."""
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform"))
from gosha_live_audio import SILENCE
from gosha_live_protocol import LiveSession, session_config
from gosha_live_tools import DeviceTools, LiveToolRunner, OPERATIONS


async def probe(key):
    result = {"ok": False, "robot_contacted": False, "function_calls": 0,
              "backend_completed": 0, "result_used": False, "finalized": False}
    device = DeviceTools(None)
    device.available = {"robot_battery": OPERATIONS["robot_battery"]}
    device.ready.set()
    async def synthetic_call(name, arguments):
        if name != "robot_battery" or json.loads(arguments) != {}:
            raise ValueError("unexpected_probe_operation")
        result["function_calls"] += 1
        return {"status": "confirmed", "device_result": {"content": [{"type": "text", "text": '{"level":42,"charging":false}'}]}}
    device.call = synthetic_call
    config = session_config({"live_voice": "meridian"}, {"base_url": "https://api.openai.com/v1", "model": "gpt-5.5"})
    config["delegation"]["responses"].update(tools=device.definitions(), tool_choice="auto")
    session = LiveSession(config, key)
    runner = LiveToolRunner(session, device, lambda: True)
    done = asyncio.Event()
    reader = sender = worker = None
    async def receive():
        async for event in session.events():
            if event.get("type") == "response.event":
                if runner.receive(event):
                    result["lifecycle_rejected"] = True
                    await runner.report_disabled()
                inner = event.get("event", {})
                if inner.get("type") == "response.output_text.delta" and result["function_calls"]:
                    result["result_used"] |= "42" in inner.get("delta", "")
                if inner.get("type") == "response.completed":
                    result["backend_completed"] += 1
                    if result["function_calls"] == 1 and result["backend_completed"] >= 2 and result["result_used"]:
                        done.set()
    async def silence():
        while True:
            await session.send_audio(SILENCE)
            await asyncio.sleep(0.06)
    try:
        await session.start()
        reader = asyncio.create_task(receive())
        sender = asyncio.create_task(silence())
        worker = asyncio.create_task(runner.run())
        await session.send("response.item.create", item={"type": "message", "role": "user", "content": [{
            "type": "input_text", "text": "Это проверка на имитаторе. Вызови один раз robot_battery и сообщи полученный заряд цифрами. Не угадывай заряд.",
        }]})
        await session.send("response.create")
        waiter = asyncio.create_task(done.wait())
        try:
            finished, _ = await asyncio.wait([waiter, reader, sender, worker], timeout=25, return_when=asyncio.FIRST_COMPLETED)
            for task in finished:
                task.result()
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
    except Exception as exc:
        result["error_category"] = type(exc).__name__
    finally:
        for task in (sender, worker):
            if task:
                task.cancel()
        await asyncio.gather(*(task for task in (sender, worker) if task), return_exceptions=True)
        if session.ws:
            await session.close()
        if reader:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        result["finalized"] = session.finalized.is_set()
    result["ok"] = done.is_set() and result["finalized"] and not runner.disabled
    return result


if __name__ == "__main__":
    result = asyncio.run(probe(os.environ.get("OPENAI_API_KEY", "")))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
