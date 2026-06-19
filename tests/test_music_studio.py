"""Тесты Музыкальной студии NeuroMule 🐎⚡️: Suno parser, enhancer, флоу, UI."""

from __future__ import annotations

import pytest

from content import messages as msg
from content.inline_keyboards import (
    music_studio_keyboard,
    result_music_keyboard_pro,
)
from platforms.music_studio import (
    MUSIC_COST,
    _music_blocked_for_free,
)
from platforms.telegram_states import MusicFlow
from services import last_music_request
from services.billing import translator as tr
from services.suno_client import SunoTrack, _find_track
from services.tariffs import TariffName


# ─── Suno parser ────────────────────────────────────────────────────────────


def test_suno_parser_flat_dict_with_audio_url() -> None:
    track = _find_track({"audio_url": "https://x/song.mp3", "title": "Hit"})
    assert isinstance(track, SunoTrack)
    assert track.audio_url == "https://x/song.mp3"
    assert track.title == "Hit"
    assert track.clip_id is None


def test_suno_parser_camel_case_and_clip_id() -> None:
    track = _find_track(
        {"audioUrl": "https://x/a.mp3", "title": "Alt", "clipId": "c-001"}
    )
    assert track is not None
    assert track.clip_id == "c-001"


def test_suno_parser_nested_list_under_data() -> None:
    payload = {
        "data": [
            {"status": "pending"},
            {
                "audio_url": "https://cdn/track.mp3",
                "title": "Deep Lo-fi",
                "id": "clp-42",
            },
        ]
    }
    track = _find_track(payload)
    assert track is not None
    assert track.audio_url == "https://cdn/track.mp3"
    assert track.title == "Deep Lo-fi"
    assert track.clip_id == "clp-42"


def test_suno_parser_returns_none_for_empty() -> None:
    assert _find_track({}) is None
    assert _find_track({"foo": "bar"}) is None
    assert _find_track([]) is None
    assert _find_track(None) is None
    assert _find_track({"audio_url": "not-a-url"}) is None


def test_suno_parser_finds_in_deeply_nested_clip() -> None:
    payload = {
        "result": {
            "clips": [
                {
                    "meta": {"audio_url": "https://x/q.mp3", "name": "Quasar"},
                }
            ]
        }
    }
    track = _find_track(payload)
    assert track is not None
    assert track.title == "Quasar"


# ─── Prompt Enhancer (Suno hi-fi) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_enhance_music_style_fallback_without_openrouter() -> None:
    class _S:
        openrouter_key = ""
        openrouter_timeout_sec = 30

    out = await tr.enhance_music_style_prompt(_S(), "лоу-фай джаз, медленно, фортепиано")
    assert tr._looks_already_hifi(out)
    assert "cinematic mix" in out
    assert "tight production" in out


@pytest.mark.asyncio
async def test_enhance_music_style_passthrough_when_already_hifi() -> None:
    class _S:
        openrouter_key = ""
        openrouter_timeout_sec = 30

    seed = "Lo-fi jazz, cinematic mix, high fidelity, tight production"
    out = await tr.enhance_music_style_prompt(_S(), seed)
    assert out.lower().count("cinematic mix") == 1


@pytest.mark.asyncio
async def test_enhance_music_style_empty_returns_empty() -> None:
    class _S:
        openrouter_key = ""
        openrouter_timeout_sec = 30

    assert await tr.enhance_music_style_prompt(_S(), "") == ""
    assert await tr.enhance_music_style_prompt(_S(), "   ") == ""


# ─── Access guard ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tariff,is_duo_partner,expected",
    [
        (TariffName.FREE, False, True),
        (TariffName.FREE, True, False),  # партнёр DUO проходит
        (TariffName.MINI, False, False),
        (TariffName.SMART, False, False),
        (TariffName.ULTRA, False, False),
    ],
)
def test_music_blocked_for_free_matrix(
    tariff: TariffName, is_duo_partner: bool, expected: bool
) -> None:
    assert _music_blocked_for_free(tariff, is_duo_partner) is expected


# ─── UI / клавиатуры ────────────────────────────────────────────────────────


