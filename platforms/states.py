"""Алиас FSM-состояний (импорт ``from platforms.states import WBAuditingStates``).

cfo-v12 (Highload + No-Token UX): загрузка Excel для аудита WB/Ozon/1C
**не требует** личных API-ключей Statistics/Marketplace. Единственный
серверный ``MASTER_WB_API_TOKEN`` — ночной кэш тарифов логистики WB.
"""

from platforms.telegram_states import (
    AdminStates,
    BloggerFlowStates,
    FeedbackStates,
    MusicFlow,
    OneCAuditingStates,
    OzonAuditingStates,
    UserFlow,
    WBAuditingStates,
    YandexAuditingStates,
)

__all__ = (
    "AdminStates",
    "BloggerFlowStates",
    "FeedbackStates",
    "MusicFlow",
    "OneCAuditingStates",
    "OzonAuditingStates",
    "UserFlow",
    "WBAuditingStates",
    "YandexAuditingStates",
)
