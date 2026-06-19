from unittest.mock import MagicMock

from platforms.telegram_quote import (
    build_quoted_user_prompt,
    extract_quoted_text,
    has_neurotext_message_input,
    is_quote_reply_to_bot,
    is_reply_to_bot_message,
    resolve_neurotext_quote_input,
)


def test_extract_from_quote_text() -> None:
    msg = MagicMock()
    msg.quote = MagicMock(text="фрагмент ответа", position=0)
    msg.reply_to_message = MagicMock(text="<b>Полный</b> ответ бота", caption=None)
    assert extract_quoted_text(msg) == "фрагмент ответа"


def test_build_prompt_with_quote() -> None:
    out = build_quoted_user_prompt("почему так?", "фрагмент")
    assert "фрагмент" in out
    assert "почему так?" in out


def test_build_prompt_without_quote() -> None:
    assert build_quoted_user_prompt("просто вопрос", None) == "просто вопрос"


def test_is_quote_reply_to_bot() -> None:
    msg = MagicMock()
    msg.quote = MagicMock(text="цитата")
    msg.reply_to_message = MagicMock(from_user=MagicMock(is_bot=True))
    assert is_quote_reply_to_bot(msg) is True
    msg.reply_to_message.from_user.is_bot = False
    assert is_quote_reply_to_bot(msg) is False
    msg.quote = None
    assert is_quote_reply_to_bot(msg) is False


def test_resolve_neurotext_quote_input() -> None:
    msg = MagicMock()
    msg.text = "почему так?"
    msg.quote = MagicMock(text="фрагмент", position=0)
    msg.reply_to_message = MagicMock(
        from_user=MagicMock(is_bot=True),
        text="полный ответ",
        caption=None,
    )
    quoted, comment = resolve_neurotext_quote_input(msg)
    assert quoted == "фрагмент"
    assert comment == "почему так?"


def test_build_prompt_uses_comment_phrase() -> None:
    out = build_quoted_user_prompt("уточни", "фраза бота")
    assert "Пользователь комментирует твою фразу" in out
    assert "<blockquote>фраза бота</blockquote>" in out
    assert "Его вопрос: уточни" in out


def test_is_reply_to_bot_without_quote() -> None:
    msg = MagicMock()
    msg.quote = None
    msg.bot = MagicMock(id=42)
    msg.reply_to_message = MagicMock(from_user=MagicMock(id=42, is_bot=True))
    assert is_reply_to_bot_message(msg) is True
    assert is_quote_reply_to_bot(msg) is False


def test_resolve_plain_reply_uses_full_bot_message() -> None:
    msg = MagicMock()
    msg.text = "почему ты так ответил?"
    msg.quote = None
    msg.bot = MagicMock(id=7)
    msg.reply_to_message = MagicMock(
        from_user=MagicMock(id=7, is_bot=True),
        text="<b>Полный</b> ответ бота",
        caption=None,
    )
    quoted, comment = resolve_neurotext_quote_input(msg)
    assert quoted == "Полный ответ бота"
    assert comment == "почему ты так ответил?"


def test_has_neurotext_message_input_reply_only() -> None:
    msg = MagicMock()
    msg.text = ""
    msg.quote = None
    msg.bot = MagicMock(id=1)
    msg.reply_to_message = MagicMock(
        from_user=MagicMock(id=1, is_bot=True),
        text="прошлый ответ",
        caption=None,
    )
    assert has_neurotext_message_input(msg) is True
