"""
Tests for asset resolver.
"""
import pytest
import base64
from universal_video_ai.content_os.asset_resolver import AssetResolver, Asset, AssetManifest, AssetType, AssetSource


@pytest.fixture
def temp_db(tmp_path):
    """Temporary database path."""
    return str(tmp_path / "test.db")


@pytest.fixture
def repo(temp_db):
    """Repository instance with initialized schema."""
    from universal_video_ai.web.store import Store
    Store(db_path=temp_db)
    from universal_video_ai.content_os.repository import ContentOSRepository
    return ContentOSRepository(temp_db)


@pytest.fixture
def resolver(repo):
    """Asset resolver instance."""
    return AssetResolver(repo)


class TestAssetResolver:
    """Test asset resolver."""
    
    def test_resolve_asset_generates_local_visual(self, resolver):
        """Test resolving an image produces a usable local visual."""
        asset = resolver.resolve_asset(
            run_id=1,
            user_id=1,
            asset_type=AssetType.IMAGE,
            description="A beautiful sunset",
            # Keep this unit test deterministic even when the build machine
            # has Pexels/Pixabay credentials configured.
            preferred_sources=[AssetSource.GENERATED],
        )
        
        assert asset is not None
        assert asset.asset_type == AssetType.IMAGE
        assert asset.source == AssetSource.GENERATED
        assert asset.local_path is not None
        assert asset.metadata.get("generated") is True
        assert asset.metadata.get("generator") in {"local_procedural_visual", "ai_image"}
        assert asset.metadata.get("generator") != "local_pil_card"
    
    def test_resolve_video_asset(self, resolver):
        """Test resolving video asset."""
        asset = resolver.resolve_asset(
            run_id=1,
            user_id=1,
            asset_type=AssetType.VIDEO,
            description="Cooking process",
        )
        
        assert asset is not None
        assert asset.asset_type == AssetType.VIDEO
        assert asset.duration_seconds is not None
        assert asset.resolution is not None

    def test_generate_gemini_image_from_inline_data(self, resolver, tmp_path, monkeypatch):
        """Gemini image provider should save inlineData bytes when configured."""
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            + b"0" * 2048
        )

        class FakeResponse:
            status_code = 200
            text = ""
            ok = True

            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "inlineData": {
                                            "mimeType": "image/png",
                                            "data": base64.b64encode(png_bytes).decode("ascii"),
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }

        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return FakeResponse()

        import requests

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(requests, "post", fake_post)

        output_path = tmp_path / "gemini.png"
        assert resolver._generate_gemini_image(output_path, "Vertical 9:16 test prompt") is True
        assert output_path.read_bytes() == png_bytes
        assert captured["url"].endswith("/v1beta/interactions")
        assert captured["json"]["response_format"]["mime_type"] == "image/jpeg"
        assert captured["json"]["response_format"]["aspect_ratio"] == "9:16"

    def test_generate_gemini_image_generate_content_uses_enum_aspect_ratio(self, resolver, tmp_path, monkeypatch):
        """generateContent fallback must use enum aspect ratio, not raw 9:16."""
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"1" * 2048

        class FakeResponse:
            def __init__(self, status_code, payload=None, text=""):
                self.status_code = status_code
                self._payload = payload or {}
                self.text = text
                self.ok = 200 <= status_code < 300

            def raise_for_status(self):
                pass

            def json(self):
                return self._payload

        calls = []

        def fake_post(url, **kwargs):
            calls.append((url, kwargs.get("json")))
            if url.endswith("/v1beta/interactions"):
                return FakeResponse(404, text="not found")
            return FakeResponse(
                200,
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "inlineData": {
                                            "mimeType": "image/png",
                                            "data": base64.b64encode(png_bytes).decode("ascii"),
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                },
            )

        import requests

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(requests, "post", fake_post)

        output_path = tmp_path / "gemini_enum.png"
        assert resolver._generate_gemini_image(output_path, "Vertical 9:16 test prompt") is True
        generate_payload = calls[-1][1]
        image_format = generate_payload["generationConfig"]["responseFormat"]["image"]
        assert image_format["mimeType"] == "IMAGE_JPEG"
        assert image_format["delivery"] == "INLINE"
        assert image_format["aspectRatio"] == "ASPECT_RATIO_NINE_BY_SIXTEEN"
        assert "imageSize" not in image_format
    
    def test_create_manifest(self, resolver, repo):
        """Test creating asset manifest."""
        assets = [
            Asset(
                asset_id="asset1",
                asset_type=AssetType.IMAGE,
                source=AssetSource.LOCAL,
                url="https://example.com/image1.jpg",
                local_path=None,
                metadata={},
                license_info="MIT",
                duration_seconds=None,
                resolution="1920x1080",
                file_size_bytes=102400,
                created_at=1234567890.0,
            ),
            Asset(
                asset_id="asset2",
                asset_type=AssetType.VIDEO,
                source=AssetSource.STOCK_API,
                url="https://example.com/video1.mp4",
                local_path=None,
                metadata={},
                license_info="Commercial",
                duration_seconds=5.0,
                resolution="1920x1080",
                file_size_bytes=512000,
                created_at=1234567891.0,
            ),
        ]
        
        manifest = resolver.create_manifest(run_id=1, user_id=1, assets=assets)
        
        assert manifest.run_id == 1
        assert manifest.user_id == 1
        assert len(manifest.assets) == 2
        assert manifest.total_size_bytes == 614400
    
    def test_get_manifest(self, resolver, repo):
        """Test retrieving asset manifest."""
        assets = [
            Asset(
                asset_id="asset1",
                asset_type=AssetType.IMAGE,
                source=AssetSource.LOCAL,
                url="https://example.com/image1.jpg",
                local_path=None,
                metadata={},
                license_info="MIT",
                duration_seconds=None,
                resolution="1920x1080",
                file_size_bytes=102400,
                created_at=1234567890.0,
            ),
        ]
        
        resolver.create_manifest(run_id=1, user_id=1, assets=assets)
        
        retrieved = resolver.get_manifest(run_id=1, user_id=1)
        
        assert retrieved is not None
        assert len(retrieved.assets) == 1
        assert retrieved.assets[0].asset_id == "asset1"
    
    def test_validate_asset(self, resolver):
        """Test asset validation."""
        # Valid asset
        valid_asset = Asset(
            asset_id="valid",
            asset_type=AssetType.IMAGE,
            source=AssetSource.LOCAL,
            url="https://example.com/image.jpg",
            local_path=None,
            metadata={},
            license_info="MIT",
            duration_seconds=None,
            resolution="1920x1080",
            file_size_bytes=102400,
            created_at=1234567890.0,
        )
        assert resolver._validate_asset(valid_asset) is True
        
        # Invalid asset (no URL or path)
        invalid_asset = Asset(
            asset_id="invalid",
            asset_type=AssetType.IMAGE,
            source=AssetSource.LOCAL,
            url=None,
            local_path=None,
            metadata={},
            license_info="MIT",
            duration_seconds=None,
            resolution="1920x1080",
            file_size_bytes=102400,
            created_at=1234567890.0,
        )
        assert resolver._validate_asset(invalid_asset) is False
