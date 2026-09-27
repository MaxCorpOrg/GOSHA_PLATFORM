#!/usr/bin/env python3
"""Bounded paid model-migration probe. All robot results are synthetic; no robot I/O."""
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform"))
from gosha_live_audio import SILENCE
from gosha_live_protocol import LiveSession, session_config
from gosha_live_tools import DeviceTools, LiveToolRunner, MOVEMENT_OPERATIONS, OPERATIONS


async def probe(config, key):
    result = {"ok": False, "robot_contacted": False, "cases": [], "finalized": False}
    result["reasoning_model"] = config["delegation"]["responses"]["model"]
    device = DeviceTools(None)
    device.available = {**MOVEMENT_OPERATIONS, "robot_battery": OPERATIONS["robot_battery"]}
    device.ready.set()
    calls, texts, models = [], [], []
    current = None
    done = asyncio.Event()

    async def synthetic_call(name, arguments):
        args = json.loads(arguments)
        calls.append({"name": name, "arguments": args})
        if name == "robot_list_movements" and args == {}:
            payload = {"status": "confirmed", "movements": [
                {"motion_id": "builtin/walk_forward", "name": "Шаги вперёд"},
                {"motion_id": "builtin/walk_backward", "name": "Шаги назад"},
                {"motion_id": "builtin/hand_wave", "name": "Помахать правой рукой"},
            ]}
        elif name == "robot_play_movement" and args in [
            {"motion_id": "builtin/walk_forward"}, {"motion_id": "builtin/walk_backward"},
        ]:
            payload = {"status": "finished", "motion_id": args["motion_id"], "completion_confirmed": True}
        elif name == "robot_stop_motion" and args == {}:
            payload = {"status": "stopped"}
        elif name == "robot_motion_status" and args == {}:
            payload = {"status": "idle"}
        elif name == "robot_battery" and args == {}:
            return {"status": "confirmed", "device_result": {"content": [
                {"type": "text", "text": '{"level":42,"charging":false}'}]}}
        else:
            return {"status": "rejected", "reason": "unexpected_probe_operation"}
        return {"status": payload["status"], "device_result": payload}

    device.call = synthetic_call
    config["delegation"]["responses"].update(tools=device.definitions(), tool_choice="auto")
    session = LiveSession(config, key)
    runner = LiveToolRunner(session, device, lambda: True)
    tasks = []

    async def receive():
        async for event in session.events():
            if event.get("type") != "response.event":
                continue
            if runner.receive(event):
                raise RuntimeError("tool_lifecycle_rejected")
            inner = event.get("event", {})
            if inner.get("type") == "response.output_text.delta" and current is not None:
                texts.append(inner.get("delta", ""))
            if inner.get("type") == "response.completed" and current is not None:
                response = inner.get("response", {})
                models.append(response.get("model", ""))
                if response.get("status") != "completed":
                    raise RuntimeError("response_incomplete")
                # Live delivers function items separately; completed.output may be empty.
                response_key = (event.get("delegation_id"), response.get("id"))
                if not runner.responses.get(response_key):
                    done.set()

    async def silence():
        while True:
            await session.send_audio(SILENCE)
            await asyncio.sleep(0.06)

    cases = [
        ("forward", "Сделай шаги вперёд.", "builtin/walk_forward"),
        ("backward", "Теперь сделай шаги назад.", "builtin/walk_backward"),
        ("stop", "Немедленно останови движение робота.", None),
        ("negative", "Не двигайся. Просто скажи, умеешь ли ты ходить вперёд.", None),
        ("battery", "Какой сейчас заряд твоего аккумулятора? Назови цифрами.", None),
    ]
    try:
        await session.start()
        tasks = [asyncio.create_task(receive()), asyncio.create_task(silence()),
                 asyncio.create_task(runner.run())]
        for name, prompt, movement in cases:
            current = name
            calls.clear(); texts.clear(); models.clear(); done.clear()
            await session.send("response.item.create", item={"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": prompt}]})
            await session.send("response.create")
            waiter = asyncio.create_task(done.wait())
            try:
                finished, _ = await asyncio.wait([waiter, *tasks], timeout=25,
                                                return_when=asyncio.FIRST_COMPLETED)
                for task in finished:
                    task.result()
            finally:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            names = [call["name"] for call in calls]
            played = [call["arguments"] for call in calls if call["name"] == "robot_play_movement"]
            selected_model = result["reasoning_model"]
            ok = done.is_set() and bool(models) and all(
                m == selected_model or m.startswith(selected_model + "-") for m in models)
            if movement:
                ok &= played == [{"motion_id": movement}]
                # First motion must be chosen from a freshly retrieved catalog.
                if name == "forward":
                    ok &= bool(names) and names[0] == "robot_list_movements"
            elif name == "stop":
                ok &= names == ["robot_stop_motion"]
            elif name == "negative":
                ok &= not played and all(n in {"robot_list_movements", "robot_motion_status", "robot_stop_motion"} for n in names)
            elif name == "battery":
                ok &= names == ["robot_battery"] and "42" in "".join(texts)
            result["cases"].append({"name": name, "ok": bool(ok), "calls": list(calls),
                                    "completed": done.is_set()})
            if not ok:
                break
    except Exception as exc:
        result["error_category"] = type(exc).__name__
    finally:
        for task in tasks[1:]:
            task.cancel()
        await asyncio.gather(*tasks[1:], return_exceptions=True)
        if session.ws:
            await session.close()
        if tasks:
            tasks[0].cancel()
            await asyncio.gather(tasks[0], return_exceptions=True)
        result["finalized"] = session.finalized.is_set()
        if isinstance(session.usage, dict):
            result["voice_seconds"] = session.usage.get("seconds")
    result["ok"] = len(result["cases"]) == len(cases) and all(c["ok"] for c in result["cases"]) and result["finalized"] and not runner.disabled
    return result


if __name__ == "__main__":
    config = session_config({"live_voice": "meridian", "live_reasoning_effort": "low"},
                            {"base_url": "https://api.openai.com/v1", "model": "gpt-5.6-luna"})
    result = asyncio.run(probe(config, os.environ.get("OPENAI_API_KEY", "")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
