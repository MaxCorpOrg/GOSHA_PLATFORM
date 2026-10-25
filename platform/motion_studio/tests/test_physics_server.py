import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer
from bridge.physics_server import ENGINE_VERSIONS, PhysicsJobs, physics_app
from test_physics_model import request


class PhysicsServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        (root / "models").mkdir()
        (root / "index.html").write_text("Motion Studio")
        (root / "models/gosha.glb").write_bytes(b"test-model")
        self.engines = patch("bridge.physics_server.engine_status", return_value=(ENGINE_VERSIONS, True))
        self.engines.start()
        self.release = asyncio.Event()

        async def worker(manager, job, payload):
            await self.release.wait()
            job.update(status="completed", result={"request_sha256": job["request_sha256"]})

        self.worker = patch.object(PhysicsJobs, "run", worker)
        self.worker.start()
        self.client = TestClient(TestServer(physics_app(root, simulation_only=True)))
        await self.client.start_server()
        self.origin = {"Origin": str(self.client.make_url("/")).rstrip("/")}

    async def asyncTearDown(self):
        await self.client.close()
        self.worker.stop()
        self.engines.stop()
        self.temp.cleanup()

    async def test_capabilities_editor_and_model_are_available_without_robot_transport(self):
        response = await self.client.get("/api/physics/capabilities")
        value = await response.json()
        self.assertEqual(response.status, 200)
        self.assertTrue(value["available"])
        self.assertTrue(value["simulation_only"])
        self.assertFalse(value["hardware_validated"])
        self.assertEqual(value["engine"], ENGINE_VERSIONS)
        self.assertEqual((await self.client.get("/")).status, 200)
        self.assertEqual((await self.client.get("/models/gosha.glb")).status, 200)
        for path in ("/physics/model.py", "/local_only/", "/ws", "/api/robot/hello"):
            self.assertEqual((await self.client.get(path)).status, 404)

    async def test_foreign_origin_host_wrong_media_and_bad_payload_cannot_start_work(self):
        self.assertEqual((await self.client.post("/api/physics/jobs", json=request())).status, 403)
        self.assertEqual((await self.client.post("/api/physics/jobs", json=request(), headers={"Origin": "http://foreign.test"})).status, 403)
        self.assertEqual((await self.client.get("/api/physics/capabilities", headers={"Host": "foreign.test"})).status, 403)
        self.assertEqual((await self.client.post("/api/physics/jobs", data="{}", headers=self.origin)).status, 415)
        self.assertEqual((await self.client.post("/api/physics/jobs", json={}, headers=self.origin)).status, 400)
        with patch("bridge.physics_server.engine_status", return_value=({}, False)):
            self.assertEqual((await self.client.post("/api/physics/jobs", json=request(), headers=self.origin)).status, 503)

    async def test_one_job_cancel_then_recalculate_and_read_back_the_completed_result(self):
        first = await self.client.post("/api/physics/jobs", json=request(), headers=self.origin)
        self.assertEqual(first.status, 202)
        job = await first.json()
        url = "/api/physics/jobs/" + job["id"]
        self.assertEqual((await self.client.post("/api/physics/jobs", json=request(), headers=self.origin)).status, 409)
        self.assertEqual((await self.client.delete(url, headers={"Origin": "http://foreign.test"})).status, 403)
        cancelled = await self.client.delete(url, headers=self.origin)
        self.assertEqual(cancelled.status, 200)
        self.assertEqual((await cancelled.json())["status"], "cancelled")
        self.assertEqual((await (await self.client.get(url)).json())["status"], "cancelled")
        self.release.set()
        second = await (await self.client.post("/api/physics/jobs", json=request(), headers=self.origin)).json()
        value = await (await self.client.get("/api/physics/jobs/" + second["id"])).json()
        self.assertEqual(value["status"], "completed")
        self.assertEqual(value["result"]["request_sha256"], value["request_sha256"])
        self.assertEqual((await self.client.get("/api/physics/jobs/missing")).status, 404)

    async def test_only_four_finished_reports_are_retained(self):
        self.release.set()
        ids = []
        for _ in range(5):
            value = await (await self.client.post("/api/physics/jobs", json=request(), headers=self.origin)).json()
            ids.append(value["id"])
        self.assertEqual((await self.client.get("/api/physics/jobs/" + ids[0])).status, 404)
        self.assertEqual((await self.client.get("/api/physics/jobs/" + ids[-1])).status, 200)

    async def test_reports_larger_than_motion_import_limit_can_be_downloaded_unchanged(self):
        content = '{"preview_only":true,"frames":"' + "x" * (2 * 1024 * 1024) + '"}'
        response = await self.client.post("/api/export", headers=self.origin,
            data={"name": "physics.json", "json": content, "raw": "1"})
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.text(), content)
        too_large = await self.client.post("/api/physics/jobs", data=content,
            headers={**self.origin, "Content-Type": "application/json"})
        self.assertEqual(too_large.status, 413)
