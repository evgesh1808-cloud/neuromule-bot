"""Тесты ночного кэша тарифов WB (services/wb_tariffs_cache.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.wb_tariffs_cache import (
    GlobalTariffsCache,
    WarehouseTariffRow,
    estimate_return_logistics_unit_rub,
    load_global_tariffs_cache,
    save_global_tariffs_cache,
    update_global_tariffs_db,
)


def _sample_cache() -> GlobalTariffsCache:
    return GlobalTariffsCache(
        updated_at="2026-05-27T00:00:00+00:00",
        source="test",
        build="cfo-v11.2-tariffs-cache",
        warehouses={
            "коледино": WarehouseTariffRow(
                warehouse_name="Коледино",
                return_base_rub=50.0,
                return_liter_rub=12.0,
            ),
            "рязань": WarehouseTariffRow(
                warehouse_name="Рязань",
                return_base_rub=45.0,
                return_liter_rub=10.0,
            ),
        },
        default_return_base_rub=50.0,
        default_return_liter_rub=10.0,
    )


def test_save_and_load_global_tariffs_cache(tmp_path: Path) -> None:
    path = tmp_path / "GLOBAL_TARIFFS_CACHE.json"
    cache = _sample_cache()
    save_global_tariffs_cache(cache, path)
    loaded = load_global_tariffs_cache(path)
    assert loaded is not None
    assert loaded.warehouses["коледино"].return_liter_rub == 12.0


def test_estimate_return_logistics_from_warehouse_cache() -> None:
    cache = _sample_cache()
    unit = estimate_return_logistics_unit_rub("Коледино", 2.0, cache=cache, floor_rub=50.0)
    assert unit == pytest.approx(74.0)  # 50 + 12*2


def test_estimate_return_logistics_floor_without_cache() -> None:
    assert estimate_return_logistics_unit_rub("", 1.0, cache=None, floor_rub=50.0) == 50.0


@pytest.mark.asyncio
async def test_update_global_tariffs_db_preserves_cache_on_api_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "GLOBAL_TARIFFS_CACHE.json"
    save_global_tariffs_cache(_sample_cache(), path)

    class _FailClient:
        async def get(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise ConnectionError("network down")

        async def aclose(self) -> None:
            pass

    ok = await update_global_tariffs_db(
        api_token="test-token",
        cache_path=path,
        http_client=_FailClient(),
    )
    assert ok is False
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["warehouses"]["коледино"]["return_liter_rub"] == 12.0


@pytest.mark.asyncio
async def test_update_global_tariffs_db_writes_merged_payload(tmp_path: Path) -> None:
    path = tmp_path / "GLOBAL_TARIFFS_CACHE.json"

    class _OkClient:
        async def get(self, url: str, **kwargs):  # noqa: ANN003
            class _Resp:
                def raise_for_status(self) -> None:
                    pass

                def json(self) -> dict:
                    if "box" in url:
                        return {
                            "response": {
                                "data": {
                                    "warehouseList": [
                                        {
                                            "warehouseName": "Тула",
                                            "boxDeliveryBase": "40",
                                            "boxDeliveryLiter": "8",
                                        }
                                    ]
                                }
                            }
                        }
                    return {
                        "response": {
                            "data": {
                                "warehouseList": [
                                    {
                                        "warehouseName": "Тула",
                                        "deliveryDumpKgtReturnBase": "55",
                                        "deliveryDumpKgtReturnLiter": "11",
                                    }
                                ]
                            }
                        }
                    }

            return _Resp()

        async def aclose(self) -> None:
            pass

    ok = await update_global_tariffs_db(
        api_token="test-token",
        cache_path=path,
        http_client=_OkClient(),
    )
    assert ok is True
    loaded = load_global_tariffs_cache(path)
    assert loaded is not None
    assert "тула" in loaded.warehouses
    assert loaded.warehouses["тула"].return_unit_rub(1.0) == pytest.approx(66.0)
