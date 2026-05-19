# Технический аудит NeuroMule (май 2026)

## Резюме

Проведён аудит безопасности, конфигурации и отказоустойчости. Критические секреты в исходниках **не обнаружены** (поиск по репозиторию). Конфигурация цен и сценариев **централизована** в `business_catalog.py` + `config.py` (чтение `.env` через pydantic-settings).

---

## 1. Безопасность и секреты

| Проверка | Статус | Комментарий |
|----------|--------|-------------|
| API-ключи в `.py` | ✅ | Только через `config.settings` |
| `.env` в git | ✅ | В `.gitignore` |
| Шаблон `.env.example` | ✅ | Плейсхолдеры `your_*`, без реальных ключей |
| Логи с токенами | ⚠️ | Не логировать `Authorization`; при отладке — маскировать |

**Важно:** файл `.env` на рабочей машине должен **никогда** не попадать в коммит. На сервере — права `600`, отдельный пользователь Docker.

**Рекомендация:** при утечке ключа из истории git — ротация TG_TOKEN, OPENROUTER, REPLICATE, GEMINI.

---

## 2. Архитектура (слои)

```
platforms/telegram_bot.py     ← сборка Dispatcher + run_telegram
platforms/handlers/*.py       ← роутеры aiogram (start, support, HD, генерация, оплата)
platforms/telegram_keyboards.py, telegram_utils.py, telegram_middleware.py
services/use_cases/           ← сценарии: chat_turn, photo_turn, payment_turn
services/billing/             ← атомарные списания, refund, тарифы
business_catalog.py           ← ЕДИНЫЙ каталог цен и реестр сценариев
config.py                     ← Settings из .env (секреты + числа)
services/repository.py        ← SQLite / aiosqlite
services/generation_jobs.py   ← очередь медиа + воркеры
services/ai_text.py           ← OpenRouter
services/replicate_client.py  ← Replicate
services/gemini_image_client.py
content/                      ← тексты и callback id
```

### SOLID / DRY (текущее состояние)

| Принцип | Оценка | Действие |
|---------|--------|----------|
| **SRP** | Лучше | `telegram_bot.py` ~70 строк; логика в `platforms/handlers/` |
| **OCP** | Хорошо | Новый видео-сценарий → строка в `business_catalog.VIDEO_SCENARIO_ENTRIES` |
| **DIP** | Хорошо | Use-cases зависят от `billing`, не от Telegram |

### Как добавить видео-сценарий (1 строка)

В `business_catalog.py` в соответствующий кортеж, например `_PAIN_50_ENTRIES`:

```python
("pain_new_scenario", "Название в меню"),
```

Цена берётся из `COST_VIDEO_TIER_50` в `.env`. Промпт — одна строка в `SCENARIO_PROMPT_TEMPLATES` (`video_pipeline.py`).

### Как добавить модель фото

В `PAID_IMAGE_MODEL_ENTRIES` в `business_catalog.py`:

```python
"new_model": ImageModelPrice(energy=20, crystals=3),
```

---

## 3. Биллинг и атомарность

- Списания: `services/billing/store.atomic_spend` + таблица `billing_charges`.
- Возврат: `refund_charge(charge_id)` при ошибках API.
- Унифицировано: `services/api_resilience.fail_generation_task()` для фото/видео/музыки/оживления.
- Чат: `chat_turn` откатывает `refund_charge` при сбое OpenRouter.

---

## 4. Внешние API (robustness)

| Провайдер | Обработка ошибок |
|-----------|------------------|
| OpenRouter | `ai_text.py`: HTTP ≠ 200 → None; `chat_turn` → refund |
| Replicate | `replicate_client.py`: try/except, None; jobs → refund |
| Gemini | `gemini_image_client.py`: timeout/HTTP; jobs → `ExternalApiError` → refund |
| Suno | `suno_client` + music worker → refund при пустом результате |

---

## 5. Юридический контур

- `accepted_terms` в БД + гейт на `/start` и middleware.
- Ссылки Telegra.ph: `SERVICE_OFFER_URL`, `PRIVACY_POLICY_URL`, `SUBSCRIPTION_TERMS_URL`.
- Поддержка: три кнопки-документа внизу FAQ-клавиатуры.

---

## 6. Известные ограничения / backlog

1. ~~Разделить `telegram_bot.py`~~ — сделано: `platforms/handlers/` + вспомогательные модули.
2. **Промпты видео** — вынести в `content/video_prompts.py` или JSON для non-dev правок.
3. **Redis rate limit** — опционально из `REDIS_URL`.
4. **Апскейл** — заглушка без Replicate; при продакшене подключить модель в конфиг.
5. **Рекуррентные платежи** — убедиться, что кнопка «Отключить автопродление» в кабинете реализована в БД.

---

## 7. Чеклист перед продакшеном

- [ ] `.env` на сервере заполнен, не в git
- [ ] `pytest tests/ -q` зелёный
- [ ] Telegra.ph ссылки актуальны в `.env`
- [ ] `ADMIN_IDS` только доверенные id
- [ ] Ротация ключей после любой утечки
