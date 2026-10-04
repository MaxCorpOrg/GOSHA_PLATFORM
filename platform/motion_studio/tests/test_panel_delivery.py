from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gosha_motion_studio_http import handle


class Request:
    def __init__(self, command="GET", *, authenticated=True, body=b"", origin="https://gosha.website"):
        self.command = command
        self.authenticated = authenticated
        self.wfile = BytesIO()
        self.rfile = BytesIO(body)
        self.headers = {"Origin": origin, "Content-Length": str(len(body)), "Content-Type": "application/x-www-form-urlencoded"}
        self.response_headers = {}
        self.status = None

    def _require_operator_auth(self):
        if not self.authenticated:
            self._send_json(401, {"ok": False})
        return self.authenticated

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers[key] = value

    def end_headers(self):
        pass

    def _send_json(self, status, value):
        self.status = status
        self.wfile.write(json.dumps(value).encode())

    def _send_bytes(self, status, body, content_type, extra_headers):
        self.status = status
        self.response_headers.update(extra_headers)
        self.wfile.write(body)


class PanelDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name) / "dist"
        (self.root / "assets").mkdir(parents=True)
        (self.root / "models").mkdir()
        (self.root / "index.html").write_text("Motion Studio")
        (self.root / "assets/index-build.js").write_text("editor")
        (self.root / "models/gosha.glb").write_bytes(b"owner-model")

    def tearDown(self):
        self.temp.cleanup()

    def request(self, path, **kwargs):
        request = Request(**kwargs)
        handled = handle(request, urlsplit(path), root=self.root)
        return request, handled

    def test_editor_model_and_scripts_require_operator_login(self):
        for path in ["/api/operator/motion-studio/", "/api/operator/motion-studio/assets/index-build.js", "/api/operator/motion-studio/models/gosha.glb"]:
            with self.subTest(path=path):
                denied, handled = self.request(path, authenticated=False)
                self.assertTrue(handled)
                self.assertEqual(denied.status, 401)
                request, handled = self.request(path)
                self.assertTrue(handled)
                self.assertEqual(request.status, 200)
                self.assertEqual(request.response_headers["X-Frame-Options"], "SAMEORIGIN")
                self.assertIn("frame-ancestors 'self'", request.response_headers["Content-Security-Policy"])
        self.assertFalse(self.request("/api/operator/robots")[1], "existing platform routes must remain untouched")

    def test_paths_encoded_traversal_and_symlinks_cannot_expose_private_files(self):
        private = Path(self.temp.name) / "secret.json"
        private.write_text("private")
        (self.root / "assets/escape.js").symlink_to(private)
        for path in ["/api/operator/motion-studio/../secret.json", "/api/operator/motion-studio/%2e%2e/secret.json", "/api/operator/motion-studio/assets/escape.js",
                "/api/operator/motion-studio/.git/config", "/api/operator/motion-studio/local_only/profile.json"]:
            request, _ = self.request(path)
            self.assertEqual(request.status, 404)
            self.assertNotIn(b"private", request.wfile.getvalue())

    def test_head_has_correct_length_and_canonical_route_redirects(self):
        request, _ = self.request("/api/operator/motion-studio/models/gosha.glb", command="HEAD")
        self.assertEqual(request.response_headers["Content-Length"], "11")
        self.assertEqual(request.wfile.getvalue(), b"")
        request, _ = self.request("/api/operator/motion-studio")
        self.assertEqual(request.status, 302)
        self.assertEqual(request.response_headers["Location"], "/api/operator/motion-studio/")

    def test_export_preserves_id_and_raw_recovery_without_file_writes(self):
        body = urlencode({"name": "Приветствие.motion.json", "json": '{"id":"existing-motion"}', "raw": "0"}).encode()
        request, _ = self.request("/api/operator/motion-studio/api/export", command="POST", body=body)
        self.assertEqual(request.status, 200)
        self.assertEqual(json.loads(request.wfile.getvalue())["id"], "existing-motion")
        self.assertIn("filename*=UTF-8''", request.response_headers["Content-Disposition"])
        for origin in ["https://foreign.test", "", "http://127.0.0.1:5176"]:
            request, _ = self.request("/api/operator/motion-studio/api/export", command="POST", body=body, origin=origin)
            self.assertEqual(request.status, 403)
        damaged = '{"damaged":'
        request, _ = self.request("/api/operator/motion-studio/api/export", command="POST",
            body=urlencode({"name": "original.json", "json": damaged, "raw": "1"}).encode())
        self.assertEqual(request.wfile.getvalue().decode(), damaged)
        for name in ["../private.json", "evil\r\n.json"]:
            request, _ = self.request("/api/operator/motion-studio/api/export", command="POST", body=urlencode({"name": name, "json": "{}"}).encode())
            self.assertEqual(request.status, 400)
