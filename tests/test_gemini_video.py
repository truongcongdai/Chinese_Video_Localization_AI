import base64

import pytest

from universal_video_ai.gemini_video import (
    GeminiVideoGenerationError,
    _generate_with_veo,
    generate_gemini_video,
)


def test_gemini_omni_writes_inline_video_and_preserves_portrait_prompt(tmp_path, monkeypatch):
    video_bytes = b"video" * 1200
    captured = {}

    class Response:
        ok = True
        status_code = 200
        headers = {}
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "steps": [
                    {
                        "type": "model_output",
                        "content": [
                            {
                                "type": "video",
                                "mime_type": "video/mp4",
                                "data": base64.b64encode(video_bytes).decode("ascii"),
                            }
                        ],
                    }
                ]
            }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return Response()

    monkeypatch.setattr("universal_video_ai.gemini_video.requests.post", fake_post)
    output = tmp_path / "scene.mp4"

    assert generate_gemini_video(
        output,
        "A red umbrella opening in heavy rain",
        "test-key",
        aspect_ratio="9:16",
        model="gemini-omni-flash-preview",
    )
    assert output.read_bytes() == video_bytes
    assert captured["url"].endswith("/v1beta/interactions")
    assert captured["json"]["input"] == "A red umbrella opening in heavy rain"
    assert captured["json"]["response_format"]["type"] == "video"
    assert captured["json"]["response_format"]["aspect_ratio"] == "9:16"
    assert captured["json"]["response_format"]["duration"] == "8s"


def test_content_os_video_prompt_keeps_exact_scene_semantics(tmp_path):
    from universal_video_ai.content_os.asset_resolver import AssetResolver

    prompt = AssetResolver(repository=None)._video_prompt(
        "A baker removes three baguettes from a stone oven"
    )

    assert "baker removes three baguettes" in prompt
    assert "SEMANTIC ANCHOR" in prompt
    assert "do not replace them" in prompt


def test_gemini_omni_sends_uploaded_product_image_as_reference(tmp_path, monkeypatch):
    product_image = tmp_path / "product.png"
    product_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"p" * 128)
    video_bytes = b"video" * 1200
    captured = {}

    class Response:
        ok = True
        status_code = 200
        headers = {}
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "steps": [{
                    "type": "model_output",
                    "content": [{
                        "type": "video",
                        "mime_type": "video/mp4",
                        "data": base64.b64encode(video_bytes).decode("ascii"),
                    }],
                }]
            }

    def fake_post(url, **kwargs):
        captured["json"] = kwargs["json"]
        return Response()

    monkeypatch.setattr("universal_video_ai.gemini_video.requests.post", fake_post)

    assert generate_gemini_video(
        tmp_path / "product_scene.mp4",
        "A creator demonstrates this exact product",
        "test-key",
        reference_images=[product_image],
    )
    assert captured["json"]["input"][0]["type"] == "image"
    assert captured["json"]["input"][0]["mime_type"] == "image/png"
    assert captured["json"]["input"][1]["text"].startswith("A creator")
    assert captured["json"]["generation_config"]["video_config"]["task"] == "reference_to_video"


def test_gemini_omni_retries_429_then_succeeds(tmp_path, monkeypatch):
    video_bytes = b"video" * 1200
    calls = []
    sleeps = []

    class RateLimitedResponse:
        status_code = 429
        headers = {"Retry-After": "1"}
        text = '{"error":{"message":"quota exceeded"}}'

    class SuccessResponse:
        status_code = 200
        headers = {}
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "output": {
                    "type": "video",
                    "mime_type": "video/mp4",
                    "data": base64.b64encode(video_bytes).decode("ascii"),
                }
            }

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        return RateLimitedResponse() if len(calls) == 1 else SuccessResponse()

    monkeypatch.setenv("GEMINI_VIDEO_MAX_RETRIES", "2")
    monkeypatch.setattr("universal_video_ai.gemini_video.requests.post", fake_post)
    monkeypatch.setattr("universal_video_ai.gemini_video.time.sleep", sleeps.append)

    assert generate_gemini_video(tmp_path / "retry.mp4", "Exact scene", "test-key")
    assert len(calls) == 2
    assert sleeps == [1.0]


