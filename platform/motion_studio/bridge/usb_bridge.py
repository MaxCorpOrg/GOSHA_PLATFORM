from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from aiohttp import WSMsgType, web
import serial
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
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5177
DEFAULT_ORIGIN = "http://127.0.0.1:5176"
PORT_ID_RE = re.compile(r"^[a-f0-9]{24}$")
LOGGER = logging.getLogger("gosha_motion_studio.usb_bridge")


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


def open_serial_port(
    port: AllowedPort,
    serial_factory: Callable[[], Any] = serial.Serial,
) -> Any:
    ser: Any | None = None

    def require_attr(name: str, value: object) -> None:
        assert ser is not None
        try:
            setattr(ser, name, value)
        except Exception as exc:
            raise BridgeError("usb_serial_safety") from exc
        if getattr(ser, name, value) != value:
            raise BridgeError("usb_serial_safety")

    try:
        ser = serial_factory()
        ser.port = port.device
        ser.baudrate = SERIAL_BAUD
        ser.timeout = SERIAL_READ_TIMEOUT
        ser.write_timeout = SERIAL_WRITE_TIMEOUT
        require_attr("exclusive", True)
        require_attr("dtr", False)
        require_attr("rts", False)
        ser.open()
        require_attr("dtr", False)
        require_attr("rts", False)
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
        serial_factory: Callable[[], Any] = serial.Serial,
        hello_timeout: float = 2.5,
        logger: logging.Logger = LOGGER,
    ) -> None:
        self.allowed_origin = allowed_origin
        self.comports = comports
        self.serial_factory = serial_factory
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
                    written = await asyncio.to_thread(serial_obj.write, frame.line)
                except Exception:
                    await close_with_error("serial_error")
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
            if hello_task is not None:
                hello_task.cancel()
            while not write_queue.empty():
                with contextlib.suppress(asyncio.QueueEmpty):
                    write_queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                write_queue.put_nowait(None)
            if serial_obj is not None:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(serial_obj.close)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self.release_owner()
        return ws


def create_app(
    *,
    allowed_origin: str = DEFAULT_ORIGIN,
    comports: Callable[[], Iterable[Any]] = list_ports.comports,
    serial_factory: Callable[[], Any] = serial.Serial,
    hello_timeout: float = 2.5,
) -> web.Application:
    controller = BridgeController(
        allowed_origin=allowed_origin,
        comports=comports,
        serial_factory=serial_factory,
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
