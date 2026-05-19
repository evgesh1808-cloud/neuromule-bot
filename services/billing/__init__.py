"""
Billing & AI Pipeline Manager — тарифы, баланс, маршрутизация генераций.

Стек: Python 3 + aiosqlite (как в основном проекте).
"""

from services.billing.manager import BillingManager, init_billing_schema, load_user_billing, refund_charge
from services.billing.types import TariffTier, UserBillingState

billing = BillingManager()

__all__ = [
    "BillingManager",
    "billing",
    "init_billing_schema",
    "load_user_billing",
    "refund_charge",
    "TariffTier",
    "UserBillingState",
]
