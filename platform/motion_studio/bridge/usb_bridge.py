from __future__ import annotations

import argparse
import asyncio
import contextlib
import errno
import fcntl
import hashlib
import json
import logging
import os
import re
import select
import termios
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from aiohttp import WSMsgType, web
from serial.tools import list_ports


LIVE_PROTOCOL = "gosha.motion.live.v1"
WIRE_PREFIX = b"@GOSHA-LIVE:"
ALLOWED_VID = 0x303A
ALLOWED_PID = 0x1001
REQUEST_JSON_MAX = 4096
RESPONSE_JSON_MAX = 16384
QUEUE_MAX = 8
SERIAL_BAUD = 115200
SERIAL_READ_TIMEOUT = 0.05
SERIAL_WRITE_TIMEOUT = 0.1
SERIAL_READ_CHUNK = 512
SERIAL_FRAME_TIMEOUT = 0.5
QUEUE_FRAME_TTL = 0.25
SERIAL_WRITE_POLL = 0.01
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5177
DEFAULT_ORIGIN = "http://127.0.0.1:5176"
PORT_ID_RE = re.compile(r"^[a-f0-9]{24}$")
LOGGER = logging.getLogger("gosha_motion_studio.usb_bridge")
RETRY_WRITE_ERRNOS = {
    errno.EAGAIN,
    errno.EALREADY,
    errno.EWOULDBLOCK,
    errno.EINPROGRESS,
    errno.EINTR,
}


class BridgeError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AllowedPort:
    port_id: str
    device: str
    label: str

    def public(self) -> dict[str, Any]:
        return {
            "port_id": self.port_id,
            "label": self.label,
            "vid": "303a",
            "pid": "1001",
        }


@dataclass(frozen=True)
class QueuedFrame:
    line: bytes
    deadline: float


def _clean_label(value: object) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"[^0-9A-Za-z ._:+-]+", " ", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:80]


def _port_id(info: Any) -> str:
    parts = [
        getattr(info, "device", ""),
        getattr(info, "serial_number", ""),
        getattr(info, "manufacturer", ""),
        getattr(info, "product", ""),
        getattr(info, "location", ""),
        getattr(info, "hwid", ""),
    ]
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()
    return digest[:24]


def list_allowed_ports(
    comports: Callable[[], Iterable[Any]] = list_ports.comports,
) -> list[AllowedPort]:
    result: list[AllowedPort] = []
    label_counts: dict[str, int] = {}
    for info in comports():
        if getattr(info, "vid", None) != ALLOWED_VID:
            continue
        if getattr(info, "pid", None) != ALLOWED_PID:
            continue
        device = str(getattr(info, "device", ""))
        if not device:
            continue
        descriptor = (
            _clean_label(getattr(info, "product", None))
            or _clean_label(getattr(info, "description", None))
            or _clean_label(getattr(info, "manufacturer", None))
            or "ESP32-S3 USB"
        )
        label = f"{descriptor} USB 303a:1001"
        label_counts[label] = label_counts.get(label, 0) + 1
        if label_counts[label] > 1:
            label = f"{label} #{label_counts[label]}"
        result.append(AllowedPort(port_id=_port_id(info), device=device, label=label))
    return result


def _clear_flag(value: int, name: str) -> int:
    return value & ~getattr(termios, name, 0)


def _cc_is_zero(value: object) -> bool:
    return value == 0 or value == b"\x00"


def _verify_tty_raw_no_hangup(fd: int) -> None:
    attrs = termios.tcgetattr(fd)
    cflag = attrs[2]
    if (cflag & termios.CLOCAL) != termios.CLOCAL:
        raise BridgeError("usb_serial_safety")
    if (cflag & termios.CREAD) != termios.CREAD:
        raise BridgeError("usb_serial_safety")
    if (cflag & termios.CSIZE) != termios.CS8:
        raise BridgeError("usb_serial_safety")
    for name in ("CRTSCTS", "HUPCL"):
        if cflag & getattr(termios, name, 0):
            raise BridgeError("usb_serial_safety")
    speed = getattr(termios, f"B{SERIAL_BAUD}", None)
    if attrs[4] != speed or attrs[5] != speed:
        raise BridgeError("usb_serial_safety")
    if not _cc_is_zero(attrs[6][termios.VMIN]):
        raise BridgeError("usb_serial_safety")
    if not _cc_is_zero(attrs[6][termios.VTIME]):
        raise BridgeError("usb_serial_safety")


