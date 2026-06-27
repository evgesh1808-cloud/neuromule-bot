"""
Ночной кэш тарифов WB (логистика / хранение / возврат) — «Робот для Робота».

Один запрос к Analytics API по ``MASTER_WB_API_TOKEN`` → ``GLOBAL_TARIFFS_CACHE.json``.
При разборе Excel клиента живые запросы к WB **запрещены** — только чтение JSON.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CACHE_FILENAME = "GLOBAL_TARIFFS_CACHE.json"
_BUILD_TAG = "cfo-v12-tariffs-cache"

# Алиасы складов из отчётов продавцов → ключи в кэше WB.
_WAREHOUSE_ALIASES: dict[str, tuple[str, ...]] = {
    "коледино": ("коледино", "koledino"),
    "подольск": ("подольск", "podolsk"),
    "электросталь": ("электросталь", "elektrostal"),
    "тула": ("тула", "tula"),
    "рязань": ("рязань", "ryazan", "рызань"),
    "казань": ("казань", "kazan"),
    "краснодар": ("краснодар", "krasnodar"),
    "невинномысск": ("невинномысск",),
    "екатеринбург": ("екатеринбург", "екб"),
}


@dataclass(frozen=True)
class WarehouseTariffRow:
    """Тарифы одного склада WB (руб. за базу + руб./литр)."""

    warehouse_name: str
    return_base_rub: float = 0.0
    return_liter_rub: float = 0.0
    delivery_base_rub: float = 0.0
    delivery_liter_rub: float = 0.0
    storage_base_rub: float = 0.0
    storage_liter_rub: float = 0.0

    def return_unit_rub(self, volume_liters: float) -> float:
        liters = max(0.1, float(volume_liters or 1.0))
        return round(self.return_base_rub + self.return_liter_rub * liters, 2)


@dataclass
class GlobalTariffsCache:
    updated_at: str
    source: str
    build: str
    warehouses: dict[str, WarehouseTariffRow]
    default_return_base_rub: float = 50.0
    default_return_liter_rub: float = 0.0

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "updated_at": self.updated_at,
            "source": self.source,
            "build": self.build,
            "default_return_base_rub": self.default_return_base_rub,
            "default_return_liter_rub": self.default_return_liter_rub,
            "warehouses": {
                key: asdict(row) for key, row in self.warehouses.items()
            },
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> GlobalTariffsCache:
        wh: dict[str, WarehouseTariffRow] = {}
        for key, raw in (data.get("warehouses") or {}).items():
            if not isinstance(raw, dict):
                continue
            wh[str(key)] = WarehouseTariffRow(
                warehouse_name=str(raw.get("warehouse_name") or key),
                return_base_rub=float(raw.get("return_base_rub", 0.0) or 0.0),
                return_liter_rub=float(raw.get("return_liter_rub", 0.0) or 0.0),
                delivery_base_rub=float(raw.get("delivery_base_rub", 0.0) or 0.0),
                delivery_liter_rub=float(raw.get("delivery_liter_rub", 0.0) or 0.0),
                storage_base_rub=float(raw.get("storage_base_rub", 0.0) or 0.0),
                storage_liter_rub=float(raw.get("storage_liter_rub", 0.0) or 0.0),
            )
        return cls(
            updated_at=str(data.get("updated_at") or ""),
            source=str(data.get("source") or ""),
            build=str(data.get("build") or _BUILD_TAG),
            warehouses=wh,
            default_return_base_rub=float(
                data.get("default_return_base_rub", 50.0) or 50.0
            ),
            default_return_liter_rub=float(
                data.get("default_return_liter_rub", 0.0) or 0.0
            ),
        )


def default_cache_path() -> Path:
    from config import settings

    custom = (getattr(settings, "wb_tariffs_cache_path", "") or "").strip()
    if custom:
        return Path(custom)
    root = Path(__file__).resolve().parent.parent
    return root / "data" / CACHE_FILENAME


def _parse_wb_money(raw: object) -> float:
    if raw is None:
        return 0.0
    s = str(raw).strip().replace(" ", "").replace(",", ".")
    if not s or s in ("—", "-"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _normalize_warehouse_key(name: str) -> str:
    clean = re.sub(r"\s+", " ", (name or "").strip().lower())
    for canonical, aliases in _WAREHOUSE_ALIASES.items():
        if any(alias in clean for alias in aliases):
            return canonical
    return clean[:64] or "unknown"


def _extract_warehouse_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    response = payload.get("response")
    if isinstance(response, dict):
        data = response.get("data")
        if isinstance(data, dict):
            wl = data.get("warehouseList")
            if isinstance(wl, list):
                return [x for x in wl if isinstance(x, dict)]
    data = payload.get("data")
    if isinstance(data, dict):
        wl = data.get("warehouseList")
        if isinstance(wl, list):
            return [x for x in wl if isinstance(x, dict)]
    if isinstance(payload.get("warehouseList"), list):
        return [x for x in payload["warehouseList"] if isinstance(x, dict)]
    return []


def _merge_tariff_payloads(
    box_payload: dict[str, Any] | None,
    return_payload: dict[str, Any] | None,
) -> dict[str, WarehouseTariffRow]:
    merged: dict[str, WarehouseTariffRow] = {}

    for item in _extract_warehouse_list(box_payload or {}):
        name = str(item.get("warehouseName") or item.get("warehouse") or "").strip()
        if not name:
            continue
        key = _normalize_warehouse_key(name)
        row = merged.get(key) or WarehouseTariffRow(warehouse_name=name)
        merged[key] = WarehouseTariffRow(
            warehouse_name=name,
            return_base_rub=row.return_base_rub,
            return_liter_rub=row.return_liter_rub,
            delivery_base_rub=_parse_wb_money(
                item.get("boxDeliveryBase") or item.get("deliveryBase")
            ),
            delivery_liter_rub=_parse_wb_money(
                item.get("boxDeliveryLiter") or item.get("deliveryLiter")
            ),
            storage_base_rub=_parse_wb_money(
                item.get("boxStorageBase") or item.get("storageBase")
            ),
            storage_liter_rub=_parse_wb_money(
                item.get("boxStorageLiter") or item.get("storageLiter")
            ),
        )

    for item in _extract_warehouse_list(return_payload or {}):
        name = str(item.get("warehouseName") or item.get("warehouse") or "").strip()
        if not name:
            continue
        key = _normalize_warehouse_key(name)
        prev = merged.get(key)
        merged[key] = WarehouseTariffRow(
            warehouse_name=name,
            return_base_rub=_parse_wb_money(
                item.get("deliveryDumpKgtReturnBase")
                or item.get("returnBase")
                or item.get("boxReturnBase")
                or (prev.return_base_rub if prev else 0.0)
            ),
            return_liter_rub=_parse_wb_money(
                item.get("deliveryDumpKgtReturnLiter")
                or item.get("returnLiter")
                or item.get("boxReturnLiter")
                or (prev.return_liter_rub if prev else 0.0)
            ),
            delivery_base_rub=prev.delivery_base_rub if prev else 0.0,
            delivery_liter_rub=prev.delivery_liter_rub if prev else 0.0,
            storage_base_rub=prev.storage_base_rub if prev else 0.0,
            storage_liter_rub=prev.storage_liter_rub if prev else 0.0,
        )

    return merged


def load_global_tariffs_cache(
    path: Path | None = None,
) -> GlobalTariffsCache | None:
    """Читает локальный JSON-кэш (без HTTP)."""
    cache_path = path or default_cache_path()
    if not cache_path.is_file():
        return None
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("wb_tariffs_cache: cannot read %s: %s", cache_path, exc)
        return None
    if not isinstance(raw, dict):
        return None
    return GlobalTariffsCache.from_json_dict(raw)


def save_global_tariffs_cache(
    cache: GlobalTariffsCache,
    path: Path | None = None,
) -> Path:
    cache_path = path or default_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(cache.to_json_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(cache_path)
    return cache_path


def resolve_warehouse_tariff(
    warehouse_name: str,
    cache: GlobalTariffsCache | None = None,
) -> WarehouseTariffRow | None:
    data = cache or load_global_tariffs_cache()
    if data is None or not data.warehouses:
        return None
    key = _normalize_warehouse_key(warehouse_name)
    if key in data.warehouses:
        return data.warehouses[key]
    for wh_key, row in data.warehouses.items():
        if key and (key in wh_key or wh_key in key):
            return row
    return None


def estimate_return_logistics_unit_rub(
    warehouse_name: str,
    volume_liters: float,
    *,
    cache: GlobalTariffsCache | None = None,
    floor_rub: float = 50.0,
) -> float:
    """
    Стоимость одной обратной логистики (руб./шт.) из ночного кэша.

    Без кэша — ``floor_rub`` (исторический пол WB ~50 ₽).
    """
    data = cache or load_global_tariffs_cache()
    liters = max(0.1, float(volume_liters or 1.0))
    if data is None:
        return floor_rub

    row = resolve_warehouse_tariff(warehouse_name, data) if warehouse_name else None
    if row is None:
        unit = data.default_return_base_rub + data.default_return_liter_rub * liters
        return max(floor_rub, round(unit, 2)) if unit > 0 else floor_rub

    unit = row.return_unit_rub(liters)
    if unit <= 0 and row.delivery_base_rub > 0:
        unit = row.delivery_base_rub + row.delivery_liter_rub * liters
    return max(floor_rub, round(unit, 2)) if unit > 0 else floor_rub


async def update_global_tariffs_db(
    *,
    api_token: str | None = None,
    base_url: str | None = None,
    cache_path: Path | None = None,
    http_client: object | None = None,
) -> bool:
    """
    Один ночной HTTPS GET к WB Tariffs API → ``GLOBAL_TARIFFS_CACHE.json``.

    При ошибке сети/API сохраняет предыдущий кэш и возвращает ``False``.
    """
    from config import settings

    token = (api_token or getattr(settings, "master_wb_api_token", "") or "").strip()
    if not token:
        logger.info("wb_tariffs_cache: MASTER_WB_API_TOKEN пуст — пропуск обновления")
        return False

    api_base = (
        base_url
        or getattr(settings, "wb_tariffs_api_base_url", "")
        or "https://common-api.wildberries.ru"
    ).rstrip("/")
    target = cache_path or default_cache_path()
    today = date.today().isoformat()
    headers = {"Authorization": token}

    import httpx

    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        box_resp = await client.get(
            f"{api_base}/api/v1/tariffs/box",
            params={"date": today},
            headers=headers,
        )
        ret_resp = await client.get(
            f"{api_base}/api/v1/tariffs/return",
            params={"date": today},
            headers=headers,
        )
        box_resp.raise_for_status()
        ret_resp.raise_for_status()
        box_json = box_resp.json()
        ret_json = ret_resp.json()
    except Exception as exc:
        logger.warning(
            "wb_tariffs_cache: API fetch failed (%s) — оставляем прежний кэш %s",
            exc,
            target,
        )
        return False
    finally:
        if own_client:
            await client.aclose()

    warehouses = _merge_tariff_payloads(box_json, ret_json)
    if not warehouses:
        logger.warning("wb_tariffs_cache: пустой ответ API — кэш не перезаписан")
        return False

    defaults = list(warehouses.values())
    avg_return_base = sum(r.return_base_rub for r in defaults) / len(defaults)
    avg_return_liter = sum(r.return_liter_rub for r in defaults) / len(defaults)

    cache = GlobalTariffsCache(
        updated_at=datetime.now(timezone.utc).isoformat(),
        source=api_base,
        build=_BUILD_TAG,
        warehouses=warehouses,
        default_return_base_rub=round(avg_return_base, 2) if avg_return_base > 0 else 50.0,
        default_return_liter_rub=round(avg_return_liter, 2),
    )
    save_global_tariffs_cache(cache, target)
    logger.info(
        "wb_tariffs_cache: обновлён %s (%d складов)",
        target,
        len(warehouses),
    )
    return True
