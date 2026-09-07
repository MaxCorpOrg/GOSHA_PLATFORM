from __future__ import annotations

import json
import queue
import time
import unittest
from types import SimpleNamespace

from aiohttp import ClientSession, WSMsgType
from aiohttp.test_utils import TestServer
import serial

from bridge.usb_bridge import (
    LIVE_PROTOCOL,
    WIRE_PREFIX,
    BridgeError,
    create_app,
    list_allowed_ports,
    open_serial_port,
    _validate_request,
)


ORIGIN = "http://127.0.0.1:5176"


def port_info(device: str = "/dev/ttyACM0", *, vid: int = 0x303A, pid: int = 0x1001):
    return SimpleNamespace(
        device=device,
        vid=vid,
        pid=pid,
        product="ESP32-S3 Serial JTAG",
        manufacturer="Espressif",
        description="USB JTAG serial debug unit",
        serial_number="",
        location="1-1",
        hwid="USB VID:PID=303A:1001",
    )


def capabilities(request_id: str) -> dict:
    return {
        "protocol": LIVE_PROTOCOL,
        "op": "capabilities",
        "request_id": request_id,
        "profile_id": "gosha-preview-v1",
        "motion_allowed": True,
        "calibrated": True,
        "calibration_id": "a" * 64,
        "watchdog_ms": 300,
        "max_rate_hz": 20,
        "stop_mode": "hold_setpoint",
        "auth_required": True,
        "joint_limits": [
            {"id": "leg_negative_x", "min": -10, "max": 10, "max_speed_dps": 15}
        ],
        "commanded_pose": {
            "arm_negative_x": 0,
            "arm_positive_x": 0,
            "leg_negative_x": 0,
            "leg_positive_x": 0,
            "foot_negative_x": 0,
            "foot_positive_x": 0,
        },
        "feedback": {"measured_position": False, "imu": False},
    }


class FakeSerial:
    def __init__(
        self,
        *,
        auto_reply: bool = True,
        split_reply: bool = False,
        never_finishes_response: bool = False,
        write_delay: float = 0,
        read_delay: float = 0,
        write_result: int | None = None,
    ) -> None:
        self.events = []
        self.writes = []
        self.incoming: queue.Queue[bytes | None] = queue.Queue()
        self.closed = False
        self.flush_called = False
        self.is_open = False
        self.auto_reply = auto_reply
        self.split_reply = split_reply
        self.never_finishes_response = never_finishes_response
        self.write_delay = write_delay
        self.read_delay = read_delay
        self.write_result = write_result

    def __setattr__(self, name, value):
        if name in {"dtr", "rts"} and "events" in self.__dict__:
            self.events.append((name, value, self.__dict__.get("is_open", False)))
        object.__setattr__(self, name, value)

    def open(self):
        self.events.append(
            (
                "open",
                getattr(self, "baudrate", None),
                getattr(self, "exclusive", None),
                getattr(self, "dtr", None),
                getattr(self, "rts", None),
            )
        )
        self.is_open = True

    def write(self, raw: bytes):
        if self.write_delay:
            time.sleep(self.write_delay)
        self.writes.append(raw)
        if self.auto_reply and raw.startswith(WIRE_PREFIX):
            request = json.loads(raw[len(WIRE_PREFIX) :].strip())
            if request.get("op") == "hello":
                if self.never_finishes_response:
                    self.incoming.put(WIRE_PREFIX + b'{"protocol"')
                    for _ in range(50):
                        self.incoming.put(b" ")
                    return len(raw) if self.write_result is None else self.write_result
                self.incoming.put(b"boot log before live\n")
                response = json.dumps(
                    capabilities(request["request_id"]),
                    separators=(",", ":"),
                ).encode()
                live_line = WIRE_PREFIX + response + b"\n"
                if self.split_reply:
                    split_at = len(WIRE_PREFIX) + 24
                    self.incoming.put(live_line[:split_at])
                    self.incoming.put(None)
                    self.incoming.put(live_line[split_at:])
                else:
                    self.incoming.put(live_line)
        return len(raw) if self.write_result is None else self.write_result

    def flush(self):
        self.flush_called = True
        raise AssertionError("serial.flush must not be called")

    def read_until(self, expected=b"\n", size=None):
        if self.read_delay:
            time.sleep(self.read_delay)
        try:
            item = self.incoming.get(timeout=getattr(self, "timeout", 0.05))
            return b"" if item is None else item
        except queue.Empty:
            return b""

    def reset_input_buffer(self):
        return None

    def close(self):
        self.closed = True
        self.is_open = False


