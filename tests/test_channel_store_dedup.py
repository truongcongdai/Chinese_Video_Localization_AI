from __future__ import annotations

from universal_video_ai.web.store import Store


def test_existing_source_urls_are_user_scoped(tmp_path) -> None:
    store = Store(tmp_path / "db.sqlite3")
    first = store.create_user("first", "hash", is_admin=True)
    second = store.create_user("second", "hash")
    store.create_job(first, "https://www.youtube.com/watch?v=aaa", "vi")
    store.create_job(second, "https://www.youtube.com/watch?v=bbb", "vi")

    found = store.existing_source_urls_for_user(
        first,
        [
            "https://www.youtube.com/watch?v=aaa",
            "https://www.youtube.com/watch?v=bbb",
        ],
    )
    assert found == {"https://www.youtube.com/watch?v=aaa"}
