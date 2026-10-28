import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from PIL import Image

from infrastructure.image_providers import (
    CloudflareImageProvider, ComfyUIImageProvider, ImageGenerationError, ImageRequest, PollinationsImageProvider,
)


class _Response:
    def __init__(self, body: bytes, content_type: str = "") -> None:
        self.body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1) -> bytes:
        return self.body


class ComfyUIImageProviderTests(unittest.TestCase):
    def test_requires_url_and_checkpoint(self) -> None:
        with self.assertRaisesRegex(ImageGenerationError, "VIDTRANS_COMFYUI_URL"):
            ComfyUIImageProvider(base_url="", checkpoint="")

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
            base_url="http://comfy:8188", checkpoint="model.safetensors", max_side=1024,
            opener=opener, sleeper=lambda _: None
        )
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "scene.png"
            provider.generate(ImageRequest(prompt="forest", width=1080, height=1920, seed=42), output)
            with Image.open(output) as result:
                self.assertEqual(result.size, (1080, 1920))
        self.assertEqual(len(requests), 3)

    def test_workflow_uses_deterministic_seed_when_zero(self) -> None:
        provider = ComfyUIImageProvider(base_url="http://comfy", checkpoint="x.safetensors", max_side=1024)
        request = ImageRequest(prompt="same prompt", width=1280, height=720)
        first = provider._workflow(request)
        second = provider._workflow(request)
        self.assertEqual(first["5"]["inputs"]["seed"], second["5"]["inputs"]["seed"])
        self.assertEqual((first["4"]["inputs"]["width"], first["4"]["inputs"]["height"]), (1024, 576))

    def test_reference_uses_ipadapter_workflow(self) -> None:
        provider = ComfyUIImageProvider(base_url="http://comfy", checkpoint="x.safetensors")
        workflow = provider._workflow(
            ImageRequest(prompt="xianxia swordswoman", width=1080, height=1920),
            "vidtrans-hero.png",
        )
        self.assertEqual(workflow["8"]["class_type"], "IPAdapterUnifiedLoader")
        self.assertEqual(workflow["8"]["inputs"]["preset"], "STANDARD (medium strength)")
        self.assertEqual(workflow["9"]["inputs"]["image"], "vidtrans-hero.png")
        self.assertEqual(workflow["5"]["inputs"]["model"], ["10", 0])

    def test_uploads_first_reference_before_queueing(self) -> None:
        png = io.BytesIO()
        Image.new("RGB", (64, 64), "purple").save(png, "PNG")
        prompt_payloads = []

        def opener(request, timeout):
            del timeout
            if request.full_url.endswith("/upload/image"):
                self.assertIn(b'filename="vidtrans-', request.data)
                return _Response(b'{"name":"uploaded.png","subfolder":"","type":"input"}')
            if request.full_url.endswith("/prompt"):
                prompt_payloads.append(json.loads(request.data))
                return _Response(b'{"prompt_id":"job-ref"}')
            if "/history/job-ref" in request.full_url:
                return _Response(json.dumps({"job-ref": {"outputs": {"7": {"images": [{
                    "filename": "result.png", "subfolder": "", "type": "output"
                }]}}}}).encode())
            if "/view?" in request.full_url:
                return _Response(png.getvalue())
            raise AssertionError(request.full_url)

        provider = ComfyUIImageProvider(
            base_url="http://comfy", checkpoint="x.safetensors", opener=opener, sleeper=lambda _: None
        )
        with tempfile.TemporaryDirectory() as folder:
            reference = Path(folder) / "hero.png"
            reference.write_bytes(png.getvalue())
            provider.generate(
                ImageRequest(prompt="hero", width=512, height=512, references=(reference,)),
                Path(folder) / "scene.png",
            )
        self.assertEqual(prompt_payloads[0]["prompt"]["9"]["inputs"]["image"], "uploaded.png")

    def test_connection_error_marks_provider_unavailable(self) -> None:
        calls = []

        def opener(_request, timeout):
            del timeout
            calls.append(1)
            raise urllib.error.URLError("offline")

        provider = ComfyUIImageProvider(
            base_url="http://comfy", checkpoint="model.safetensors", opener=opener, sleeper=lambda _: None
        )
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ImageGenerationError) as raised:
            provider.generate(ImageRequest(prompt="forest", width=512, height=512), Path(folder) / "scene.png")
        self.assertTrue(raised.exception.unavailable)
        self.assertIn("Không kết nối được ComfyUI", str(raised.exception))
        self.assertEqual(len(calls), provider.connect_attempts)

    def test_cancelled_job_removes_its_running_comfyui_prompt(self) -> None:
        posted = []

        def opener(request, timeout):
            del timeout
            path = request.full_url.removeprefix("http://comfy")
            if request.data is not None:
                posted.append((path, json.loads(request.data)))
            if path == "/prompt":
                return _Response(json.dumps({"prompt_id": "p1"}).encode())
            if path == "/queue" and request.data is None:
                return _Response(json.dumps({"queue_running": [[0, "p1", {}]], "queue_pending": []}).encode())
            return _Response(b"")

        def cancelled() -> None:
            raise RuntimeError("cancelled by user")

        provider = ComfyUIImageProvider(
            base_url="http://comfy", checkpoint="model.safetensors", opener=opener, sleeper=lambda _: None,
        )
        provider.check_active = cancelled
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(RuntimeError):
            provider.generate(ImageRequest(prompt="forest", width=512, height=512), Path(folder) / "scene.png")
        self.assertEqual([path for path, _ in posted], ["/prompt", "/queue", "/interrupt"])
        self.assertEqual(posted[1][1], {"delete": ["p1"]})

    def test_dropped_connection_is_retried_instead_of_marking_provider_down(self) -> None:
        failures = [ConnectionResetError("reset")]

        def opener(request, timeout):
            del timeout
            if request.full_url.endswith("/prompt"):
                if failures:
                    raise failures.pop()
                return _Response(json.dumps({"prompt_id": "p1"}).encode())
            raise urllib.error.HTTPError(request.full_url, 500, "stop", {}, io.BytesIO(b"boom"))

        provider = ComfyUIImageProvider(
            base_url="http://comfy", checkpoint="model.safetensors", opener=opener, sleeper=lambda _: None
        )
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ImageGenerationError) as raised:
            provider.generate(ImageRequest(prompt="forest", width=512, height=512), Path(folder) / "scene.png")
        # The reset /prompt was retried and reached /history; that HTTP error is not "unavailable".
        self.assertFalse(getattr(raised.exception, "unavailable", False))
        self.assertIn("HTTP 500", str(raised.exception))

    def test_history_error_is_reported_without_downloading(self) -> None:
        calls = []

        def opener(request, timeout):
            del timeout
            calls.append(request.full_url)
            if request.full_url.endswith("/prompt"):
                return _Response(b'{"prompt_id":"job-error"}')
            return _Response(json.dumps({
                "job-error": {"status": {"status_str": "error", "messages": [["execution_error", "bad model"]]}}
            }).encode())

        provider = ComfyUIImageProvider(
            base_url="http://comfy", checkpoint="bad.safetensors", opener=opener, sleeper=lambda _: None
        )
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(
            ImageGenerationError, "sinh ảnh thất bại"
        ):
            provider.generate(ImageRequest(prompt="forest", width=512, height=512), Path(folder) / "scene.png")
        self.assertEqual(len(calls), 2)

    def test_poll_timeout_is_bounded(self) -> None:
        def opener(request, timeout):
            del timeout
            if request.full_url.endswith("/prompt"):
                return _Response(b'{"prompt_id":"job-slow"}')
            raise AssertionError("history must not be polled after a zero-second deadline")

        provider = ComfyUIImageProvider(
            base_url="http://comfy", checkpoint="model.safetensors", timeout=0, opener=opener
        )
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ImageGenerationError, "quá thời gian"):
            provider.generate(ImageRequest(prompt="forest", width=512, height=512), Path(folder) / "scene.png")


