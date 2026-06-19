"""Тесты единого ядра ``services/webapp_gallery.py`` (Mini App backend).

Покрывают:
  • миграция таблицы ``webapp_gallery`` (идемпотентна);
  • атомарная запись новой публикации, корректный hashtag-рубрикатор;
  • защита от двойной публикации одной ``task_id`` (двойной клик юзера);
  • видимость = 1 по умолчанию, ``hide_publication`` мягко скрывает;
  • ``list_recent_publications`` отдаёт фронту анонимные поля (без user_id);
  • фильтрация по ``task_type``.
"""

from __future__ import annotations

import pytest

from services import webapp_gallery


@pytest.mark.asyncio
async def test_schema_is_idempotent(repo_module) -> None:
    await webapp_gallery.ensure_schema()
    await webapp_gallery.ensure_schema()  # повторно — не должно падать


@pytest.mark.asyncio
async def test_publish_and_list_recent(repo_module) -> None:
    ok = await webapp_gallery.publish_to_gallery(
        task_id="task-photo-1",
        user_id=111,
        task_type="photo",
        prompt="cinematic horse",
        media_url="https://cdn.test/horse.png",
    )
    assert ok is True

    items = await webapp_gallery.list_recent_publications(limit=10)
    assert len(items) == 1
    item = items[0]
    assert item.task_id == "task-photo-1"
    assert item.task_type == "photo"
    assert item.media_url == "https://cdn.test/horse.png"
    assert item.hashtag == "#gallery_flux"
    # Анонимность: dataclass принципиально не содержит user_id.
    assert not hasattr(item, "user_id")


@pytest.mark.asyncio
async def test_publish_dedup_same_task_id(repo_module) -> None:
    first = await webapp_gallery.publish_to_gallery(
        task_id="dup-task",
        user_id=222,
        task_type="video",
        prompt="x",
        media_url="https://cdn.test/v.mp4",
    )
    second = await webapp_gallery.publish_to_gallery(
        task_id="dup-task",
        user_id=222,
        task_type="video",
        prompt="x2",
        media_url="https://cdn.test/v2.mp4",
    )
    assert first is True
    assert second is False  # двойной клик не порождает дубль


@pytest.mark.asyncio
async def test_hide_publication_removes_from_listing(repo_module) -> None:
    await webapp_gallery.publish_to_gallery(
        task_id="hidden-task",
        user_id=333,
        task_type="music",
        prompt="hifi",
        media_url="https://cdn.test/track.mp3",
    )

    items_before = await webapp_gallery.list_recent_publications()
    assert any(i.task_id == "hidden-task" for i in items_before)

    hidden_ok = await webapp_gallery.hide_publication("hidden-task")
    assert hidden_ok is True

    items_after = await webapp_gallery.list_recent_publications()
    assert all(i.task_id != "hidden-task" for i in items_after)


@pytest.mark.asyncio
async def test_filter_by_task_type(repo_module) -> None:
    await webapp_gallery.publish_to_gallery(
        task_id="ph-1", user_id=1, task_type="photo",
        prompt="a", media_url="https://x/a.png",
    )
    await webapp_gallery.publish_to_gallery(
        task_id="vi-1", user_id=1, task_type="video",
        prompt="b", media_url="https://x/b.mp4",
    )
    photos = await webapp_gallery.list_recent_publications(task_type="photo")
    videos = await webapp_gallery.list_recent_publications(task_type="video")
    assert {i.task_id for i in photos} == {"ph-1"}
    assert {i.task_id for i in videos} == {"vi-1"}


@pytest.mark.asyncio
async def test_publish_rejects_empty_media_url(repo_module) -> None:
    ok = await webapp_gallery.publish_to_gallery(
        task_id="empty-task",
        user_id=1,
        task_type="photo",
        prompt="x",
        media_url="",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_hashtag_router_covers_all_types(repo_module) -> None:
    cases = [
        ("ph-h", "photo", "#gallery_flux"),
        ("vi-h", "video", "#studio_video"),
        ("an-h", "animate", "#studio_video"),
        ("mu-h", "music", "#radio_suno"),
    ]
    for task_id, ttype, expected_tag in cases:
        await webapp_gallery.publish_to_gallery(
            task_id=task_id, user_id=1, task_type=ttype,  # type: ignore[arg-type]
            prompt="x", media_url=f"https://x/{task_id}",
        )
    items = await webapp_gallery.list_recent_publications(limit=20)
    by_task = {i.task_id: i.hashtag for i in items}
    for task_id, _, expected_tag in cases:
        assert by_task[task_id] == expected_tag
