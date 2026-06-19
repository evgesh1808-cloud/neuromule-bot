"""PRO-видео, пранки и кастомные сценарии."""

from __future__ import annotations

from dataclasses import dataclass

from business_catalog import catalog
from services.billing import store
from services.billing.types import SpendFeature, SpendResult, TariffTier, VideoRoutePlan, VideoScenarioSpec

# Базовые EN-промпты для Replicate (дополняются текстом пользователя).
SCENARIO_PROMPT_TEMPLATES: dict[str, str] = {
    "pain_homework_explosion": "Cinematic comedy, parent and child at desk, homework papers explode magically, warm home light, 5 seconds",
    "pain_diaries_rain": "School diaries raining from sky onto desk, dramatic slow motion, 5 sec",
    "pain_lego_minefield": "Living room floor covered with LEGO bricks, painful funny walk, cinematic, 5 sec",
    "pain_socks_evolution": "Socks multiplying endlessly in laundry room, surreal humor, 5 sec",
    "pain_shopping_bags": "Person carrying too many shopping bags tearing, comedy, 5 sec",
    "pain_repair_forever": "Endless home renovation, workers never finish, timelapse style, 5 sec",
    "pain_laptop_melt": "Laptop overheating with cartoon smoke, deadline stress, cinematic, 5 sec",
    "pain_coffee_zombie": "Exhausted office worker transformed coffee zombie, funny horror lite, 5 sec",
    "pain_documents_flood": "Paper documents flooding office desk, bureaucracy satire, 5 sec",
    "pain_wallet_moth": "Empty wallet with moths flying out, salary gone metaphor, 5 sec",
    "pain_wildberries_addict": "Endless delivery boxes piling at door, marketplace addiction, 5 sec",
    "pain_credit_monster": "Cartoon credit monster chasing adult, dark comedy, 5 sec",
    "pain_travel_packing": "Overstuffed suitcase won't close, travel chaos, 5 sec",
    "pain_travel_passports": "Frantic search for passports before trip, handheld shake, 5 sec",
    "pain_travel_mosquitoes": "Mosquito swarm at summer cottage, slap comedy, 5 sec",
    "pain_travel_flight_delay": "Airport departure board flipping delays, tired travelers, 5 sec",
    "pain_teenager_bunker": "Messy teenager room bunker, toxic smell visual joke, cinematic, 5 sec",
    "pain_dishes_tower": "Tower of dirty dishes wobbling in kitchen, epic scale, 5 sec",
    "pain_zoom_catastrophe": "Chaotic video call grid, pets and disasters, comedy, 5 sec",
    "pain_diet_break": "Midnight fridge raid feast, guilty pleasure cinematic, 5 sec",
    "pain_travel_beach_reality": "Expectation vs reality beach split screen, humor, 5 sec",
    "pain_travel_hotel_view": "Hotel window view construction site instead of sea, 5 sec",
    "face_prank_shaved": "Portrait morph shaved head prank, realistic, subtle motion",
    "face_prank_tattoo": "Face covered with tattoos appearing, prank reveal",
    "face_prank_arrested": "Person in police arrest pose prank, cinematic",
    "face_prank_rich": "Luxury rich lifestyle transformation prank on face",
    "face_prank_swollen": "Swollen bee sting face prank, exaggerated comedy",
    "face_prank_weight_gain": "Rapid weight gain face morph prank, humor",
    "face_prank_avatar_grandpa": "Age to 80 years face morph, realistic",
    "face_prank_crying_filter": "Dramatic crying filter exaggerated, viral style",
    "face_swap_clown": "Face swap clown makeup, circus background motion",
    "face_swap_anime": "Anime style face transformation epic lighting",
    "face_vip_shrek": "Shrek face swap fantasy swamp cinematic",
    "face_vip_mona_lisa": "Mona Lisa face alive subtle smile animation",
    "face_vip_zombie": "Zombie face horror makeup cinematic",
    "face_vip_cyber_terminator": "Terminator cyborg face metal reveal cinematic",
    "video_pro_5sec": "Cinematic professional short clip, dramatic lighting, 5 seconds",
    "video_extend_5sec": "Continue same scene smoothly for 5 more seconds",
    "video_long_pro": "Extended cinematic scene 15 seconds continuous motion",
}


