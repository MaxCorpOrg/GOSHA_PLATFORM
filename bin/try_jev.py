#!/usr/bin/env python3
"""Проверить JEV на тексте и имитаторе через существующие команды Гоши."""
import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "platform"))
from gosha_jev_trial import JevTrialClient, MODEL, PROFILE, SimulatedRobot, TrialDispatcher, TrialError


async def run(args):
    if args.key_file:
        raw = Path(args.key_file).read_text(encoding="utf-8-sig").strip()
        tokens = set(re.findall(r"apikey_[A-Za-z0-9_-]+", raw))
        key = tokens.pop() if len(tokens) == 1 else raw
    else:
        key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key:
        raise TrialError("key_missing_use_key_file_or_TYPESAFE_API_KEY")
    cases = ([{"id": "manual", "text": args.text}] if args.text else
             json.loads((ROOT / "platform/fixtures/jev_trial_cases.json").read_text()))
    client = JevTrialClient(key, max_calls=len(cases))
    robot = SimulatedRobot()
    await robot.device.discover(retry_delays=())
    dispatcher, results = TrialDispatcher(robot.device), []
    try:
        for case in cases:
            before = len(robot.calls)
            response, elapsed = client.evaluate(case["text"])
            result = await dispatcher.dispatch(case["id"], response)
            choice = response["answers"]["skill"]["choice"]
            sent = robot.calls[before:]
            expected = case.get("expected")
            row = {"id": case["id"], "text": case["text"], "choice": choice,
                   "probability": response["answers"]["skill"]["probabilities"][choice],
                   "latency_ms": elapsed, "result": result, "mcp_calls": sent,
                   "usage": response["usage"]}
            if expected is not None:
                expected_calls = 1 if case.get("dispatch", False) else 0
                row["correct"] = choice == expected and len(sent) == expected_calls
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        client.close()
        robot.device.close()
    timings = sorted(row["latency_ms"] for row in results)
    summary = {"model": MODEL, "profile": PROFILE, "simulator_only": True,
               "cases": len(results), "correct": sum(r.get("correct", False) for r in results),
               "http_requests": client.calls, "input_tokens": client.input_tokens,
               "output_tokens": client.output_tokens, "median_ms": statistics.median(timings),
               "p95_ms": timings[math.ceil(0.95 * len(timings)) - 1],
               "note": "Время HTTPS-запроса; распознавание, голос и физическое движение не измерены."}
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({"summary": summary, "results": results},
                                         ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if any(r.get("correct") is False for r in results) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", help="Файл ключа TypeSafe; иначе TYPESAFE_API_KEY")
    parser.add_argument("--text", help="Одна завершённая реплика вместо набора примеров")
    parser.add_argument("--output", help="Сохранить результат в JSON")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except (TrialError, OSError) as exc:
        print(str(exc) if isinstance(exc, TrialError) else "local_file_error", file=sys.stderr)
        raise SystemExit(2)