def test_gemini_video_strict_error_exposes_quota_failure(tmp_path, monkeypatch):
    class RateLimitedResponse:
        status_code = 429
        headers = {}
        text = '{"error":{"message":"quota exceeded"}}'

    monkeypatch.setenv("GEMINI_VIDEO_MAX_RETRIES", "0")
    monkeypatch.setenv("GEMINI_VEO_FALLBACK_MODEL", "gemini-omni-flash-preview")
    monkeypatch.setattr(
        "universal_video_ai.gemini_video.requests.post",
        lambda *args, **kwargs: RateLimitedResponse(),
    )

    with pytest.raises(GeminiVideoGenerationError, match="429"):
        generate_gemini_video(
            tmp_path / "failed.mp4",
            "Exact scene",
            "test-key",
            raise_on_error=True,
        )


def test_free_tier_limit_zero_does_not_retry_or_call_veo(tmp_path, monkeypatch):
    calls = []

    class FreeTierResponse:
        status_code = 429
        headers = {}
        text = "Quota exceeded for generate_content_free_tier_requests, limit: 0"

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        return FreeTierResponse()

    monkeypatch.setenv("GEMINI_VIDEO_MAX_RETRIES", "2")
    monkeypatch.setattr("universal_video_ai.gemini_video.requests.post", fake_post)
    monkeypatch.setattr(
        "universal_video_ai.gemini_video._generate_with_veo",
        lambda *args, **kwargs: pytest.fail("Veo must not be called without paid-tier quota"),
    )

    with pytest.raises(GeminiVideoGenerationError, match="paid tier"):
        generate_gemini_video(
            tmp_path / "free-tier.mp4",
            "Exact scene",
            "test-key",
            raise_on_error=True,
        )
    assert len(calls) == 1


def test_gemini_uri_delivery_extracts_file_id_and_downloads(tmp_path, monkeypatch):
    video_bytes = b"video" * 1200
    get_urls = []

    class CreateResponse:
        status_code = 200
        headers = {}
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "steps": [{
                    "type": "model_output",
                    "content": [{
                        "type": "video",
                        "mime_type": "video/mp4",
                        "uri": "https://generativelanguage.googleapis.com/v1beta/files/file-123:download?alt=media",
                    }],
                }]
            }

    class GetResponse:
        def __init__(self, metadata=False):
            self.ok = True
            self.content = b"" if metadata else video_bytes

        def json(self):
            return {"state": "ACTIVE"}

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        get_urls.append(url)
        return GetResponse(metadata=not url.endswith(":download"))

    monkeypatch.setattr(
        "universal_video_ai.gemini_video.requests.post",
        lambda *args, **kwargs: CreateResponse(),
    )
    monkeypatch.setattr("universal_video_ai.gemini_video.requests.get", fake_get)

    output = tmp_path / "uri.mp4"
    assert generate_gemini_video(output, "Exact scene", "test-key")
    assert output.read_bytes() == video_bytes
    assert get_urls[0].endswith("/files/file-123")
    assert get_urls[1].endswith("/files/file-123:download")


def test_veo_31_request_omits_unsupported_enhance_prompt(tmp_path, monkeypatch):
    captured = {}

    class FakeVideo:
        pass

    class FakeOperation:
        done = True
        response = type(
            "Response",
            (),
            {"generated_videos": [type("Generated", (), {"video": FakeVideo()})()]},
        )()

    class FakeFiles:
        def download(self, file):
            return b"video" * 1200

    class FakeModels:
        def generate_videos(self, **kwargs):
            captured["config"] = kwargs["config"]
            return FakeOperation()

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = FakeModels()
            self.files = FakeFiles()

    monkeypatch.setattr("google.genai.Client", FakeClient)
    output = tmp_path / "veo.mp4"

    assert _generate_with_veo(
        output,
        "A precise scene",
        "test-key",
        "veo-3.1-generate-preview",
        "9:16",
        8,
        30,
        0.01,
    )
    assert captured["config"].enhance_prompt is None
    assert output.stat().st_size > 4096
