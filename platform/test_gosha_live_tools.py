"""Function execution contracts, with a simulated device and provider only."""
import asyncio
import json
import unittest

from gosha_live_tools import DeviceTools, LiveToolRunner, OPERATIONS


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent = []
        self.replies = asyncio.Queue()
        async def send(kind, **fields):
            self.sent.append((kind, fields))
            await self.replies.put(fields["payload"])
        self.device = DeviceTools(send, timeout=0.2)

    async def reply(self, result):
        request = await asyncio.wait_for(self.replies.get(), 1)
        self.device.receive({"jsonrpc": "2.0", "id": request["id"], "result": result})
        return request

    async def test_discovery_paginates_and_excludes_operator_and_legacy_tools(self):
        task = asyncio.create_task(self.device.discover())
        init = await self.reply({"protocolVersion": "2024-11-05"})
        self.assertEqual(init["method"], "initialize")
        notification = await self.replies.get()
        self.assertEqual(notification["method"], "notifications/initialized")
        first = await self.reply({"tools": [{"name": "self.get_device_status"}, {"name": "self.otto.action"}], "nextCursor": "page2"})
        second = await self.reply({"tools": [{"name": "self.audio_speaker.set_volume"}, {"name": "self.reboot"}, {"name": "self.otto.get_ip"}]})
        await task
        self.assertEqual(first["params"], {})
        self.assertEqual(second["params"], {"cursor": "page2"})
        self.assertEqual(set(self.device.available), {"robot_device_status", "robot_set_volume"})
        self.assertTrue(self.device.ready.is_set())
        self.assertTrue(all(tool["strict"] for tool in self.device.definitions()))

    async def test_discovery_timeout_does_not_offer_unconfirmed_functions(self):
        await self.device.discover()
        self.assertTrue(self.device.ready.is_set())
        self.assertEqual(self.device.definitions(), [])
        self.assertEqual(self.device.pending, {})

    async def test_invalid_or_unavailable_call_never_reaches_device(self):
        self.device.available = dict(OPERATIONS)
        for name, args in [("self.reboot", "{}"), ("robot_set_volume", "{\"volume\":true}"),
                           ("robot_set_volume", "{\"volume\":101}"), ("robot_set_volume", "{}"),
                           ("robot_set_volume", "{\"volume\":2,\"url\":\"bad\"}"),
                           ("robot_set_theme", "{\"theme\":\"bad\"}"), ("robot_battery", "[]")]:
            result = await self.device.call(name, args)
            self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.sent, [])

    async def test_reply_correlation_and_device_result_preserved(self):
        self.device.available = dict(OPERATIONS)
        call = asyncio.create_task(self.device.call("robot_set_theme", '{"theme":"dark"}'))
        request = await self.replies.get()
        self.device.receive({"jsonrpc": "2.0", "id": request["id"] + 1, "result": {}})
        self.device.receive({"jsonrpc": "2.0", "id": str(request["id"]), "result": {}})
        await asyncio.sleep(0)
        self.assertFalse(call.done())
        result = {"content": [{"type": "text", "text": "false"}], "isError": False}
        self.device.receive({"jsonrpc": "2.0", "id": request["id"], "result": result})
        self.assertEqual(await call, {"status": "confirmed", "device_result": result})
        self.assertEqual(self.device.pending, {})

    async def test_device_rejection_and_timeout_are_not_success_or_retried(self):
        self.device.available = dict(OPERATIONS)
        task = asyncio.create_task(self.device.call("robot_battery", "{}"))
        request = await self.replies.get()
        self.device.receive({"jsonrpc": "2.0", "id": request["id"], "error": {"message": "do not log this"}})
        self.assertEqual((await task)["status"], "failed")
        result = await self.device.call("robot_battery", "{}")
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["retry"])
        self.assertEqual(len(self.sent), 2)

    async def test_cancelled_inflight_call_records_unknown_and_cleans_rpc(self):
        self.device.available = dict(OPERATIONS)
        task = asyncio.create_task(self.device.call("robot_set_volume", '{"volume":20}'))
        await self.replies.get()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.device.journal[-1]["status"], "unknown")
        self.assertEqual(self.device.pending, {})


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.invocations = []
        self.active = True
        self.release = asyncio.Event()
        self.result_sent = asyncio.Event()
        self.device = DeviceTools(None)
        self.device.ready.set()
        async def call(name, arguments):
            self.invocations.append((name, arguments))
            await self.release.wait()
            return {"status": "confirmed"}
        async def send(kind, **fields):
            self.events.append((kind, fields))
            if kind == "response.create":
                self.result_sent.set()
        self.device.call = call
        self.send = send
        self.runner = LiveToolRunner(self, self.device, lambda: self.active)
        self.worker = asyncio.create_task(self.runner.run())

    async def asyncTearDown(self):
        self.worker.cancel()
        await asyncio.gather(self.worker, return_exceptions=True)

    def event(self, kind, **fields):
        self.runner.receive({"type": "response.event", "delegation_id": "delegation1", "event": {"type": kind, **fields}})

    def begin(self, response_id="response1", calls=("call1",)):
        self.event("response.created", response={"id": response_id, "output": []})
        for call_id in calls:
            self.event("response.output_item.done", item={"type": "function_call", "call_id": call_id,
                                                         "name": "robot_battery", "arguments": "{}"})

    async def test_collect_calls_and_wait_terminal_then_all_results_before_continue(self):
        self.begin(calls=("call1", "call2"))
        await asyncio.sleep(0)
        self.assertEqual(self.invocations, [])
        self.event("response.completed", response={"id": "response1", "output": []})
        await asyncio.sleep(0)
        self.assertEqual(len(self.invocations), 1)
        self.assertEqual(self.events, [])
        self.release.set()
        await asyncio.wait_for(self.result_sent.wait(), 1)
        self.assertEqual([kind for kind, _ in self.events], ["response.item.create", "response.item.create", "response.create"])
        self.assertEqual([fields["item"]["call_id"] for _, fields in self.events[:2]], ["call1", "call2"])
        self.assertEqual(self.events[-1][1], {})

    async def test_duplicate_items_and_terminal_event_execute_once(self):
        self.begin(calls=("call1", "call1"))
        for _ in range(2):
            self.event("response.completed", response={"id": "response1", "output": []})
        self.release.set()
        await asyncio.wait_for(self.result_sent.wait(), 1)
        await asyncio.sleep(0)
        self.assertEqual(len(self.invocations), 1)
        self.assertEqual(len(self.events), 2)

    async def test_cancelled_queue_does_not_execute_or_continue(self):
        self.begin()
        self.event("response.completed", response={"id": "response1", "output": []})
        self.active = False
        self.release.set()
        await asyncio.wait_for(self.worker, 1)
        self.assertEqual(self.invocations, [])
        self.assertEqual(self.events, [])

    async def test_abort_during_rpc_does_not_publish_stale_result(self):
        self.begin()
        self.event("response.completed", response={"id": "response1", "output": []})
        await asyncio.sleep(0)
        self.active = False
        self.release.set()
        await asyncio.wait_for(self.worker, 1)
        self.assertEqual(len(self.invocations), 1)
        self.assertEqual(self.events, [])

    async def test_invalid_lifecycle_disables_actions_without_raising(self):
        self.assertTrue(self.runner.receive({"event": {"type": "response.created", "response": {"id": "r"}}}))
        self.begin()
        self.event("response.completed", response={"id": "response1"})
        await asyncio.sleep(0)
        self.assertTrue(self.runner.disabled)
        self.assertFalse(self.worker.done())
        self.assertEqual(self.invocations, [])

    async def test_queue_overflow_disables_pending_actions_without_raising(self):
        for index in range(5):
            self.begin(response_id=f"r{index}", calls=(f"c{index}",))
            self.event("response.completed", response={"id": f"r{index}"})
        await asyncio.sleep(0)
        self.assertTrue(self.runner.disabled)
        self.assertFalse(self.worker.done())
        self.assertEqual(self.invocations, [])

    async def test_call_id_replayed_in_another_response_is_not_executed(self):
        self.begin()
        self.event("response.completed", response={"id": "response1"})
        self.release.set()
        await asyncio.wait_for(self.result_sent.wait(), 1)
        self.begin(response_id="response2")
        self.event("response.completed", response={"id": "response2"})
        await asyncio.sleep(0)
        self.assertEqual(len(self.invocations), 1)
        self.assertEqual([kind for kind, _ in self.events].count("response.create"), 1)
        self.assertTrue(self.runner.disabled)
        self.assertFalse(self.worker.done())

    async def test_disable_during_rpc_drops_old_result_but_keeps_worker_alive(self):
        self.begin()
        self.event("response.completed", response={"id": "response1"})
        await asyncio.sleep(0)
        self.runner.disable()
        self.release.set()
        await asyncio.sleep(0)
        self.assertEqual(len(self.invocations), 1)
        self.assertEqual(self.events, [])
        self.assertFalse(self.worker.done())


if __name__ == "__main__":
    unittest.main()
