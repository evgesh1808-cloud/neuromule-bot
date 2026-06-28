"""Извлечение текста (YouTube, web, PDF/DOCX/TXT) и саммари через OpenAI gpt-4o-mini."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from openai import AsyncOpenAI

from config import settings

logger = logging.getLogger(__name__)

MIN_TEXT_LEN = 100
MAX_INPUT_CHARS = 40_000
MAX_FILE_CHARS = 30_000
DEFAULT_CHUNK = 4_000

URL_RE = re.compile(r"https?://[^\s<>\"']+")
YOUTUBE_ID_RE = re.compile(r"(?:v=|youtu\.be/|embed/)([0-9A-Za-z_-]{11})")

SYSTEM_PROMPT = """Ты — профессиональный ИИ-аналитик и эксперт по глубокому анализу текстовых транскриптов видео. Твоя задача — делать бескомпромиссно точные, емкие и структурированные саммари на основе предоставленного текста (расшифровки видео).

Соблюдай строгие правила:
1. НЕ выдумывай факты. Если в тексте чего-то нет (например, имени автора или точного названия), не добавляй это «от себя» для красоты.
2. Будь точен в причинно-следственных связях. Обращай внимание на контекст: если спикер говорит, что метод имеет побочные эффекты, не пиши, что этот метод их лечит.
3. Извлекай скрытую суть, а не банальные приветствия или общие фразы. Ищи конкретные данные, термины, развенчанные мифы и неочевидные выводы.
4. Используй простой, понятный язык без сложной канцелярии, но сохраняй профессиональные термины, если их объясняет спикер.

Форматируй ответ СТРОГО по следующему шаблону (используй markdown, короткие абзацы и списки). Язык ответа — язык исходного текста:

👑 ГЛАВНАЯ СУТЬ (1-2 предложения)
Сформулируй ключевой месседж всего видео. О чем оно глобально и какой главный вывод должен сделать зритель?

📊 КЛЮЧЕВЫЕ ТЕЗИСЫ И АНАЛИЗ (3-5 блоков)
Разбей информацию на логические темы. Внутри каждого блока укажи:
• Важные факты, цифры, имена, названия препаратов/технологий или явлений.
• Развенчанные мифы: четко фиксируй формат "Миф: ... / Реальность: ...".
• Причинно-следственные связи (почему происходит именно так, а не иначе).

💡 ПРАКТИЧЕСКИЕ ИНСАЙТЫ И ОГРАНИЧЕНИЯ (Списки)
1. Главные рекомендации: что конкретно спикер советует делать или внедрять.
2. Категорические запреты, риски и противопоказания: Четко выдели ситуации, когда описанные методы/процедуры НЕЛЬЗЯ применять (например, фазы обострения болезней, строгие табу спикеров, финансовые риски, ситуации, когда метод нанесет прямой вред).
3. Важные условия: от каких сопутствующих факторов (образ жизни, подготовка, фазы циклов/процессов, бюджет) зависит успех.