def _configure_tty_raw_no_hangup(fd: int) -> None:
    attrs = termios.tcgetattr(fd)
    iflag, oflag, cflag, lflag, _ispeed, _ospeed, cc = attrs

    for name in (
        "INLCR",
        "IGNCR",
        "ICRNL",
        "IGNBRK",
        "BRKINT",
        "PARMRK",
        "ISTRIP",
        "INPCK",
        "IXON",
        "IXOFF",
        "IXANY",
    ):
        iflag = _clear_flag(iflag, name)
    for name in ("OPOST", "ONLCR", "OCRNL"):
        oflag = _clear_flag(oflag, name)
    for name in (
        "ICANON",
        "ECHO",
        "ECHOE",
        "ECHOK",
        "ECHONL",
        "ISIG",
        "IEXTEN",
        "ECHOCTL",
        "ECHOKE",
    ):
        lflag = _clear_flag(lflag, name)

    cflag = _clear_flag(cflag, "CSIZE")
    cflag |= termios.CS8 | termios.CLOCAL | termios.CREAD
    for name in ("PARENB", "PARODD", "CMSPAR", "CSTOPB", "CRTSCTS", "HUPCL"):
        cflag = _clear_flag(cflag, name)

    speed = getattr(termios, f"B{SERIAL_BAUD}", None)
    if speed is None:
        raise BridgeError("usb_serial_safety")
    attrs = [iflag, oflag, cflag, lflag, speed, speed, cc]
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    _verify_tty_raw_no_hangup(fd)


class PosixSerialDevice:
    def __init__(
        self,
        device: str,
        *,
        baudrate: int = SERIAL_BAUD,
        timeout: float = SERIAL_READ_TIMEOUT,
        write_timeout: float = SERIAL_WRITE_TIMEOUT,
    ) -> None:
        if baudrate != SERIAL_BAUD or write_timeout > SERIAL_WRITE_TIMEOUT:
            raise BridgeError("usb_serial_safety")
        self.port = device
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = write_timeout
        self.exclusive = True
        self.fd: int | None = None
        self.is_open = False

    def open(self) -> None:
        if self.is_open:
            raise BridgeError("usb_port_busy")
        fd: int | None = None
        stage = "open"
        try:
            fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            stage = "lock"
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            stage = "configure"
            _configure_tty_raw_no_hangup(fd)
            self.fd = fd
            self.is_open = True
        except BlockingIOError as exc:
            if fd is not None:
                with contextlib.suppress(Exception):
                    os.close(fd)
            code = "usb_serial_safety" if stage == "configure" else "usb_port_busy"
            raise BridgeError(code) from exc
        except BridgeError:
            if fd is not None:
                with contextlib.suppress(Exception):
                    os.close(fd)
            raise
        except Exception as exc:
            if fd is not None:
                with contextlib.suppress(Exception):
                    os.close(fd)
            code = "usb_serial_safety" if stage == "configure" else "usb_port_busy"
            raise BridgeError(code) from exc

    def fileno(self) -> int:
        if self.fd is None or not self.is_open:
            raise BridgeError("serial_error")
        return self.fd

    def reset_input_buffer(self) -> None:
        if self.fd is None or not self.is_open:
            raise BridgeError("serial_error")
        termios.tcflush(self.fd, termios.TCIFLUSH)

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        fd = self.fileno()
        limit = size if size is not None else 1
        line = bytearray()
        deadline = time.monotonic() + self.timeout
        while len(line) < limit:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                readable, _, _ = select.select([fd], [], [], remaining)
            except OSError as exc:
                if exc.errno in RETRY_WRITE_ERRNOS:
                    continue
                raise BridgeError("serial_error") from exc
            if not readable:
                break
            try:
                chunk = os.read(fd, 1)
            except OSError as exc:
                if exc.errno in RETRY_WRITE_ERRNOS:
                    continue
                raise BridgeError("serial_error") from exc
            if not chunk:
                raise BridgeError("serial_error")
            line.extend(chunk)
            if line.endswith(expected):
                break
        return bytes(line)

    def close(self) -> None:
        fd, self.fd = self.fd, None
        self.is_open = False
        if fd is not None:
            with contextlib.suppress(Exception):
                os.close(fd)


def _make_serial_device(
    port: AllowedPort,
    serial_factory: Callable[[str], Any] | None,
) -> Any:
    if serial_factory is None:
        return PosixSerialDevice(port.device)
    return serial_factory(port.device)


