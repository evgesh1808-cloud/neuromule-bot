"""Типы Billing & AI Pipeline Manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class TariffTier(str, Enum):
    FREE = "FREE"
    MINI = "MINI"
    SMART = "SMART"
    ULTRA = "ULTRA"

    @classmethod
    def from_db(cls, raw: str | None) -> TariffTier:
        v = (raw or "FREE").strip().upper()
        if v in ("MINI",):
            return cls.MINI
        if v in ("SMART",):
            return cls.SMART
        if v in ("ULTRA",):
            return cls.ULTRA
        return cls.FREE


class CurrencyKind(str, Enum):
    ENERGY = "energy"
    CRYSTALS = "crystals"
    FREE_SLOT = "free_slot"
    NONE = "none"


class ShopPackName(str, Enum):
    MINI = "MINI"
    SMART = "SMART"
    ULTRA = "ULTRA"
    CRYSTALS_10 = "crystals_10"
    CRYSTALS_40 = "crystals_40"
    CRYSTALS_100 = "crystals_100"


class SpendFeature(str, Enum):
    CHAT = "chat"
    IMAGE = "image"
    HD_REPORT = "hd_report"
    HD_MATCH = "hd_match"
    HD_ADVICE = "hd_advice"
    VIDEO = "video"
    VIDEO_EXTEND = "video_extend"
    VIDEO_LONG = "video_long"
    ANIMATE = "animate"
    MUSIC = "music"
    UPSCALE = "upscale"


@dataclass(frozen=True)
class UserBillingState:
    """Снимок биллинга пользователя."""

    user_id: int
    current_tariff: TariffTier
    energy_free: int
    energy_paid: int
    crystals: int
    last_energy_reset: str | None
    invited_by_id: int | None
    first_purchase_done: bool
    photo_daily_date: str | None
    photo_daily_count: int

    @property
    def total_energy(self) -> int:
        return self.energy_free + self.energy_paid


@dataclass(frozen=True)
class ChargeBreakdown:
    """Детализация списания для отката."""

    charge_id: str
    energy_free: int = 0
    energy_paid: int = 0
    crystals: int = 0
    used_photo_free_slot: bool = False


@dataclass(frozen=True)
class SpendResult:
    ok: bool
    charge: ChargeBreakdown | None = None
    error: str = ""


@dataclass(frozen=True)
class ChatRoutePlan:
    """План маршрутизации текстового чата."""

    model_id: str
    price_type: CurrencyKind
    energy_cost: int
    crystal_cost: int
    is_expert_role: bool
    blocked: bool = False
    block_reason: str = ""


@dataclass(frozen=True)
class ImageSpendPlan:
    model_key: str
    energy_cost: int
    crystal_cost: int
    crystals_only: bool
    use_free_daily_slot: bool


@dataclass(frozen=True)
class VideoScenarioSpec:
    scenario_id: str
    title_ru: str
    crystal_cost: int
    category: str
    replicate_model: str = "luma/ray-flash"
    needs_face: bool = False
    needs_translate: bool = False


@dataclass(frozen=True)
class VideoRoutePlan:
    scenario: VideoScenarioSpec
    crystal_cost: int
    queue_priority: int
    extend_available: bool = True


@dataclass(frozen=True)
class PurchaseResult:
    ok: bool
    pack_name: str
    tariff_updated: TariffTier | None = None
    energy_paid_added: int = 0
    crystals_added: int = 0
    referral_crystals_to_inviter: int = 0
    error: str = ""