def test_music_studio_keyboard_has_three_modes() -> None:
    kb = music_studio_keyboard()
    cbs = {row[0].callback_data for row in kb.inline_keyboard}
    assert cbs == {
        msg.CB_MUSIC_MODE_AI,
        msg.CB_MUSIC_MODE_CUSTOM,
        msg.CB_MUSIC_MODE_INSTRUMENTAL,
    }


def test_result_music_keyboard_pro_has_four_upsell_buttons() -> None:
    kb = result_music_keyboard_pro()
    upsell = [row[0].callback_data for row in kb.inline_keyboard[:4]]
    assert upsell == [
        msg.CB_MUSIC_CLIP,
        msg.CB_MUSIC_EXTEND,
        msg.CB_MUSIC_VOICE_CLONE,
        msg.CB_MUSIC_PUBLISH,
    ]
    # цены вшиты в текст для прозрачности юзера
    texts = " ".join(b.text for row in kb.inline_keyboard for b in row)
    assert "20 💎" in texts
    assert "15 💎" in texts
    assert "10 💎" in texts
    # Виральный ряд Галереи: «Поделиться» + «Переслать другу».
    share_row = kb.inline_keyboard[-1]
    assert any(b.callback_data == msg.CB_SHARE_TO_GALLERY for b in share_row)
    assert any(
        b.switch_inline_query is not None and b.switch_inline_query.startswith("get_media_")
        for b in share_row
    )


def test_music_studio_intro_is_html_branded() -> None:
    assert "<b>Музыкальная студия NeuroMule 🐎⚡️</b>" in msg.TXT_MUSIC_STUDIO_INTRO
    assert "Suno AI v4" in msg.TXT_MUSIC_STUDIO_INTRO
    assert "15 💎" in msg.TXT_MUSIC_STUDIO_INTRO


def test_music_queue_accepted_mentions_2026_and_timing() -> None:
    assert "NeuroMule 2026" in msg.TXT_MUSIC_QUEUE_ACCEPTED
    assert "от 1 до 3 минут" in msg.TXT_MUSIC_QUEUE_ACCEPTED


def test_music_failed_text_mentions_refund() -> None:
    assert "Кристаллы" in msg.TXT_MUSIC_SUNO_FAILED
    assert "возвращены" in msg.TXT_MUSIC_SUNO_FAILED.lower() or \
        "возвращены" in msg.TXT_MUSIC_SUNO_FAILED


def test_music_insufficient_balance_renders_html() -> None:
    rendered = msg.TXT_MUSIC_INSUFFICIENT_CRYSTALS.format(balance=7)
    assert "<b>" in rendered
    assert "15 💎" in rendered
    assert "<b>7 💎</b>" in rendered


# ─── FSM states ─────────────────────────────────────────────────────────────


def test_music_flow_has_three_modes_plus_lyrics_chain() -> None:
    states = {s.state for s in (
        MusicFlow.waiting_for_style_prompt,
        MusicFlow.waiting_for_custom_lyrics,
        MusicFlow.waiting_for_custom_style,
        MusicFlow.waiting_for_instrumental_style,
    )}
    # 4 уникальных state'а (custom-режим — 2 шага)
    assert len(states) == 4


# ─── last_music_request cache ───────────────────────────────────────────────


def test_last_music_request_roundtrip() -> None:
    uid = 999_001
    last_music_request.clear(uid)
    assert last_music_request.get(uid) is None

    last_music_request.remember(
        uid,
        style="lo-fi jazz",
        lyrics="moonlight blues",
        make_instrumental=False,
        clip_id="abc-1",
    )
    snap = last_music_request.get(uid)
    assert snap is not None
    assert snap.style == "lo-fi jazz"
    assert snap.lyrics == "moonlight blues"
    assert snap.make_instrumental is False
    assert snap.clip_id == "abc-1"

    last_music_request.clear(uid)
    assert last_music_request.get(uid) is None


# ─── Constants & contract ───────────────────────────────────────────────────


def test_music_cost_constant_is_15() -> None:
    assert MUSIC_COST == 15


def test_gen_task_has_new_music_fields() -> None:
    from services.generation_jobs import GenTask

    fields = GenTask.__dataclass_fields__
    assert "music_lyrics" in fields
    assert "music_instrumental" in fields
    assert "music_continue_clip_id" in fields
