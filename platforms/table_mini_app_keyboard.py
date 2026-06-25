"""Inline-клавиатура Telegram Web App для интерактивных отчётов table_generator."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from config import settings
from content import messages as msg
from services.table_chart_types import ChartType

# Дефолт для dev; в проде задайте WEBAPP_TABLE_REPORTS_URL в .env (полный GitHub Pages URL).
_DEFAULT_MINI_APP_TEMPLATE = (
    "https://your-user.github.io/neuromule-table/?report_id={report_id}"
)
# Версия UI — сбрасывает кэш Telegram WebApp при обновлении дашборда.
_MINI_APP_UI_VERSION = "20260527e"


def build_table_mini_app_url(
    report_id: int | str,
    *,
    platform: str | None = None,
) -> str:
    """
    URL Mini App с актуальным ``report_id`` из SQLite.

    Формат в ``.env``:
    ``https://<user>.github.io/<repo>/?report_id={report_id}``

    Не используйте ``https://github.io{report_id}`` — нужен полный путь Pages.
    """
    template = (settings.webapp_table_reports_url or _DEFAULT_MINI_APP_TEMPLATE).strip()
    rid = str(report_id).strip()
    if not rid:
        raise ValueError("report_id is required for mini app URL")
    if "{report_id}" in template:
        url = template.format(report_id=rid)
    elif template.endswith(("=", "&")):
        url = f"{template}{rid}"
    else:
        sep = "&" if "?" in template else "?"
        url = f"{template}{sep}report_id={rid}"

    api_base = (settings.mini_app_api_base_url or "").strip().rstrip("/")
    if api_base and "api_base=" not in url:
        joiner = "&" if "?" in url else "?"
        url = f"{url}{joiner}api_base={api_base}"
    if "ui_v=" not in url:
        joiner = "&" if "?" in url else "?"
        url = f"{url}{joiner}ui_v={_MINI_APP_UI_VERSION}"
    if platform:
        from services.marketplace_platform import normalize_marketplace_platform

        platform_key = normalize_marketplace_platform(platform)
        api_map = {
            "wildberries": "wildberries",
            "ozon": "ozon",
            "yandex": "yandex",
            "1c": "1c",
        }
        platform_api = api_map.get(platform_key, "wildberries")
        if "platform=" not in url:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}platform={platform_api}"
    return url


def get_table_mini_app_keyboard(
    report_id: int | str | None,
    *,
    platform: str | None = None,
) -> InlineKeyboardMarkup | None:
    """Кнопка Web App — премиальный дашборд ABC и What-If."""
    if report_id is None:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.BTN_MINI_APP_DASHBOARD,
                    web_app=WebAppInfo(url=build_table_mini_app_url(report_id, platform=platform)),
                )
            ]
        ]
    )


def _chart_row(
    active: ChartType,
    report_id: int | str | None = None,
) -> list[InlineKeyboardButton]:
    _wb_keys = {
        ChartType.PIE: "pie",
        ChartType.LINE: "line",
        ChartType.BAR: "barh",
    }

    def _btn(label: str, chart: ChartType) -> InlineKeyboardButton:
        suffix = " ✓" if active == chart else ""
        wb_key = _wb_keys[chart]
        if report_id is not None:
            callback_data = f"{msg.CB_WB_CHART_PREFIX}{wb_key}:{report_id}"
        else:
            callback_data = f"{msg.CB_TABLE_CHART_PREFIX}{chart.value}"
        return InlineKeyboardButton(
            text=f"{label}{suffix}",
            callback_data=callback_data,
        )

    return [
        _btn(msg.BTN_TABLE_CHART_PIE, ChartType.PIE),
        _btn(msg.BTN_TABLE_CHART_LINE, ChartType.LINE),
        _btn(msg.BTN_TABLE_CHART_BAR, ChartType.BAR),
    ]


def table_delivery_keyboard(
    chart_type: ChartType,
    report_id: int | str | None = None,
    *,
    platform: str | None = None,
) -> InlineKeyboardMarkup:
    """Mini App + переключатели типа графика (pie/line/bar)."""
    rows: list[list[InlineKeyboardButton]] = []
    mini_row = get_table_mini_app_keyboard(report_id, platform=platform)
    if mini_row is not None:
        rows.extend(mini_row.inline_keyboard)
    rows.append(_chart_row(chart_type, report_id))
    return InlineKeyboardMarkup(inline_keyboard=rows)