class UsbBridgeUnitTests(unittest.TestCase):
    def test_enumeration_allows_only_usb_303a_1001_and_hides_paths(self):
        ports = list_allowed_ports(
            lambda: [
                port_info("/dev/ttyACM0"),
                port_info("/dev/ttyUSB9", vid=0x1234),
                port_info("/tmp/not-a-device", pid=0x9999),
            ]
        )
        self.assertEqual(len(ports), 1)
        public = ports[0].public()
        self.assertRegex(public["port_id"], r"^[a-f0-9]{24}$")
        self.assertEqual(public["vid"], "303a")
        self.assertEqual(public["pid"], "1001")
        self.assertNotIn("device", public)
        self.assertNotIn("path", public)
        self.assertNotIn("/", public["label"])

    def test_serial_open_deasserts_lines_before_open_and_uses_exclusive_115200(self):
        fake = FakeSerial(auto_reply=False)
        port = list_allowed_ports(lambda: [port_info()])[0]
        opened = open_serial_port(port, lambda: fake)
        self.assertIs(opened, fake)
        self.assertIn(("dtr", False, False), fake.events)
        self.assertIn(("rts", False, False), fake.events)
        self.assertIn(("open", 115200, True, False, False), fake.events)
        self.assertEqual(fake.write_timeout, 0.1)

    def test_serial_safety_failure_refuses_open_before_dtr_or_rts_toggle(self):
        class UnsafeSerial(FakeSerial):
            def __setattr__(self, name, value):
                if name == "dtr" and value is False:
                    object.__setattr__(self, name, True)
                    return
                super().__setattr__(name, value)

        unsafe = UnsafeSerial(auto_reply=False)
        port = list_allowed_ports(lambda: [port_info()])[0]
        with self.assertRaises(BridgeError) as caught:
            open_serial_port(port, lambda: unsafe)
        self.assertEqual(caught.exception.code, "usb_serial_safety")
        self.assertFalse(unsafe.is_open)
        self.assertTrue(unsafe.closed)

    def test_request_size_is_bounded_before_wire_encoding(self):
        with self.assertRaises(BridgeError) as caught:
            _validate_request(
                json.dumps(
                    {
                        "protocol": LIVE_PROTOCOL,
                        "op": "hello",
                        "request_id": "r1",
                        "padding": "x" * 5000,
                    }
                )
            )
        self.assertEqual(caught.exception.code, "usb_bridge_request_too_large")


class UsbBridgeNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def start_bridge(
        self,
        *,
        infos=None,
        serial_factory=None,
        hello_timeout=0.2,
    ):
        if infos is None:
            infos = [port_info()]
        if serial_factory is None:
            serial_factory = FakeSerial
        app = create_app(
            allowed_origin=ORIGIN,
            comports=lambda: infos,
            serial_factory=serial_factory,
            hello_timeout=hello_timeout,
        )
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        return server

    async def ws_connect(self, server: TestServer, port_id: str):
        session = ClientSession(headers={"Origin": ORIGIN})
        self.addAsyncCleanup(session.close)
        url = str(server.make_url(f"/live?port_id={port_id}")).replace(
            "http://",
            "ws://",
            1,
        )
        ws = await session.ws_connect(url)
        self.addAsyncCleanup(ws.close)
        return ws

    async def test_websocket_wraps_split_live_json_and_ignores_boot_logs(self):
        fake = FakeSerial(split_reply=True)
        info = port_info()
        port_id = list_allowed_ports(lambda: [info])[0].port_id
        server = await self.start_bridge(infos=[info], serial_factory=lambda: fake)
        ws = await self.ws_connect(server, port_id)
        await ws.send_str(
            json.dumps(
                {"protocol": LIVE_PROTOCOL, "op": "hello", "request_id": "r1"}
            )
        )
        message = await ws.receive(timeout=1)
        self.assertEqual(message.type, WSMsgType.TEXT)
        data = json.loads(message.data)
        self.assertEqual(data["op"], "capabilities")
        self.assertEqual(data["request_id"], "r1")
        self.assertEqual(len(fake.writes), 1)
        self.assertTrue(fake.writes[0].startswith(WIRE_PREFIX))
        self.assertIn(b'"op":"hello"', fake.writes[0])
        self.assertFalse(fake.flush_called)
        await ws.close()
        self.assertTrue(fake.closed)

    async def test_absent_device_reports_distinct_error_without_opening_serial(self):
        opened = []

        def factory():
            opened.append(True)
            return FakeSerial()

        server = await self.start_bridge(infos=[], serial_factory=factory)
        ws = await self.ws_connect(server, "a" * 24)
        await ws.send_str(
            json.dumps(
                {"protocol": LIVE_PROTOCOL, "op": "hello", "request_id": "r-absent"}
            )
        )
        message = await ws.receive(timeout=1)
        data = json.loads(message.data)
        self.assertEqual(data["code"], "usb_device_absent")
        self.assertEqual(data["request_id"], "r-absent")
        self.assertEqual(opened, [])

    async def test_busy_port_and_firmware_nohello_are_distinct_errors(self):
        info = port_info()
        port_id = list_allowed_ports(lambda: [info])[0].port_id

        def busy_factory():
            raise serial.SerialException("busy")

        busy_server = await self.start_bridge(infos=[info], serial_factory=busy_factory)
        busy_ws = await self.ws_connect(busy_server, port_id)
        await busy_ws.send_str(
            json.dumps(
                {"protocol": LIVE_PROTOCOL, "op": "hello", "request_id": "r-busy"}
            )
        )
        busy_message = await busy_ws.receive(timeout=1)
        self.assertEqual(json.loads(busy_message.data)["code"], "usb_port_busy")

        silent = FakeSerial(auto_reply=False)
        nohello_server = await self.start_bridge(
            infos=[info],
            serial_factory=lambda: silent,
            hello_timeout=0.05,
        )
        nohello_ws = await self.ws_connect(nohello_server, port_id)
        await nohello_ws.send_str(
            json.dumps(
                {"protocol": LIVE_PROTOCOL, "op": "hello", "request_id": "r-nohello"}
            )
        )
        nohello_message = await nohello_ws.receive(timeout=1)
        nohello = json.loads(nohello_message.data)
        self.assertEqual(nohello["code"], "firmware_nohello")
        self.assertEqual(nohello["request_id"], "r-nohello")
        self.assertEqual(len(silent.writes), 1)

    async def test_partial_write_and_stale_queue_close_without_replay(self):
        info = port_info()
        port_id = list_allowed_ports(lambda: [info])[0].port_id

        partial = FakeSerial(auto_reply=False, write_result=0)
        partial_server = await self.start_bridge(
            infos=[info],
            serial_factory=lambda: partial,
            hello_timeout=1,
        )
        partial_ws = await self.ws_connect(partial_server, port_id)
        await partial_ws.send_str(
            json.dumps(
                {"protocol": LIVE_PROTOCOL, "op": "hello", "request_id": "r-partial"}
            )
        )
        partial_message = await partial_ws.receive(timeout=1)
        self.assertEqual(json.loads(partial_message.data)["code"], "serial_error")
        self.assertFalse(partial.flush_called)

        slow = FakeSerial(auto_reply=False, write_delay=0.35)
        slow_server = await self.start_bridge(
            infos=[info],
            serial_factory=lambda: slow,
            hello_timeout=1,
        )
        slow_ws = await self.ws_connect(slow_server, port_id)
        await slow_ws.send_str(
            json.dumps(
                {"protocol": LIVE_PROTOCOL, "op": "hello", "request_id": "r-stale"}
            )
        )
        await slow_ws.send_str(
            json.dumps(
                {
                    "protocol": LIVE_PROTOCOL,
                    "op": "keepalive",
                    "session_id": "session-queued",
                    "seq": 1,
                }
            )
        )
        stale_message = await slow_ws.receive(timeout=1)
        self.assertEqual(
            json.loads(stale_message.data)["code"],
            "usb_bridge_queue_stale",
        )
        self.assertEqual(len(slow.writes), 1)

    async def test_partial_prefixed_response_times_out_while_chunks_continue(self):
        info = port_info()
        port_id = list_allowed_ports(lambda: [info])[0].port_id
        fake = FakeSerial(
            never_finishes_response=True,
            read_delay=0.04,
        )
        server = await self.start_bridge(
            infos=[info],
            serial_factory=lambda: fake,
            hello_timeout=1,
        )
        ws = await self.ws_connect(server, port_id)
        await ws.send_str(
            json.dumps(
                {"protocol": LIVE_PROTOCOL, "op": "hello", "request_id": "r-drip"}
            )
        )
        message = await ws.receive(timeout=0.7)
        self.assertEqual(
            json.loads(message.data)["code"],
            "usb_bridge_invalid_response",
        )

    async def test_origin_and_single_owner_are_enforced(self):
        info = port_info()
        port_id = list_allowed_ports(lambda: [info])[0].port_id
        server = await self.start_bridge(
            infos=[info],
            serial_factory=lambda: FakeSerial(auto_reply=False),
            hello_timeout=1,
        )
        async with ClientSession(headers={"Origin": "http://127.0.0.1:5175"}) as bad:
            response = await bad.get(server.make_url("/ports"))
            self.assertEqual(response.status, 403)

        first = await self.ws_connect(server, port_id)
        second = await self.ws_connect(server, port_id)
        message = await second.receive(timeout=1)
        self.assertEqual(json.loads(message.data)["code"], "usb_port_busy")
        await first.close()


if __name__ == "__main__":
    unittest.main()