def _png(size=(256, 256)) -> bytes:
    data = io.BytesIO()
    Image.new("RGB", size, "teal").save(data, "PNG")
    return data.getvalue()


class CloudflareImageProviderTests(unittest.TestCase):
    def test_requires_account_and_token(self) -> None:
        with self.assertRaisesRegex(ImageGenerationError, "CLOUDFLARE_API_TOKEN"):
            CloudflareImageProvider(account_id="", api_token="")

    def test_flux_decodes_json_image_and_fits_scene(self) -> None:
        import base64
        seen = []

        def opener(request, timeout):
            seen.append(request)
            body = json.dumps({"success": True, "result": {"image": base64.b64encode(_png()).decode()}})
            return _Response(body.encode(), "application/json")

        provider = CloudflareImageProvider(account_id="acc", api_token="tok", opener=opener, sleeper=lambda _: None,
                                           model="@cf/black-forest-labs/flux-1-schnell", steps=4)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "scene.png"
            provider.generate(ImageRequest(prompt="forest", width=1080, height=1920, seed=7), output)
            with Image.open(output) as result:
                self.assertEqual(result.size, (1080, 1920))
        request = seen[0]
        self.assertTrue(request.full_url.endswith("/accounts/acc/ai/run/@cf/black-forest-labs/flux-1-schnell"))
        self.assertEqual(request.get_header("Authorization"), "Bearer tok")
        payload = json.loads(request.data)
        self.assertEqual((payload["steps"], payload["seed"]), (4, 7))
        self.assertIn("vertical 9:16 composition", payload["prompt"])

    def test_sdxl_sends_size_and_reads_raw_png(self) -> None:
        def opener(request, timeout):
            payload = json.loads(request.data)
            self.assertEqual((payload["width"], payload["height"]), (576, 1024))
            return _Response(_png((576, 1024)), "image/png")

        provider = CloudflareImageProvider(account_id="a", api_token="t", opener=opener, sleeper=lambda _: None,
                                           model="@cf/stabilityai/stable-diffusion-xl-base-1.0")
        with tempfile.TemporaryDirectory() as folder:
            provider.generate(ImageRequest(prompt="x", width=1080, height=1920), Path(folder) / "s.png")

    def test_exhausted_daily_quota_marks_provider_unavailable(self) -> None:
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, io.BytesIO(b"{}"))

        provider = CloudflareImageProvider(account_id="a", api_token="t", opener=opener, sleeper=lambda _: None,
                                           attempts=2)
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ImageGenerationError) as caught:
            provider.generate(ImageRequest(prompt="x", width=512, height=512), Path(folder) / "s.png")
        self.assertTrue(caught.exception.unavailable)
        self.assertTrue(caught.exception.rate_limited)

    def test_bad_token_fails_without_retry(self) -> None:
        calls = []

        def opener(request, timeout):
            calls.append(1)
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"bad token"))

        provider = CloudflareImageProvider(account_id="a", api_token="t", opener=opener, sleeper=lambda _: None)
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ImageGenerationError) as caught:
            provider.generate(ImageRequest(prompt="x", width=512, height=512), Path(folder) / "s.png")
        self.assertTrue(caught.exception.unavailable)
        self.assertEqual(len(calls), 1)


