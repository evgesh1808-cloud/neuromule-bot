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
# ``FREE_TEXT_MODEL`` / ``PAID_TEXT_MODEL`` в .env (без случайных openrouter/free|auto).
_DEFAULT_FREE_CHAT_MODEL = "google/gemini-2.5-flash"
FREE_CHAT_MODEL = (settings.free_text_model or "").strip() or _DEFAULT_FREE_CHAT_MODEL
PAID_CHAT_MODEL = (settings.paid_text_model or "").strip() or FREE_CHAT_MODEL


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
    """Одна строка реестра — id, заголовок UI, ключ тарифа, категория.

    ``inputs_needed`` — упорядоченный список ключей, которые FSM соберёт у
    пользователя перед стартом Replicate-job (например ``("selfie_self",
    "selfie_friend")`` для двойной замены лиц). Пустой кортеж = inputs не нужны
    либо обрабатываются через ``needs_face`` / ``needs_translate``.
    """

    scenario_id: str
    title_ru: str
    tier: str  # ключ в VideoTierCosts: tier_50 | tier_70 | ...
    category: str
    needs_face: bool = False
    needs_translate: bool = True
    inputs_needed: tuple[str, ...] = ()


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
            inputs_needed=("photo_self", "text_script"),
        ),
        VideoScenarioEntry(
            "custom_video_script",
            "Видео + Сценарий пользователя",
            "custom_video",
            "custom",
            inputs_needed=("video_mp4", "text_script"),
        ),
        VideoScenarioEntry("video_pro_5sec", "PRO-видео 5 сек", "pro_5sec", "pro_base"),
        VideoScenarioEntry("video_extend_5sec", "Продлить видео (+5 сек)", "extend_5sec", "extend"),
        VideoScenarioEntry(
            "video_long_pro",
            "Длинное PRO-видео (15–20 сек)",
            "long_15_20",
            "long",
        ),
        VideoScenarioEntry(
            "face_double_prank",
            "Пранк на двоих — замена двух лиц",
            "tier_50",
            "face_double",
            needs_face=True,
            inputs_needed=("selfie_self", "selfie_friend"),
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

def build_paid_image_model_entries(cfg: "Settings") -> dict[str, ImageModelPrice]:
    """Матрица PRO-фото из ``config`` (без хардкода в пайплайне)."""
    return {
        "imagen4": ImageModelPrice(cfg.paid_imagen_energy_cost, cfg.paid_imagen_crystal_cost),
        "flux_schnell": ImageModelPrice(cfg.paid_flux_energy_cost, cfg.paid_flux_crystal_cost),
        "nano_banana2": ImageModelPrice(cfg.paid_banana2_energy_cost, cfg.paid_banana2_crystal_cost),
        "nano_banana_pro": ImageModelPrice(
            cfg.paid_banana_pro_energy_cost, cfg.paid_banana_pro_crystal_cost
        ),
        "gpt_image2": ImageModelPrice(
            0, cfg.cost_image_dalle_crystals, crystals_only=True
        ),
    }


@dataclass(frozen=True)
class BusinessCatalog:
    chat: ChatCosts
    video_tiers: VideoTierCosts
    legal: LegalUrls
    daily_free_energy: int
    free_imagen_daily_limit: int
    free_imagen_overlimit_cost: int
    free_pro_image_cost: int
    free_other_image_crystals: int
    hd_advice_cost: int
    hd_full_report_cost: int
    hd_match_cost: int
    music_cost: int
    animate_cost: int
    upscale_cost: int
    referral_first_purchase_crystals: int
    shop_packs: dict[str, dict[str, int | str | bool | None]]
    video_entries: tuple[VideoScenarioEntry, ...]
    paid_image_models: dict[str, ImageModelPrice]
    image_aliases: dict[str, str]

    def video_crystal_cost(self, tier_key: str) -> int:
        return _tier_cost(self.video_tiers, tier_key)

    def scenario_cost_map(self) -> dict[str, int]:
        return {e.scenario_id: self.video_crystal_cost(e.tier) for e in self.video_entries}


def _shop_pack(
    *,
    name: str,
    price_rub: int,
    price_stars: int,
    paid_energy: int,
    crystals: int,
    days: int | None,
    duo_access: bool,
    tariff: str | None,
) -> dict[str, int | str | bool | None]:
    """Единая запись каталога: новые поля + legacy-ключи для инвойсов."""
    return {
        "name": name,
        "price_rub": price_rub,
        "price_stars": price_stars,
        "paid_energy": paid_energy,
        "energy_paid": paid_energy,
        "crystals": crystals,
        "days": days,
        "duo_access": duo_access,
        "family_access": duo_access,  # deprecated alias
        "tariff": tariff,
        "rub_kopecks": price_rub * 100,
        "stars": price_stars,
    }


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
    shop_packs: dict[str, dict[str, int | str | bool | None]] = {
        "MINI": _shop_pack(
            name="Пакет MINI",
            price_rub=cfg.mini_rub_kopecks // 100,
            price_stars=cfg.mini_stars,
            paid_energy=cfg.mini_energy,
            crystals=cfg.mini_crystals,
            days=cfg.mini_days,
            duo_access=False,
            tariff="MINI",
        ),
        "SMART": _shop_pack(
            name="Пакет SMART",
            price_rub=cfg.smart_rub_kopecks // 100,
            price_stars=cfg.smart_stars,
            paid_energy=cfg.smart_energy,
            crystals=cfg.smart_crystals,
            days=cfg.smart_days,
            duo_access=False,
            tariff="SMART",
        ),
        "ULTRA_3DAYS": _shop_pack(
            name="Пакет ULTRA (3 дня)",
            price_rub=cfg.ultra_3d_rub_kopecks // 100,
            price_stars=cfg.ultra_3d_stars,
            paid_energy=cfg.ultra_3d_energy,
            crystals=cfg.ultra_3d_crystals,
            days=cfg.ultra_3d_days,
            duo_access=False,
            tariff="ULTRA",
        ),
        "ULTRA_1WEEK": _shop_pack(
            name="Пакет ULTRA (1 неделя)",
            price_rub=cfg.ultra_1w_rub_kopecks // 100,
            price_stars=cfg.ultra_1w_stars,
            paid_energy=cfg.ultra_1w_energy,
            crystals=cfg.ultra_1w_crystals,
            days=cfg.ultra_1w_days,
            duo_access=False,
            tariff="ULTRA",
        ),
        "ULTRA_1MONTH": _shop_pack(
            name="Пакет ULTRA (1 месяц)",
            price_rub=cfg.ultra_1m_rub_kopecks // 100,
            price_stars=cfg.ultra_1m_stars,
            paid_energy=cfg.ultra_1m_energy,
            crystals=cfg.ultra_1m_crystals,
            days=cfg.ultra_1m_days,
            duo_access=True,
            tariff="ULTRA",
        ),
        "ULTRA": _shop_pack(
            name="Пакет ULTRA (1 месяц)",
            price_rub=cfg.ultra_1m_rub_kopecks // 100,
            price_stars=cfg.ultra_1m_stars,
            paid_energy=cfg.ultra_1m_energy,
            crystals=cfg.ultra_1m_crystals,
            days=cfg.ultra_1m_days,
            duo_access=True,
            tariff="ULTRA",
        ),
        "crystals_10": _shop_pack(
            name="10 Кристаллов",
            price_rub=cfg.crystals_10_rub_kopecks // 100,
            price_stars=cfg.crystals_10_stars,
            paid_energy=0,
            crystals=cfg.crystals_10_amount,
            days=None,
            duo_access=False,
            tariff=None,
        ),
        "crystals_40": _shop_pack(
            name="40 Кристаллов",
            price_rub=cfg.crystals_40_rub_kopecks // 100,
            price_stars=cfg.crystals_40_stars,
            paid_energy=0,
            crystals=cfg.crystals_40_amount,
            days=None,
            duo_access=False,
            tariff=None,
        ),
        "crystals_100": _shop_pack(
            name="100 Кристаллов",
            price_rub=cfg.crystals_100_rub_kopecks // 100,
            price_stars=cfg.crystals_100_stars,
            paid_energy=0,
            crystals=cfg.crystals_100_amount,
            days=None,
            duo_access=False,
            tariff=None,
        ),
    }
    paid = build_paid_image_model_entries(cfg)

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
        free_imagen_overlimit_cost=cfg.free_imagen_overlimit_cost,
        free_pro_image_cost=cfg.cost_image_pro,
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
