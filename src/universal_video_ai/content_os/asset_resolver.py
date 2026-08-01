"""
Asset resolver for Content OS.

Manages asset discovery, validation, and fallback for video production.
"""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum
import time


class AssetType(str, Enum):
    """Types of assets."""
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    STOCK_FOOTAGE = "stock_footage"
    STOCK_IMAGE = "stock_image"


class AssetSource(str, Enum):
    """Asset sources."""
    LOCAL = "local"
    STOCK_API = "stock_api"
    GENERATED = "generated"
    USER_PROVIDED = "user_provided"


@dataclass
class Asset:
    """An asset for video production."""
    asset_id: str
    asset_type: AssetType
    source: AssetSource
    url: str
    local_path: Optional[str]
    metadata: Dict[str, Any]
    license_info: str
    duration_seconds: Optional[float]
    resolution: Optional[str]
    file_size_bytes: Optional[int]
    created_at: float


@dataclass
class AssetManifest:
    """Manifest of all assets for a run."""
    run_id: int
    user_id: int
    assets: List[Asset]
    total_size_bytes: int
    created_at: float
    updated_at: float


class AssetResolver:
    """
    Resolves and manages assets for video production.
    
    Features:
    - Asset discovery from multiple sources
    - Validation and quality checks
    - Fallback mechanisms
    - License tracking
    """
    
    def __init__(self, repository):
        self.repository = repository
    
    def resolve_asset(
        self,
        run_id: int,
        user_id: int,
        asset_type: AssetType,
        description: str,
        preferred_sources: List[AssetSource] = None,
    ) -> Optional[Asset]:
        """
        Resolve an asset based on description and type.
        
        Args:
            run_id: Run ID
            user_id: User ID
            asset_type: Type of asset needed
            description: Description of what's needed
            preferred_sources: Preferred sources to check first
        
        Returns:
            Resolved asset or None
        """
        if preferred_sources is None:
            preferred_sources = [AssetSource.LOCAL, AssetSource.STOCK_API, AssetSource.GENERATED]
        
        for source in preferred_sources:
            asset = self._try_source(source, asset_type, description)
            if asset:
                # Validate asset
                if self._validate_asset(asset):
                    return asset
        
        # Fallback to default placeholder
        return self._get_fallback_asset(asset_type)
    
    def _try_source(
        self, source: AssetSource, asset_type: AssetType, description: str
    ) -> Optional[Asset]:
        """Try to get asset from a specific source."""
        if source == AssetSource.LOCAL:
            return self._search_local_assets(asset_type, description)
        elif source == AssetSource.STOCK_API:
            return self._search_stock_assets(asset_type, description)
        elif source == AssetSource.GENERATED:
            return self._generate_asset(asset_type, description)
        return None
    
    def _search_local_assets(
        self, asset_type: AssetType, description: str
    ) -> Optional[Asset]:
        """Search for local assets."""
        # In a real implementation, this would search local file system
        # For now, return None to trigger fallback
        return None
    
    def _search_stock_assets(
        self, asset_type: AssetType, description: str
    ) -> Optional[Asset]:
        """Search stock asset APIs."""
        # In a real implementation, this would call stock APIs like Pexels, Unsplash
        # For now, return None to trigger fallback
        return None
    
    def _generate_asset(
        self, asset_type: AssetType, description: str
    ) -> Optional[Asset]:
        """Generate an asset using FFmpeg text cards."""
        import subprocess
        from pathlib import Path
        
        if asset_type != AssetType.IMAGE:
            return None
        
        # Create output directory
        output_dir = Path("local_data/content_os/generated_assets")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        asset_id = f"generated_{int(time.time())}"
        output_path = output_dir / f"{asset_id}.png"
        
        # Create a text card using FFmpeg
        # Truncate description to fit
        text = description[:100] if len(description) > 100 else description
        
        cmd = [
            "ffmpeg",
            "-f", "lavfi",
            "-i", f"color=c=#2d3436:s=1080x1920:d=1:r=30",
            "-vf", f"drawtext=text='{text}':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=#0984e3@0.3:boxborderw=10",
            "-frames:v", "1",
            "-y",
            str(output_path)
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and output_path.exists():
                return Asset(
                    asset_id=asset_id,
                    asset_type=asset_type,
                    source=AssetSource.GENERATED,
                    url=str(output_path),
                    local_path=str(output_path),
                    metadata={"description": description, "generated": True},
                    license_info="Generated by system",
                    duration_seconds=None,
                    resolution="1080x1920",
                    file_size_bytes=output_path.stat().st_size,
                    created_at=time.time(),
                )
        except Exception as e:
            pass
        
        return None
    
    def _validate_asset(self, asset: Asset) -> bool:
        """Validate an asset meets quality requirements."""
        # Basic validation checks
        if not asset.url and not asset.local_path:
            return False
        
        if asset.asset_type == AssetType.VIDEO:
            if not asset.duration_seconds or asset.duration_seconds <= 0:
                return False
        
        if asset.asset_type in [AssetType.IMAGE, AssetType.VIDEO]:
            if not asset.resolution:
                return False
        
        return True
    
    def _get_fallback_asset(self, asset_type: AssetType) -> Asset:
        """Get a fallback placeholder asset."""
        return Asset(
            asset_id=f"fallback_{asset_type}_{int(time.time())}",
            asset_type=asset_type,
            source=AssetSource.GENERATED,
            url=f"https://placeholder.com/{asset_type}.png",
            local_path=None,
            metadata={"fallback": True, "description": "Placeholder asset"},
            license_info="Public domain placeholder",
            duration_seconds=5.0 if asset_type == AssetType.VIDEO else None,
            resolution="1920x1080" if asset_type in [AssetType.IMAGE, AssetType.VIDEO] else None,
            file_size_bytes=102400,
            created_at=time.time(),
        )
    
    def create_manifest(
        self, run_id: int, user_id: int, assets: List[Asset]
    ) -> AssetManifest:
        """
        Create an asset manifest for a run.
        
        Args:
            run_id: Run ID
            user_id: User ID
            assets: List of assets
        
        Returns:
            Asset manifest
        """
        total_size = sum(a.file_size_bytes or 0 for a in assets)
        
        manifest = AssetManifest(
            run_id=run_id,
            user_id=user_id,
            assets=assets,
            total_size_bytes=total_size,
            created_at=time.time(),
            updated_at=time.time(),
        )
        
        # Store as artifact
        self._store_manifest(manifest)
        
        return manifest
    
    def _store_manifest(self, manifest: AssetManifest):
        """Store manifest as artifact."""
        data = {
            "run_id": manifest.run_id,
            "user_id": manifest.user_id,
            "assets": [
                {
                    "asset_id": a.asset_id,
                    "asset_type": a.asset_type.value,
                    "source": a.source.value,
                    "url": a.url,
                    "local_path": a.local_path,
                    "metadata": a.metadata,
                    "license_info": a.license_info,
                    "duration_seconds": a.duration_seconds,
                    "resolution": a.resolution,
                    "file_size_bytes": a.file_size_bytes,
                    "created_at": a.created_at,
                }
                for a in manifest.assets
            ],
            "total_size_bytes": manifest.total_size_bytes,
            "created_at": manifest.created_at,
            "updated_at": manifest.updated_at,
        }
        
        self.repository.create_artifact(
            run_id=manifest.run_id,
            user_id=manifest.user_id,
            artifact_type="asset_manifest",
            version=1,
            schema_version="1.0",
            path=f"/manifests/{manifest.run_id}.json",
            checksum="",
            metadata=data,
            created_by_agent="AssetResolver",
        )
    
    def get_manifest(
        self, run_id: int, user_id: int
    ) -> Optional[AssetManifest]:
        """Get asset manifest for a run."""
        artifacts = self.repository.list_artifacts(run_id)
        
        for artifact in artifacts:
            if artifact.artifact_type == "asset_manifest":
                try:
                    data = artifact.metadata if hasattr(artifact, 'metadata') else {}
                    if data:
                        # Convert asset dicts back to Asset objects
                        asset_data = data.get("assets", [])
                        assets = [
                            Asset(
                                asset_id=a["asset_id"],
                                asset_type=AssetType(a["asset_type"]),
                                source=AssetSource(a["source"]),
                                url=a["url"],
                                local_path=a["local_path"],
                                metadata=a["metadata"],
                                license_info=a["license_info"],
                                duration_seconds=a["duration_seconds"],
                                resolution=a["resolution"],
                                file_size_bytes=a["file_size_bytes"],
                                created_at=a["created_at"],
                            )
                            for a in asset_data
                        ]
                        data["assets"] = assets
                        return AssetManifest(**data)
                except (TypeError, KeyError, ValueError):
                    continue
        
        return None
