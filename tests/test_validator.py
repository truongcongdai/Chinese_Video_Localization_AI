# tests/test_validator.py
from pathlib import Path

import pytest

from universal_video_ai.downloader.validator import FileValidator, UrlValidator, validate_url_or_raise


def test_url_validator_valid_and_invalid():
    valid = "https://www.example.com/watch?v=1"
    assert UrlValidator.is_valid(valid)
    # validate_or_raise should not raise for valid
    validate_url_or_raise(valid)

    # missing scheme
    invalid1 = "www.example.com/path"
    assert not UrlValidator.is_valid(invalid1)
    with pytest.raises(ValueError):
        validate_url_or_raise(invalid1)

    # unsupported scheme
    invalid2 = "ftp://example.com/resource"
    assert not UrlValidator.is_valid(invalid2)
    with pytest.raises(ValueError):
        validate_url_or_raise(invalid2)

    # empty / garbage
    assert not UrlValidator.is_valid("")
    with pytest.raises(ValueError):
        validate_url_or_raise("")


def test_file_validator_basic(tmp_path: Path):
    f = tmp_path / "video.mp4"
    data = b"\x00\x01\x02"
    f.write_bytes(data)

    # valid file
    assert FileValidator.is_valid(f)
    FileValidator.validate_or_raise(f)

    # min_size_bytes larger than file size -> invalid
    with pytest.raises(ValueError):
        FileValidator.validate_or_raise(f, min_size_bytes=10)

    # extension allowed
    assert FileValidator.is_valid(f, allowed_extensions=("mp4",))
    FileValidator.validate_or_raise(f, allowed_extensions=("mp4",))

    # extension not allowed
    with pytest.raises(ValueError):
        FileValidator.validate_or_raise(f, allowed_extensions=("mkv", "avi"))

    # non-file path (directory)
    d = tmp_path / "subdir"
    d.mkdir()
    with pytest.raises(ValueError):
        FileValidator.validate_or_raise(d)


def test_file_validator_empty_file(tmp_path: Path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")  # zero bytes
    assert not FileValidator.is_valid(empty)
    with pytest.raises(ValueError):
        FileValidator.validate_or_raise(empty)