def build_video_scenario_registry() -> dict[str, VideoScenarioSpec]:
    """Реестр из ``business_catalog.VIDEO_SCENARIO_ENTRIES`` + цены из каталога."""
    reg: dict[str, VideoScenarioSpec] = {}
    for entry in catalog.video_entries:
        cost = catalog.video_crystal_cost(entry.tier)
        reg[entry.scenario_id] = VideoScenarioSpec(
            scenario_id=entry.scenario_id,
            title_ru=entry.title_ru,
            crystal_cost=cost,
            category=entry.category,
            needs_face=entry.needs_face,
            needs_translate=entry.needs_translate,
        )
    return reg


VIDEO_SCENARIOS: dict[str, VideoScenarioSpec] = build_video_scenario_registry()


def resolve_video_prompt(scenario_id: str, user_text: str = "") -> str:
    base = SCENARIO_PROMPT_TEMPLATES.get(scenario_id, "Cinematic scene, professional lighting, 5 seconds")
    extra = (user_text or "").strip()
    if extra:
        return f"{base}. User scene: {extra}"
    return base


def scenario_requires_user_photo(scenario_id: str) -> bool:
    spec = VIDEO_SCENARIOS.get(scenario_id)
    return bool(spec and spec.needs_face)


def scenario_requires_user_text(scenario_id: str) -> bool:
    if scenario_id in ("custom_text_only", "custom_photo_script", "custom_video_script", "video_pro_5sec"):
        return True
    return False


@dataclass(frozen=True)
class VideoAccessResult:
    allowed: bool
    reason: str = ""


def check_video_tariff(tariff: TariffTier) -> VideoAccessResult:
    """Видео доступно SMART/ULTRA (за 💎). FREE и MINI заблокированы."""
    from services.tariffs import can_use_video, normalize_tariff

    tname = normalize_tariff(tariff.value)
    if can_use_video(tname):
        return VideoAccessResult(allowed=True)
    if tname.value == "mini":
        return VideoAccessResult(allowed=False, reason="video_smart_or_higher_required")
    return VideoAccessResult(allowed=False, reason="video_smart_or_higher_required")


def resolve_video_route(scenario_id: str, tariff: TariffTier) -> VideoRoutePlan | None:
    spec = VIDEO_SCENARIOS.get(scenario_id)
    if not spec:
        return None
    priority = 1 if tariff is TariffTier.ULTRA else (2 if tariff is TariffTier.SMART else 3)
    return VideoRoutePlan(
        scenario=spec,
        crystal_cost=spec.crystal_cost,
        queue_priority=priority,
        extend_available=spec.category not in ("extend", "long"),
    )


async def spend_video_scenario(user_id: int, scenario_id: str) -> SpendResult:
    user = await store.load_user_billing(user_id)
    if user.current_tariff is TariffTier.FREE:
        return SpendResult(ok=False, error="free_premium_create_blocked")
    access = check_video_tariff(user.current_tariff)
    if not access.allowed:
        return SpendResult(ok=False, error=access.reason)
    route = resolve_video_route(scenario_id, user.current_tariff)
    if not route:
        return SpendResult(ok=False, error="unknown_scenario")
    charge = await store.atomic_spend(
        user_id,
        SpendFeature.VIDEO.value,
        energy_need=0,
        crystal_need=route.crystal_cost,
        crystals_only=True,
        reserve_photo_slot=False,
        photo_daily_limit=0,
    )
    if not charge:
        return SpendResult(ok=False, error="insufficient_crystals")
    return SpendResult(ok=True, charge=charge)


async def spend_video_extend(user_id: int) -> SpendResult:
    return await spend_video_scenario(user_id, "video_extend_5sec")


async def spend_video_long(user_id: int) -> SpendResult:
    return await spend_video_scenario(user_id, "video_long_pro")
