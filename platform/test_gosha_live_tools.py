"""Function execution contracts, with a simulated device and provider only."""
import asyncio
import json
import time
import unittest

from gosha_live_tools import DeviceTools, LiveToolRunner, OPERATIONS, MOVEMENT_OPERATIONS


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

    async def test_runtime_catalog_replaces_editor_tools(self):
        task = asyncio.create_task(self.device.discover())
        await self.reply({"protocolVersion": "2024-11-05"})
        await self.replies.get()
        await self.reply({"tools": [{"name": spec["native"]} for spec in MOVEMENT_OPERATIONS.values()]
                                   + [{"name": "self.otto.action"}, {"name": "self.otto.servo_sequences"}]})
        await task
        self.assertEqual(set(self.device.available), set(MOVEMENT_OPERATIONS))
        self.assertNotIn("robot_enable_right_arm", self.device.available)
        self.assertNotIn("robot_wave_right_hand", self.device.available)

    async def test_named_play_sends_one_mcp_call_without_servo_commands(self):
        self.device.available = dict(MOVEMENT_OPERATIONS)
        call = asyncio.create_task(self.device.call("robot_play_movement", '{"motion_id":"builtin/hand_wave"}'))
        request = await self.replies.get()
        self.assertEqual(request["method"], "tools/call")
        self.assertEqual(request["params"]["name"], "self.motion.play")
        args = request["params"]["arguments"]
        self.assertEqual(set(args), {"motion_id", "request_id"})
        self.assertEqual(len(args["request_id"]), 32)
        self.device.receive({"jsonrpc": "2.0", "id": request["id"], "result": {
            "content": [{"type": "text", "text": json.dumps({"status": "in_progress", "completion_confirmed": False})}]}})
        result = await call
        self.assertEqual(result["status"], "in_progress")
        self.assertFalse(result["device_result"]["completion_confirmed"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], "mcp")

    async def test_model_cannot_supply_request_id_or_raw_positions(self):
        self.device.available = dict(MOVEMENT_OPERATIONS)
        for args in [{"motion_id": "builtin/hand_wave", "request_id": "old"},
                     {"motion_id": "builtin/hand_wave", "angle": 70}, {"motion_id": ""},
                     {"motion_id": "x" * 129}]:
            self.assertEqual((await self.device.call("robot_play_movement", json.dumps(args)))["status"], "rejected")
        self.assertEqual(self.sent, [])

    async def test_catalog_read_does_not_prepare_or_start_drives(self):
        self.device.available = dict(MOVEMENT_OPERATIONS)
        call = asyncio.create_task(self.device.call("robot_list_movements", '{}'))
        request = await self.reply({"content": [{"type": "text", "text": '{"status":"confirmed","movements":[]}'}]})
        self.assertEqual(request["params"], {"name": "self.motion.list", "arguments": {}})
        self.assertEqual((await call)["status"], "confirmed")
        self.assertFalse(self.device.movement_may_be_running)
        self.assertEqual(len(self.sent), 1)

    async def test_device_movement_failure_is_not_a_success(self):
        self.device.available = dict(MOVEMENT_OPERATIONS)
        call = asyncio.create_task(self.device.call("robot_play_movement", '{"motion_id":"builtin/hand_wave"}'))
        await self.reply({"content": [{"type": "text", "text": '{"status":"rejected","reason":"editor_busy"}'}]})
        result = await call
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["device_result"]["reason"], "editor_busy")
        self.assertEqual(self.device.counts["failed"], 1)
        self.assertEqual(self.device.counts["confirmed"], 0)

    async def test_local_movement_extends_idle_wait_with_a_finite_deadline(self):
        self.device.available = dict(MOVEMENT_OPERATIONS)
        call = asyncio.create_task(self.device.call("robot_play_movement", '{"motion_id":"stored/long"}'))
        await self.reply({"content": [{"type": "text", "text": '{"status":"in_progress","duration_ms":120000}'}]})
        await call
        self.assertTrue(self.device.movement_keeps_session_alive(time.monotonic() + 100))
        self.assertFalse(self.device.movement_keeps_session_alive(time.monotonic() + 151))
        deadline = self.device.movement_busy_until
        status = asyncio.create_task(self.device.call("robot_motion_status", '{}'))
        await self.reply({"content": [{"type": "text", "text": '{"status":"in_progress","duration_ms":120000}'}]})
        await status
        self.assertEqual(self.device.movement_busy_until, deadline)
        status = asyncio.create_task(self.device.call("robot_motion_status", '{}'))
        await self.reply({"content": [{"type": "text", "text": '{"status":"finished"}'}]})
        await status
        self.assertFalse(self.device.movement_keeps_session_alive(time.monotonic()))

    async def test_discovery_timeout_does_not_offer_unconfirmed_functions(self):
        await self.device.discover(retry_delays=(0, 0))
        self.assertTrue(self.device.ready.is_set())
        self.assertEqual(self.device.definitions(), [])
        self.assertEqual(self.device.pending, {})
        self.assertEqual([fields["payload"]["method"] for _, fields in self.sent], ["initialize"] * 3)

    async def test_lost_initialize_recovers_and_late_reply_cannot_complete_new_request(self):
        task = asyncio.create_task(self.device.discover(retry_delays=(0, 0)))
        lost = await self.replies.get()
        retry = await asyncio.wait_for(self.replies.get(), 1)
        self.assertEqual(retry["method"], "initialize")
        self.assertNotEqual(lost["id"], retry["id"])
        self.device.receive({"jsonrpc": "2.0", "id": lost["id"], "result": {}})
        await asyncio.sleep(0)
        self.assertTrue(self.replies.empty())
        self.assertFalse(self.device.ready.is_set())
        self.device.receive({"jsonrpc": "2.0", "id": retry["id"], "result": {}})
        self.assertEqual((await self.replies.get())["method"], "notifications/initialized")
        await self.reply({"tools": [{"name": spec["native"]} for spec in MOVEMENT_OPERATIONS.values()]})
        await task
        self.assertEqual(set(self.device.available), set(MOVEMENT_OPERATIONS))
        self.assertTrue(self.device.ready.is_set())
        self.assertEqual(self.device.pending, {})
        self.assertNotIn("tools/call", [fields["payload"]["method"] for _, fields in self.sent])

    async def test_lost_listing_page_restarts_catalog_without_publishing_partial_tools(self):
        task = asyncio.create_task(self.device.discover(retry_delays=(0,)))
        await self.reply({})
        await self.replies.get()
        await self.reply({"tools": [{"name": "self.battery.get_level"}], "nextCursor": "page2"})
        lost = await self.replies.get()
        self.assertEqual(lost["params"], {"cursor": "page2"})
        self.assertEqual(self.device.definitions(), [])
        retry = await self.reply({})
        self.assertEqual(retry["method"], "initialize")
        await self.replies.get()
        listing = await self.reply({"tools": [{"name": "self.motion.list"}]})
        self.assertEqual(listing["params"], {})
        await task
        self.assertEqual(set(self.device.available), {"robot_list_movements"})
        self.assertEqual(self.device.counts["discovered"], 1)
        self.assertEqual(self.device.pending, {})

    async def test_cancelled_discovery_does_not_retry_after_connection_closes(self):
        task = asyncio.create_task(self.device.discover(retry_delays=(1, 2)))
        await self.replies.get()
        async with asyncio.timeout(1):
            while self.device.pending:
                await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.device.pending, {})

    async def test_lost_movement_ack_is_never_retried(self):
        self.device.available = dict(MOVEMENT_OPERATIONS)
        result = await self.device.call("robot_play_movement", '{"motion_id":"builtin/hand_wave"}')
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["retry"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1]["payload"]["params"]["name"], "self.motion.play")

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
