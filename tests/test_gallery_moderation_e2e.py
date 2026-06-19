"""PR-G: end-to-end модерационный flow галереи.

Покрывает обработчики ``cb_gallery_approve`` и ``cb_gallery_reject``:

* approve happy path → ``_cross_post`` вызван → клавиатура снята →
  автору отправлен HTML-уведомитель через ``safe_send_user_message``;
* approve без записи в кэше (запись истекла / рестарт бота);
* approve из НЕ-модерационного чата → жёсткий guard, ничего не делается;
* approve когда ``_cross_post`` падает → CRITICAL не нужен, юзер
  получает summary с проваленными витринами;
* reject happy path → клавиатура снята → автору отправлен HTML-пуш;
* reject без записи в кэше → нет push'а (некого пушить).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pytest_mock import MockerFixture

from content import messages as msg
from platforms.handlers import gallery_flow
from services import last_share_media
from services.last_share_media import ShareMediaEntry


# ── Хелперы ──────────────────────────────────────────────────────────────


def _make_entry(task_id: str = "tsk_42", uid: int = 777) -> ShareMediaEntry:
    return ShareMediaEntry(
        user_id=uid,
        task_id=task_id,
        task_type="photo",
        prompt="cat in space",
        file_id="AgACAgIAAxk_FAKE",
    )


def _make_callback(
    mocker: MockerFixture,
    *,
    data: str,
    chat_id: int,
    uid: int = 999,
) -> SimpleNamespace:
    """Стаб CallbackQuery с минимумом полей, нужных хэндлеру.

    ``uid`` — id модератора (нерелевантно для логики), ``chat_id`` —
    чат, из которого пришёл callback (важно для гарда).
    """

    message = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        bot=SimpleNamespace(),  # бот будет mock'аться через safe_send_user_message
        reply=mocker.AsyncMock(),
        edit_reply_markup=mocker.AsyncMock(),
    )
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=uid),
        message=message,
        answer=mocker.AsyncMock(),
    )


@pytest.fixture
def patch_settings(mocker: MockerFixture):
    """Подменяем gallery_flow.app_settings stub'ом — pydantic frozen
    нельзя monkeypatch'ить полем."""

    def _apply(moderation_chat_id: int):
        stub = SimpleNamespace(gallery_moderation_chat_id=moderation_chat_id)
        mocker.patch.object(gallery_flow, "app_settings", stub)

    return _apply


@pytest.fixture(autouse=True)
def _reset_share_cache():
    last_share_media._BY_USER.clear()
    last_share_media._BY_TASK.clear()
    last_share_media._TS.clear()
    yield
    last_share_media._BY_USER.clear()
    last_share_media._BY_TASK.clear()
    last_share_media._TS.clear()


# ── approve_gal: happy path ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_happy_path_cross_posts_and_notifies_author(
    mocker: MockerFixture, patch_settings
) -> None:
    patch_settings(moderation_chat_id=-100200300)
    entry = _make_entry()
    last_share_media.remember(
        user_id=entry.user_id,
        task_id=entry.task_id,
        task_type=entry.task_type,
        prompt=entry.prompt,
        file_id=entry.file_id,
    )

    cross_post = mocker.patch.object(
        gallery_flow,
        "_cross_post",
        new=mocker.AsyncMock(
            return_value={"webapp": True, "telegram": True, "vk": True, "max_app": True}
        ),
    )
    safe_send = mocker.patch.object(
        gallery_flow, "safe_send_user_message", new=mocker.AsyncMock(return_value=True)
    )

    cb = _make_callback(
        mocker,
        data=f"{msg.CB_GALLERY_APPROVE_PREFIX}{entry.task_id}",
        chat_id=-100200300,
    )

    await gallery_flow.cb_gallery_approve(cb)

    cross_post.assert_awaited_once()
    cb.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    cb.message.reply.assert_awaited_once()
    safe_send.assert_awaited_once()
    _, kwargs = safe_send.call_args
    # автор-кому пушим = entry.user_id, и сообщение — именно apprived-notify.
    assert safe_send.await_args.args[1] == entry.user_id
    assert safe_send.await_args.args[2] == msg.TXT_GALLERY_MOD_APPROVED_NOTIFY
    assert kwargs.get("context") == "gallery_approve_notify"


# ── approve_gal: запись истекла / отсутствует ─────────────────────────────


@pytest.mark.asyncio
async def test_approve_missing_entry_does_not_cross_post_or_notify(
    mocker: MockerFixture, patch_settings
) -> None:
    patch_settings(moderation_chat_id=-100200300)

    cross_post = mocker.patch.object(
        gallery_flow, "_cross_post", new=mocker.AsyncMock()
    )
    safe_send = mocker.patch.object(
        gallery_flow, "safe_send_user_message", new=mocker.AsyncMock()
    )

    cb = _make_callback(
        mocker,
        data=f"{msg.CB_GALLERY_APPROVE_PREFIX}ghost_task",
        chat_id=-100200300,
    )

    await gallery_flow.cb_gallery_approve(cb)

    cross_post.assert_not_awaited()
    safe_send.assert_not_awaited()
    cb.message.reply.assert_awaited_once()
    rendered = cb.message.reply.await_args.args[0]
    assert "не найден" in rendered or "устарела" in rendered


