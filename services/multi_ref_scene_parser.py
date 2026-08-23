"""Scene Director: map Russian multi-ref prompts to reference slots and placements."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from config import Settings

logger = logging.getLogger(__name__)

SCENE_DIRECTOR_MODEL = "google/gemini-2.5-flash"
SCENE_DIRECTOR_TIMEOUT_SEC = 45.0

_FENCED_JSON_RE = re.compile(
    r"^\s*```(?:json)?\s*\n?(.*?)\n?```\s*$",
    re.DOTALL | re.IGNORECASE,
)
_FENCE_OPEN_RE = re.compile(r"^\s*```(?:json)?\s*\n?", re.IGNORECASE)
_FENCE_CLOSE_RE = re.compile(r"\n?```\s*$")

_REF_SLOT_MAPPER_SYSTEM = (
    "You map input_references indices to group portrait roles. "
    "Given a Russian user prompt and English face descriptions per index (0-based), "
    "assign EVERY reference to exactly one role. "
    "CRITICAL: match by GENDER and APPARENT AGE from face descriptions — "
    "upload order often does NOT match narrative order in the prompt. "
    "ref_index in output MUST point to the face description that matches the role — "
    "never assign mother/woman to a girl child description or daughter to adult female. "
    "A girl/child face must map to daughter/child, NEVER mother/woman. "
    "An adult female face must map to mother/woman, NEVER daughter/child. "
    "A boy/child male face must map to son/child, NEVER father/man. "
    "When two children: use hair/face cues to assign daughter vs son uniquely. "
    "Output JSON only:\n"
    '{"characters":[{"ref_index":0,"label":"father","placement":"as in user scene",'
    '"appearance_anchor":"adult male, strong jaw"}]}\n'
    "Rules: unique ref_index; cover all indices 0..N-1; "
    "appearance_anchor from that index face description; never output numeric ages."
)

_SCENE_DIRECTOR_SYSTEM = (
    "You are a Scene Director for multi-reference AI group portraits. "
    "Given a Russian user prompt and face descriptions for each input_references index "
    "(0-based), assign every reference to a distinct character role with spatial placement. "
    "Handle any combination: couples, parents/children, past/present self, large families, "
    "owners with pets, album collages, etc. "
    "Match references to roles using face descriptions — upload order may NOT match narrative order. "
    "Output JSON only with this schema:\n"
    '{"characters":[{"ref_index":0,"label":"husband","placement":"left foreground","appearance_anchor":"..."}],'
    '"scene_description_en":"cinematic English scene description"}\n'
    "Rules: ref_index must be unique per character; cover all references when possible; "
    "appearance_anchor is a short English identity cue from the matching face description; "
    "scene_description_en is a photorealistic cinematic scene in English; "
    "never invent numeric ages. "
    "If the user prompt contains EDIT REQUEST, map the edit to the correct character by "
    "role (e.g. mother/mom/мама) and keep all other characters unchanged."
)


class SceneCharacter(BaseModel):
    ref_index: int = Field(ge=0, le=9)
    label: str = Field(min_length=1, max_length=80)
    placement: str = Field(min_length=1, max_length=200)
    appearance_anchor: str = Field(default="", max_length=300)


class SceneLayout(BaseModel):
    characters: list[SceneCharacter] = Field(min_length=1, max_length=10)
    scene_description_en: str = Field(min_length=1, max_length=2000)


def strip_json_markdown_fence(raw: str) -> str:
    """Remove ```json ... ``` wrappers before JSON parsing."""
    text = (raw or "").strip()
    if not text:
        return text
    match = _FENCED_JSON_RE.match(text)
    if match:
        return match.group(1).strip()
    text = _FENCE_OPEN_RE.sub("", text, count=1)
    text = _FENCE_CLOSE_RE.sub("", text, count=1)
    return text.strip()


def _build_director_user_message(user_prompt_ru: str, face_descriptions: list[str]) -> str:
    lines = [
        f"User prompt (Russian): {(user_prompt_ru or '').strip()}",
        f"Number of input_references: {len(face_descriptions)}",
        "Face descriptions by index:",
    ]
    for idx, desc in enumerate(face_descriptions):
        lines.append(f"  input_references[{idx}]: {(desc or '').strip() or '(no description)'}")
    return "\n".join(lines)


def _fallback_scene_layout(user_prompt_ru: str, face_descriptions: list[str]) -> SceneLayout:
    count = max(2, len(face_descriptions))
    characters: list[SceneCharacter] = []
    for idx in range(count):
        desc = (face_descriptions[idx] if idx < len(face_descriptions) else "").strip()
        characters.append(
            SceneCharacter(
                ref_index=idx,
                label=f"Person {idx + 1}",
                placement="in the scene as described by the user",
                appearance_anchor=desc[:200] if desc else f"match input_references[{idx}]",
            )
        )
    return SceneLayout(
        characters=characters,
        scene_description_en=(user_prompt_ru or "group portrait together").strip(),
    )


def _parse_scene_layout_json(raw: str) -> SceneLayout:
    cleaned = strip_json_markdown_fence(raw)
    data: Any = json.loads(cleaned)
    return SceneLayout.model_validate(data)


async def parse_multi_ref_scene(
    settings: Settings,
    user_prompt_ru: str,
    face_descriptions: list[str],
) -> SceneLayout:
    """Map Russian prompt + face descriptions → structured scene layout."""
    if len(face_descriptions) < 2:
        raise ValueError("multi-ref scene requires at least 2 face descriptions")

    from services.ai_text import ask_ai_messages

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SCENE_DIRECTOR_SYSTEM},
        {"role": "user", "content": _build_director_user_message(user_prompt_ru, face_descriptions)},
    ]
    try:
        completion = await ask_ai_messages(
            settings,
            messages,
            models=[SCENE_DIRECTOR_MODEL],
            max_tokens=512,
            timeout=SCENE_DIRECTOR_TIMEOUT_SEC,
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        raw = (completion.content or "").strip()
        if not raw:
            raise ValueError("empty scene director response")
        layout = _parse_scene_layout_json(raw)
    except (json.JSONDecodeError, ValidationError, ValueError, RuntimeError) as exc:
        logger.warning("multi_ref_scene_parser fallback (%s)", exc)
        return _fallback_scene_layout(user_prompt_ru, face_descriptions)
    except Exception:
        logger.exception("multi_ref_scene_parser failed, using fallback layout")
        return _fallback_scene_layout(user_prompt_ru, face_descriptions)

    seen: set[int] = set()
    deduped: list[SceneCharacter] = []
    for character in layout.characters:
        if character.ref_index in seen:
            continue
        if character.ref_index >= len(face_descriptions):
            continue
        seen.add(character.ref_index)
        deduped.append(character)

    if len(deduped) < 2:
        return _fallback_scene_layout(user_prompt_ru, face_descriptions)

    return SceneLayout(
        characters=deduped,
        scene_description_en=layout.scene_description_en.strip(),
    )


def _fallback_slot_layout(user_prompt_ru: str, face_descriptions: list[str]) -> SceneLayout:
    """Face-keyword heuristic when LLM slot mapper fails."""
    from services.group_ref_slot_map import build_group_slot_layout

    return build_group_slot_layout(user_prompt_ru, face_descriptions, mapped_layout=None)


async def map_multi_ref_slots(
    settings: Settings,
    user_prompt_ru: str,
    face_descriptions: list[str],
) -> SceneLayout:
    """Face-aware ref_index → role mapping without paraphrasing the user scene."""
    if len(face_descriptions) < 2:
        raise ValueError("multi-ref slot map requires at least 2 face descriptions")

    from services.ai_text import ask_ai_messages

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _REF_SLOT_MAPPER_SYSTEM},
        {"role": "user", "content": _build_director_user_message(user_prompt_ru, face_descriptions)},
    ]
    prompt = (user_prompt_ru or "").strip()
    try:
        completion = await ask_ai_messages(
            settings,
            messages,
            models=[SCENE_DIRECTOR_MODEL],
            max_tokens=384,
            timeout=SCENE_DIRECTOR_TIMEOUT_SEC,
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        raw = (completion.content or "").strip()
        if not raw:
            raise ValueError("empty ref slot mapper response")
        data: Any = json.loads(strip_json_markdown_fence(raw))
        characters_raw = data.get("characters") if isinstance(data, dict) else None
        if not isinstance(characters_raw, list):
            raise ValueError("missing characters array")
        characters = [SceneCharacter.model_validate(item) for item in characters_raw]
    except (json.JSONDecodeError, ValidationError, ValueError, RuntimeError) as exc:
        logger.warning("multi_ref_slot_mapper fallback (%s)", exc)
        return _fallback_slot_layout(prompt, face_descriptions)
    except Exception:
        logger.exception("multi_ref_slot_mapper failed, using fallback layout")
        return _fallback_slot_layout(prompt, face_descriptions)

    seen: set[int] = set()
    deduped: list[SceneCharacter] = []
    for character in characters:
        if character.ref_index in seen:
            continue
        if character.ref_index >= len(face_descriptions):
            continue
        seen.add(character.ref_index)
        desc = (face_descriptions[character.ref_index] or "").strip()
        deduped.append(
            SceneCharacter(
                ref_index=character.ref_index,
                label=character.label,
                placement=(character.placement or "as in user scene").strip(),
                appearance_anchor=(character.appearance_anchor or desc[:200] or character.label).strip(),
            )
        )

    if len(deduped) < len(face_descriptions):
        covered = {c.ref_index for c in deduped}
        for idx in range(len(face_descriptions)):
            if idx in covered:
                continue
            desc = (face_descriptions[idx] or "").strip()
            deduped.append(
                SceneCharacter(
                    ref_index=idx,
                    label=f"Person {idx + 1}",
                    placement="as in user scene",
                    appearance_anchor=desc[:200] if desc else f"match input_references[{idx}]",
                )
            )

    if len(deduped) < 2:
        return _fallback_slot_layout(prompt, face_descriptions)

    return SceneLayout(characters=deduped, scene_description_en=prompt or "group portrait together")
