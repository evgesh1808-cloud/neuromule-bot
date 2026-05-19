"""Фасад Billing & AI Pipeline Manager."""

from __future__ import annotations

from services.billing import chat_pipeline, hd_pipeline, image_pipeline, shop, store, video_pipeline
from services.billing.store import init_billing_schema, load_user_billing, refund_charge, reset_daily_free_energy
from services.billing.translator import translate_prompt_to_english
from services.billing.types import (
    ChatRoutePlan,
    PurchaseResult,
    SpendResult,
    UserBillingState,
    VideoRoutePlan,
)

__all__ = [
    "BillingManager",
    "init_billing_schema",
    "load_user_billing",
    "reset_daily_free_energy",
    "refund_charge",
]


class BillingManager:
    """Единая точка входа для use-case и generation workers."""

    # Shop
    process_purchase = staticmethod(shop.process_purchase)
    pack_name_from_catalog_index = staticmethod(shop.pack_name_from_catalog_index)

    # Chat
    plan_text_chat = staticmethod(chat_pipeline.plan_text_chat)
    handle_text_chat = staticmethod(chat_pipeline.handle_text_chat)

    # Images
    spend_image_resource = staticmethod(image_pipeline.spend_image_resource)
    normalize_image_model = staticmethod(image_pipeline.normalize_image_model)

    # HD
    spend_hd_advice = staticmethod(hd_pipeline.spend_hd_advice)
    spend_hd_full_report = staticmethod(hd_pipeline.spend_hd_full_report)
    spend_hd_match = staticmethod(hd_pipeline.spend_hd_match)
    spend_upscale = staticmethod(hd_pipeline.spend_upscale)
    spend_animate = staticmethod(hd_pipeline.spend_animate)
    spend_music = staticmethod(hd_pipeline.spend_music)

    # Video
    VIDEO_SCENARIOS = video_pipeline.VIDEO_SCENARIOS
    resolve_video_route = staticmethod(video_pipeline.resolve_video_route)
    spend_video_scenario = staticmethod(video_pipeline.spend_video_scenario)
    spend_video_extend = staticmethod(video_pipeline.spend_video_extend)
    spend_video_long = staticmethod(video_pipeline.spend_video_long)

    # Utils
    translate_prompt_to_english = staticmethod(translate_prompt_to_english)
    refund = staticmethod(refund_charge)
    load_user = staticmethod(load_user_billing)