# ── approve_gal: callback пришёл НЕ из модер-чата ────────────────────────


@pytest.mark.asyncio
async def test_approve_from_wrong_chat_is_rejected_by_guard(
    mocker: MockerFixture, patch_settings
) -> None:
    patch_settings(moderation_chat_id=-100200300)
    entry = _make_entry()
    last_share_media.remember(
        user_id=entry.user_id,
        task_id=entry.task_id,
        task_type=entry.task_type,
        prompt=entry.prompt,
        file_id=entry.file_id,
    )

    cross_post = mocker.patch.object(
        gallery_flow, "_cross_post", new=mocker.AsyncMock()
    )
    safe_send = mocker.patch.object(
        gallery_flow, "safe_send_user_message", new=mocker.AsyncMock()
    )

    # Подделка: юзер шлёт approve_gal из собственного личного чата.
    cb = _make_callback(
        mocker,
        data=f"{msg.CB_GALLERY_APPROVE_PREFIX}{entry.task_id}",
        chat_id=777,  # ≠ moderation_chat_id
    )

    await gallery_flow.cb_gallery_approve(cb)

    cross_post.assert_not_awaited()
    safe_send.assert_not_awaited()
    cb.message.reply.assert_not_awaited()


# ── approve_gal: _cross_post бросает исключение ──────────────────────────


@pytest.mark.asyncio
async def test_approve_when_cross_post_crashes_still_notifies_author(
    mocker: MockerFixture, patch_settings, caplog: pytest.LogCaptureFixture
) -> None:
    """Если _cross_post упал — юзер всё равно получает уведомление
    (отдельные витрины могут быть в дауне, это не фатально)."""

    patch_settings(moderation_chat_id=-100200300)
    entry = _make_entry()
    last_share_media.remember(
        user_id=entry.user_id,
        task_id=entry.task_id,
        task_type=entry.task_type,
        prompt=entry.prompt,
        file_id=entry.file_id,
    )

    mocker.patch.object(
        gallery_flow,
        "_cross_post",
        new=mocker.AsyncMock(side_effect=RuntimeError("vk down")),
    )
    safe_send = mocker.patch.object(
        gallery_flow, "safe_send_user_message", new=mocker.AsyncMock(return_value=True)
    )

    cb = _make_callback(
        mocker,
        data=f"{msg.CB_GALLERY_APPROVE_PREFIX}{entry.task_id}",
        chat_id=-100200300,
    )

    await gallery_flow.cb_gallery_approve(cb)

    cb.message.reply.assert_awaited_once()
    safe_send.assert_awaited_once()


# ── reject_gal: happy path ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reject_happy_path_notifies_author(
    mocker: MockerFixture, patch_settings
) -> None:
    patch_settings(moderation_chat_id=-100200300)
    entry = _make_entry()
    last_share_media.remember(
        user_id=entry.user_id,
        task_id=entry.task_id,
        task_type=entry.task_type,
        prompt=entry.prompt,
        file_id=entry.file_id,
    )

    cross_post = mocker.patch.object(
        gallery_flow, "_cross_post", new=mocker.AsyncMock()
    )
    safe_send = mocker.patch.object(
        gallery_flow, "safe_send_user_message", new=mocker.AsyncMock(return_value=True)
    )

    cb = _make_callback(
        mocker,
        data=f"{msg.CB_GALLERY_REJECT_PREFIX}{entry.task_id}",
        chat_id=-100200300,
    )

    await gallery_flow.cb_gallery_reject(cb)

    cross_post.assert_not_awaited()  # reject НИКОГДА не публикует
    cb.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    cb.message.reply.assert_awaited_once()
    safe_send.assert_awaited_once()
    assert safe_send.await_args.args[1] == entry.user_id
    assert safe_send.await_args.args[2] == msg.TXT_GALLERY_MOD_REJECTED_NOTIFY


# ── reject_gal: запись истекла → нет пуша ────────────────────────────────


@pytest.mark.asyncio
async def test_reject_missing_entry_skips_author_notification(
    mocker: MockerFixture, patch_settings
) -> None:
    patch_settings(moderation_chat_id=-100200300)

    safe_send = mocker.patch.object(
        gallery_flow, "safe_send_user_message", new=mocker.AsyncMock()
    )

    cb = _make_callback(
        mocker,
        data=f"{msg.CB_GALLERY_REJECT_PREFIX}ghost",
        chat_id=-100200300,
    )

    await gallery_flow.cb_gallery_reject(cb)

    # Клавиатура снята, текст «Отклонено» отправлен — но автору НЕ пушим,
    # потому что entry не найден (либо ttl истёк, либо рестарт бота).
    cb.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    cb.message.reply.assert_awaited_once()
    safe_send.assert_not_awaited()
