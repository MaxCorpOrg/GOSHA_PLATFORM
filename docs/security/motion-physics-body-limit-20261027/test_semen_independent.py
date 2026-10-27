"""Independent review probes by Semyon; isolated loopback only, never physics.runner.

No dependency on the package author's new test module. Select the baseline HTTP
pipeline case to independently reproduce the bug; run all eight on the patched
copy. No live product or hardware access.
"""
import asyncio
from contextlib import ExitStack
import gzip
import importlib.abc
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class RejectRealRunner(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "physics.runner" or fullname.startswith("physics.runner."):
            raise AssertionError("Real physics.runner import forbidden in independent QA")
        return None


sys.meta_path.insert(0, RejectRealRunner())
assert "physics.runner" not in sys.modules

from aiohttp import ClientTimeout, TCPConnector
from aiohttp.test_utils import TestClient, TestServer
from aiohttp import web
from bridge import physics_server
from physics.model import JOINTS, PROFILE

LIMIT = 2 * 1024 * 1024
FACTS = []


def motion_document():
    # Constructed independently, no import from the author's regression module.
    return {"motion": {
        "schema_version": 1,
        "profile_id": PROFILE["profile_id"],
        "profile_version": 1,
        "units": "relative_degrees",
        "preview_only": True,
        "hardware_validated": False,
        "name": "Самостоятельная проверка Ж🙂",
        "duration_ms": 700,
        "interpolation": "linear",
        "keyframes": [{"time_ms": 0, "pose": {
            item["id"]: (item["min"] + item["max"]) / 2 for item in JOINTS
        }}],
    }}


def encoded_document():
    return json.dumps(motion_document(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def utf8_body_at(size):
    value = encoded_document()
    # Deliberately place the four-byte emoji across a 64 KiB read boundary.
    index = value.index("🙂".encode("utf-8"))
    prefix = b" " * (65535 - index)
    body = prefix + value
    assert len(body) <= size
    return body + b" " * (size - len(body))


async def divided(body):
    # First cut splits the emoji, second completes it; no reliance on TCP packets.
    for part in (body[:65536], body[65536:65539], body[65539:LIMIT], body[LIMIT:]):
        if part:
            yield part
            await asyncio.sleep(0)


class IndependentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(TemporaryDirectory(prefix="qa-semen-"))
        dist = Path(self.temp)
        (dist / "models").mkdir()
        (dist / "index.html").write_text("Independent synthetic fixture", encoding="utf-8")
        (dist / "models/gosha.glb").write_bytes(b"synthetic-only")
        self.started = []

        async def fake_work(manager, job, payload):
            self.started.append(payload["motion"]["name"])
            job.update(status="completed", result={"independent_synthetic": True})

        self.stack.enter_context(patch.object(physics_server, "engine_status", return_value=({}, True)))
        self.stack.enter_context(patch.object(physics_server.PhysicsJobs, "run", fake_work))
        self.stack.enter_context(patch("asyncio.create_subprocess_exec", side_effect=AssertionError("No worker subprocess allowed")))
        self.stack.enter_context(patch("asyncio.create_subprocess_shell", side_effect=AssertionError("No subprocess shell allowed")))
        self.client = TestClient(TestServer(physics_server.physics_app(dist, simulation_only=True), host="127.0.0.1"),
                                 connector=TCPConnector(limit=1), timeout=ClientTimeout(total=6))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.url = self.client.make_url("/")
        self.origin = str(self.url).rstrip("/")
        self.headers = {"Origin": self.origin, "Content-Type": "application/json"}

    async def asyncTearDown(self):
        self.assertNotIn("physics.runner", sys.modules)

    def fact(self, **fields):
        FACTS.append({"test": self._testMethodName, **fields})

    async def connection(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.url.port)
        self.addAsyncCleanup(self.close_connection, writer)
        return reader, writer

    @staticmethod
    async def close_connection(writer):
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError):
            pass

    def raw_headers(self, *, length=None, close=False):
        fields = ["POST /api/physics/jobs HTTP/1.1",
                  "Host: 127.0.0.1:" + str(self.url.port),
                  "Origin: " + self.origin, "Content-Type: application/json",
                  "Connection: " + ("close" if close else "keep-alive")]
        fields.append("Transfer-Encoding: chunked" if length is None else "Content-Length: " + str(length))
        return ("\r\n".join(fields) + "\r\n\r\n").encode("ascii")

    @staticmethod
    def chunk(value):
        return format(len(value), "x").encode("ascii") + b"\r\n" + value + b"\r\n"

    async def response(self, reader):
        headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=4)
        lines = headers.split(b"\r\n")
        status = int(lines[0].split()[1])
        fields = {line.split(b":", 1)[0].lower(): line.split(b":", 1)[1].strip()
                  for line in lines[1:] if b":" in line}
        self.assertIn(b"content-length", fields)
        size = int(fields[b"content-length"])
        self.assertLess(size, 8192)
        body = await asyncio.wait_for(reader.readexactly(size), timeout=4)
        return status, fields, body

    async def post(self, body, *, chunked=False, extra_headers=None):
        headers = {**self.headers, **(extra_headers or {})}
        before = len(self.started)
        reply = await self.client.post("/api/physics/jobs", data=divided(body) if chunked else body,
                                       headers=headers)
        await reply.read()
        await asyncio.sleep(0)
        sent = reply.request_info.headers
        if chunked:
            self.assertNotIn("Content-Length", sent)
            self.assertEqual(sent.get("Transfer-Encoding"), "chunked")
        else:
            self.assertEqual(int(sent["Content-Length"]), len(body))
        return reply.status, len(self.started) - before

    async def test_raw_exact_limit_then_one_gets_413_before_chunk_terminator(self):
        reader, writer = await self.connection()
        writer.write(self.raw_headers())
        writer.write(self.chunk(b" " * LIMIT))
        await writer.drain()
        # The sender deliberately never sends the terminating zero-length chunk.
        with patch.object(physics_server.json, "loads", side_effect=AssertionError("No JSON parse on overflow")), \
             patch.object(physics_server, "validate_request", side_effect=AssertionError("No model parse on overflow")):
            writer.write(self.chunk(b" "))
            await writer.drain()
            status, headers, _ = await self.response(reader)
        self.assertEqual(status, 413)
        self.assertEqual(self.started, [])
        self.fact(status=status, chunk_lengths=[LIMIT, 1], terminal_zero_chunk_sent=False,
                  json_and_model_forbidden=True, synthetic_jobs=0)

    async def test_fragmented_overflow_then_valid_request_on_same_tcp_connection(self):
        reader, writer = await self.connection()
        first = utf8_body_at(LIMIT + 1)
        second = encoded_document()
        # A complete chunked overflow and subsequent valid request are pipelined
        # on this exact writer/reader pair; a reconnect cannot satisfy the test.
        writer.write(self.raw_headers())
        writer.write(self.chunk(first[:LIMIT]))
        writer.write(self.chunk(first[LIMIT:]))
        writer.write(b"0\r\n\r\n")
        writer.write(self.raw_headers(length=len(second), close=True) + second)
        await writer.drain()
        first_status, _, _ = await self.response(reader)
        self.assertEqual(first_status, 413)
        next_status, _, _ = await self.response(reader)
        self.assertEqual(next_status, 202)
        await asyncio.sleep(0)
        self.assertEqual(len(self.started), 1)
        self.fact(statuses=[first_status, next_status], same_tcp_connection=True,
                  oversized_body_bytes=len(first), synthetic_jobs=1)

    async def test_multibyte_utf8_at_byte_limit_and_one_over_both_transports(self):
        for size, expected in ((LIMIT, 202), (LIMIT + 1, 413)):
            body = utf8_body_at(size)
            characters = len(body.decode("utf-8"))
            self.assertLess(characters, LIMIT)
            self.assertEqual(body[65535:65539], "🙂".encode("utf-8"))
            for chunked in (False, True):
                with self.subTest(size=size, chunked=chunked):
                    status, jobs = await self.post(body, chunked=chunked)
                    self.assertEqual(status, expected)
                    self.assertEqual(jobs, 1 if expected == 202 else 0)
                    self.fact(bytes=size, characters=characters, chunked=chunked,
                              emoji_start_offset=65535, status=status, synthetic_jobs=jobs)

    async def test_helper_decodes_utf8_only_after_cross_boundary_reads(self):
        data = utf8_body_at(LIMIT)

        class ByteStream:
            def __init__(self):
                self.offset = 0
                self.sizes = []
            async def read(self, amount):
                self.sizes.append(amount)
                result = data[self.offset:self.offset + amount]
                self.offset += len(result)
                return result

        stream = ByteStream()
        request = SimpleNamespace(content_length=None, content=stream, charset="utf-8")
        decoded = await physics_server.read_motion_json(request)
        self.assertEqual(decoded, motion_document())
        self.assertEqual(stream.offset, LIMIT)
        self.assertEqual(stream.sizes[0], 65536)
        self.assertEqual(stream.sizes[-1], 1)  # EOF check, not a zero-sized read.
        self.assertEqual(len(stream.sizes), 33)
        self.fact(read_bytes=stream.offset, first_read=stream.sizes[0], last_read=stream.sizes[-1],
                  read_calls=len(stream.sizes), unicode_roundtrip=True)

    async def test_helper_short_reads_stop_at_limit_plus_one_without_json(self):
        class ShortStream:
            def __init__(self):
                self.bytes = 0
                self.requests = []
            async def read(self, amount):
                self.requests.append(amount)
                # Vary actual return lengths to exercise partial reads.
                actual = min(amount, (65535, 2, 17, 65536)[(len(self.requests) - 1) % 4])
                self.bytes += actual
                return b" " * actual

        stream = ShortStream()
        request = SimpleNamespace(content_length=None, content=stream, charset="utf-8")
        with patch.object(physics_server.json, "loads", side_effect=AssertionError("No JSON on overflow")):
            with self.assertRaises(web.HTTPRequestEntityTooLarge):
                await physics_server.read_motion_json(request)
        self.assertEqual(stream.bytes, LIMIT + 1)
        self.assertGreater(min(stream.requests), 0)
        self.assertLessEqual(max(stream.requests), 65536)
        self.fact(read_bytes=stream.bytes, read_calls=len(stream.requests),
                  max_requested=max(stream.requests), json_forbidden=True)

    async def test_announced_overflow_does_not_read_any_body(self):
        class NoReads:
            async def read(self, amount):
                raise AssertionError("Content-Length overflow must not read the stream")

        request = SimpleNamespace(content_length=LIMIT + 1, content=NoReads(), charset="utf-8")
        with patch.object(physics_server.json, "loads", side_effect=AssertionError("No JSON on announced overflow")):
            with self.assertRaises(web.HTTPRequestEntityTooLarge):
                await physics_server.read_motion_json(request)
        self.fact(announced_bytes=LIMIT + 1, body_read_calls=0, json_forbidden=True)

    async def test_gzip_decoded_bytes_bounded_for_fixed_and_chunked(self):
        for plain, expected in ((encoded_document(), 202), (utf8_body_at(LIMIT + 1), 413)):
            wire = gzip.compress(plain, mtime=0)
            self.assertLess(len(wire), LIMIT)
            for chunked in (False, True):
                with self.subTest(decoded_bytes=len(plain), chunked=chunked):
                    status, jobs = await self.post(wire, chunked=chunked,
                                                  extra_headers={"Content-Encoding": "gzip"})
                    self.assertEqual(status, expected)
                    self.assertEqual(jobs, 1 if expected == 202 else 0)
                    self.fact(wire_bytes=len(wire), decoded_bytes=len(plain), chunked=chunked,
                              status=status, synthetic_jobs=jobs)

    async def test_bad_utf8_returns_400_then_valid_request_is_202(self):
        invalid = b'{"motion":"\xff"}'
        first_status, first_jobs = await self.post(invalid, chunked=True)
        self.assertEqual(first_status, 400)
        self.assertEqual(first_jobs, 0)
        second_status, second_jobs = await self.post(encoded_document())
        self.assertEqual(second_status, 202)
        self.assertEqual(second_jobs, 1)
        self.fact(statuses=[first_status, second_status], synthetic_jobs=[first_jobs, second_jobs])


if __name__ == "__main__":
    unittest.main(verbosity=2)
