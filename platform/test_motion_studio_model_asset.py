"""The model URL in the actual client must pass the unchanged authenticated route."""
import io
from pathlib import Path
import re
import tempfile
import unittest
from urllib.parse import urlparse

from gosha_motion_studio_http import handle


class Handler:
    command = "GET"
    def __init__(self, authorized=True):
        self.authorized = authorized
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = {}
    def _require_operator_auth(self):
        return self.authorized
    def send_response(self, status):
        self.status = status
    def send_header(self, key, value):
        self.headers[key] = value
    def end_headers(self):
        pass
    def _send_json(self, status, value):
        self.status = status


class ModelRouteTests(unittest.TestCase):
    def test_actual_scene_model_url_is_served_with_version_query(self):
        source = (Path(__file__).parent / "motion_studio/src/scene.js").read_text()
        relative = re.search(r"BASE_URL\}(models/[^`]+)", source)[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "models").mkdir()
            (root / "models/gosha.glb").write_bytes(b"glTF model fixture")
            h = Handler()
            self.assertTrue(handle(h, urlparse("/api/operator/motion-studio/" + relative), root=root))
            self.assertEqual(h.status, 200)
            self.assertEqual(h.headers["Content-Type"], "model/gltf-binary")
            self.assertEqual(h.wfile.getvalue(), b"glTF model fixture")
            self.assertEqual(h.headers["Cache-Control"], "no-cache")

    def test_new_model_still_requires_operator_auth(self):
        h = Handler(authorized=False)
        self.assertTrue(handle(h, urlparse("/api/operator/motion-studio/models/gosha.glb?v=208bb99d")))
        self.assertEqual(h.wfile.getvalue(), b"")
        self.assertIsNone(h.status)


if __name__ == "__main__":
    unittest.main()
