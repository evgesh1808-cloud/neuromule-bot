"""Suggested Replies для роли ``standard``: парсинг ``===КНОПКИ===`` + callback."""

from __future__ import annotations

import html
import logging
import re
import secrets
from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from content import messages as msg

logger = logging.getLogger(__name__)

BUTTONS_MARKER = "===КНОПКИ==="
# Модель пишет КНОПКИ/кнопки/Кнопки, иногда с пробелами.
_BUTTONS_MARKER_RE = re.compile(r"===\s*кнопки\s*===", flags=re.IGNORECASE)
_MAX_LABELS = 3
_MAX_LABEL_CHARS = 48  # полный текст follow-up в кэше / на клик
_MAX_BUTTON_DISPLAY_CHARS = 34  # читаемый текст на кнопке (мобильный Telegram)
_CONTEXT_ID_LEN = 8
# Telegram Bot API: callback_data 1–64 bytes (UTF-8).
_TG_CALLBACK_DATA_MAX_BYTES = 64
_CHAT_HINT_PREFIX_BYTES = len(msg.CB_CHAT_HINT_PREFIX.encode("utf-8"))

# FREE: последний резерв, если из текста ответа якоря не извлеклись.
FREE_FALLBACK_SUGGESTED_REPLIES: tuple[str, ...] = (
    "Уточни детали",
    "Приведи пример",
    "Какой следующий шаг?",
)
# ASCII-резерв, если UTF-8 callback внезапно не влез (не должно случаться).
_EMERGENCY_ASCII_HINTS: tuple[str, ...] = ("More details", "Give example", "Next step")

# Шаблонные лейблы — выкидываем, если есть якоря из тела ответа.
_GENERIC_HINT_NORMS: frozenset[str] = frozenset(
    {
        "расскажи подробнее",
        "расскажи подробнее?",
        "дай пример",
        "дай пример?",
        "что делать дальше",
        "что делать дальше?",
        "что дальше",
        "что дальше?",
        "подробнее",
        "подробнее?",
        "уточни детали",
        "уточни детали?",
        "приведи пример",
        "приведи пример?",
        "какой следующий шаг",
        "какой следующий шаг?",
        "ещё",
        "еще",
        "продолжай",
        "продолжи",
        "понятно",
        "ок",
        "уточни",
        "пример",
        "первый вопрос",
        "первый вопрос?",
        "второй вопрос",
        "второй вопрос?",
        "третий вопрос",
        "третий вопрос?",
        "трогательное",
        "трогательное и душевное",
        "короткое смс-поздравление",
        "драйвовое",
        "официальное",
    }
)

_STOPWORDS_RU: frozenset[str] = frozenset(
    {
        "это",
        "эта",
        "этот",
        "эти",
        "как",
        "что",
        "для",
        "или",
        "если",
        "также",
        "чтобы",
        "при",
        "без",
        "над",
        "под",
        "про",
        "все",
        "всё",
        "вас",
        "вам",
        "ваш",
        "ваша",
        "ваше",
        "можно",
        "нужно",
        "будет",
        "есть",
        "нет",
        "уже",
        "ещё",
        "еще",
        "очень",
        "просто",
        "только",
        "после",
        "перед",
        "между",
        "через",
        "более",
        "менее",
        "которые",
        "который",
        "которая",
        "которое",
        "такой",
        "такая",
        "такое",
        "такие",
        "свой",
        "своя",
        "свое",
        "свои",
        "один",
        "одна",
        "одно",
        "когда",
        "куда",
        "откуда",
        "почему",
        "зачем",
        "здесь",
        "там",
        "тогда",
        "сейчас",
        "сегодня",
        "потом",
        "сначала",
        "например",
        "нейросеть",
        "нейромул",
        "neuromule",
    }
)

# context_id -> (user_id, labels) — только legacy ``std_reply:`` (старые сообщения)
_CACHE: dict[str, tuple[int, tuple[str, ...]]] = {}
_BY_USER: dict[int, str] = {}


def sanitize_suggested_label(label: str) -> str:
    """Убирает HTML/кавычки/мусор — иначе callback ломается или выглядит «мёртвым»."""
    text = html.unescape((label or "").strip())
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) >= 2 and text[0] in "\"'«“„" and text[-1] in "\"'»”":
        text = text[1:-1].strip()
    text = re.sub(r"^[\d]+[.)]\s*", "", text)
    text = re.sub(r"^[-•*]+\s*", "", text)
    return text[:_MAX_LABEL_CHARS]