class PollinationsImageProviderTests(unittest.TestCase):
    def test_builds_url_and_saves_scene(self) -> None:
        seen = []

        def opener(request, timeout):
            seen.append(request)
            return _Response(_png((576, 1024)), "image/jpeg")

        provider = PollinationsImageProvider(opener=opener, sleeper=lambda _: None, token="")
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "scene.png"
            provider.generate(ImageRequest(prompt="a cat / samurai", width=1080, height=1920, seed=3), output)
            with Image.open(output) as result:
                self.assertEqual(result.size, (1080, 1920))
        url = seen[0].full_url
        self.assertIn("/prompt/a%20cat%20%2F%20samurai?", url)
        self.assertIn("width=576", url)
        self.assertIn("height=1024", url)
        self.assertIn("seed=3", url)
        self.assertIsNone(seen[0].get_header("Authorization"))

    def test_token_is_sent_and_server_errors_are_retried(self) -> None:
        calls = []

        def opener(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 502, "Bad Gateway", {}, io.BytesIO(b""))
            return _Response(_png(), "image/jpeg")

        provider = PollinationsImageProvider(opener=opener, sleeper=lambda _: None, token="sk")
        with tempfile.TemporaryDirectory() as folder:
            provider.generate(ImageRequest(prompt="x", width=512, height=512), Path(folder) / "s.png")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].get_header("Authorization"), "Bearer sk")


if __name__ == "__main__":
    unittest.main()
