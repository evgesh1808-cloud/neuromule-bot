"""
Единый каталог бизнес-правил NeuroMule (DRY).

Все цены, лимиты, ссылки на документы и реестр видео-сценариев строятся из ``config.settings``
(pydantic-settings читает ``.env`` — эквивалент безопасного ``os.getenv`` без хардкода секретов).

Добавление видео-сценария: одна строка в ``VIDEO_SCENARIO_ENTRIES``.
Добавление модели фото: одна строка в ``PAID_IMAGE_MODEL_ENTRIES``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from config import settings

if TYPE_CHECKING:
    from config import Settings


# --- Текстовый чат (модели) ---
FREE_CHAT_MODEL = "google/gemini-2.0-flash-lite:free"
PAID_CHAT_MODEL = "openrouter/auto"


@dataclass(frozen=True)
class ChatCosts:
    standard_energy: int
    standard_crystals: int
    expert_energy: int
    expert_crystals: int


@dataclass(frozen=True)
class VideoTierCosts:
    tier_50: int
    tier_70: int
    tier_80: int
    tier_100: int
    pro_5sec: int
    extend_5sec: int
    long_15_20: int
    custom_text: int
    custom_photo: int
    custom_video: int


@dataclass(frozen=True)
class LegalUrls:
    offer: str
    privacy: str
    subscription: str


@dataclass(frozen=True)
class ImageModelPrice:
    """Платная матрица: (energy, crystals), crystals_only."""

    energy: int
    crystals: int
    crystals_only: bool = False


@dataclass(frozen=True)
class VideoScenarioEntry:
    """Одна строка реестра — id, заголовок UI, ключ тарифа, категория."""

    scenario_id: str
    title_ru: str
    tier: str  # ключ в VideoTierCosts: tier_50 | tier_70 | ...
    category: str
    needs_face: bool = False
    needs_translate: bool = True


def _tier_cost(tiers: VideoTierCosts, tier_key: str) -> int:
    return int(getattr(tiers, tier_key))


# Бытовые боли
_PAIN_50_ENTRIES: tuple[tuple[str, str], ...] = (
    ("pain_homework_explosion", "Уроки с ребенком"),
    ("pain_diaries_rain", "Нашествие двоек"),
    ("pain_lego_minefield", "Минное поле LEGO"),
    ("pain_socks_evolution", "Эволюция носков"),
    ("pain_shopping_bags", "Пакет с пакетами"),
    ("pain_repair_forever", "Вечный ремонт"),
    ("pain_laptop_melt", "Горящий дедлайн"),
    ("pain_coffee_zombie", "Передоз кофе"),
    ("pain_documents_flood", "Бюрократия"),
    ("pain_wallet_moth", "Зарплата улетела"),
    ("pain_wildberries_addict", "Султан маркетплейсов"),
    ("pain_credit_monster", "Ипотека и Кредиты"),
    ("pain_travel_packing", "Чемодан не закрывается"),
    ("pain_travel_passports", "Где паспорта?!"),
    ("pain_travel_mosquitoes", "Отдых на даче/Москиты"),
    ("pain_travel_flight_delay", "Рейс задержан"),
)

_PAIN_70_ENTRIES: tuple[tuple[str, str], ...] = (
    ("pain_teenager_bunker", "Комната подростка / Токсичный бункер"),
    ("pain_dishes_tower", "Эверест из посуды"),
    ("pain_zoom_catastrophe", "Адский созвон"),
    ("pain_diet_break", "Ночной дожор"),
    ("pain_travel_beach_reality", "Ожидание vs Реальность на пляже"),
    ("pain_travel_hotel_view", "Вид на море / Стройка"),
)

_FACE_70_ENTRIES: tuple[tuple[str, str], ...] = (
    ("face_prank_shaved", "Налысо"),
    ("face_prank_tattoo", "Лицо в татуировках"),
    ("face_prank_arrested", "Меня арестовали"),
    ("face_prank_rich", "Успешный успех"),
    ("face_prank_swollen", "Укусила пчела / Отек"),
    ("face_prank_weight_gain", "Сорвался с диеты (+30 кг)"),
    ("face_prank_avatar_grandpa", "Мне уже 80 лет"),
    ("face_prank_crying_filter", "Истерика / Крик"),
)

_FACE_80_ENTRIES: tuple[tuple[str, str], ...] = (
    ("face_swap_clown", "Преврати друга в клоуна"),
    ("face_swap_anime", "Аниме-эпик"),
)

_FACE_100_ENTRIES: tuple[tuple[str, str], ...] = (
    ("face_vip_shrek", "Я — Шрек"),
    ("face_vip_mona_lisa", "Живая Мона Лиза"),
    ("face_vip_zombie", "Зомби-апокалипсис"),
    ("face_vip_cyber_terminator", "Терминатор Т-800"),
)


def _entries_from_pairs(
    pairs: tuple[tuple[str, str], ...],
    *,
    tier: str,
    category: str,
    needs_face: bool = False,
) -> tuple[VideoScenarioEntry, ...]:
    return tuple(
        VideoScenarioEntry(
            scenario_id=sid,
            title_ru=title,
            tier=tier,
            category=category,
            needs_face=needs_face,
        )
        for sid, title in pairs
    )


def build_video_scenario_entries() -> tuple[VideoScenarioEntry, ...]:
    """Полный реестр сценариев для биллинга и UI."""
    custom = (
        VideoScenarioEntry("custom_text_only", "Только Текст", "custom_text", "custom"),
        VideoScenarioEntry(
            "custom_photo_script",
            "Фото + Сценарий пользователя",
            "custom_photo",
            "custom",
        ),
        VideoScenarioEntry(
            "custom_video_script",
            "Видео + Сценарий пользователя",
            "custom_video",
            "custom",
        ),
        VideoScenarioEntry("video_pro_5sec", "PRO-видео 5 сек", "pro_5sec", "pro_base"),
        VideoScenarioEntry("video_extend_5sec", "Продлить видео (+5 сек)", "extend_5sec", "extend"),
        VideoScenarioEntry(
            "video_long_pro",
            "Длинное PRO-видео (15–20 сек)",
            "long_15_20",
            "long",
        ),
    )
    return (
        _entries_from_pairs(_PAIN_50_ENTRIES, tier="tier_50", category="pain")
        + _entries_from_pairs(_PAIN_70_ENTRIES, tier="tier_70", category="pain_heavy")
        + _entries_from_pairs(_FACE_70_ENTRIES, tier="tier_70", category="face_prank", needs_face=True)
        + _entries_from_pairs(_FACE_80_ENTRIES, tier="tier_80", category="face_swap", needs_face=True)
        + _entries_from_pairs(_FACE_100_ENTRIES, tier="tier_100", category="face_vip", needs_face=True)
        + custom
    )


VIDEO_SCENARIO_ENTRIES: tuple[VideoScenarioEntry, ...] = build_video_scenario_entries()


# Алиасы id меню → ключ модели
IMAGE_MODEL_ALIASES: dict[str, str] = {
    "imagen4": "imagen4",
    "imagen_4": "imagen4",
    "flux-schnell": "flux_schnell",
    "flux_schnell": "flux_schnell",
    "gpt_image2": "gpt_image2",
    "dalle_3": "gpt_image2",
    "nano_banana2": "nano_banana2",
    "nano_banana_pro": "nano_banana_pro",
}

# Платные модели: добавление = одна строка (ключ читается в image_pipeline)
PAID_IMAGE_MODEL_ENTRIES: dict[str, ImageModelPrice] = {
    "imagen4": ImageModelPrice(10, 2),
    "flux_schnell": ImageModelPrice(30, 3),
    "nano_banana2": ImageModelPrice(15, 2),
    "nano_banana_pro": ImageModelPrice(35, 3),
    "gpt_image2": ImageModelPrice(0, 5, crystals_only=True),
}


@dataclass(frozen=True)
class BusinessCatalog:
    chat: ChatCosts
    video_tiers: VideoTierCosts
    legal: LegalUrls
    daily_free_energy: int
    free_imagen_daily_limit: int
    free_other_image_crystals: int
    hd_advice_cost: int
    hd_full_report_cost: int
    hd_match_cost: int
    music_cost: int
    animate_cost: int
    upscale_cost: int
    referral_first_purchase_crystals: int
    shop_packs: dict[str, dict[str, int | str | None]]
    video_entries: tuple[VideoScenarioEntry, ...]
    paid_image_models: dict[str, ImageModelPrice]
    image_aliases: dict[str, str]

    def video_crystal_cost(self, tier_key: str) -> int:
        return _tier_cost(self.video_tiers, tier_key)

    def scenario_cost_map(self) -> dict[str, int]:
        return {e.scenario_id: self.video_crystal_cost(e.tier) for e in self.video_entries}


def build_catalog(s: Settings | None = None) -> BusinessCatalog:
    cfg = s or settings
    video = VideoTierCosts(
        tier_50=cfg.cost_video_tier_50,
        tier_70=cfg.cost_video_tier_70,
        tier_80=cfg.cost_video_tier_80,
        tier_100=cfg.cost_video_tier_100,
        pro_5sec=cfg.cost_video_pro_5sec,
        extend_5sec=cfg.cost_video_extend,
        long_15_20=cfg.cost_video_long,
        custom_text=cfg.cost_video_custom_text,
        custom_photo=cfg.cost_video_custom_photo,
        custom_video=cfg.cost_video_custom_video,
    )
    shop_packs: dict[str, dict[str, int | str | None]] = {
        "MINI": {
            "tariff": "MINI",
            "energy_paid": cfg.mini_energy,
            "crystals": cfg.mini_crystals,
            "rub_kopecks": cfg.mini_rub_kopecks,
            "stars": cfg.mini_stars,
        },
        "SMART": {
            "tariff": "SMART",
            "energy_paid": cfg.smart_energy,
            "crystals": cfg.smart_crystals,
            "rub_kopecks": cfg.smart_rub_kopecks,
            "stars": cfg.smart_stars,
        },
        "ULTRA": {
            "tariff": "ULTRA",
            "energy_paid": cfg.ultra_energy,
            "crystals": cfg.ultra_crystals,
            "rub_kopecks": cfg.ultra_rub_kopecks,
            "stars": cfg.ultra_stars,
        },
        "crystals_10": {
            "tariff": None,
            "energy_paid": 0,
            "crystals": cfg.crystals_10_amount,
            "rub_kopecks": cfg.crystals_10_rub_kopecks,
            "stars": cfg.crystals_10_stars,
        },
        "crystals_40": {
            "tariff": None,
            "energy_paid": 0,
            "crystals": cfg.crystals_40_amount,
            "rub_kopecks": cfg.crystals_40_rub_kopecks,
            "stars": cfg.crystals_40_stars,
        },
        "crystals_100": {
            "tariff": None,
            "energy_paid": 0,
            "crystals": cfg.crystals_100_amount,
            "rub_kopecks": cfg.crystals_100_rub_kopecks,
            "stars": cfg.crystals_100_stars,
        },
    }
    paid = dict(PAID_IMAGE_MODEL_ENTRIES)
    paid["gpt_image2"] = ImageModelPrice(0, cfg.cost_image_dalle_crystals, crystals_only=True)

    return BusinessCatalog(
        chat=ChatCosts(
            standard_energy=cfg.cost_chat_standard_energy,
            standard_crystals=cfg.cost_chat_standard_crystals,
            expert_energy=cfg.cost_chat_expert_energy,
            expert_crystals=cfg.cost_chat_expert_crystals,
        ),
        video_tiers=video,
        legal=LegalUrls(
            offer=cfg.service_offer_url,
            privacy=cfg.privacy_policy_url,
            subscription=cfg.subscription_terms_url,
        ),
        daily_free_energy=cfg.daily_free_energy,
        free_imagen_daily_limit=cfg.free_daily_photo_limit,
        free_other_image_crystals=cfg.cost_image_pro,
        hd_advice_cost=0,
        hd_full_report_cost=cfg.cost_hd,
        hd_match_cost=cfg.cost_match,
        music_cost=cfg.cost_music,
        animate_cost=cfg.cost_animate,
        upscale_cost=cfg.cost_upscale,
        referral_first_purchase_crystals=cfg.referral_bonus_energy,
        shop_packs=shop_packs,
        video_entries=VIDEO_SCENARIO_ENTRIES,
        paid_image_models=paid,
        image_aliases=dict(IMAGE_MODEL_ALIASES),
    )


catalog = build_catalog()