📐 КРАТКИЙ ИТОГ (1 предложение)
Финальный punchline-вывод."""

ALLOWED_FILE_EXTENSIONS = frozenset({"pdf", "docx", "txt"})

_openai_client: AsyncOpenAI | None = None


@dataclass(slots=True, frozen=True)
class SummarizeResult:
    ok: bool
    summary: str = ""
    error_code: str = ""
    error_message: str = ""


SourceKind = Literal["plain", "youtube", "article", "file", "vk_video"]

# Страницы-заглушки (VK, соцсети без контента) — не отправляем в LLM.
_JUNK_PAGE_MARKERS = (
    "устаревший браузер",
    "outdated browser",
    "update your browser",
    "обновите браузер",
    "рекомендуемые браузеры: opera",
)


def is_vk_video_url(url: str) -> bool:
    lower = (url or "").lower()
    return (
        "vkvideo.ru" in lower
        or "vk.com/video" in lower
        or "vk.com/clip" in lower
        or "m.vk.com/video" in lower
    )


def chunk_text(text: str, limit: int = DEFAULT_CHUNK) -> list[str]:
    if not text:
        return []
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def summarizer_llm_configured() -> bool:
    """Достаточно OPENAI_API_KEY или уже используемого OPENROUTER_API_KEY."""
    return bool(settings.openai_api_key.strip() or settings.openrouter_key.strip())


def _openrouter_model_id() -> str:
    model = settings.summarizer_model.strip() or "gpt-4o-mini"
    if "/" in model:
        return model
    return f"openai/{model}"


def _openai() -> AsyncOpenAI:
    global _openai_client
    key = settings.openai_api_key.strip()
    if not key:
        raise RuntimeError("Задайте OPENAI_API_KEY в .env")
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=key)
    return _openai_client


def _youtube_video_id(url: str) -> str | None:
    match = YOUTUBE_ID_RE.search(url)
    return match.group(1) if match else None


def _youtube_sync(video_id: str) -> str | None:
    from youtube_transcript_api import YouTubeTranscriptApi

    def _join_transcript(items: object) -> str:
        if not items:
            return ""
        first = items[0] if isinstance(items, list) and items else items
        if isinstance(first, dict):
            return " ".join(str(item.get("text", "")) for item in items)
        return " ".join(str(getattr(snippet, "text", snippet)) for snippet in items)

    try:
        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["ru"])
        except Exception:
            transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["en"])
        text = _join_transcript(transcript).strip()
        if len(text) > MIN_TEXT_LEN:
            return text[:MAX_INPUT_CHARS]
        return None
    except Exception as exc:
        logger.warning("YouTube subtitles failed for %s: %s", video_id, exc)
        try:
            api = YouTubeTranscriptApi()
            try:
                fetched = api.fetch(video_id, languages=["ru"])
            except Exception:
                fetched = api.fetch(video_id, languages=["en"])
            text = _join_transcript(fetched).strip()
            if len(text) > MIN_TEXT_LEN:
                return text[:MAX_INPUT_CHARS]
        except Exception as exc2:
            logger.warning("YouTube subtitles fallback failed for %s: %s", video_id, exc2)
        return None


async def extract_youtube_text(url: str) -> str | None:
    video_id = _youtube_video_id(url)
    if not video_id:
        return None
    return await asyncio.to_thread(_youtube_sync, video_id)


def _web_sync(url: str) -> str | None:
    from bs4 import BeautifulSoup

    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; NeuroMuleSummarizer/1.0)"}
        response = httpx.get(url, headers=headers, timeout=15.0, follow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(separator=" ")).strip()
        if not text:
            return None
        lower = text.lower()
        if any(marker in lower for marker in _JUNK_PAGE_MARKERS):
            logger.warning("web page looks like browser stub, skip: %s", url[:120])
            return None
        return text[:MAX_FILE_CHARS] if len(text) >= MIN_TEXT_LEN else None
    except Exception:
        return None


async def extract_web_article_text(url: str) -> str | None:
    return await asyncio.to_thread(_web_sync, url)


async def _complete_summarizer_llm(messages: list[dict[str, str]]) -> str:
    if settings.openai_api_key.strip():
        response = await _openai().chat.completions.create(
            model=settings.summarizer_model,
            messages=messages,
            temperature=0.2,
        )
        return (response.choices[0].message.content or "").strip()

    if settings.openrouter_key.strip():
        from services.ai_text import ask_ai_messages

        result = await ask_ai_messages(
            settings,
            messages,
            models=[_openrouter_model_id()],
            temperature=0.2,
            max_tokens=settings.openrouter_premium_max_output_tokens,
        )
        return (result.get("content") or "").strip()

    raise RuntimeError("Задайте OPENAI_API_KEY или OPENROUTER_API_KEY в .env")


def _file_sync(path: Path, extension: str) -> str | None:
    ext = extension.lower().lstrip(".")
    try:
        if ext == "pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            text = "".join(page.extract_text() or "" for page in reader.pages)
            return text[:MAX_FILE_CHARS] if text.strip() else None
        if ext == "docx":
            import docx

            document = docx.Document(str(path))
            text = "\n".join(para.text for para in document.paragraphs)
            return text[:MAX_FILE_CHARS] if text.strip() else None
        if ext == "txt":
            raw = path.read_text(encoding="utf-8", errors="ignore")
            return raw[:MAX_FILE_CHARS] if raw.strip() else None
    except Exception:
        return None
    return None


async def extract_file_text(data: bytes, extension: str) -> str | None:
    ext = extension.lower().lstrip(".")
    if ext not in ALLOWED_FILE_EXTENSIONS:
        return None
    path = Path(f"_summarizer_upload.{ext}")
    try:
        path.write_bytes(data)
        return await asyncio.to_thread(_file_sync, path, ext)
    finally:
        path.unlink(missing_ok=True)


async def resolve_raw_text(user_text: str | None) -> tuple[str | None, SourceKind]:
    text = (user_text or "").strip()
    if not text:
        return None, "plain"

    url_match = URL_RE.search(text)
    if not url_match:
        return text, "plain"

    url = url_match.group(0).rstrip(").,]")
    if is_vk_video_url(url):
        return None, "vk_video"
    if "youtube.com" in url or "youtu.be" in url:
        return await extract_youtube_text(url), "youtube"
    return await extract_web_article_text(url), "article"


async def summarize_text(raw_text: str | None) -> SummarizeResult:
    cleaned = (raw_text or "").strip()
    if len(cleaned) < MIN_TEXT_LEN:
        return SummarizeResult(
            ok=False,
            error_code="too_short",
            error_message="Не удалось извлечь текст или он слишком короткий (минимум 100 символов).",
        )

    payload = cleaned[:MAX_INPUT_CHARS]
    if not summarizer_llm_configured():
        return SummarizeResult(
            ok=False,
            error_code="no_api_key",
            error_message="Саммаризатор недоступен: задайте OPENAI_API_KEY или OPENROUTER_API_KEY в .env.",
        )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Сделай саммари этого материала:\n\n{payload}"},
    ]
    try:
        summary = await _complete_summarizer_llm(messages)
        if not summary:
            return SummarizeResult(
                ok=False,
                error_code="ai_empty",
                error_message="Модель вернула пустой ответ.",
            )
        return SummarizeResult(ok=True, summary=summary)
    except Exception:
        logger.exception("summarizer LLM error")
        return SummarizeResult(
            ok=False,
            error_code="ai_failed",
            error_message="Ошибка ИИ-модели. Попробуйте позже.",
        )


async def summarize_from_user_input(user_text: str | None) -> SummarizeResult:
    raw, _kind = await resolve_raw_text(user_text)
    return await summarize_text(raw)


async def summarize_from_file(data: bytes, extension: str) -> SummarizeResult:
    raw = await extract_file_text(data, extension)
    return await summarize_text(raw)
