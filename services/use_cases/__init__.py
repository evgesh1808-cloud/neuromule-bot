"""
Сценарии (use cases): бизнес-логика без привязки к Telegram-типам.

Модули:
  - ``chat_turn`` — свободный чат с историей и LLM.
  - ``photo_generation_turn`` / ``video_generation_turn`` / ``animate_generation_turn`` / ``music_generation_turn`` — постановка задач генерации.
  - ``promo_turn`` — промокоды.
  - ``payment_turn`` — успешная оплата invoice.
  - ``payment_invoice_turn`` — сборка параметров счёта (до ``answer_invoice``).
  - ``payment_shop_turn`` — тексты экрана магазина (пакеты / выбор способа оплаты).
  - ``tariff_shop_nav_turn`` — разбор callback магазина (назад к пакетам / выбор пакета).
  - ``start_ui_turn`` — опции превью ссылок для стартовых сообщений.
  - ``cabinet_turn`` — текст экрана «👤 Мой профиль» (балансы / рефералы).
  - ``start_turn`` — сценарий ``/start`` (подписка на канал, deep-link реферала).

Конфиг — ``pydantic-settings`` (``config.Settings``). Логи — ``services.app_logging``.
Rate limit чата — ``services.rate_limit_service`` (Redis при ``REDIS_URL``, иначе SQLite).
"""
