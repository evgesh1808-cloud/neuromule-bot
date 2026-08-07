"""Тесты вирусной кнопки «↪️ Поделиться результатом» под фото (только FREE)."""

from __future__ import annotations

import urllib.parse
from urllib.parse import parse_qs, urlparse

from content import messages as msg
from content.inline_keyboards import result_photo_keyboard
from services.photo_share import get_photo_share_url, resolve_photo_share_url
from services.tariffs import TariffName


def _parse_share_url(share_url: str) -> tuple[str, str]:
    parsed = urlparse(share_url)
    qs = parse_qs(parsed.query)
    return urllib.parse.unquote(qs["url"][0]), urllib.parse.unquote(qs["text"][0])


def test_get_photo_share_url_encodes_prompt_and_ref_link() -> None:
    url = get_photo_share_url("космический закат & звёзды", 42_001)
    assert url.startswith("https://t.me/share/url?")

    ref_link, share_text = _parse_share_url(url)
    assert ref_link == "https://t.me/NeuroMule_bot?start=ref42001"
    assert "космический закат & звёзды" in share_text
    assert share_text.startswith("🎨 Оцените шедевр")


def test_get_photo_share_url_truncates_prompt_to_180_chars() -> None:
    long_prompt = "а" * 250
    _, share_text = _parse_share_url(get_photo_share_url(long_prompt, 1))
    inner = share_text.split("«", 1)[1].rstrip("»")
    assert len(inner) == 180


def test_resolve_photo_share_url_free_only() -> None:
    assert resolve_photo_share_url(TariffName.FREE, "prompt", 99) is not None
    assert resolve_photo_share_url("Free", "prompt", 99) is not None
    assert resolve_photo_share_url(TariffName.MINI, "prompt", 99) is None
    assert resolve_photo_share_url(TariffName.SMART, "prompt", 99) is None
    assert resolve_photo_share_url(TariffName.ULTRA, "prompt", 99) is None


def test_result_photo_keyboard_free_share_on_first_row() -> None:
    share = "https://t.me/share/url?url=x&text=y"
    kb = result_photo_keyboard(
        task_id="ph_1",
        photo_share_url=share,
        download_callback=f"{msg.CB_DL_FILE_PREFIX}t:ph_1",
    )

    assert kb.inline_keyboard[0][0].url == share
    assert msg.TXT_PHOTO_SHARE_RESULT_BTN in kb.inline_keyboard[0][0].text
    assert kb.inline_keyboard[1][0].text == msg.BTN_PHOTO_REFINE
    assert kb.inline_keyboard[2][0].text.startswith("🪄")
    dl_row = [r for r in kb.inline_keyboard if r and r[0].text == msg.BTN_DOWNLOAD_UNCOMPRESSED]
    assert len(dl_row) == 1
    assert dl_row[0][0].callback_data == f"{msg.CB_DL_FILE_PREFIX}t:ph_1"


def test_result_photo_keyboard_paid_no_share_first_row_is_animate() -> None:
    kb = result_photo_keyboard(
        task_id="ph_2",
        photo_share_url=None,
        download_callback=f"{msg.CB_DL_FILE_PREFIX}t:ph_2",
    )

    first_btn = kb.inline_keyboard[0][0]
    assert first_btn.text == msg.BTN_PHOTO_REFINE
    assert kb.inline_keyboard[1][0].text.startswith("🪄")
    assert first_btn.url is None

    gallery_row = kb.inline_keyboard[-1]
    forward_btns = [b for b in gallery_row if b.switch_inline_query]
    assert len(forward_btns) == 1
    assert forward_btns[0].text == msg.TXT_GALLERY_FORWARD_FRIEND_BTN
    assert forward_btns[0].switch_inline_query == "get_media_ph_2"
