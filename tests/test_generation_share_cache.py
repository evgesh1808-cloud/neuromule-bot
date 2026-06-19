"""Тесты: воркеры генерации кэшируют медиа в ``last_share_media``.

Под каждым успешным результатом (photo / video / animate / music) бот
должен запомнить ``file_id`` Telegram + (когда применимо) оригинальный
URL внешнего API — это нужно кнопке «📢 Поделиться в Галерее».

Сетевые вызовы внешних API сюда не доходят: мы патчим клиенты на
сторону самих воркеров.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from services import generation_jobs, last_share_media
from services.generation_jobs import GenTask


# ─── helpers ────────────────────────────────────────────────────────────────


@dataclass
class _SentLog:
    photo_calls: list[dict[str, Any]] = field(default_factory=list)
    video_calls: list[dict[str, Any]] = field(default_factory=list)
    audio_calls: list[dict[str, Any]] = field(default_factory=list)


def _make_bot(log: _SentLog) -> SimpleNamespace:
    async def send_photo(*args, **kwargs):
        log.photo_calls.append({"args": args, "kwargs": kwargs})
        return SimpleNamespace(
            photo=[SimpleNamespace(file_id="tg_photo_xxs"), SimpleNamespace(file_id="tg_photo_xl")]
        )

    async def send_video(*args, **kwargs):
        log.video_calls.append({"args": args, "kwargs": kwargs})
        return SimpleNamespace(video=SimpleNamespace(file_id="tg_video_big"))

    async def send_audio(*args, **kwargs):
        log.audio_calls.append({"args": args, "kwargs": kwargs})
        return SimpleNamespace(audio=SimpleNamespace(file_id="tg_audio_hifi"))

    async def send_message(*args, **kwargs):
        return SimpleNamespace()

    return SimpleNamespace(
        send_photo=send_photo,
        send_video=send_video,
        send_audio=send_audio,
        send_message=send_message,
    )


def _task(
    *,
    user_id: int,
    task_id: str,
    task_type: str,
    bot,
    prompt: str = "epic test",
    image_model_id: str = "imagen4",
    scenario_id: str = "",
    charged: int = 5,
    file_id: str | None = None,
) -> GenTask:
    return GenTask(
        task_id=task_id,
        bot=bot,  # type: ignore[arg-type]
        chat_id=user_id,
        user_id=user_id,
        task_type=task_type,  # type: ignore[arg-type]
        prompt=prompt,
        image_model_id=image_model_id,
        scenario_id=scenario_id,
        file_id=file_id,
        charged_crystals=charged,
    )


# ─── photo ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_photo_worker_caches_share_media(monkeypatch) -> None:
    log = _SentLog()
    bot = _make_bot(log)
    user_id = 70_001
    last_share_media.clear(user_id)

    # Стабим внешний клиент Imagen — возвращаем URL, никакого HTTP не нужно.
    async def _fake_generate(model_key: str, prompt: str):
        return "https://cdn.fake/imagen.png"

    monkeypatch.setattr(generation_jobs, "_generate_photo_result", _fake_generate)

    # chat_action_loop — заглушка, чтобы не висеть на бесконечном лупе.
    class _NoopAction:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        generation_jobs, "chat_action_loop", lambda *a, **kw: _NoopAction()
    )

    task = _task(user_id=user_id, task_id="ph_001", task_type="photo", bot=bot)
    await generation_jobs._photo_stub_worker(task)

    assert task.status == "completed"
    assert log.photo_calls, "send_photo must be called"

    entry = last_share_media.get_by_task("ph_001")
    assert entry is not None
    assert entry.task_type == "photo"
    assert entry.file_id == "tg_photo_xl"     # самый крупный размер
    assert entry.media_url == "https://cdn.fake/imagen.png"
    assert entry.prompt == "epic test"


# ─── video ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_video_worker_caches_share_media(monkeypatch) -> None:
    log = _SentLog()
    bot = _make_bot(log)
    user_id = 70_002
    last_share_media.clear(user_id)

    monkeypatch.setattr(generation_jobs, "replicate_configured", lambda: True)

    async def _fake_replicate(model: str, inputs: dict):
        return "https://cdn.fake/replicate.mp4"

    async def _fake_enhance(_settings, _prompt):
        return "enhanced prompt"

    async def _fake_row(_uid):
        return SimpleNamespace(crystals=100)

    monkeypatch.setattr(generation_jobs, "call_replicate_model", _fake_replicate)
    monkeypatch.setattr(
        generation_jobs, "enhance_video_prompt_for_replicate", _fake_enhance
    )
    monkeypatch.setattr(generation_jobs, "get_user_row", _fake_row)

    class _NoopAction:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        generation_jobs, "chat_action_loop", lambda *a, **kw: _NoopAction()
    )

    task = _task(user_id=user_id, task_id="vid_001", task_type="video", bot=bot)
    await generation_jobs._video_stub_worker(task)

    assert task.status == "completed"
    assert log.video_calls, "send_video must be called"

    entry = last_share_media.get_by_task("vid_001")
    assert entry is not None
    assert entry.task_type == "video"
    assert entry.file_id == "tg_video_big"
    assert entry.media_url == "https://cdn.fake/replicate.mp4"


# ─── animate ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_animate_worker_caches_share_media(monkeypatch) -> None:
    log = _SentLog()
    bot = _make_bot(log)
    user_id = 70_003
    last_share_media.clear(user_id)

    monkeypatch.setattr(generation_jobs, "replicate_configured", lambda: True)

    async def _fake_dl(_bot, _file_id):
        return "https://cdn.fake/selfie.jpg"

    async def _fake_replicate(model: str, inputs: dict):
        return "https://cdn.fake/animate.mp4"

    async def _fake_row(_uid):
        return SimpleNamespace(crystals=80)

    monkeypatch.setattr(generation_jobs, "telegram_photo_download_url", _fake_dl)
    monkeypatch.setattr(generation_jobs, "call_replicate_model", _fake_replicate)
    monkeypatch.setattr(generation_jobs, "get_user_row", _fake_row)

    class _NoopAction:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        generation_jobs, "chat_action_loop", lambda *a, **kw: _NoopAction()
    )

    task = _task(
        user_id=user_id,
        task_id="anim_001",
        task_type="animate",
        bot=bot,
        file_id="src_selfie",
    )
    await generation_jobs._animate_stub_worker(task)

    assert task.status == "completed"
    assert log.video_calls

    entry = last_share_media.get_by_task("anim_001")
    assert entry is not None
    assert entry.task_type == "animate"
    assert entry.file_id == "tg_video_big"
    assert entry.media_url == "https://cdn.fake/animate.mp4"


# ─── music ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_music_worker_caches_share_media(monkeypatch) -> None:
    log = _SentLog()
    bot = _make_bot(log)
    user_id = 70_004
    last_share_media.clear(user_id)

    from services.suno_client import SunoTrack

    monkeypatch.setattr(generation_jobs, "suno_configured", lambda: True)

    async def _fake_enhance(_settings, raw: str):
        return raw + " | premium mix"

    async def _fake_suno(*args, **kwargs):
        return SunoTrack(
            audio_url="https://cdn.fake/suno.mp3",
            title="NeuroMule 🐎",
            clip_id="suno-xyz",
        )

    async def _fake_cover(_prompt):
        return None

    async def _fake_row(_uid):
        return SimpleNamespace(crystals=60)

    monkeypatch.setattr(generation_jobs, "enhance_music_style_prompt", _fake_enhance)
    monkeypatch.setattr(generation_jobs, "generate_music_track", _fake_suno)
    monkeypatch.setattr(generation_jobs, "_build_music_cover", _fake_cover)
    monkeypatch.setattr(generation_jobs, "get_user_row", _fake_row)

    class _NoopAction:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        generation_jobs, "chat_action_loop", lambda *a, **kw: _NoopAction()
    )

    task = _task(
        user_id=user_id,
        task_id="mus_001",
        task_type="music",
        bot=bot,
        prompt="lofi cinematic",
    )
    await generation_jobs._music_stub_worker(task)

    assert task.status == "completed"
    assert log.audio_calls

    entry = last_share_media.get_by_task("mus_001")
    assert entry is not None
    assert entry.task_type == "music"
    assert entry.file_id == "tg_audio_hifi"
    assert entry.media_url == "https://cdn.fake/suno.mp3"


# ─── failure path does NOT poison cache ─────────────────────────────────────


@pytest.mark.asyncio
async def test_failed_photo_worker_does_not_cache(monkeypatch) -> None:
    """При падении внешнего API кэш не должен забиваться — пользователь
    должен увидеть ошибку и auto-refund, а кнопка «Поделиться» не должна
    показывать вчерашний шедевр."""

    log = _SentLog()
    bot = _make_bot(log)
    user_id = 70_005
    last_share_media.clear(user_id)

    async def _boom(model_key: str, prompt: str):
        raise RuntimeError("OpenRouter down")

    async def _fake_fail(task, *, user_message: str, log_msg: str):
        task.status = "failed"

    monkeypatch.setattr(generation_jobs, "_generate_photo_result", _boom)
    monkeypatch.setattr(generation_jobs, "fail_generation_task", _fake_fail)

    class _NoopAction:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        generation_jobs, "chat_action_loop", lambda *a, **kw: _NoopAction()
    )

    task = _task(user_id=user_id, task_id="ph_fail", task_type="photo", bot=bot)
    await generation_jobs._photo_stub_worker(task)

    assert task.status == "failed"
    assert last_share_media.get_by_task("ph_fail") is None
    assert last_share_media.get_by_user(user_id) is None
