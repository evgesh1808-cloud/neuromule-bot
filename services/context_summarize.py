"""Сжатие длинного диалога (summarize вместо hard-delete).

Контракт:

* Экспертные роли: ``role_allows_dialog_summary`` + ``maybe_compact_messages`` —
  при soft-пороге токенов «голова» → ``[DIALOG_SUMMARY]``, хвост ``keep_pairs``.
* Роль ``standard``: ``compact_standard_dialog_context`` — ультра-короткий
  ``[Контекст: …]`` в system + последний user (без сырых коуч-реплик в истории).
* Fail-open: timeout / ошибка LLM / пустой ответ → эвристика или исходный
  payload; чат не падает.

Модуль не импортирует aiogram. ``ask_fn`` инъецируется снаружи
(в ``chat_turn`` — thin-wrapper над ``ask_ai_messages``).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from config import settings
from services import metrics
from services.ai_text import estimate_messages_prompt_tokens

logger = logging.getLogger(__name__)

DIALOG_SUMMARY_MARKER = "[DIALOG_SUMMARY]"
STANDARD_CONTEXT_MARKER = "[Контекст:"
FOLLOWUP_REF_MARKER = "[Справка:"

# Короткие follow-up (кнопки подсказок): оставляем последний ответ ассистента в payload.
_SHORT_FOLLOWUP_MAX_CHARS = 96
# Полный прошлый ответ в role=assistant провоцирует копирование — кладём в system-справку.
_FOLLOWUP_REF_MAX_CHARS = 1400

AskFn = Callable[..., Awaitable[dict[str, Any]]]


_ROLES_SKIP_SUMMARY = frozenset({"table_generator"})
_COMPLIANCE_TAIL_SPLIT = re.compile(
    r"\n\n\[(?:Системный|Блогер-формат|Compliance:|ROUTE LOCK:|Системный хвост)",
    re.IGNORECASE,
)


def role_allows_dialog_summary(role_id: str) -> bool:
    """True для экспертных ролей (включая ``standard``); ``table_generator`` — False."""
    rid = (role_id or "").strip().lower()
    return bool(rid) and rid not in _ROLES_SKIP_SUMMARY


def _strip_compliance_tail(text: str) -> str:
    parts = _COMPLIANCE_TAIL_SPLIT.split(text, maxsplit=1)
    return (parts[0] if parts else text).strip()


def _heuristic_standard_context(prior: Sequence[dict[str, Any]]) -> str:
    """Дешёвая выжимка без LLM: последний ответ бота + последние user-реплики."""
    last_assistant = ""
    for msg in reversed(prior):
        if msg.get("role") != "assistant":
            continue
        text = _strip_compliance_tail(_text_of(msg))
        if text:
            last_assistant = text
            break

    user_bits: list[str] = []
    for msg in prior:
        if msg.get("role") != "user":
            continue
        text = _strip_compliance_tail(_text_of(msg))
        if text:
            user_bits.append(text)

    parts: list[str] = []
    if last_assistant:
        clip = re.sub(r"\s+", " ", last_assistant).strip()
        if len(clip) > 140:
            clip = clip[:137].rstrip() + "…"
        parts.append(clip)
    if user_bits:
        u = re.sub(r"\s+", " ", user_bits[-1]).strip()
        if len(u) > 80:
            u = u[:77].rstrip() + "…"
        parts.append(u)
    raw = " | ".join(parts)
    if not raw:
        return "предыдущий диалог"
    if len(raw) > 200:
        raw = raw[:197].rstrip() + "…"
    return raw


def _is_short_followup_user(user_msg: dict[str, Any]) -> bool:
    """True для коротких уточнений (Suggested Replies / «Про сроки?»)."""
    text = _strip_compliance_tail(_text_of(user_msg))
    if not text:
        return False
    return len(text) <= _SHORT_FOLLOWUP_MAX_CHARS


def _last_assistant_before(
    messages: Sequence[dict[str, Any]],
    *,
    before_idx: int,
) -> dict[str, Any] | None:
    for i in range(before_idx - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            return dict(messages[i])
    return None


def format_standard_context_block(summary: str) -> str:
    body = re.sub(r"\s+", " ", (summary or "").strip())
    body = body.removeprefix("[Контекст:").removesuffix("]").strip()
    if not body:
        body = "предыдущий диалог"
    if len(body) > 200:
        body = body[:197].rstrip() + "…"
    return f"[Контекст: {body}]"


async def _call_standard_context_llm(
    prior: Sequence[dict[str, Any]],
    *,
    ask_fn: AskFn,
) -> str | None:
    """Ультра-короткая выжимка для Standard. Сбой → ``None`` (эвристика наверх)."""
    max_chars = min(160, int(settings.chat_summary_max_chars))
    model_id = str(settings.chat_summary_model).strip()
    timeout_sec = min(12.0, float(settings.chat_summary_timeout_sec))
    lines: list[str] = []
    for msg in prior:
        role = str(msg.get("role") or "?")
        text = _strip_compliance_tail(_text_of(msg))
        if not text:
            continue
        if len(text) > 400:
            text = text[:400] + "…"
        lines.append(f"{role}: {text}")
    transcript = "\n".join(lines)
    if not transcript.strip():
        return None
    prompt = (
        "Сжми предыдущий диалог в ОДНУ короткую фразу на русском "
        f"(не более {max_chars} символов), как тема беседы "
        "(пример: Обсуждали секцию тхэквондо для сына 7 лет). "
        "Без Markdown, без кавычек, без слова Контекст, без вступлений.\n\n"
        f"---\n{transcript}\n---"
    )
    payload: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "Ты — служебный компрессор контекста. "
                "Верни только одну короткую фразу-тему диалога."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    try:
        async with asyncio.timeout(max(1.0, timeout_sec)):
            result = await ask_fn(
                payload,
                models=[model_id],
                max_tokens=min(120, max(32, max_chars // 2)),
                timeout=timeout_sec,
                temperature=0.1,
            )
    except TimeoutError:
        logger.warning("context_summarize: standard context LLM timeout")
        metrics.incr("chat.standard_context_fail", {"reason": "timeout"})
        return None
    except Exception:
        logger.warning("context_summarize: standard context LLM failed", exc_info=True)
        metrics.incr("chat.standard_context_fail", {"reason": "llm_error"})
        return None

    content = ""
    if isinstance(result, dict):
        content = str(result.get("content") or "").strip()
    if not content:
        metrics.incr("chat.standard_context_fail", {"reason": "empty"})
        return None
    content = re.sub(r"\s+", " ", content).strip(" \"'")
    if len(content) > max_chars * 2:
        content = content[: max_chars * 2].rstrip() + "…"
    return content


def _clip_context_summary(text: str, *, max_chars: int = 200) -> str:
    clip = re.sub(r"\s+", " ", (text or "").strip())
    if len(clip) > max_chars:
        return clip[: max_chars - 1].rstrip() + "…"
    return clip or "предыдущий диалог"


def _plain_ref_text(text: str) -> str:
    """HTML → плоский текст для справки (без тегов, компактно)."""
    plain = re.sub(r"(?is)<br\s*/?>", "\n", text or "")
    plain = re.sub(r"(?is)</p>", "\n", plain)
    plain = re.sub(r"<[^>]+>", " ", plain)
    plain = re.sub(r"[ \t]+", " ", plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    return plain.strip()


_HINT_TOPIC_RE = re.compile(
    r"(?is)^(?:ещё\s+про|еще\s+про|про|что\s+учесть\s+в|нюансы|риски|как\s+сделать\s+на\s+практике|"
    r"добавь\s+практики\s+по\s+теме|какие\s+нюансы\s+важны\s+для)\s*[:\s«»\"]*(.+?)\s*$"
)


def topic_from_followup(text: str) -> str:
    """Достаёт тему из подписи кнопки / короткого уточнения."""
    q = _strip_compliance_tail(text or "").strip().rstrip("?.!…").strip()
    if not q:
        return ""
    m = _HINT_TOPIC_RE.match(q)
    if m:
        return m.group(1).strip(" «»\"'„“").strip()
    # «Как сделать на практике: «X»» уже разобран выше; иначе короткий хвост.
    return q[:48]


def focus_anchor_for_followup(anchor: str, user_text: str) -> str:
    """Оставляет из прошлого ответа только фрагмент про тему кнопки.

    Иначе free-модель снова пересказывает весь список — «одни и те же сообщения».
    """
    plain = _plain_ref_text(_strip_compliance_tail(anchor or ""))
    if not plain:
        return ""
    topic = topic_from_followup(user_text)
    topic_l = topic.lower().strip()
    if len(topic_l) < 2:
        return plain[:700]

    parts = re.split(r"(?=\n\s*(?:\d+[.)]|[-•*])\s+)", "\n" + plain)
    hits = [p.strip() for p in parts if p.strip() and topic_l in p.lower()]
    if hits:
        hits.sort(key=len)
        return hits[0][:900]

    for chunk in re.split(r"\n{2,}|(?<=[.!?])\s+", plain):
        piece = chunk.strip()
        if len(piece) > 20 and topic_l in piece.lower():
            return piece[:900]

    # Тема не найдена целиком — попробуем первое значимое слово темы.
    first = re.split(r"\s+", topic_l)[0]
    if len(first) >= 4:
        for p in parts:
            if p.strip() and first in p.lower():
                return p.strip()[:900]
    return plain[:500]


def format_followup_reference(text: str, *, question: str | None = None) -> str:
    """Справка для кнопки/уточнения: узкий фрагмент, без копирования всего ответа."""
    body = _plain_ref_text(_strip_compliance_tail(text or ""))
    if not body:
        body = "фрагмент прошлого ответа по теме"
    if len(body) > _FOLLOWUP_REF_MAX_CHARS:
        body = body[: _FOLLOWUP_REF_MAX_CHARS - 1].rstrip() + "…"
    q = _strip_compliance_tail(question or "").strip()
    if len(q) > 80:
        q = q[:77].rstrip() + "…"
    head = (
        f"{FOLLOWUP_REF_MARKER} пользователь нажал уточнение «{q}». "
        if q
        else f"{FOLLOWUP_REF_MARKER} "
    )
    return (
        f"{head}"
        "Ниже только релевантный фрагмент прошлого ответа. "
        "Дай НОВЫЙ практический ответ именно на это уточнение (2–4 шага). "
        "Запрещено: копировать справку, повторять весь старый список, "
        "присылать тот же текст что раньше. "
        "В ===КНОПКИ=== — 3 новых коротких вопроса по только что раскрытому, "
        "не те же самые кнопки.]\n"
        f"{body}"
    )


def _inject_system_blocks(
    system_msgs: list[dict[str, Any]],
    *blocks: str,
) -> list[dict[str, Any]]:
    cleaned = [b.strip() for b in blocks if (b or "").strip()]
    if not cleaned:
        return system_msgs

    context_block = next(
        (b for b in cleaned if b.startswith(STANDARD_CONTEXT_MARKER)),
        None,
    )
    other = [b for b in cleaned if b != context_block]

    if not system_msgs:
        parts = [p for p in (context_block, *other) if p]
        return [{"role": "system", "content": "\n\n".join(parts)}]

    sys = dict(system_msgs[-1])
    content = str(sys.get("content") or "")
    if FOLLOWUP_REF_MARKER in content:
        content = re.sub(
            rf"{re.escape(FOLLOWUP_REF_MARKER)}.*?(?=\n\n\[|\Z)",
            "",
            content,
            count=1,
            flags=re.DOTALL,
        ).rstrip()
    if context_block:
        if STANDARD_CONTEXT_MARKER in content:
            content = re.sub(
                r"\[Контекст:[^\]]*\]",
                context_block,
                content,
                count=1,
            )
        else:
            content = f"{content.rstrip()}\n\n{context_block}"
    if other:
        content = f"{content.rstrip()}\n\n" + "\n\n".join(other)
    sys["content"] = content
    return [*system_msgs[:-1], sys]


async def compact_standard_dialog_context(
    messages: list[dict[str, Any]],
    *,
    ask_fn: AskFn | None = None,
    anchor_assistant_text: str | None = None,
) -> list[dict[str, Any]]:
    """
    Standard: system (+ ультра-короткий [Контекст: …]) + последний user.

    Для коротких follow-up / кнопок прошлый ответ кладём в system-справку
    (не в role=assistant) — иначе free-модели копируют старое сообщение.
    ``anchor_assistant_text`` — текст сообщения под кнопкой (даже если в
    истории уже есть более новый ответ).
    """
    if not messages:
        return messages

    system_msgs = [dict(m) for m in messages if m.get("role") == "system"]
    last_user: dict[str, Any] | None = None
    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user = dict(messages[i])
            last_user_idx = i
            break
    if last_user is None:
        return messages

    prior = [
        m
        for i, m in enumerate(messages)
        if i < last_user_idx and m.get("role") != "system"
    ]
    anchor = _strip_compliance_tail(anchor_assistant_text or "").strip()
    short_followup = _is_short_followup_user(last_user)

    ref_source = anchor
    if not ref_source and short_followup:
        last_assistant = _last_assistant_before(messages, before_idx=last_user_idx)
        if last_assistant is not None:
            ref_source = _strip_compliance_tail(_text_of(last_assistant)).strip()

    # Кнопка / короткое уточнение: узкая справка в system, без assistant-эха.
    if ref_source and (anchor or short_followup):
        user_q = _strip_compliance_tail(_text_of(last_user))
        focused = focus_anchor_for_followup(ref_source, user_q)
        summary = _clip_context_summary(
            topic_from_followup(user_q) or focused,
            max_chars=120,
        )
        context_block = format_standard_context_block(summary)
        ref_block = format_followup_reference(focused, question=user_q)
        system_msgs = _inject_system_blocks(system_msgs, context_block, ref_block)
        messages[:] = [*system_msgs, last_user]
        metrics.incr("chat.standard_context_compacted")
        if anchor:
            metrics.incr("chat.standard_context_anchor")
        metrics.incr("chat.standard_context_followup_ref")
        return messages

    if not prior:
        messages[:] = [*system_msgs, last_user]
        return messages

    summary: str | None = None
    if ask_fn is not None and bool(settings.chat_summary_enabled):
        summary = await _call_standard_context_llm(prior, ask_fn=ask_fn)
    if not summary:
        summary = _heuristic_standard_context(prior)

    context_block = format_standard_context_block(summary)
    system_msgs = _inject_system_blocks(system_msgs, context_block)

    messages[:] = [*system_msgs, last_user]
    metrics.incr("chat.standard_context_compacted")
    return messages


def _text_of(msg: dict[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return "\n".join(parts)
    return str(content or "")


def split_head_and_tail(
    messages: list[dict[str, Any]],
    keep_pairs: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Делит **диалоговую** часть (без system) на ``(head, tail)``.

    ``tail`` — последние ``keep_pairs`` user-сообщений и все реплики после
    первой из этих user (включая assistant'ов между ними).
    System-сообщения в результат не входят — caller сохраняет их отдельно.
    """
    keep = max(0, int(keep_pairs))
    dialog = [m for m in messages if m.get("role") != "system"]
    if keep <= 0 or not dialog:
        return dialog, []

    user_indices = [i for i, m in enumerate(dialog) if m.get("role") == "user"]
    if len(user_indices) <= keep:
        return [], dialog

    cut = user_indices[-keep]
    return dialog[:cut], dialog[cut:]