def polish_hint_label(label: str) -> str:
    """Грамотная короткая подпись кнопки: заглавная, без мусора, вопрос по делу."""
    text = sanitize_suggested_label(label)
    if not text:
        return ""
    # Убрать обрубки вроде «Про …» / «Пример:» без смысла.
    text = re.sub(r"^(про|пример|риски|шаги|альтернатива)\s*[:—–-]?\s*$", "", text, flags=re.I).strip()
    if not text:
        return ""
    for i, ch in enumerate(text):
        if ch.isalpha():
            text = text[:i] + ch.upper() + text[i + 1 :]
            break
    low = text.lower()
    question_starts = (
        "как ",
        "что ",
        "какие ",
        "какой ",
        "какая ",
        "какое ",
        "зачем ",
        "почему ",
        "где ",
        "когда ",
        "сколько ",
        "можно ",
        "нужно ",
        "с какого ",
        "с каких ",
    )
    if not text.endswith(("?", "…")) and (
        low.startswith(question_starts) or low.startswith("про ")
    ):
        text = text.rstrip(".!;:") + "?"
    return text[:_MAX_LABEL_CHARS]


def fit_label_for_chat_hint(label: str) -> str:
    """Обрезает лейбл так, чтобы ``chat_hint:<текст>`` всегда ≤64 байт UTF-8."""
    text = sanitize_suggested_label(label)
    if not text:
        return ""
    budget = _TG_CALLBACK_DATA_MAX_BYTES - _CHAT_HINT_PREFIX_BYTES
    if budget <= 0:
        return ""
    raw = text.encode("utf-8")
    if len(raw) <= budget:
        return text
    ell = "…"
    ell_b = ell.encode("utf-8")
    keep = max(0, budget - len(ell_b))
    cut = raw[:keep].decode("utf-8", errors="ignore").rstrip()
    if not cut:
        return ""
    return cut + ell


def button_display_text(
    label: str,
    *,
    max_chars: int = _MAX_BUTTON_DISPLAY_CHARS,
) -> str:
    """Короткий видимый текст кнопки (не путать с полным follow-up в кэше)."""
    text = sanitize_suggested_label(label)
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip(" .,;:—–-") + "…"


def expand_suggested_reply_prompt(label: str) -> str:
    """Кликнутая подсказка → текст user-сообщения.

    Без мета-инструкций («продолжая разговор / опираясь на ответ») —
    paid Standard путает их с prompt injection и отвечает SYSTEM SECURITY INFO.
    Контекст уже в истории диалога; сюда идёт только сам follow-up.
    """
    q = sanitize_suggested_label(label) or (label or "").strip()
    if not q:
        return "Что делать дальше по этой теме?"
    return q


def _norm_hint(label: str) -> str:
    return re.sub(r"\s+", " ", (label or "").strip().lower()).rstrip("?.!…")


def is_generic_hint_label(label: str) -> bool:
    """True для шаблонных «подробнее/пример» — их лучше заменить якорями из ответа."""
    n = _norm_hint(label)
    if not n:
        return True
    if n in _GENERIC_HINT_NORMS:
        return True
    if re.fullmatch(r"(первый|второй|третий)\s+вопрос\??", n):
        return True
    return False


def _plain_answer_text(body: str) -> str:
    text = html.unescape(body or "")
    text = re.sub(r"(?is)<pre\b[^>]*>.*?</pre>", " ", text)
    text = re.sub(r"(?is)<code\b[^>]*>.*?</code>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _clip_anchor(phrase: str, *, max_words: int = 3, max_chars: int = 22) -> str:
    words = [w for w in re.split(r"\s+", (phrase or "").strip()) if w]
    if not words:
        return ""
    clipped = " ".join(words[:max_words]).strip(" .,;:!?—–-")
    if len(clipped) > max_chars:
        clipped = clipped[: max_chars - 1].rstrip() + "…"
    return clipped


def _extract_answer_anchors(body: str, *, limit: int = 5) -> list[str]:
    """Якоря темы из тела ответа: <b>, пункты списка, значимые слова."""
    raw = body or ""
    anchors: list[str] = []
    seen: set[str] = set()

    def _push(phrase: str) -> None:
        clip = _clip_anchor(sanitize_suggested_label(phrase))
        if not clip:
            return
        key = _norm_hint(clip)
        if len(key) < 3 or key in seen or key in _STOPWORDS_RU:
            return
        if is_generic_hint_label(clip):
            return
        seen.add(key)
        anchors.append(clip)

    for m in re.finditer(r"(?is)<b>(.*?)</b>", raw):
        _push(m.group(1))
        if len(anchors) >= limit:
            return anchors

    plain_lines = re.sub(r"<[^>]+>", "\n", raw)
    for line in plain_lines.splitlines():
        line = sanitize_suggested_label(line)
        if not line:
            continue
        m = re.match(r"^(?:\d+[.)]|[-•*])\s+(.+)$", line)
        if m:
            _push(m.group(1))
            if len(anchors) >= limit:
                return anchors

    plain = _plain_answer_text(raw)
    for tok in re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9\-]{3,}", plain):
        low = tok.lower()
        if low in _STOPWORDS_RU or low.isdigit():
            continue
        _push(tok)
        if len(anchors) >= limit:
            break
    return anchors


