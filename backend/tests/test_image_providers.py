import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from infrastructure.image_providers import ComfyUIImageProvider, ImageRequest


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1) -> bytes:
        return self.body


class ComfyUIImageProviderTests(unittest.TestCase):
    def test_generates_and_downloads_comfyui_result(self) -> None:
        png = io.BytesIO()
        Image.new("RGB", (128, 128), "navy").save(png, "PNG")
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            if request.full_url.endswith("/prompt"):
                payload = json.loads(request.data)
                self.assertEqual(payload["prompt"]["1"]["inputs"]["ckpt_name"], "model.safetensors")
                self.assertEqual(payload["prompt"]["4"]["inputs"]["width"], 576)
                self.assertEqual(payload["prompt"]["4"]["inputs"]["height"], 1024)
                return _Response(b'{"prompt_id":"job-1"}')
            if "/history/job-1" in request.full_url:
                return _Response(json.dumps({"job-1": {"outputs": {"7": {"images": [{
                    "filename": "VidTrans_00001_.png", "subfolder": "", "type": "output"
                }]}}}}).encode())
            if "/view?" in request.full_url:
                return _Response(png.getvalue())
            raise AssertionError(request.full_url)

        provider = ComfyUIImageProvider(
            base_url="http://comfy:8188", checkpoint="model.safetensors", opener=opener, sleeper=lambda _: None
        )
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "scene.png"
            provider.generate(ImageRequest(prompt="forest", width=1080, height=1920, seed=42), output)
            with Image.open(output) as result:
                self.assertEqual(result.size, (1080, 1920))
        self.assertEqual(len(requests), 3)

    def test_workflow_uses_deterministic_seed_when_zero(self) -> None:
        provider = ComfyUIImageProvider(base_url="http://comfy", checkpoint="x.safetensors")
        request = ImageRequest(prompt="same prompt", width=1280, height=720)
        first = provider._workflow(request)
        second = provider._workflow(request)
        self.assertEqual(first["5"]["inputs"]["seed"], second["5"]["inputs"]["seed"])
        self.assertEqual((first["4"]["inputs"]["width"], first["4"]["inputs"]["height"]), (1024, 576))


if __name__ == "__main__":
    unittest.main()
