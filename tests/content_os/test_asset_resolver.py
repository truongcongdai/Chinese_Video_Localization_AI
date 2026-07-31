"""
Tests for asset resolver.
"""
import pytest
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
    
    def test_resolve_asset_with_fallback(self, resolver):
        """Test resolving asset falls back to placeholder."""
        asset = resolver.resolve_asset(
            run_id=1,
            user_id=1,
            asset_type=AssetType.IMAGE,
            description="A beautiful sunset",
        )
        
        assert asset is not None
        assert asset.asset_type == AssetType.IMAGE
        assert asset.source == AssetSource.GENERATED
        assert asset.metadata.get("fallback") is True
    
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