def derive_contextual_free_hints(body: str) -> list[str]:
    """До 3 follow-up по якорям ответа (без второго вызова LLM)."""
    # COPY PACK: заголовки стилей («Трогательное…») — не follow-up.
    try:
        from services.copy_pack import suppress_suggested_replies_for_answer

        if suppress_suggested_replies_for_answer(body or ""):
            return []
    except Exception:
        pass
    anchors = _extract_answer_anchors(body)
    if not anchors:
        return []
    out: list[str] = []
    templates = (
        "Как работает {a}?",
        "Какие нюансы у {a}?",
        "Как применить {a}?",
        "Какие риски у {a}?",
        "С чего начать с {a}?",
    )
    for i, anchor in enumerate(anchors):
        if len(out) >= _MAX_LABELS:
            break
        a = sanitize_suggested_label(anchor).rstrip("?.!…")
        if not a:
            continue
        label = polish_hint_label(templates[i % len(templates)].format(a=a))
        if label and label not in out and not is_generic_hint_label(label):
            out.append(label)
    if len(out) < _MAX_LABELS and anchors:
        a0 = sanitize_suggested_label(anchors[0]).rstrip("?.!…")
        for extra in (
            f"Пример с {a0}?",
            f"Частые ошибки с {a0}?",
            f"Что проверить в {a0}?",
        ):
            if len(out) >= _MAX_LABELS:
                break
            fitted = polish_hint_label(extra)
            if fitted and fitted not in out and not is_generic_hint_label(fitted):
                out.append(fitted)
    return out[:_MAX_LABELS]


def has_buttons_marker(text: str) -> bool:
    """True, если в тексте есть маркер кнопок (любой регистр / пробелы)."""
    return bool(_BUTTONS_MARKER_RE.search(text or ""))


def clean_text_before_marker(text: str) -> str:
    """Текст ответа без блока ``===КНОПКИ===`` (для отправки пользователю)."""
    raw = text or ""
    m = _BUTTONS_MARKER_RE.search(raw)
    if not m:
        return raw.strip()
    return raw[: m.start()].rstrip()


def force_append_free_buttons_block(
    text: str,
    *,
    labels: Sequence[str] | None = None,
) -> str:
    """Если маркер забыт — бэкенд жёстко дописывает ``===КНОПКИ===`` + 3 строки."""
    raw = (text or "").rstrip()
    if has_buttons_marker(raw):
        return raw
    body = raw.strip()
    hints = ensure_free_hint_labels(labels, body=body)
    block = "\n".join((BUTTONS_MARKER, *hints))
    logger.info("suggested_replies: FORCE append ===КНОПКИ=== (marker missing)")
    return f"{body}\n{block}" if body else block


def prepare_free_standard_reply(
    model_text: str,
) -> tuple[str, list[str], InlineKeyboardMarkup]:
    """Полный FREE-пайплайн: дописка маркера → чистый текст → всегда 3 кнопки.

    Эквивалент схемы:
    force marker → clean_text_before_marker → build_free_hint_keyboard.
    """
    forced = force_append_free_buttons_block(model_text)
    body, labels = split_suggested_replies(forced, fallback_if_missing=True)
    labels = ensure_free_hint_labels(labels, body=body)
    kb = build_free_hint_keyboard(labels, body=body)
    return body, labels, kb