def build_summary_prompt(head: Sequence[dict[str, Any]], *, max_chars: int) -> str:
    lines: list[str] = []
    for msg in head:
        role = str(msg.get("role") or "?")
        text = _text_of(msg).strip()
        if not text:
            continue
        if len(text) > 800:
            text = text[:800] + "…"
        lines.append(f"{role}: {text}")
    transcript = "\n".join(lines)
    limit = max(120, int(max_chars))
    return (
        f"Сжми диалог ниже в краткую выжимку на русском (не более {limit} символов). "
        "Сохрани имена, факты, решения и незакрытые задачи. Без Markdown, без вступлений.\n\n"
        f"---\n{transcript}\n---"
    )


def format_summary_message(summary: str) -> dict[str, str]:
    body = (summary or "").strip()
    if DIALOG_SUMMARY_MARKER not in body:
        body = f"{DIALOG_SUMMARY_MARKER}\n{body}"
    return {"role": "assistant", "content": body}


async def _call_summary_llm(
    head: Sequence[dict[str, Any]],
    *,
    ask_fn: AskFn,
) -> str | None:
    """Один вызов дешёвой модели. Любой сбой → ``None`` (fail-open наверх)."""
    max_chars = int(settings.chat_summary_max_chars)
    model_id = str(settings.chat_summary_model).strip()
    timeout_sec = float(settings.chat_summary_timeout_sec)
    prompt = build_summary_prompt(head, max_chars=max_chars)
    payload: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "Ты — служебный компрессор истории диалога. "
                "Верни только краткую выжимку фактов, без роли ассистента для пользователя."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    try:
        async with asyncio.timeout(max(1.0, timeout_sec)):
            result = await ask_fn(
                payload,
                models=[model_id],
                max_tokens=min(400, max(64, max_chars // 2)),
                timeout=timeout_sec,
                temperature=0.2,
            )
    except TimeoutError:
        logger.warning(
            "context_summarize: LLM timeout after %.1fs",
            timeout_sec,
        )
        metrics.incr("chat.context_summarize_fail", {"reason": "timeout"})
        return None
    except Exception:
        logger.warning("context_summarize: LLM call failed", exc_info=True)
        metrics.incr("chat.context_summarize_fail", {"reason": "llm_error"})
        return None

    content = ""
    if isinstance(result, dict):
        content = str(result.get("content") or "").strip()
    if not content:
        metrics.incr("chat.context_summarize_fail", {"reason": "empty"})
        return None
    hard_cap = max(120, max_chars * 2)
    if len(content) > hard_cap:
        content = content[:hard_cap].rstrip() + "…"
    return content


async def maybe_compact_messages(
    messages: list[dict[str, Any]],
    trigger_tokens: int,
    ask_fn: AskFn,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Уплотняет payload при превышении soft-порога ``trigger_tokens``.

    Caller обязан заранее проверить ``role_allows_dialog_summary(role_id)``
    и ``settings.chat_summary_enabled``.

    Returns:
        ``(messages, summarized)`` — при любой ошибке исходный список и ``False``.
    """
    if not messages:
        return messages, False

    try:
        est = int(
            estimate_messages_prompt_tokens(
                messages,
                settings=settings,
            )
        )
    except Exception:
        logger.warning("context_summarize: estimate_tokens failed", exc_info=True)
        return messages, False

    keep_pairs = int(settings.chat_summary_keep_pairs)
    user_count = sum(1 for m in messages if m.get("role") == "user")
    need = est >= int(trigger_tokens) or user_count > max(1, keep_pairs)
    if not need:
        return messages, False

    system_msgs = [m for m in messages if m.get("role") == "system"]
    head, tail = split_head_and_tail(messages, keep_pairs)
    if not head:
        return messages, False

    summary = await _call_summary_llm(head, ask_fn=ask_fn)
    if not summary:
        return messages, False

    compacted: list[dict[str, Any]] = [
        *system_msgs,
        format_summary_message(summary),
        *tail,
    ]
    metrics.incr("chat.context_summarized", {"role": "dialog"})
    return compacted, True
