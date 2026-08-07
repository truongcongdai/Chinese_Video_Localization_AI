"""
Content OS artifact store.

Manages versioned artifact storage with atomic writes and checksums.
"""
import hashlib
import json
import logging
import tempfile
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime
from .enums import ArtifactType
from .exceptions import ArtifactNotFoundError, ArtifactValidationError

logger = logging.getLogger(__name__)


class ArtifactStore:
    """
    Manages versioned artifact storage.
    
    Artifacts are stored as:
    local_data/content_os/<user_id>/<project_id>/<run_id>/<artifact_type>.v<version>.json
    
    Each artifact includes:
    - Version number (incrementing)
    - Schema version
    - Checksum (SHA256)
    - Creating agent
    - Creation timestamp
    - Metadata
    """
    
    SCHEMA_VERSION = "1.0"
    
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_run_dir(self, user_id: int, project_id: int, run_id: int) -> Path:
        """Get the directory for a specific run."""
        run_dir = self.base_dir / str(user_id) / str(project_id) / str(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir
    
    def _get_artifact_path(
        self,
        user_id: int,
        project_id: int,
        run_id: int,
        artifact_type: ArtifactType,
        version: int,
    ) -> Path:
        """Get the path for a specific artifact version."""
        run_dir = self._get_run_dir(user_id, project_id, run_id)
        filename = f"{artifact_type.value}.v{version}.json"
        return run_dir / filename
    
    def _get_next_version(
        self,
        user_id: int,
        project_id: int,
        run_id: int,
        artifact_type: ArtifactType,
    ) -> int:
        """Get the next version number for an artifact type."""
        run_dir = self._get_run_dir(user_id, project_id, run_id)
        pattern = f"{artifact_type.value}.v*.json"
        existing = list(run_dir.glob(pattern))
        if not existing:
            return 1
        # Extract version numbers and find max
        versions = []
        for path in existing:
            try:
                # Extract version from filename like "script.v3.json"
                stem = path.stem  # "script.v3"
                version_str = stem.split('.v')[-1]
                versions.append(int(version_str))
            except (ValueError, IndexError):
                continue
        return max(versions) + 1 if versions else 1
    
    def _calculate_checksum(self, data: bytes) -> str:
        """Calculate SHA256 checksum."""
        return hashlib.sha256(data).hexdigest()
    
    def write(
        self,
        user_id: int,
        project_id: int,
        run_id: int,
        artifact_type: ArtifactType,
        data: Dict[str, Any],
        created_by_agent: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Write an artifact atomically.
        
        Args:
            user_id: User ID
            project_id: Project ID
            run_id: Run ID
            artifact_type: Type of artifact
            data: Artifact data (will be JSON serialized)
            created_by_agent: Name of agent that created this
            metadata: Optional metadata dictionary
            
        Returns:
            Dictionary with artifact info (path, version, checksum, etc.)
        """
        metadata = metadata or {}
        version = self._get_next_version(user_id, project_id, run_id, artifact_type)
        target_path = self._get_artifact_path(user_id, project_id, run_id, artifact_type, version)
        
        # Prepare artifact content
        artifact_content = {
            "schema_version": self.SCHEMA_VERSION,
            "artifact_type": artifact_type.value,
            "version": version,
            "created_by_agent": created_by_agent,
            "created_at": datetime.utcnow().isoformat(),
            "metadata": metadata,
            "data": data,
        }
        
        # Serialize and calculate checksum
        json_str = json.dumps(artifact_content, ensure_ascii=False, indent=2)
        json_bytes = json_str.encode('utf-8')
        checksum = self._calculate_checksum(json_bytes)
        
        # Atomic write: write to temp file, then rename
        temp_path = target_path.with_suffix('.tmp')
        try:
            temp_path.write_bytes(json_bytes)
            temp_path.replace(target_path)
        except Exception as e:
            if temp_path.exists():
                temp_path.unlink()
            raise ArtifactValidationError(f"Failed to write artifact: {e}") from e
        
        logger.info(
            f"Wrote artifact {artifact_type.value} v{version} for run {run_id} "
            f"(checksum: {checksum[:16]}...)"
        )
        
        return {
            "path": str(target_path),
            "version": version,
            "checksum": checksum,
            "schema_version": self.SCHEMA_VERSION,
            "created_by_agent": created_by_agent,
            "created_at": artifact_content["created_at"],
        }
    
    def read(
        self,
        user_id: int,
        project_id: int,
        run_id: int,
        artifact_type: ArtifactType,
        version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Read an artifact.
        
        Args:
            user_id: User ID
            project_id: Project ID
            run_id: Run ID
            artifact_type: Type of artifact
            version: Version to read (None for latest)
            
        Returns:
            Artifact data dictionary
            
        Raises:
            ArtifactNotFoundError: If artifact doesn't exist
        """
        if version is None:
            version = self._get_next_version(user_id, project_id, run_id, artifact_type) - 1
        
        path = self._get_artifact_path(user_id, project_id, run_id, artifact_type, version)
        
        if not path.exists():
            raise ArtifactNotFoundError(
                f"Artifact {artifact_type.value} v{version} not found at {path}"
            )
        
        try:
            content = json.loads(path.read_text(encoding='utf-8'))
            # Validate checksum if present
            if 'checksum' in content:
                json_str = json.dumps(content, ensure_ascii=False, indent=2)
                json_bytes = json_str.encode('utf-8')
                calculated = self._calculate_checksum(json_bytes)
                if calculated != content['checksum']:
                    logger.warning(
                        f"Checksum mismatch for artifact {artifact_type.value} v{version}: "
                        f"expected {content['checksum']}, got {calculated}"
                    )
            return content
        except Exception as e:
            raise ArtifactValidationError(f"Failed to read artifact: {e}") from e
    
    def list_artifacts(
        self,
        user_id: int,
        project_id: int,
        run_id: int,
    ) -> List[Dict[str, Any]]:
        """
        List all artifacts for a run.
        
        Returns:
            List of artifact info dictionaries
        """
        run_dir = self._get_run_dir(user_id, project_id, run_id)
        artifacts = []
        
        for path in run_dir.glob("*.json"):
            try:
                # Parse filename: "script.v3.json"
                stem = path.stem
                if '.v' not in stem:
                    continue
                artifact_type_str, version_str = stem.rsplit('.v', 1)
                version = int(version_str)
                
                # Read basic info without loading full data
                content = json.loads(path.read_text(encoding='utf-8'))
                artifacts.append({
                    "artifact_type": artifact_type_str,
                    "version": version,
                    "path": str(path),
                    "checksum": content.get('checksum', ''),
                    "created_by_agent": content.get('created_by_agent', ''),
                    "created_at": content.get('created_at', ''),
                })
            except Exception as e:
                logger.warning(f"Failed to list artifact {path}: {e}")
        
        return sorted(artifacts, key=lambda x: (x['artifact_type'], x['version']))
    
    def delete_run_artifacts(
        self,
        user_id: int,
        project_id: int,
        run_id: int,
    ) -> None:
        """Delete all artifacts for a run."""
        run_dir = self._get_run_dir(user_id, project_id, run_id)
        if run_dir.exists():
            shutil.rmtree(run_dir)
            logger.info(f"Deleted artifacts for run {run_id}")
    
    def validate_path(self, user_id: int, path: str) -> bool:
        """
        Validate that a path is safe and belongs to the user.
        
        Prevents path traversal attacks.
        """
        try:
            resolved = Path(path).resolve()
            user_dir = self.base_dir / str(user_id)
            resolved_user_dir = user_dir.resolve()
            
            # Check if resolved path is within user directory
            try:
                resolved.relative_to(resolved_user_dir)
                return True
            except ValueError:
                return False
        except Exception:
            return False