def split_suggested_replies(
    text: str,
    *,
    fallback_if_missing: bool = False,
) -> tuple[str, list[str]]:
    """Отделяет тело ответа от блока ``===КНОПКИ===`` (если есть).

    ``fallback_if_missing=True`` (FREE standard): при отсутствии маркера/лейблов
    сначала дописывает блок маркера, затем гарантирует 3 лейбла.
    """
    raw = text or ""
    if fallback_if_missing and not has_buttons_marker(raw):
        raw = force_append_free_buttons_block(raw)

    m = _BUTTONS_MARKER_RE.search(raw)
    if not m:
        body = raw.strip()
        if fallback_if_missing:
            return body, ensure_free_hint_labels(body=body)
        return body, []

    body = raw[: m.start()].rstrip()
    tail = raw[m.end() :]
    labels: list[str] = []
    for line in tail.splitlines():
        # Полный лейбл в кэш/ответ; усечение под chat_hint — только при сборке FREE callback.
        fitted = polish_hint_label(line or "")
        if fitted and fitted not in labels:
            labels.append(fitted)
        if len(labels) >= _MAX_LABELS:
            break
    if fallback_if_missing:
        labels = ensure_free_hint_labels(labels, body=body)
    return body, labels



def remember_suggested_replies(user_id: int, labels: Sequence[str]) -> str | None:
    """Кладёт полные подписи в кэш; возвращает ``context_id`` или ``None`` если пусто.

    Не дописывает FREE-фолбэк — только то, что реально показали на кнопках.
    """
    clean_list: list[str] = []
    for raw in labels:
        fitted = polish_hint_label(str(raw))
        if fitted and fitted not in clean_list:
            clean_list.append(fitted)
        if len(clean_list) >= _MAX_LABELS:
            break
    clean = tuple(clean_list)
    if not clean:
        return None
    context_id = secrets.token_hex(_CONTEXT_ID_LEN // 2)
    prev = _BY_USER.get(int(user_id))
    if prev and prev in _CACHE:
        _CACHE.pop(prev, None)
    _CACHE[context_id] = (int(user_id), clean)
    _BY_USER[int(user_id)] = context_id
    return context_id


def callback_data_fits(data: str) -> bool:
    return len((data or "").encode("utf-8")) <= _TG_CALLBACK_DATA_MAX_BYTES


def build_chat_hint_callback(label: str) -> str | None:
    """``chat_hint:<текст>`` — всегда усечённый под 64 байта (не ``None`` из‑за длины)."""
    text = fit_label_for_chat_hint(label)
    if not text:
        return None
    data = f"{msg.CB_CHAT_HINT_PREFIX}{text}"
    if callback_data_fits(data):
        return data
    logger.warning("suggested_replies: chat_hint still too long after fit len=%s", len(data.encode()))
    return None


def parse_chat_hint_callback(data: str) -> str | None:
    """``chat_hint:<текст>`` → текст вопроса."""
    prefix = msg.CB_CHAT_HINT_PREFIX
    raw = data or ""
    if not raw.startswith(prefix):
        return None
    text = raw[len(prefix) :].strip()
    return text or None


def ensure_free_hint_labels(
    labels: Sequence[str] | None = None,
    *,
    body: str | None = None,
) -> list[str]:
    """Ровно 3 коротких рабочих лейбла (для кнопок и follow-up).

    Сначала валидные лейблы модели (не шаблонные, если есть тело),
    затем якоря из ``body``, затем статический FREE-фолбэк.
    """
    contextual = derive_contextual_free_hints(body or "")
    out: list[str] = []
    for raw in labels or ():
        fitted = polish_hint_label(str(raw))
        if not fitted or fitted in out:
            continue
        # Шаблонные «подробнее» выкидываем, если можем заменить контекстом.
        if contextual and is_generic_hint_label(fitted):
            continue
        out.append(fitted)
        if len(out) >= _MAX_LABELS:
            break
    for fb in contextual:
        if len(out) >= _MAX_LABELS:
            break
        fitted = polish_hint_label(fb)
        if fitted and fitted not in out:
            out.append(fitted)
    for fb in FREE_FALLBACK_SUGGESTED_REPLIES:
        if len(out) >= _MAX_LABELS:
            break
        fitted = polish_hint_label(fb)
        if fitted and fitted not in out:
            out.append(fitted)
    i = 0
    while len(out) < _MAX_LABELS and i < len(_EMERGENCY_ASCII_HINTS):
        out.append(_EMERGENCY_ASCII_HINTS[i])
        i += 1
    return out[:_MAX_LABELS]


def build_free_hint_keyboard(
    labels: Sequence[str] | None = None,
    *,
    body: str | None = None,
    from_model_text: str | None = None,
) -> InlineKeyboardMarkup:
    """Железная клавиатура FREE: всегда 3 кнопки ``chat_hint:``, никогда ``None``.

    ``from_model_text`` — сырой ответ модели: допишет маркер при необходимости и распарсит кнопки.
    """
    if from_model_text is not None:
        clean_body, clean_labels = split_suggested_replies(
            from_model_text,
            fallback_if_missing=True,
        )
        return build_free_hint_keyboard(clean_labels, body=clean_body)

    clean = ensure_free_hint_labels(labels, body=body)
    rows: list[list[InlineKeyboardButton]] = []
    for i, label in enumerate(clean):
        callback_data = build_chat_hint_callback(label)
        if callback_data is None:
            emergency = _EMERGENCY_ASCII_HINTS[i % len(_EMERGENCY_ASCII_HINTS)]
            callback_data = build_chat_hint_callback(emergency) or (
                f"{msg.CB_CHAT_HINT_PREFIX}{emergency}"
            )
            # Truncate bytes if needed
            while not callback_data_fits(callback_data) and len(callback_data) > len(
                msg.CB_CHAT_HINT_PREFIX
            ):
                callback_data = callback_data[:-1]
            label = emergency
        # На кнопке — короткий текст; в callback — усечённый под 64 байта chat_hint.
        btn_text = button_display_text(label) or parse_chat_hint_callback(callback_data) or "…"
        rows.append([InlineKeyboardButton(text=btn_text, callback_data=callback_data)])
    if not rows:
        rows = [
            [
                InlineKeyboardButton(
                    text="Next",
                    callback_data=f"{msg.CB_CHAT_HINT_PREFIX}Next",
                )
            ]
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def resolve_suggested_reply(
    context_id: str,
    index: int,
    *,
    user_id: int,
) -> str | None:
    """Достаёт полный текст кнопки по legacy ``std_reply:<idx>:<context_id>``."""
    cid = (context_id or "").strip()
    entry = _CACHE.get(cid)
    if entry is None:
        return None
    owner_id, labels = entry
    if int(owner_id) != int(user_id):
        return None
    if index < 0 or index >= len(labels):
        return None
    return labels[index]


def resolve_suggested_reply_latest(user_id: int, index: int) -> str | None:
    """Мягкий fallback: последняя сессия подсказок пользователя по индексу."""
    cid = _BY_USER.get(int(user_id))
    if not cid:
        return None
    return resolve_suggested_reply(cid, index, user_id=user_id)


def parse_std_reply_callback(data: str) -> tuple[int, str] | None:
    """``std_reply:<index>:<context_id>`` → ``(index, context_id)``."""
    prefix = msg.CB_STD_REPLY_PREFIX
    raw = (data or "").strip()
    if not raw.startswith(prefix):
        return None
    rest = raw[len(prefix) :]
    if ":" not in rest:
        return None
    idx_s, context_id = rest.split(":", 1)
    context_id = context_id.strip()
    if not context_id:
        return None
    try:
        index = int(idx_s)
    except ValueError:
        return None
    if index < 0 or index >= _MAX_LABELS:
        return None
    return index, context_id


def build_suggested_replies_keyboard(
    context_id: str,
    labels: Sequence[str],
) -> InlineKeyboardMarkup | None:
    """Инлайн-кнопки Suggested Replies (paid / общий путь).

    Полный текст follow-up лежит в in-memory кэше ``context_id``;
    callback = ``std_reply:<idx>:<context_id>`` (без обрезки смысла в 64 байта).
    На кнопке — короткий display-текст.
    """
    cid = (context_id or "").strip()
    if not cid:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for i, label in enumerate(labels):
        full = sanitize_suggested_label(str(label))
        if not full:
            continue
        btn_text = button_display_text(full)
        if not btn_text:
            continue
        callback_data = f"{msg.CB_STD_REPLY_PREFIX}{i}:{cid}"
        if not callback_data_fits(callback_data):
            # context_id слишком длинный — не должно случаться (8 hex).
            logger.warning(
                "suggested_replies: std_reply callback too long cid=%s",
                cid,
            )
            continue
        rows.append([InlineKeyboardButton(text=btn_text, callback_data=callback_data)])
        if len(rows) >= _MAX_LABELS:
            break
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_standard_zero_balance_keyboard() -> InlineKeyboardMarkup:
    """Тариф / кристаллы / рефералка при нулевом балансе на Suggested Reply."""
    from platforms.telegram_utils import _invite_switch_query

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Повысить тариф до VIP",
                    callback_data=msg.CB_OPEN_TARIFFS,
                )
            ],
            [
                InlineKeyboardButton(
                    text="💎 Докупить кристаллы отдельно",
                    callback_data=msg.CB_BUY_CRYSTALS_ONLY_MENU,
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Пригласить друзей",
                    switch_inline_query=_invite_switch_query(),
                )
            ],
        ]
    )


def clear_suggested_replies_for_tests() -> None:
    """Только тесты."""
    _CACHE.clear()
    _BY_USER.clear()
