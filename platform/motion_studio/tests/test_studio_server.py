from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from aiohttp.test_utils import TestClient, TestServer
from bridge.studio_server import create_app


class StudioServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        (root / "models").mkdir()
        (root / "assets").mkdir()
        (root / "index.html").write_text("<!doctype html><title>Motion Studio</title>")
        (root / "models/gosha.glb").write_bytes(b"full-model")
        (root / "assets/editor.js").write_text("export const ready = true;")
        self.client = TestClient(TestServer(create_app(root)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_editor_model_and_javascript_are_served_without_node(self):
        response = await self.client.get("/")
        self.assertEqual(response.status, 200)
        self.assertIn("Motion Studio", await response.text())
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Cache-Control"], "no-cache")
        model = await self.client.get("/models/gosha.glb")
        self.assertEqual(await model.read(), b"full-model")
        script = await self.client.get("/assets/editor.js")
        self.assertIn("javascript", script.content_type)

    async def test_unknown_routes_and_foreign_hosts_cannot_expose_the_workspace(self):
        for url in ["/package.json", "/local_only/profile.json", "/.git/config"]:
            response = await self.client.get(url)
            self.assertEqual(response.status, 404)
        response = await self.client.get("/", headers={"Host": "foreign.test"})
        self.assertEqual(response.status, 403)

    async def test_an_incomplete_distribution_refuses_startup(self):
        (Path(self.temp.name) / "models/gosha.glb").unlink()
        with self.assertRaises(ValueError):
            create_app(Path(self.temp.name))

    async def test_download_preserves_motion_identity_and_cannot_write_files(self):
        origin = str(self.client.make_url("/")).rstrip("/")
        data = {"name": "Приветствие.motion.json", "json": '{"id":"existing-motion","name":"Приветствие"}'}
        response = await self.client.post("/api/export", data=data, headers={"Origin": origin})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["id"], "existing-motion")
        self.assertIn("filename*=UTF-8''", response.headers["Content-Disposition"])
        rejected = await self.client.post("/api/export", data=data, headers={"Origin": "https://foreign.test"})
        self.assertEqual(rejected.status, 403)
        rejected = await self.client.post("/api/export", data={**data, "name": "../private.json"}, headers={"Origin": origin})
        self.assertEqual(rejected.status, 400)
        original = '{"damaged-library":'
        backup = await self.client.post("/api/export", data={"name": "original.json", "json": original, "raw": "1"}, headers={"Origin": origin})
        self.assertEqual(await backup.text(), original, "a damaged backup must remain recoverable byte for byte")
