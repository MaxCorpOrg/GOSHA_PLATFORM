from __future__ import annotations

import asyncio
import errno
import json
import os
import queue
import serial
import termios
import threading
import time
import unittest
from types import SimpleNamespace

from aiohttp import ClientSession, WSMsgType
from aiohttp.test_utils import TestServer

import bridge.usb_bridge as bridge
from bridge.usb_bridge import (
    LIVE_PROTOCOL,
    WIRE_PREFIX,
    BridgeError,
    PosixSerialDevice,
    create_app,
    list_allowed_ports,
    open_serial_port,
    _write_frame_bounded,
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
        self.events.append(("open", getattr(self, "port", None)))
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


def fake_serial_writer(serial_obj, raw: bytes, timeout_seconds: float, should_stop):
    if should_stop():
        raise BridgeError("serial_error")
    return serial_obj.write(raw)


async def wait_for_condition(predicate, timeout: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


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

    def test_serial_open_uses_injected_adapter_without_modem_line_setters(self):
        fake = FakeSerial(auto_reply=False)
        port = list_allowed_ports(lambda: [port_info()])[0]
        opened = open_serial_port(
            port,
            lambda device: setattr(fake, "port", device) or fake,
        )
        self.assertIs(opened, fake)
        self.assertEqual(fake.port, "/dev/ttyACM0")
        self.assertEqual(fake.events, [("open", "/dev/ttyACM0")])
        self.assertFalse(any(event[0] in {"dtr", "rts"} for event in fake.events))

    def test_posix_serial_open_sets_raw_mode_without_hupcl_or_flow_control(self):
        master_fd, slave_fd = os.openpty()
        device = os.ttyname(slave_fd)
        os.close(slave_fd)
        serial_obj = PosixSerialDevice(device)
        try:
            serial_obj.open()
            attrs = termios.tcgetattr(serial_obj.fileno())
            cflag = attrs[2]
            self.assertTrue(cflag & termios.CLOCAL)
            self.assertTrue(cflag & termios.CREAD)
            self.assertFalse(cflag & termios.HUPCL)
            if hasattr(termios, "CRTSCTS"):
                self.assertFalse(cflag & termios.CRTSCTS)
            self.assertEqual(attrs[4], termios.B115200)
            self.assertEqual(attrs[5], termios.B115200)
        finally:
            serial_obj.close()
            os.close(master_fd)

    def test_posix_serial_open_fails_closed_when_tty_verify_fails(self):
        original_open = bridge.os.open
        original_close = bridge.os.close
        original_flock = bridge.fcntl.flock
        original_configure = bridge._configure_tty_raw_no_hangup
        closed = []

        def fake_configure(_fd):
            raise BridgeError("usb_serial_safety")

        try:
            bridge.os.open = lambda *_args: 123
            bridge.os.close = lambda fd: closed.append(fd)
            bridge.fcntl.flock = lambda *_args: None
            bridge._configure_tty_raw_no_hangup = fake_configure
            serial_obj = PosixSerialDevice("/dev/fake")
            with self.assertRaises(BridgeError) as caught:
                serial_obj.open()
            self.assertEqual(caught.exception.code, "usb_serial_safety")
            self.assertEqual(serial_obj.fd, None)
            self.assertFalse(serial_obj.is_open)
            self.assertEqual(closed, [123])
        finally:
            bridge.os.open = original_open
            bridge.os.close = original_close
            bridge.fcntl.flock = original_flock
            bridge._configure_tty_raw_no_hangup = original_configure

    def test_bounded_posix_write_bypasses_pyserial_false_timeout_after_full_write(self):
        read_fd, write_fd = os.pipe()
        os.set_blocking(write_fd, False)

        class PipeSerial:
            pyserial_write_called = False

            def fileno(self):
                return write_fd

            def write(self, raw):
                self.pyserial_write_called = True
                raise serial.SerialTimeoutException("Write timeout")

        payload = b"@GOSHA-LIVE:{\"protocol\":\"gosha.motion.live.v1\"}\n"
        try:
            serial_obj = PipeSerial()
            written = _write_frame_bounded(
                serial_obj,
                payload,
                0.1,
                lambda: False,
            )
            self.assertEqual(written, len(payload))
            self.assertFalse(serial_obj.pyserial_write_called)
            self.assertEqual(os.read(read_fd, len(payload)), payload)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_bounded_posix_write_retries_eagain_and_partial_without_final_select(self):
        payload = b"0123456789"
        writes = []
        selects = []

        def fake_write(_fd, chunk):
            writes.append(bytes(chunk))
            if len(writes) == 1:
                raise BlockingIOError(errno.EAGAIN, "again")
            if len(writes) == 2:
                return 4
            return len(chunk)

        def fake_select(_fd, timeout):
            selects.append(timeout)
            return True

        serial_obj = SimpleNamespace(fileno=lambda: 99)
        written = _write_frame_bounded(
            serial_obj,
            payload,
            0.1,
            lambda: False,
            os_write=fake_write,
            select_writable=fake_select,
        )
        self.assertEqual(written, len(payload))
        self.assertEqual(writes, [payload, payload, payload[4:]])
        self.assertEqual(len(selects), 2)
        self.assertTrue(all(0 < timeout <= 0.1 for timeout in selects))

    def test_bounded_posix_write_deadline_is_at_most_write_timeout(self):
        payload = b"x"
        selects = []

        def fake_write(_fd, _chunk):
            raise BlockingIOError(errno.EAGAIN, "again")

        def fake_select(_fd, timeout):
            selects.append(timeout)
            time.sleep(timeout)
            return False

        started = time.monotonic()
        with self.assertRaises(BridgeError) as caught:
            _write_frame_bounded(
                SimpleNamespace(fileno=lambda: 99),
                payload,
                1.0,
                lambda: False,
                os_write=fake_write,
                select_writable=fake_select,
            )
        self.assertEqual(caught.exception.code, "usb_bridge_queue_stale")
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertTrue(selects)
        self.assertTrue(all(0 < timeout <= 0.011 for timeout in selects))

    def test_bounded_posix_write_stop_event_cancels_after_partial(self):
        payload = b"abcdef"
        writes = []
        stopped = False

        def fake_write(_fd, chunk):
            writes.append(bytes(chunk))
            return 2

        def fake_select(_fd, _timeout):
            nonlocal stopped
            stopped = True
            return True

        with self.assertRaises(BridgeError) as caught:
            _write_frame_bounded(
                SimpleNamespace(fileno=lambda: 99),
                payload,
                0.1,
                lambda: stopped,
                os_write=fake_write,
                select_writable=fake_select,
            )
        self.assertEqual(caught.exception.code, "serial_error")
        self.assertEqual(writes, [payload])

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
        serial_writer=None,
        hello_timeout=0.2,
    ):
        if infos is None:
            infos = [port_info()]
        if serial_factory is None:
            serial_factory = lambda _device: FakeSerial()
        if serial_writer is None:
            serial_writer = fake_serial_writer
        app = create_app(
            allowed_origin=ORIGIN,
            comports=lambda: infos,
            serial_factory=serial_factory,
            serial_writer=serial_writer,
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
        server = await self.start_bridge(infos=[info], serial_factory=lambda _device: fake)
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
        self.assertTrue(await wait_for_condition(lambda: fake.closed))

    async def test_absent_device_reports_distinct_error_without_opening_serial(self):
        opened = []

        def factory():
            opened.append(True)
            return FakeSerial()

        server = await self.start_bridge(infos=[], serial_factory=lambda _device: factory())
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

        busy_server = await self.start_bridge(
            infos=[info],
            serial_factory=lambda _device: busy_factory(),
        )
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
            serial_factory=lambda _device: silent,
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
            serial_factory=lambda _device: partial,
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

        slow = FakeSerial(auto_reply=False, write_delay=0.09)
        slow_server = await self.start_bridge(
            infos=[info],
            serial_factory=lambda _device: slow,
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
        for seq in range(2, 7):
            await slow_ws.send_str(
                json.dumps(
                    {
                        "protocol": LIVE_PROTOCOL,
                        "op": "keepalive",
                        "session_id": "session-queued",
                        "seq": seq,
                    }
                )
            )
        stale_message = await slow_ws.receive(timeout=1)
        self.assertEqual(
            json.loads(stale_message.data)["code"],
            "usb_bridge_queue_stale",
        )
        self.assertLess(len(slow.writes), 7)

    async def test_cleanup_waits_for_writer_before_closing_serial(self):
        info = port_info()
        port_id = list_allowed_ports(lambda: [info])[0].port_id
        fake = FakeSerial(auto_reply=False)
        writer_started = threading.Event()
        writer_done = threading.Event()
        observed = {}

        def slow_bounded_writer(serial_obj, raw, _timeout_seconds, should_stop):
            serial_obj.writes.append(raw)
            writer_started.set()
            time.sleep(0.08)
            observed["closed_during_write"] = serial_obj.closed
            observed["stop_seen"] = should_stop()
            writer_done.set()
            return len(raw)

        server = await self.start_bridge(
            infos=[info],
            serial_factory=lambda _device: fake,
            serial_writer=slow_bounded_writer,
            hello_timeout=1,
        )
        ws = await self.ws_connect(server, port_id)
        await ws.send_str(
            json.dumps(
                {"protocol": LIVE_PROTOCOL, "op": "hello", "request_id": "r-cleanup"}
            )
        )
        self.assertTrue(await asyncio.to_thread(writer_started.wait, 0.5))
        await ws.close()
        self.assertTrue(await asyncio.to_thread(writer_done.wait, 0.5))
        self.assertFalse(observed["closed_during_write"])
        self.assertTrue(observed["stop_seen"])
        self.assertTrue(await wait_for_condition(lambda: fake.closed))

    async def test_partial_prefixed_response_times_out_while_chunks_continue(self):
        info = port_info()
        port_id = list_allowed_ports(lambda: [info])[0].port_id
        fake = FakeSerial(
            never_finishes_response=True,
            read_delay=0.04,
        )
        server = await self.start_bridge(
            infos=[info],
            serial_factory=lambda _device: fake,
            hello_timeout=bridge.SERIAL_FRAME_TIMEOUT + 1,
        )
        ws = await self.ws_connect(server, port_id)
        await ws.send_str(
            json.dumps(
                {"protocol": LIVE_PROTOCOL, "op": "hello", "request_id": "r-drip"}
            )
        )
        message = await ws.receive(timeout=bridge.SERIAL_FRAME_TIMEOUT + 0.5)
        self.assertEqual(
            json.loads(message.data)["code"],
            "usb_bridge_invalid_response",
        )

    async def test_origin_and_single_owner_are_enforced(self):
        info = port_info()
        port_id = list_allowed_ports(lambda: [info])[0].port_id
        server = await self.start_bridge(
            infos=[info],
            serial_factory=lambda _device: FakeSerial(auto_reply=False),
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
