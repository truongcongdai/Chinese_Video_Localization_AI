from pathlib import Path

from universal_video_ai.cookies.manager import CookieManager


def test_save_and_find(tmp_path: Path):
    manager = CookieManager(cookie_dir=tmp_path)
    domain = "example.com"
    content = "TESTCOOKIE=1"
    saved = manager.save_cookie(domain, content)

    assert saved.exists()
    found = manager.find_cookie_for_domain(domain)
    assert found is not None
    assert found.resolve() == saved.resolve()
    loaded = manager.load_cookie_file(found)
    assert loaded == content


def test_get_downloader_args(tmp_path: Path):
    manager = CookieManager(cookie_dir=tmp_path)
    domain = "mysite.org"
    content = "A=1"
    saved = manager.save_cookie(domain, content)

    args = manager.get_downloader_args(domain)
    assert args == ["--cookies", str(saved)]


def test_clear_cookie_single_and_all(tmp_path: Path):
    manager = CookieManager(cookie_dir=tmp_path)
    a = manager.save_cookie("a.com", "A=1")
    b = manager.save_cookie("b.com", "B=1")

    # clear single
    manager.clear_cookie("a.com")
    assert not a.exists()
    assert b.exists()

    # clear all
    manager.clear_cookie()
    assert not any(tmp_path.iterdir())