def open_serial_port(
    port: AllowedPort,
    serial_factory: Callable[[str], Any] | None = None,
) -> Any:
    ser: Any | None = None

    try:
        ser = _make_serial_device(port, serial_factory)
        ser.open()
        with contextlib.suppress(Exception):
            ser.reset_input_buffer()
        return ser
    except BridgeError:
        if ser is not None:
            with contextlib.suppress(Exception):
                ser.close()
        raise
    except Exception as exc:
        if ser is not None:
            with contextlib.suppress(Exception):
                ser.close()
        raise BridgeError("usb_port_busy") from exc


def _serial_fd(serial_obj: Any) -> int:
    fileno = getattr(serial_obj, "fileno", None)
    if callable(fileno):
        fd = fileno()
    else:
        fd = getattr(serial_obj, "fd", None)
    if not isinstance(fd, int) or fd < 0:
        raise BridgeError("serial_error")
    return fd


def _select_writable(fd: int, timeout: float) -> bool:
    try:
        _, writable, _ = select.select([], [fd], [], timeout)
    except OSError as exc:
        if exc.errno in RETRY_WRITE_ERRNOS:
            return False
        raise BridgeError("serial_error") from exc
    return bool(writable)


def _write_frame_bounded(
    serial_obj: Any,
    data: bytes,
    timeout_seconds: float,
    should_stop: Callable[[], bool],
    *,
    os_write: Callable[[int, memoryview], int] = os.write,
    select_writable: Callable[[int, float], bool] = _select_writable,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    timeout_seconds = min(timeout_seconds, SERIAL_WRITE_TIMEOUT)
    if timeout_seconds <= 0:
        raise BridgeError("usb_bridge_queue_stale")
    fd = _serial_fd(serial_obj)
    view = memoryview(data)
    deadline = clock() + timeout_seconds
    offset = 0
    wait_before_next_write = False

    while offset < len(view):
        if should_stop():
            raise BridgeError("serial_error")
        remaining = deadline - clock()
        if remaining <= 0:
            raise BridgeError("usb_bridge_queue_stale")
        if wait_before_next_write:
            wait_time = min(remaining, SERIAL_WRITE_POLL)
            if not select_writable(fd, wait_time):
                continue
            if should_stop():
                raise BridgeError("serial_error")
            remaining = deadline - clock()
            if remaining <= 0:
                raise BridgeError("usb_bridge_queue_stale")
        try:
            written = os_write(fd, view[offset:])
        except OSError as exc:
            if exc.errno in RETRY_WRITE_ERRNOS:
                wait_before_next_write = True
                continue
            raise BridgeError("serial_error") from exc
        if written <= 0:
            raise BridgeError("serial_error")
        offset += written
        wait_before_next_write = offset < len(view)
    return offset


def _compact_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _live_error(code: str, request_id: object | None = None) -> str:
    payload: dict[str, Any] = {"protocol": LIVE_PROTOCOL, "op": "error", "code": code}
    if isinstance(request_id, str) and request_id:
        payload["request_id"] = request_id
    return _compact_json(payload)


def _validate_request(raw: str) -> tuple[dict[str, Any], bytes]:
    if len(raw.encode("utf-8")) > REQUEST_JSON_MAX:
        raise BridgeError("usb_bridge_request_too_large")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BridgeError("usb_bridge_invalid_request") from exc
    if not isinstance(data, dict) or data.get("protocol") != LIVE_PROTOCOL:
        raise BridgeError("usb_bridge_invalid_request")
    compact = _compact_json(data).encode("utf-8")
    if len(compact) > REQUEST_JSON_MAX:
        raise BridgeError("usb_bridge_request_too_large")
    return data, WIRE_PREFIX + compact + b"\n"


def _decode_response(raw: bytes) -> str | None:
    if not raw:
        return None
    if not raw.startswith(WIRE_PREFIX):
        return None
    payload = raw[len(WIRE_PREFIX) :].strip()
    if len(payload) > RESPONSE_JSON_MAX:
        raise BridgeError("usb_bridge_response_too_large")
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeError("usb_bridge_invalid_response") from exc
    if not isinstance(data, dict) or data.get("protocol") != LIVE_PROTOCOL:
        raise BridgeError("usb_bridge_invalid_response")
    return _compact_json(data)


class BridgeController:
    def __init__(
        self,
        *,
        allowed_origin: str = DEFAULT_ORIGIN,
        comports: Callable[[], Iterable[Any]] = list_ports.comports,
        serial_factory: Callable[[str], Any] | None = None,
        serial_writer: Callable[[Any, bytes, float, Callable[[], bool]], int] = _write_frame_bounded,
        hello_timeout: float = 2.5,
        logger: logging.Logger = LOGGER,
    ) -> None:
        self.allowed_origin = allowed_origin
        self.comports = comports
        self.serial_factory = serial_factory
        self.serial_writer = serial_writer
        self.hello_timeout = hello_timeout
        self.logger = logger
        self._active = False
        self._owner_lock = asyncio.Lock()

    def check_origin(self, request: web.Request) -> None:
        if request.headers.get("Origin") != self.allowed_origin:
            raise web.HTTPForbidden(text="origin_not_allowed")

    def cors_headers(self) -> dict[str, str]:
        return {
            "Access-Control-Allow-Origin": self.allowed_origin,
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Vary": "Origin",
        }

    def ports(self) -> list[AllowedPort]:
        return list_allowed_ports(self.comports)

    async def acquire_owner(self) -> bool:
        async with self._owner_lock:
            if self._active:
                return False
            self._active = True
            return True

    async def release_owner(self) -> None:
        async with self._owner_lock:
            self._active = False

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "protocol": LIVE_PROTOCOL})

    async def handle_options(self, request: web.Request) -> web.Response:
        self.check_origin(request)
        return web.Response(status=204, headers=self.cors_headers())

    async def handle_ports(self, request: web.Request) -> web.Response:
        self.check_origin(request)
        return web.json_response(
            {"protocol": LIVE_PROTOCOL, "ports": [port.public() for port in self.ports()]},
            headers=self.cors_headers(),
        )

    async def send_error(
        self,
        ws: web.WebSocketResponse,
        code: str,
        request_id: object | None = None,
    ) -> None:
        if not ws.closed:
            await ws.send_str(_live_error(code, request_id))

    async def handle_live(self, request: web.Request) -> web.WebSocketResponse:
        self.check_origin(request)
        port_id = request.query.get("port_id", "")
        if not PORT_ID_RE.fullmatch(port_id):
            raise web.HTTPBadRequest(text="invalid_port_id")

        ws = web.WebSocketResponse(max_msg_size=REQUEST_JSON_MAX)
        await ws.prepare(request)

        if not await self.acquire_owner():
            await self.send_error(ws, "usb_port_busy")
            await ws.close()
            return ws

        serial_obj: Any | None = None
        write_queue: asyncio.Queue[QueuedFrame | None] = asyncio.Queue(
            maxsize=QUEUE_MAX,
        )
        stop_event = asyncio.Event()
        first_response = asyncio.Event()
        tasks: list[asyncio.Task[Any]] = []
        hello_task: asyncio.Task[Any] | None = None

        async def close_with_error(
            code: str,
            request_id: object | None = None,
        ) -> None:
            stop_event.set()
            await self.send_error(ws, code, request_id)
            await ws.close()

        async def nohello_watchdog(request_id: object | None) -> None:
            try:
                await asyncio.wait_for(first_response.wait(), timeout=self.hello_timeout)
            except asyncio.TimeoutError:
                await close_with_error("firmware_nohello", request_id)

        async def ensure_serial(request_id: object | None) -> bool:
            nonlocal serial_obj
            if serial_obj is not None:
                return True
            port = next((item for item in self.ports() if item.port_id == port_id), None)
            if port is None:
                await close_with_error("usb_device_absent", request_id)
                return False
            try:
                serial_obj = await asyncio.to_thread(
                    open_serial_port,
                    port,
                    self.serial_factory,
                )
            except BridgeError as exc:
                await close_with_error(exc.code, request_id)
                return False
            tasks.append(asyncio.create_task(serial_reader()))
            tasks.append(asyncio.create_task(serial_writer()))
            return True

        async def serial_reader() -> None:
            assert serial_obj is not None
            limit = len(WIRE_PREFIX) + RESPONSE_JSON_MAX + 1
            buffer = bytearray()
            frame_started_at: float | None = None
            while not stop_event.is_set():
                try:
                    chunk = await asyncio.to_thread(
                        serial_obj.read_until,
                        b"\n",
                        SERIAL_READ_CHUNK,
                    )
                except Exception:
                    await close_with_error("serial_error")
                    return
                if stop_event.is_set():
                    return
                now = asyncio.get_running_loop().time()
                if (
                    frame_started_at is not None
                    and now - frame_started_at > SERIAL_FRAME_TIMEOUT
                ):
                    if bytes(buffer).startswith(WIRE_PREFIX):
                        await close_with_error("usb_bridge_invalid_response")
                        return
                    buffer.clear()
                    frame_started_at = None
                if not chunk:
                    continue
                if not buffer:
                    frame_started_at = now
                buffer.extend(chunk)
                if len(buffer) > limit:
                    await close_with_error("usb_bridge_response_too_large")
                    return
                while b"\n" in buffer:
                    raw, _, remainder = buffer.partition(b"\n")
                    buffer = bytearray(remainder)
                    frame_started_at = now if buffer else None
                    try:
                        response = _decode_response(raw + b"\n")
                    except BridgeError as exc:
                        await close_with_error(exc.code)
                        return
                    if response is None:
                        continue
                    first_response.set()
                    await ws.send_str(response)

        async def serial_writer() -> None:
            assert serial_obj is not None
            while not stop_event.is_set():
                frame = await write_queue.get()
                if frame is None:
                    return
                if stop_event.is_set():
                    return
                if asyncio.get_running_loop().time() > frame.deadline:
                    await close_with_error("usb_bridge_queue_stale")
                    return
                try:
                    timeout_seconds = min(
                        SERIAL_WRITE_TIMEOUT,
                        frame.deadline - asyncio.get_running_loop().time(),
                    )
                    written = await asyncio.to_thread(
                        self.serial_writer,
                        serial_obj,
                        frame.line,
                        timeout_seconds,
                        stop_event.is_set,
                    )
                except BridgeError as exc:
                    await close_with_error(exc.code)
                    return
                except Exception:
                    await close_with_error("serial_error")
                    return
                if stop_event.is_set():
                    return
                if written != len(frame.line):
                    await close_with_error("serial_error")
                    return

        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    await close_with_error("usb_bridge_invalid_request")
                    break
                request_id: object | None = None
                try:
                    data, line = _validate_request(message.data)
                    request_id = data.get("request_id")
                    if serial_obj is None and data.get("op") != "hello":
                        raise BridgeError("usb_bridge_invalid_request")
                    if not await ensure_serial(request_id):
                        break
                    if write_queue.full():
                        raise BridgeError("usb_bridge_queue_full")
                    write_queue.put_nowait(
                        QueuedFrame(
                            line=line,
                            deadline=asyncio.get_running_loop().time()
                            + QUEUE_FRAME_TTL,
                        )
                    )
                    if data.get("op") == "hello" and hello_task is None:
                        hello_task = asyncio.create_task(nohello_watchdog(request_id))
                except BridgeError as exc:
                    await close_with_error(exc.code, request_id)
                    break
        finally:
            stop_event.set()
            cancel_requested = False

            async def run_cleanup(awaitable_factory: Callable[[], Any]) -> Any:
                nonlocal cancel_requested
                try:
                    return await awaitable_factory()
                except asyncio.CancelledError:
                    cancel_requested = True
                    current = asyncio.current_task()
                    if current is not None and hasattr(current, "uncancel"):
                        current.uncancel()
                    return await awaitable_factory()

            async def drain_io_tasks() -> None:
                if not tasks:
                    return
                done, pending = await asyncio.wait(
                    tasks,
                    timeout=SERIAL_READ_TIMEOUT + SERIAL_WRITE_TIMEOUT + 0.2,
                )
                if pending:
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)

            if hello_task is not None:
                hello_task.cancel()
            while not write_queue.empty():
                with contextlib.suppress(asyncio.QueueEmpty):
                    write_queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                write_queue.put_nowait(None)
            await run_cleanup(drain_io_tasks)
            if serial_obj is not None:
                with contextlib.suppress(Exception):
                    serial_obj.close()
            await run_cleanup(self.release_owner)
            if cancel_requested:
                raise asyncio.CancelledError
        return ws


def create_app(
    *,
    allowed_origin: str = DEFAULT_ORIGIN,
    comports: Callable[[], Iterable[Any]] = list_ports.comports,
    serial_factory: Callable[[str], Any] | None = None,
    serial_writer: Callable[[Any, bytes, float, Callable[[], bool]], int] = _write_frame_bounded,
    hello_timeout: float = 2.5,
) -> web.Application:
    controller = BridgeController(
        allowed_origin=allowed_origin,
        comports=comports,
        serial_factory=serial_factory,
        serial_writer=serial_writer,
        hello_timeout=hello_timeout,
    )
    app = web.Application(client_max_size=REQUEST_JSON_MAX + 1024)
    app.router.add_get("/health", controller.handle_health)
    app.router.add_options("/ports", controller.handle_options)
    app.router.add_get("/ports", controller.handle_ports)
    app.router.add_get("/live", controller.handle_live)
    return app


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gosha Motion Studio USB bridge")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    parser.add_argument("--log-level", default="WARNING")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.host != DEFAULT_HOST:
        raise SystemExit("USB bridge must bind to 127.0.0.1")
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))
    app = create_app(allowed_origin=args.origin)
    web.run_app(app, host=args.host, port=args.port, access_log=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
