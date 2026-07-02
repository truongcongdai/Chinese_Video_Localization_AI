# tests/test_batch.py
import pytest
from universal_video_ai.jobs.batch import BatchProcessor, BatchStatus


def test_batch_processor_create():
    """Test batch creation."""
    processor = BatchProcessor()
    urls = ["https://youtube.com/1", "https://youtube.com/2"]
    batch = processor.create_batch("batch_1", user_id=999, video_urls=urls)

    assert batch.batch_id == "batch_1"
    assert batch.user_id == 999
    assert batch.total_videos == 2
    assert len(batch.video_urls) == 2


def test_batch_processor_progress():
    """Test batch progress tracking."""
    processor = BatchProcessor()
    urls = ["url1", "url2", "url3"]
    batch = processor.create_batch("batch_1", 999, urls)

    assert batch.progress_percent() == 0.0

    processor.record_completion("batch_1", success=True)
    assert batch.progress_percent() == pytest.approx(33.33, 0.1)

    processor.record_completion("batch_1", success=False)
    assert batch.progress_percent() == pytest.approx(66.66, 0.1)


def test_batch_processor_parse_csv():
    """Test CSV parsing."""
    processor = BatchProcessor()
    csv_content = """url,title
https://youtube.com/1,Video 1
https://youtube.com/2,Video 2
"""
    urls = processor.parse_csv(csv_content)
    assert len(urls) == 2
    assert urls[0] == "https://youtube.com/1"


def test_batch_status_enum():
    """Test batch status enum."""
    assert BatchStatus.PENDING.value == "pending"
    assert BatchStatus.PROCESSING.value == "processing"
    assert BatchStatus.COMPLETED.value == "completed"
    assert BatchStatus.FAILED.value == "failed"