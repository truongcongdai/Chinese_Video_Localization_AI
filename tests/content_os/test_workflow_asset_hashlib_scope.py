"""Regression tests for Content OS asset checksum handling."""
from pathlib import Path


def test_workflow_imports_hashlib_at_module_scope():
    source = Path("src/universal_video_ai/content_os/workflow.py").read_text(encoding="utf-8")
    assert "import hashlib" in source.splitlines()[:20]
    assert "        import hashlib" not in source


def test_workflow_does_not_use_dynamic_path_import_for_asset_checksums():
    source = Path("src/universal_video_ai/content_os/workflow.py").read_text(encoding="utf-8")
    assert '__import__("pathlib")' not in source