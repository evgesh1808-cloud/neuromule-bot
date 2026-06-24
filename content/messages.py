"""Все пользовательские тексты и callback-идентификаторы Telegram-меню."""

from __future__ import annotations

import html as html_module

# --- callback ids (Telegram inline) ---
CB_CREATE_TEXT = "create_text"
CB_CREATE_IMAGE = "create_image"
CB_CREATE_ANIMATE = "create_animate"
CB_CREATE_VIDEO = "create_video"
CB_CREATE_MUSIC = "create_music"
CB_MUSIC_MODE_AI = "music_mode_ai"
CB_MUSIC_MODE_CUSTOM = "music_mode_custom"
CB_MUSIC_MODE_INSTRUMENTAL = "music_mode_instrumental"
CB_MUSIC_CLIP = "music_clip"
CB_MUSIC_EXTEND = "music_extend"
CB_MUSIC_VOICE_CLONE = "music_voice_clone"
CB_MUSIC_PUBLISH = "music_publish"

# ─── TOS-gate (Telegra.ph оферта/политика/подписка) ─────────────────────────
CB_ACCEPT_LEGAL_TOS = "accept_legal_tos"
TXT_TOS_ACCEPT_BTN = "✅ Принять условия и Запустить"
TXT_TOS_WELCOME_GATE = (
    "🐎⚡️ <b>Добро пожаловать в NeuroMule!</b>\n\n"
    "Перед запуском, пожалуйста, ознакомься с правилами сервиса:\n\n"
    "<a href=\"{offer_url}\">Публичная оферта</a> — "
    "договор об оказании услуг.\n"
    "<a href=\"{privacy_url}\">Политика конфиденциальности</a> — "
    "правила обработки персональных данных.\n"
    "<a href=\"{subscription_url}\">Условия регулярных платежей</a> — "
    "правила автосписания при оплате тарифов.\n\n"
    "Нажимая кнопку ниже, ты подтверждаешь согласие со всеми тремя документами."
)
TXT_TOS_ACCEPTED_FLASH = "🚀 Условия приняты. Запускаем NeuroMule 🐎⚡️…"

# ─── Reviews & Gallery (NeuroMule 🐎⚡️ Виральный локомотив) ───────────────
CB_LEAVE_REVIEW = "leave_review"
CB_REVIEW_APPROVE_PREFIX = "rev_ok:"   # rev_ok:<review_id>
CB_REVIEW_REJECT_PREFIX = "rev_no:"    # rev_no:<review_id>
CB_SHARE_TO_GALLERY = "share_to_gallery"
CB_GALLERY_CONFIRM = "confirm_gallery_publish"
CB_GALLERY_CANCEL = "cancel_gallery_publish"
CB_GALLERY_APPROVE_PREFIX = "approve_gal:"   # approve_gal:<task_id>
CB_GALLERY_REJECT_PREFIX = "reject_gal:"     # reject_gal:<task_id>

TXT_GALLERY_APPROVE_BTN = "👍 Одобрить публикацию"
TXT_GALLERY_REJECT_BTN = "👎 Отклонить"
TXT_GALLERY_MODERATION_HEADER = (
    "🛡 <b>Премодерация Галереи NeuroMule 🐎⚡️</b>\n"
    "<b>task_id:</b> <code>{task_id}</code>\n"
    "<b>тип:</b> {task_type}\n"
    "<b>user_id:</b> <code>{user_id}</code>\n\n"
    "<i>Промпт:</i> {prompt}"
)
TXT_GALLERY_AWAITING_MODERATION = (
    "🛡 <b>Шедевр отправлен на премодерацию NeuroMule 🐎⚡️</b>\n\n"
    "Через несколько минут модератор проверит контент. После одобрения "
    "твоя работа автоматически появится в Галерее и Telegram-канале с "
    "тематическим хэштегом. Анонимность профиля сохраняется."
)
TXT_GALLERY_AUTOPUBLISHED_NO_MOD = (
    "🚀 <b>Опубликовано без премодерации</b>\n\n"
    "Модерационный чат не настроен. Если контент окажется неуместным, "
    "его можно отозвать — напиши в поддержку с task_id."
)
TXT_GALLERY_MOD_APPROVED_NOTIFY = (
    "✅ Твоя публикация прошла премодерацию и появилась в Галерее "
    "NeuroMule 🐎⚡️. Спасибо за вклад!"
)
TXT_GALLERY_MOD_REJECTED_NOTIFY = (
    "ℹ️ Твоя публикация не прошла премодерацию (NSFW / правила платформ). "
    "Создание новых шедевров остаётся доступным. 🐎⚡️"
)

TXT_REVIEW_BUTTON = "✍️ Оставить отзыв (+5 ⚡)"
TXT_REVIEW_ASK = (
    "✍️ <b>Оставь отзыв NeuroMule 🐎⚡️</b>\n\n"
    "Поделись опытом или скинь скриншот любимого результата — текст или медиа. "
    "За активность атомарно начислим тебе <b>+5 ⚡</b> Энергии 🎁."
)
TXT_REVIEW_THANKS = (
    "🎉 <b>Твой отзыв успешно принят на модерацию!</b>\n\n"
    "Тебе начислено: <code>+5 ⚡</code> Энергии. "
    "Спасибо, что помогаешь NeuroMule 🐎⚡️ становиться лучше!"
)
TXT_REVIEW_REJECTED_NOTIFY = (
    "ℹ️ Твой отзыв не прошёл модерацию — не переживай, бонус <code>+5 ⚡</code> "
    "остаётся у тебя. Пиши ещё, мы за откровенность 🐎⚡️."
)
TXT_REVIEW_APPROVED_NOTIFY = (
    "✅ Твой отзыв одобрен и опубликован в нашем канале с тегом "
    "<code>#user_reviews</code>. Респект! 🐎⚡️"
)
TXT_REVIEW_ADMIN_HEADER = (
    "🛠 <b>Новый отзыв на модерацию</b>\n"
    "<b>User ID:</b> <code>{user_id}</code>\n"
    "<b>Tag:</b> #review_{user_id}"
)
TXT_REVIEW_APPROVE_BTN = "✅ Одобрить для канала"
TXT_REVIEW_REJECT_BTN = "❌ Отклонить"

TXT_GALLERY_SHARE_BTN = "📢 Поделиться в Галерее"
TXT_GALLERY_FORWARD_FRIEND_BTN = "🚀 Переслать другу в ЛС"
TXT_GALLERY_VIRAL_INVITE_BTN = "🎸 Создать свой шедевр в NeuroMule"
TXT_GALLERY_CONFIRM_TEXT = (
    "📢 <b>Публикация в Галерее NeuroMule 2026</b>\n\n"
    "Ты собираешься отправить свою генерацию в наши официальные публичные "
    "каналы.\n\n"
    "👀 <b>Что увидят другие:</b> твой медиафайл и текст промпта ИИ.\n\n"
    "🔒 <b>Гарантия анонимности:</b> твоё имя, @username и Telegram ID "
    "останутся <b>скрытыми на 100%</b>. Никто не сможет связать "
    "публикацию с твоим профилем.\n\n"
    "Подтверждаешь публикацию на всеобщее обозрение? 👇"
)
TXT_GALLERY_CONFIRM_BTN = "✅ Да, опубликовать!"
TXT_GALLERY_CANCEL_BTN = "❌ Отмена"
TXT_GALLERY_PUBLISHED_OK = (
    "🚀 <b>Твой шедевр улетел в Галерею NeuroMule 🐎⚡️!</b>\n\n"
    "Telegram-канал, ВКонтакте и MAX App уже получают копию. "
    "Спасибо за вклад в коллекцию ИИ-творчества 💜"
)
TXT_GALLERY_PUBLISHED_PARTIAL = (
    "✅ <b>Опубликовано частично</b>\n\n"
    "Некоторые витрины временно недоступны: <code>{failed}</code>. "
    "Мы попробуем доставить твой шедевр позже 🐎⚡️."
)
TXT_GALLERY_PUBLISHED_EMPTY = (
    "⚠️ Витрины кросс-постинга временно недоступны. Не переживай — "
    "твоя генерация осталась в боте, попробуй ещё раз позже 🐎⚡️."
)
TXT_GALLERY_NOT_FOUND = (
    "⚠️ Не нашёл медиа для публикации. Сначала создай новый шедевр 🐎⚡️."
)
TXT_GALLERY_CANCELLED = "👌 Публикация отменена — твой шедевр остаётся приватным."

GALLERY_HASHTAGS = {
    "photo": "#gallery_flux",
    "video": "#studio_video",
    "animate": "#studio_video",
    "music": "#radio_suno",
}

TXT_INLINE_VIRAL_FOOTER = (
    "\n───────────────────\n"
    "⚡ <b>Сгенерировано через @NeuroMule_bot 🐎⚡️</b>"
)
TXT_INLINE_FREE_LOCK_TITLE = "🔒 Доступ ограничен"
TXT_INLINE_FREE_LOCK_DESCRIPTION = (
    "Инлайн-режим открыт на тарифах MINI / SMART / ULTRA. "
    "Нажми, чтобы активировать премиум!"
)
TXT_INLINE_FREE_LOCK_MESSAGE = (
    "🔒 <b>Инлайн-режим NeuroMule 🐎⚡️</b>\n\n"
    "Эта премиум-фишка работает на тарифах <b>MINI / SMART / ULTRA</b>. "
    "Открой бота и активируй тариф — и ИИ-ответы будут вылетать прямо из "
    "любого чата за 1 ⚡."
)
TXT_INLINE_INSUFFICIENT_TITLE = "⚠️ Недостаточно ресурсов на балансе"
TXT_INLINE_INSUFFICIENT_DESCRIPTION = (
    "Нужно минимум 1 ⚡ или 1 💎, чтобы NeuroMule ответил. "
    "Открой бота и пополни баланс."
)
TXT_INLINE_INSUFFICIENT_MESSAGE = (
    "⚠️ <b>Не хватает ресурсов для inline-ответа</b>\n\n"
    "Каждый запрос — это 1 ⚡ или 1 💎. Пополни энергию или кристаллы "
    "в боте 🐎⚡️ и возвращайся писать запросы прямо из любого чата."
)
TXT_INLINE_EMPTY_TITLE = "💡 Напиши запрос для NeuroMule"
TXT_INLINE_EMPTY_DESCRIPTION = (
    "Например: «придумай 5 виральных хуков для Reels про AI-музыку»."
)
TXT_INLINE_AI_FAILED_TITLE = "😵 Сейчас ИИ перегружен"
TXT_INLINE_AI_FAILED_DESCRIPTION = (
    "Попробуй ещё раз через секунду — кристаллы автоматически возвращены."
)
TXT_INLINE_AI_FAILED_MESSAGE = (
    "😵 <b>OpenRouter временно недоступен</b>\n\n"
    "Кристаллы/энергия автоматически возвращены на твой баланс. "
    "Попробуй ещё раз через секунду 🐎⚡️."
)
TXT_INLINE_RESULT_BTN = "🎁 Забрать 30 бесплатных запросов"
CB_UPSCALE_START = "upscale_start"
CB_HD_PREMIUM_BUY = "hd_premium_buy"
CB_HD_FREE_ADVICE = "hd_free_advice"
CB_MATCH_START = "match_start"
CB_HD_MATCH_FAMILY_PREFIX = "hd_match_fam:"
CB_HD_MATCH_MANUAL = "hd_match_manual"
CB_HD_REPORT_PREFIX = "hd_report:"
CB_HD_REPORT_MONEY = "hd_report:money"
CB_HD_REPORT_LOVE = "hd_report:love"
CB_HD_REPORT_ENERGY = "hd_report:energy"
CB_HD_REPORT_PLAN = "hd_report:plan"
CB_HD_REPORT_PDF = "hd_report:pdf"
CB_CABINET_PROMO = "cabinet_promo"
CB_REFRESH_PROFILE = "refresh_profile"
CB_OPEN_TARIFFS = "open_tariffs"
CB_BUY_BUNDLE_MENU = "buy_bundle_menu"
CB_BUY_CRYSTALS_ONLY_MENU = "buy_crystals_only_menu"
CB_CLOSE_TARIFFS = "close_tariffs"
CB_ENTER_PROMOCODE = "enter_promocode"
CB_OPEN_MEMORY = "open_memory"
CB_SET_MEMORY = "set_memory"
CB_CLEAR_MEMORY = "clear_memory"
CB_OPEN_FAMILY = "open_family"
CB_FAMILY_ADD = "family_add"
CB_FAMILY_UNLINK_PREFIX = "family_unlink:"
CB_SHOW_INSTRUCTION = "show_instruction"
CB_CHECK_SUBSCRIPTION = "check_subscription"
CB_RECHECK_SUBSCRIPTION = "recheck_subscription"
CB_RESULT_ANIMATE = "res_anim"
CB_RESULT_REPEAT_PHOTO = "res_repeat_ph"
CB_RESULT_HD_PRO = "res_hd_pro"
CB_RESULT_PREMIUM = "res_premium"
CB_RESULT_GALLERY = "res_gallery"
CB_RESULT_MP3 = "res_mp3"
CB_RESULT_EDIT_LYRICS = "res_edit_lyrics"
CB_SERVICE_RULES = "service_rules"
CB_SUPPORT_WRITE_QUESTION = "support_write_question"
CB_SUPP_FAQ = "supp_faq"
CB_SUPP_GUIDES = "supp_guides"
CB_SUPP_PAYMENT_ISSUE = "supp_payment_issue"
CB_MANAGE_SUBSCRIPTION = "manage_subscription"
CB_WRITE_TO_MANAGER = "write_to_manager"
CB_CLOSE_SUPPORT = "close_support"
CB_BACK_TO_SUPP_MAIN = "back_to_supp_main"
CB_CHECK_PENDING_PAYMENT = "check_pending_payment"
CB_FAQ_ENERGY = "faq_energy"
CB_FAQ_HD_DIFF = "faq_hd_diff"
CB_FAQ_SLOW_GEN = "faq_slow_gen"
CB_FAQ_PROMPTS = "faq_prompts"
CB_FAQ_PRIVACY = "faq_privacy"
CB_FAQ_CANCEL_SUB = "faq_cancel_sub"
CB_FAQ_HD_SOURCE = "faq_hd_source"
CB_FAQ_REFUND_CRYSTALS = "faq_refund_crystals"
CB_FAQ_STARS_COST = "faq_stars_cost"
CB_ACCEPT_RULES = "accept_rules"
CB_BACK_MAIN = "back_main"
CB_BACK_CREATE = "back_create"
CB_IMG_PREFIX = "img:"
CB_ADMIN_STATS = "admin_stats"
CB_ADMIN_GIVE_CRYSTALS = "admin_give_crystals"
CB_ADMIN_START_BROADCAST = "admin_start_broadcast"
# Алиасы для обратной совместимости со старыми callback_data.
CB_ADMIN_GRANT_CRYSTALS = CB_ADMIN_GIVE_CRYSTALS
CB_ADMIN_BROADCAST = CB_ADMIN_START_BROADCAST
CB_TEXT_ROLE_PREFIX = "text_role:"
CB_SET_ROLE_PREFIX = "set_role:"
CB_SHOW_TABLE_SUBCATEGORIES = "show_table_subcategories"
CB_SHOW_LIFESTYLE_SUBCATEGORIES = "show_lifestyle_subcategories"
CB_BACK_TO_ROLES_MENU = "back_to_roles_menu"
CB_NEW_DIALOG = "new_dialog"
CB_BACK_TO_TOOLS = "back_to_tools"
CB_TABLE_CHART_PREFIX = "tbl_chart:"
CB_WB_CHART_PREFIX = "wb_chart:"
BTN_TABLE_CHART_PIE = "🔄 Круговая"
BTN_TABLE_CHART_LINE = "📊 Линейная"
BTN_TABLE_CHART_BAR = "📈 Гистограмма"

BTN_DAILY_ADVICE = "🔮 Совет дня"
BTN_PROFILE = "👤 Мой профиль"
BTN_PROFILE_LEGACY = "👤 Профиль"
BTN_HD_SECTION = "🧬 Дизайн Человека"
BTN_CREATE = "🎨 Создать"
BTN_TARIFFS = "🚀 Тарифы"
BTN_SUPPORT = "🆘 Поддержка"
BTN_SUPPORT_LEGACY = "Поддержка"
BTN_SUPPORT_LEGACY2 = "🙋‍♂️ FAQ / Поддержка"

# Reply-подменю «🎨 Создать»
BTN_REPLY_NEUROTEXT = "📝 Нейротекст"
BTN_REPLY_IMAGE = "🖼 Изображение"
BTN_REPLY_ANIMATE = "✨ Оживить фото"
BTN_REPLY_MUSIC = "🎸 Музыка"
BTN_REPLY_VIDEO = "🎬 Видео"
BTN_REPLY_HD = "🧬 Дизайн человека"

CREATE_REPLY_MENU_BUTTONS = (
    BTN_REPLY_NEUROTEXT,
    BTN_REPLY_IMAGE,
    BTN_REPLY_ANIMATE,
    BTN_REPLY_MUSIC,
    BTN_REPLY_VIDEO,
    BTN_REPLY_HD,
)

USER_MAIN_MENU_BUTTONS = (
    BTN_CREATE,
    BTN_DAILY_ADVICE,
    BTN_TARIFFS,
    BTN_PROFILE,
    BTN_SUPPORT,
)

PROFILE_MENU_BUTTONS = (BTN_PROFILE, BTN_PROFILE_LEGACY)

ALL_REPLY_NAV_BUTTONS = USER_MAIN_MENU_BUTTONS + CREATE_REPLY_MENU_BUTTONS
INSTRUCTION_INLINE_BUTTON_LABEL = "📍 Инструкция"
ADMIN_MAIN_MENU_BUTTON = "⚙️ Админ-панель"

TEXT_ROLES: tuple[tuple[str, str], ...] = (
    ("⚪ Стандарт", "standard"),
    ("📄 Саммари", "summary"),
    ("📊 ИИ-Аналитика & Таблицы", "table_generator"),
    ("🎙️ Сценарии & Подкасты", "podcast_doc"),
    ("📱 Блогер", "blogger_content"),
    ("🧠 ИИ-Коуч", "psychologist_coach"),
    ("🏃‍♂️ Фитнес", "fitness_nutrition"),
    ("🍳 ИИ-Шеф", "chef_recipes"),
)
PREMIUM_TEXT_ROLE_IDS = {role_id for _, role_id in TEXT_ROLES if role_id != "standard"}

TEXT_ROLE_COSTS: dict[str, tuple[int, int]] = {
    "standard": (1, 1),
    "summary": (5, 3),
    "table_generator": (20, 10),
    "podcast_doc": (40, 20),
    "blogger_content": (5, 3),
    "psychologist_coach": (5, 3),
    "fitness_nutrition": (5, 3),
    "chef_recipes": (5, 3),
    # Обратная совместимость FSM / истории диалога
    "academic": (5, 3),
    "psychologist": (5, 3),
    "speaker": (5, 3),
    "blogger": (5, 3),
    "analyst": (5, 3),
    "storyteller": (5, 3),
}

FREE_TARIFF_ALLOWED_ROLES: frozenset[str] = frozenset({"standard"})
SMART_TARIFF_REQUIRED_ROLES: frozenset[str] = frozenset({"podcast_doc"})

# Псевдодинамические примеры для режима «Стандарт» в нейротексте.
MULE_STATIC_EXAMPLES = [
    "«Пост в Telegram про утренние привычки, легкий и юмористический тон»",
    "«Статья про то, как бороться с выгоранием на работе, поддерживающий стиль»",
    "«Описание для карточки товара на маркетплейсе, продающий и убедительный тон»",
    "«Сценарий для Reels про главные тренды в дизайне, динамичный стиль»",
    "«Email-рассылка для клиентов об обновлении программы лояльности, деловой тон»",
]

CB_CLEAR_CONTEXT = "clear_context"
TXT_NEUROTEXT_CLEAR_BTN = "🔔 Новый диалог"
TXT_NEUROTEXT_CLEAR_DONE = "🧹 <b>Контекст очищен.</b> Можно стартовать новую тему."

IMAGE_MODELS: tuple[tuple[str, str], ...] = (
    ("Imagen 4", "imagen4"),
    ("Flux Schnell", "flux-schnell"),
    ("DALL-E 3", "gpt_image2"),
    ("Nano Banana 2", "nano_banana2"),
    ("Nano Banana Pro", "nano_banana_pro"),
)

IMAGE_MODEL_IDS = {mid for _, mid in IMAGE_MODELS}


def get_text_image_models(tariff) -> str:
    """Динамическое меню моделей фото: цены зависят от тарифа пользователя."""
    from services.billing.types import TariffTier

    if not isinstance(tariff, TariffTier):
        tariff = TariffTier.from_db(str(tariff))
    base_text = (
        "Выбери модель ниже, затем опиши желаемое изображение текстом.\n\n"
        "<b>Доступные нейросети:</b>\n"
    )
    if tariff is TariffTier.FREE:
        return base_text + (
            "🎨 <b>Imagen 4</b> — 3 кадра/день БЕСПЛАТНО (далее — 2 💎)\n"
            "⚡ <b>Flux Schnell</b> — 3 💎\n"
            "🔒 <b>DALL-E 3</b> — <i>Доступно на тарифах Premium</i>\n"
            "🔒 <b>Nano Banana 2</b> — <i>Доступно на тарифах Premium</i>\n"
            "🔒 <b>Nano Banana Pro</b> — <i>Доступно на тарифах Premium</i>"
        )
    return base_text + (
        "🎨 <b>Imagen 4</b> — 10 ⚡ (или 2 💎)\n"
        "⚡ <b>Flux Schnell</b> — 30 ⚡ (или 3 💎)\n"
        "🖼 <b>DALL-E 3</b> — 5 💎\n"
        "🍌 <b>Nano Banana 2</b> — 15 ⚡ (или 2 💎)\n"
        "🚀 <b>Nano Banana Pro</b> — 35 ⚡ (или 3 💎)"
    )


MAIN_MENU_BUTTONS = ALL_REPLY_NAV_BUTTONS  # фильтр общего чата и отмены FSM.

TXT_ACTIVATION_SUCCESS = (
    "🎉 <b>Доступ открыт! Успешная активация Мула.</b>\n\n"
    "Выбирай направление в меню ниже и давай создавать шедевры! 👇"
)
TXT_CREATE_REPLY_INTRO = "Выбери инструмент 👇"

CB_HD_SECTION = "hd_section"
CB_HD_REPORT_OPEN = "hd_report_open"

# Шесть инструментов в сетке 2×3 (пары по рядам); «Назад» — в create_menu().
# Порядок кортежа = порядок кнопок слева направо, сверху вниз:
#   ряд 1: Нейротекст | Изображение
#   ряд 2: Оживить фото | Видео
#   ряд 3: Музыка | Дизайн Человека
CREATE_MENU_GRID = (
    ("📝 Нейротекст", CB_CREATE_TEXT),
    ("🖼️ Изображение", CB_CREATE_IMAGE),
    ("✨ Оживить фото", CB_CREATE_ANIMATE),
    ("🎬 Видео", CB_CREATE_VIDEO),
    ("🎸 Музыка", CB_CREATE_MUSIC),
    ("🧬 Дизайн Человека", CB_HD_SECTION),
)
CREATE_MENU_BACK_ROW = ("⬅️ Назад в главное меню", CB_BACK_MAIN)
# Совместимость: полный кортеж для тестов и старых импортов (последняя строка — только «Назад» в create_menu).
CREATE_MENU_BUTTONS = CREATE_MENU_GRID + (CREATE_MENU_BACK_ROW,)

TXT_HD_SECTION_INTRO = (
    "<b>🧬 Дизайн Человека</b>\n\n"
    "Здесь — <b>полный разбор</b> по дате рождения и совместимость с партнёром "
    "(после покупки разбора).\n"
    "Выбери действие кнопкой ниже."
)
TXT_HD_PRO_UNLOCKED = (
    "<b>Ваш разбор готов!</b>\n"
    "Открыта функция проверки <b>совместимости</b>!"
)
TXT_MATCH_LOCKED = (
    "👩‍❤️‍👨 Совместимость откроется после покупки 🗺️ полного разбора личности."
)


def format_match_cost_line(settings: object) -> str:
    return f"👩‍❤️‍👨 Совместимость стоит {int(getattr(settings, 'cost_match', 50))} 💎."
TXT_HD_ALREADY_PURCHASED = (
    "<b>Разбор уже у тебя есть.</b>\n\n"
    "Открой разделы отчёта или проверь совместимость с партнёром — кнопки ниже."
)

# Подписи inline-кнопок (Дизайн человека / разделы отчёта)
TXT_HD_INLINE_FULL_REPORT = "🗺️ Полный разбор личности — {cost} 💎"
TXT_HD_INLINE_VIEW_REPORT = "🗺️ Посмотреть мой разбор"
TXT_HD_INLINE_COMPATIBILITY = "💞 Рассчитать Совместимость (50 💎)"
TXT_HD_BTN_REPORT_MONEY = "💰 Мои деньги"
TXT_HD_BTN_REPORT_LOVE = "❤️ Отношения"
TXT_HD_BTN_REPORT_ENERGY = "⚡️ Энергия"
TXT_HD_BTN_REPORT_PLAN = "📅 План на 30 дней"
TXT_HD_BTN_REPORT_PDF = "📄 Скачать PDF версию"

SUPPORT_TOPICS = (
    "Технические сбои и ошибки",
    "Вопросы оплаты и тарифов",
    "Предложения по улучшению бота",
)

TARIFF_PLANS = (
    "🎁 ТАРИФ: БЕСПЛАТНО\n"
    "Твой ежедневный базовый старт\n"
    "• ⚡️ 30 Энергии/день — только для текстового чата (Стандарт: 1 ⚡/сообщение)\n"
    "• 📝 Роль «Стандарт» в нейротексте\n"
    "• 🎨 Imagen 4 — 3 бесплатных фото в день; PRO (Flux) — 2 💎\n"
    "• ❌ Кристаллы, Видео и Музыка — недоступны\n"
    "Стоимость: 0 ₽",
    "📦 ПАКЕТ: MINI\n"
    "Для тех, кто ценит комфорт и качество\n"
    "• ⚡️ 500 Энергии\n"
    "• 💎 10 Кристаллов\n"
    "• ✅ Доступ ко всем Экспертным ролям: Психолог, Аналитик, Блогер и другие\n"
    "• 🖼 PRO-фото и расширенные модели — за 💎\n"
    "Стоимость: 349 ₽ / 250 ⭐",
    "🚀 ПАКЕТ: SMART (ХИТ)\n"
    "Оптимальный маршрут для активных создателей\n"
    "• ⚡️ 1500 Энергии + 🔥 Безлимитный текст на mini-моделях\n"
    "• 💎 35 Кристаллов\n"
    "• ✅ Все Экспертные роли + приоритет в очереди\n"
    "• 🎸 Музыка Suno — 15 💎 за трек\n"
    "Стоимость: 790 ₽ / 570 ⭐",
    "👑 ПАКЕТ: ULTRA (3 дня)\n"
    "Киностудия на выходные — только для тебя\n"
    "• ⚡️ 500 Энергии\n"
    "• 💎 10 Кристаллов\n"
    "Стоимость: 290 ₽ / 210 ⭐",
    "👑 ПАКЕТ: ULTRA (1 неделя)\n"
    "• ⚡️ 1800 Энергии + 💎 35 Кристаллов\n"
    "Стоимость: 690 ₽ / 500 ⭐",
    "👑 ПАКЕТ: ULTRA (1 месяц, КИНОСТУДИЯ)\n"
    "Максимум выносливости. Искусство без границ\n"
    "• ⚡️ 7000 Энергии\n"
    "• 💎 120 Кристаллов\n"
    "• ✅ Доступ к самым мощным ИИ: GPT-o1 / Claude 3.5\n"
    "• 🎬 PRO-видео и пранки (Luma) — от 35 💎, приоритет в очереди\n"
    "• 👫 <b>Опция DUO</b> — доступ на двоих (ты + 1 партнёр)\n"
    "Стоимость: 2490 ₽ / 1800 ⭐",
    "📍 НУЖНЫ ТОЛЬКО КРИСТАЛЛЫ?\n"
    "• 10 💎 — 249 ₽ / 180 ⭐\n"
    "• 40 💎 — 690 ₽ / 500 ⭐\n"
    "• 100 💎 — 1490 ₽ / 1080 ⭐ (Выгодно!)\n\n"
    "Энергия в платных пакетах не сгорает в конце дня и доступна до полного использования.",
)

# --- тексты ---
TXT_SECTION_INTRO = "Нейроны на старте! Какой инструмент задействуем для этого маршрута?"
TXT_SELECT_TOOL = TXT_SECTION_INTRO
TXT_BACK_TO_MAIN = "Твой маршрут проложен! ⚡️"
TXT_BACK_TO_TOOLS = "Назад к инструментам"
TXT_LOW_ENERGY = (
    "⚡ Энергии не хватает.\n\n"
    "Пополни баланс в «Тарифах» или пригласи друга в разделе «👤 Мой профиль» — так больше людей узнают про бота."
)
TXT_FREE_CREATE_BLOCKED = (
    "❌ <b>Доступ заблокирован</b>\n\n"
    "На тарифе <code>FREE</code> генерация видео, музыки и полных разборов по Дизайну Человека "
    "заблокирована.\n\n"
    "💡 <i>Накопленные за друзей Кристаллы ты можешь потратить прямо сейчас на создание "
    "шедевров в разделе <b>[ 🖼 Изображение ] ➔ PRO (Flux)</b>! Чтобы открыть доступ к видео "
    "и видео-сценариям, активируй любой платный пакет (MINI, SMART или ULTRA) в меню "
    "«🚀 Тарифы».</i>"
)
TXT_FREE_IMAGE_MODEL_BLOCKED = (
    "❌ <b>На тарифе FREE</b> в разделе «🖼 Изображение» доступны только "
    "<b>Imagen 4</b> (3 бесплатных в день) и <b>Flux Schnell</b> за <b>2 💎</b> "
    "(реферальные кристаллы). Выбери одну из этих моделей."
)

TXT_INSUFFICIENT_BALANCE = (
    "Мул устал. Нужно подкрепиться лимитами 🪫\n\n"
    "Приходи завтра за бесплатной энергией или пополни 💎 в «Тарифах»."
)
TXT_CHAT_RATE_LIMIT = (
    "⏳ Слишком много сообщений подряд. Подождите немного и напишите снова — так мы защищаем Системы от пиков."
)
TXT_CHAT_EMPTY = "Напишите текст сообщения — пустое сообщение не обрабатываю."
TXT_CHAT_CONTEXT_TOO_LARGE = (
    "Сообщение слишком длинное для одного запроса к модели. Сократите текст или разбейте на части."
)
TXT_TABLE_GENERATOR_STATUS = (
    "⏳ Анализирую данные... Процесс генерации и сборки отчета займет от 1 до 3 минут. "
    "Пожалуйста, подождите."
)
CB_TABLE_SUBROLE_PREFIX = "set_table_subrole:"
TXT_TABLE_SUBROLE_MENU = (
    "📊 <b>Аналитика таблиц</b>\n\n"
    "Выберите тип отчёта — от этого зависит локальная математика и формат Excel:"
)
TXT_TABLE_SUBROLE_READY = (
    "📥 Отлично! Режим выбран. Отправьте ваш .xlsx файл или напишите данные текстом для анализа."
)
TXT_TABLE_SUBROLE_STANDARD = (
    "📥 <b>Режим Базового отчета выбран!</b>\n\n"
    "Я проведу классический технический анализ вашей таблицы, посчитаю суммы, "
    "средние показатели и построю график трендов.\n\n"
    "<b>Действие:</b> Отправьте ваш .xlsx файл в чат."
)
TXT_TABLE_SUBROLE_WB_OZON = (
    "📥 <b>Режим Аналитики WB/Ozon выбран!</b>\n\n"
    "Я проанализирую еженедельный отчет маркетплейса, автоматически рассчитаю общую выручку, "
    "налог 6% УСН (для ИП), чистую прибыль, рекламную нагрузку и юнит-показатели за 0 рублей.\n\n"
    "<b>Действие:</b> Отправьте ваш .xlsx файл отчета реализации маркетплейса в чат."
)
TXT_WB_FINANCE_MINI_APP_CTA = (
    "💡 <b>Хватит загружать отчёты вручную!</b> Подключите <b>«Автопилот по API»</b> в меню бота. "
    "Система будет сама каждую ночь оцифровывать ваш бизнес, делать ABC-анализ матрицы "
    "и присылать этот дашборд ровно в <b>09:00</b> утра, пока вы пьёте кофе. "
    "<b>Первые 3 дня — бесплатно!</b>\n\n"
    "Нажмите на кнопку ниже, чтобы открыть интерактивный дашборд Mini App "
    "с полным ABC-анализом и калькулятором гипотез!"
)
TXT_TABLE_SUBROLE_TRAFFIC = (
    "📥 <b>Режим Маркетинга (ROI/CPA) выбран!</b>\n\n"
    "Я рассчитаю сквозную юнит-экономику вашей рекламы (CTR, CPC, CPA, ROI), "
    "выявив неэффективные кампании, сливающие бюджет.\n\n"
    "<b>Действие:</b> Отправьте рекламную выгрузку в формате .xlsx или .csv в чат."
)
TXT_TABLE_SUBROLE_SEO = (
    "📥 <b>Режим SEO (Excel) выбран!</b>\n\n"
    "Я найду в вашей таблице колонку с наименованиями товаров и построчно сгенерирую "
    "уникальные продающие SEO-описания через ИИ-модель.\n\n"
    "<b>Действие:</b> Отправьте ваш .xlsx файл со списком товаров в чат."
)

_TABLE_SUBROLE_INSTRUCTIONS: dict[str, str] = {
    "standard_report": TXT_TABLE_SUBROLE_STANDARD,
    "wb_ozon_finance": TXT_TABLE_SUBROLE_WB_OZON,
    "traffic_marketing": TXT_TABLE_SUBROLE_TRAFFIC,
    "mass_seo_generation": TXT_TABLE_SUBROLE_SEO,
}


def table_subrole_instruction(subrole_id: str) -> str:
    """B2B-инструкция после выбора под-режима table_generator."""
    from services.table_subrole_types import normalize_table_subrole

    sid = normalize_table_subrole(subrole_id)
    return _TABLE_SUBROLE_INSTRUCTIONS.get(sid, TXT_TABLE_SUBROLE_READY)


TXT_TABLE_AI_DEGRADATION_NOTICE = (
    "\n\n⚠️ Расширенная аналитика временно недоступна. Сформирован точный "
    "финансовый отчёт на основе ваших данных. Списания за консалтинг не произошло."
)
TXT_TABLE_AI_FAILED_NO_ROWS = (
    "⚠️ Не удалось разобрать таблицу. Отправьте файл <b>.xlsx</b> или <b>.csv</b> — "
    "соберём отчёт локально без списания за консалтинг."
)
TXT_TABLE_COLUMN_PARSE_WARNING = (
    "⚠️ Не удалось автоматически распознать структуру колонок в файле. "
    "Пожалуйста, убедитесь, что вы загрузили оригинальный еженедельный отчет Wildberries/Ozon."
)
TXT_CHAT_DAILY_LIMIT = "Лимит бесплатного тарифа: 30 текстов в день. Оформи MINI/SMART/ULTRA для продолжения."
TXT_RESET_OK = (
    "🧹 История диалога с нейросетью и долговременная память очищены. "
    "Можете начать общение с чистого листа."
)
TXT_SUBSCRIPTION_BLOCKED = (
    "🛑 ДОСТУП ОГРАНИЧЕН.\n\n"
    "Чтобы использовать мощь нейросетей, подпишись на наш канал. Там же мы раздаём промокоды!\n\n"
    "👉 {channel_url}"
)
TXT_SUBSCRIPTION_CHANNEL_BUTTON = "Подписаться на канал"

# /start — HTML (ParseMode.HTML). Шаблоны с {channel_url} форматировать через
# services.use_cases.start_ui_turn.format_start_message_html (безопасная подстановка URL).
# Нет подписки на канал — два сообщения + без превью ссылки.
TXT_START_FIRST_MEET_NEED_CHANNEL_1 = (
    "🌟 <b>NeuroMule</b> в сети. Процессоры заряжены и готовы к твоим идеям!\n\n"
    "🤖 Добро пожаловать в мир безграничных возможностей!\n\n"
    "Ты в одном шаге от доступа к самым мощным нейросетям планеты: "
    "<code>GPT-4o</code>, <code>Flux</code> и <code>Suno</code> в одном месте. 🚀\n\n"
    "Твой текущий статус: <b>FREE</b> 🎁"
)
TXT_START_FIRST_MEET_NEED_CHANNEL_2 = (
    "• 📝 Текстовый ИИ-чат: 1 ⚡️ за сообщение (30 ⚡️ обновляются ежедневно)\n"
    "• 🎨 Imagen 4 — бесплатно, <code>{photo_daily_limit}</code> раза в день\n"
    "• 💎 PRO-фото (Flux Schnell) — 2 💎\n\n"
    "Чтобы снять ограничения и начать творить, подпишись на наш "
    '<a href="{channel_url}">официальный канал</a>. '
    "Там мы делимся лайфхаками, секретными промптами и дарим промокоды."
)

# /start — единое приветствие (HTML), показывается всегда, без проверки подписки.
TXT_START_WELCOME = (
    "Мул на связи! Нейроны на старте и готовы к твоим идеям! 🐎⚡️\n"
    "🤖 Добро пожаловать в единую точку доступа к самым мощным нейросетям планеты: "
    "<code>GPT-4o</code>, <code>Flux</code> и <code>Suno</code> в одном месте.\n\n"
    "Твой текущий статус: <b>FREE</b> 🎁\n"
    "• ⚡️ 30 Энергии — только для сообщений в текстовом ИИ-чате (сброс в 00:00)\n"
    "• 🎨 Базовая генерация фото (Imagen 4) — БЕСПЛАТНО (3 раза в день)\n"
    "• 💎 PRO-фото (Flux Schnell) — всего 2 💎\n\n"
    "В канале @mulendeeva_ai мы делимся лайфхаками, секретными промптами и дарим промокоды — "
    "подписывайся! 🔔\n\n"
    "Выбирай направление в меню ниже 👇"
)
TXT_START_MAIN_MENU_PROMPT = "Главное меню доступно ниже."

# Hard Paywall: /start до подписки на канал и нажатия «Я подписался! Запустить».
TXT_START_PAYWALL = (
    "Мул на связи! Нейроны на старте и готовы к твоим идеям! 🐎⚡️\n\n"
    "Привет! Рады видеть тебя в команде NeuroMule!\n\n"
    "**Твой текущий статус:** `FREE` 🎁\n\n"
    "🔮 Бесплатный совет дня по Дизайну Человека!\n"
    "🎁 Ежедневно: 30 ⚡ Энергии на чат и 3 бесплатных фото!\n\n"
    "✨ **Чтобы активировать Мула и запустить генерацию, сделай один простой шаг:**\n"
    "Подпишись на наш официальный канал @mulendeeva_ai. "
    "Там мы делимся секретными промптами и лайфхаками! 👇\n\n"
    "Ждем тебя! 💛\n\n"
    "⚠️ *Нажимая кнопку проверки, ты принимаешь условия "
    "[Публичной оферты]({offer_url}), [Политики конфиденциальности]({privacy_url}) "
    "и [Условий регулярных платежей]({subscription_url}), "
    "а также даешь согласие на обработку медиафайлов.*"
)
TXT_PAYWALL_SUBSCRIBE_BTN = "📢 1. Подписаться на канал"
TXT_PAYWALL_CHECK_BTN = "✅ 2. Я подписался! Запустить"

TXT_TERMS_REQUIRED = (
    "Для работы с ботом подпишись на канал @mulendeeva_ai и нажми "
    "«✅ 2. Я подписался! Запустить» на экране /start — там же условия сервиса."
)
TXT_TERMS_ACCEPT_BTN = "✅ 2. Я подписался! Запустить"

# Сохранён для обратной совместимости в тестах/импортах: формат идентичен старому.
TXT_START_FIRST_MEET_OK = TXT_START_WELCOME

# Шлюз подписки на канал (мягкая проверка при нажатии любых кнопок, кроме /start).
TXT_CHANNEL_GATE = (
    "Мул готов к выходу, но путь закрыт! 🚧\n"
    "Подпишись на канал @mulendeeva_ai и нажми «✅ 2. Я подписался! Запустить»."
)
TXT_CHANNEL_GATE_SUBSCRIBE_BTN = TXT_PAYWALL_SUBSCRIBE_BTN
TXT_CHANNEL_GATE_CHECK_BTN = TXT_PAYWALL_CHECK_BTN
TXT_CHANNEL_GATE_OK = "✅"
TXT_CHANNEL_GATE_FAIL = (
    "❌ Подписка не найдена. Пожалуйста, сначала подпишись на канал @mulendeeva_ai, "
    "а затем нажми кнопку проверки еще раз! Мул ждет команду 🐎"
)
TXT_ABOUT_BOT = (
    "Помогаю с текстами (нейросеть), изображениями (несколько моделей), "
    "оживлением фото, видео и музыкой. "
    "Мультиплатформенный бот @NeuroMule_bot."
)
TXT_CREATE_TEXT_HINT = (
    "📝 Нейротекст: выбери режим.\n"
    "• <b>Стандарт</b> — 1 ⚡ (или 1 💎)\n"
    "• <b>Экспертные роли</b> — 5 ⚡ или 3 💎\n\n"
    "На тарифе FREE доступен только Стандарт."
)
TXT_PREMIUM_ROLE_LOCKED = (
    "Экспертные роли доступны с тарифа MINI.\n"
    "Стоимость ответа: 5 ⚡ или 3 💎. Загляни в «🚀 Тарифы»."
)
TXT_CHAT_EXPERT_INSUFFICIENT = (
    "Для экспертной роли нужно минимум <b>5 ⚡</b> или <b>3 💎</b>.\n"
    "Смени роль на «Стандарт» (1 ⚡) или пополни баланс в «🚀 Тарифы»."
)
TXT_CHAT_ROLE_FALLBACK_STANDARD = (
    "✨ <b>Экспертная роль временно недоступна</b>\n\n"
    "💎 На балансе не хватает кристаллов для полноценного режима — "
    "поэтому этот ответ подготовлен в <b>🔘 Стандарте</b> (базовый помощник).\n\n"
    "🚀 Пополни 💎 или открой пакет MINI — и снова включай Саммари, Таблицы и другие роли "
    "без ограничений. Кнопка ниже — в магазин NeuroMule."
)
TXT_CHAT_ZERO_BALANCE_PREMIUM = (
    "Мул устал: на балансе <b>0 ⚡</b> и <b>0 💎</b>.\n\n"
    "Подкрепись лимитами завтра или оформи премиум в «🚀 Тарифы» — "
    "там больше энергии и экспертные роли нейротекста."
)
TXT_CREATE_IMAGE_AFTER_MODEL = (
    "Опиши изображение одним сообщением: стиль, ключевые объекты, фон, освещение и формат (квадрат / вертикаль)."
)
TXT_CREATE_ANIMATE_HINT = "Пришли одним сообщением фото (как файл или сжатое изображение), которое нужно оживить."
TXT_UPSCALE_HINT = "Пришли фото, и я улучшу его четкость до максимума. Стоимость: 1 💎."
TXT_UPSCALE_PROCESSING = "Прокладываю кратчайший путь через нейроны... Списываю 1 💎 и готовлю UPSCALE."
TXT_UPSCALE_SUCCESS = "Доставлено в лучшем виде! 📦\n\n🔍 UPSCALE готов.\nСписано: 1 💎\nОстаток: {balance} 💎"
TXT_UPSCALE_FAILED = "Нейронная подкова разболталась. Сейчас подкуем и попробуем снова!"
TXT_CREATE_VIDEO_HINT = (
    "🎬 <b>PRO-видео</b> — только тариф <b>ULTRA</b>.\n\n"
    "Выбери категорию сценария ниже. Цены в 💎:\n"
    "• Бытовые пранки — 50–70 💎\n"
    "• Пранки с лицом — 70–100 💎 (нужно фото)\n"
    "• Свой сценарий — 40–80 💎\n"
    "• PRO 5 сек — 35 💎\n\n"
    "После ролика можно продлить (+5 сек) или заказать длинное видео."
)
TXT_VIDEO_NEED_PHOTO = "Пришли <b>фото с лицом</b> одним сообщением — для этого пранка нужен твой кадр."
TXT_VIDEO_NEED_PROMPT = "Опиши сцену текстом одним сообщением."
TXT_VIDEO_EXTEND_OK = "⏱ Запрос на продление (+5 сек) принят в очередь."
TXT_VIDEO_LONG_OK = "🎞 Запрос на длинное PRO-видео принят в очередь."
TXT_CREATE_MUSIC_HINT = (
    "Опиши трек: жанр, темп (BPM или «медленно»), настроение, язык вокала (если нужен), референс-артиста по желанию."
)
TXT_HD_NEED_CHANNEL = (
    "<b>Нужна подписка на канал</b>\n\n"
    "Чтобы купить <b>полный разбор личности</b>, сначала подпишись на наш канал — там бонусы и промокоды."
)
TXT_HD_NEED_CHANNEL_ALERT = "Сначала подпишись на канал, чтобы купить разбор."
TXT_HD_ASK_BIRTH_DATA = (
    "<b>Полный разбор личности</b> — <b>{cost} 💎</b>\n\n"
    "Пришли <b>одним сообщением</b> данные для расчёта:\n"
    "• Тип (если знаешь): Манифестор / Генератор / Проектор / Рефлектор\n"
    "• Дата рождения, <b>точное время</b> и <b>город</b> рождения (для часового пояса)."
)
TXT_HD_EMPTY_DATA = (
    "Не вижу данных. Пришли <b>тип</b> (если знаешь), <b>дату</b>, <b>время</b> и <b>город</b> рождения одним сообщением."
)
TXT_HD_PROCESSING = "<b>Мул пошёл в облака…</b> Скоро вернусь с ответом 🐎☁️"
TXT_HD_SUCCESS = "<b>Доставлено в лучшем виде!</b> 📦 Ниже текст отчёта и PDF."
TXT_HD_FAILED = (
    "<b>Сервис временно не ответил.</b>\n"
    "Кристаллы возвращены на баланс. Попробуй позже или загляни в <b>🚀 Тарифы</b>."
)
TXT_HD_INSUFFICIENT_CRYSTALS = (
    "<b>Недостаточно кристаллов</b>\n\n"
    "Для полного разбора нужно <b>{cost} 💎</b>.\n"
    "Пополни баланс в разделе <b>🚀 Тарифы</b>."
)
TXT_HD_INSUFFICIENT_CRYSTALS_ALERT = "Нужно {cost} 💎 для полного разбора."
TXT_NOT_ENOUGH_CRYSTALS = (
    "Недостаточно 💎\n\n"
    "Нужно: <b>{amount} 💎</b>\n"
    "На балансе: <b>{balance} 💎</b>"
)
TXT_HD_PAYMENT_OK = (
    "<b>Списание прошло успешно</b>\n"
    "Списано: <b>{cost} 💎</b>\n"
    "Ваш остаток: <b>{balance} 💎</b>"
)
TXT_HD_REPORT_READY = (
    "<b>🔮 Бодиграф расшифрован!</b>\n\n"
    "Выбери раздел для изучения — кнопки ниже."
)
TXT_HD_REPORT_NOT_FOUND = (
    "<b>Отчёт не найден.</b>\n"
    "Сначала оформи полный разбор личности заново."
)
TXT_HD_REPORT_NOT_FOUND_ALERT = "Сначала оформи полный разбор личности."
TXT_HD_PDF_CAPTION = "<b>PDF-отчёт</b> по Дизайну человека с бодиграфом."

# Анимация ожидания совета дня (plain, без HTML — совмещается с потоком текста от модели)
TXT_HD_DAILY_ANIM_1 = "⏳ Анализирую…"
TXT_HD_DAILY_ANIM_2 = "🔮 Считываю поле…"
TXT_HD_DAILY_ANIM_3 = "✨ Настраиваю связь…"
TXT_HD_DAILY_ADVICE_CONNECTING = "🔮 Настраиваю связь с твоим полем..."
TXT_HD_DAILY_ADVICE_ALREADY_TODAY = (
    "Вы уже получили свой совет дня сегодня. Возвращайтесь завтра! 🌌"
)
TXT_HD_DAILY_ADVICE_BUSY = (
    "Совет дня уже готовится. Подождите пару минут и не нажимайте кнопку повторно. 🔮"
)
TXT_HD_DAILY_ADVICE_FULL_REPORT_BTN = "💎 Получить Полный Разбор"
TXT_HD_DAILY_ADVICE_GENERATION_FAILED = (
    "Не удалось подготовить совет дня. Высшие силы сейчас перегружены ответами. "
    "🌌 Пожалуйста, попробуйте позже!"
)
TXT_HD_DAILY_ADVICE_CTA = (
    "💎 Усиль свой магнетизм в NeuroMule: загляни в раздел Создать — "
    "там визуал, музыка и инструменты для твоего бренда."
)

# Кнопка «Назад» из подменю Дизайна человека к списку инструментов
TXT_HD_BACK_TO_TOOLS = "⬅️ Назад"
TXT_HD_FREE_ADVICE_PROCESSING = "Готовлю бесплатный совет дня через Gemini."
TXT_HD_FREE_ADVICE_USED = (
    "⏳ <b>Твой «Совет дня» на сегодня уже успешно получен!</b>\n\n"
    "Следующее предсказание ИИ откроется завтра ровно в 00:00 по МСК. 🐎⚡️"
)
TXT_HD_FREE_ADVICE_USED_ALERT = (
    "⏳ Твой «Совет дня» на сегодня уже успешно получен! "
    "Следующее предсказание ИИ откроется завтра ровно в 00:00 по МСК. 🐎⚡️"
)
TXT_HD_FREE_ADVICE_GIFT_RECEIPT = (
    "\n───────────────────\n"
    "🧾 Чек операции @NeuroMule_bot 🐎⚡️\n"
    "• Списано: 0 ⚡ (Твой ежедневный подарок)\n"
    "• Следующий совет: завтра в 00:00 по МСК"
)
TXT_HD_FREE_ADVICE_FAILED = (
    "<b>Не удалось подготовить совет дня.</b>\n"
    "Попробуй чуть позже — сервис временно перегружен."
)
TXT_ADVICE_BIRTH_ASK = (
    "<b>📌 Для персонального совета нужны данные рождения</b>\n\n"
    "Пришли <b>одним сообщением</b>:\n"
    "• <b>дату</b> рождения (например, <code>14.05.1990</code>)\n"
    "• <b>точное время</b> (если есть — например <code>14:35</code>)\n"
    "• <b>место</b>: город или населённый пункт рождения (для часового пояса)\n"
    "• по желанию — строка <code>роль: мама / предприниматель / эксперт</code>\n\n"
    "<i>Если ты уже делал платный разбор в боте — данные подтянутся сами после его создания.</i>"
)
TXT_ADVICE_BIRTH_INVALID = (
    "<b>Не вижу дату рождения в сообщении.</b>\n\n"
    "Укажи хотя бы дату в формате <b>ДД.ММ.ГГГГ</b> и по возможности время и город."
)
TXT_ADVICE_BIRTH_SAVED = "<b>Принято.</b> Собираю твой персональный совет дня…"
TXT_ADVICE_BIRTH_CANCELLED = "<b>Ввод данных отменён.</b>\nВыбери действие в меню."
TXT_ADVICE_NEED_STATE = "<b>Сначала пришли дату, время и место рождения,</b>\nили снова нажми «🔮 Совет дня»."

TXT_MATCH_ASK_SECOND = (
    "{cost_line}\n\n"
    "Пришли данные второго человека одним сообщением: дата рождения, точное время и город."
)
TXT_MATCH_ASK_BOTH = (
    "{cost_line}\n\n"
    "У меня ещё нет твоих данных Бодиграфа. Пришли данные обоих людей одним сообщением:\n"
    "Вы: дата рождения, точное время и город\n"
    "Партнер: дата рождения, точное время и город"
)
TXT_MATCH_PROCESSING = "Считаю наложение карт через эфемериды и готовлю анализ совместимости через Gemini."
TXT_MATCH_INSUFFICIENT_CRYSTALS = "Для совместимости нужно {cost_match} 💎. Пополни кристаллы в «🚀 Тарифы»."


def format_match_ask_second(settings: object) -> str:
    return TXT_MATCH_ASK_SECOND.format(cost_line=format_match_cost_line(settings))


def format_match_ask_both(settings: object) -> str:
    return TXT_MATCH_ASK_BOTH.format(cost_line=format_match_cost_line(settings))


def format_match_insufficient_crystals(settings: object) -> str:
    return TXT_MATCH_INSUFFICIENT_CRYSTALS.format(
        cost_match=int(getattr(settings, "cost_match", 50)),
    )
TXT_MATCH_FAILED = "Не удалось подготовить совместимость. Списание возвращено, попробуй позже."
TXT_MATCH_EMPTY_DATA = "Пришли данные второго человека текстом."
TXT_PHOTO_PROCESS = (
    "Принял запрос. Модель: {model}. Генерация будет подключена к API.\n\n{wait_note}"
)
TXT_VIDEO_PROCESS = "Видео-задача принята. Обработка подключается.\n\n{wait_note}"
TXT_ANIMATE_PROCESS = "Фото принято. Оживление подключается.\n\n{wait_note}"
TXT_MUSIC_PROCESS = "Трек в работе. Подключение аудио-API.\n\n{wait_note}"
TXT_PROFILE_TEMPLATE = (
    "👤 <b>Мой профиль NeuroMule</b>\n\n"
    "🆔 <b>Твой ID:</b> <code>{user_id}</code>\n"
    "{crystals_info_line}\n\n"
    "---\n\n"
    "📊 <b>Текущий тариф:</b> {tariff_status_info}\n"
    "• ⚡️ <b>Доступная Энергия:</b> {energy_bar} {energy_balance} / 30\n"
    "• 🎨 <b>Базовые фото:</b> {photos_bar} {free_photos_left} / 3 сегодня\n\n"
    "{limits_info_text}\n\n"
    "---\n\n"
    "🧬 <b>Твой Дизайн Человека:</b>\n"
    "{hd_profile_status}\n\n"
    "---\n\n"
    "🤝 <b>Реферальная программа Мула</b>\n"
    "Приглашай друзей по своей ссылке и генерируй PRO-фото бесплатно! "
    "За каждого друга, который подпишется на канал, ты мгновенно получишь "
    "<b>+2 💎 Кристалла</b> (это 1 PRO-генерация Flux)!\n\n"
    "🔗 <b>Твоя ссылка для приглашений:</b>\n"
    "<code>{ref_link}</code>\n\n"
    "👥 <i>Уже приглашено друзей: {total_ref_count} человек</i>"
)
TXT_CABINET_TEMPLATE = TXT_PROFILE_TEMPLATE
TXT_PROFILE_REFRESH_BUTTON = "🔄 Обновить баланс"
TXT_PROFILE_TARIFFS_BUTTON = "🚀 Пополнить баланс / Тарифы"
TXT_PROFILE_PROMO_BUTTON = "🎁 Ввести промокод"
TXT_PROFILE_MEMORY_BUTTON = "🧠 Моя память"
TXT_PROFILE_DUO_BUTTON = "👫 Управление DUO-доступом"
TXT_PROFILE_FAMILY_BUTTON = TXT_PROFILE_DUO_BUTTON  # deprecated alias

# --- ИИ-Память ---
TXT_MEMORY_INTRO_EMPTY = (
    "🧠 <b>Моя память NeuroMule 🐎⚡️</b>\n\n"
    "Пока у меня нет о тебе никаких заметок. Расскажи 3-5 предложений о себе — "
    "имя, возраст, профессия, любимые темы, стиль общения. Я буду подмешивать "
    "эти данные перед каждым запросом к ИИ, чтобы ответы были максимально "
    "точно под тебя.\n\n"
    "📝 Просто отправь следующим сообщением свой текст, и я его сохраню."
)
TXT_MEMORY_INTRO_FILLED = (
    "🧠 <b>Моя память NeuroMule 🐎⚡️</b>\n\n"
    "Я уже запомнил о тебе:\n\n<blockquote>{memory}</blockquote>\n\n"
    "📝 Отправь новый текст — он перезапишет память. Или нажми «Очистить»."
)
TXT_MEMORY_PROMPT = (
    "🧠 Запиши любые факты, которые ИИ должен помнить: имя, возраст, профессия, "
    "хобби, стиль ответа. Текст до 1500 символов."
)
TXT_MEMORY_SAVED = (
    "✅ <b>Память обновлена!</b>\n"
    "Эти данные я буду подмешивать перед каждым запросом к ИИ. Кнопка «🧹 Новый "
    "диалог» эту память не стирает — только историю чата 🐎⚡️."
)
TXT_MEMORY_CLEARED = "🧠 Память очищена. Я больше не использую старые заметки 🐎⚡️."
TXT_MEMORY_TOO_LONG = (
    "⚠️ Слишком длинный текст. Сократи до 1500 символов — лаконичнее значит точнее."
)
TXT_MEMORY_BTN_SET = "✍️ Записать память"
TXT_MEMORY_BTN_CLEAR = "🗑 Очистить"
TXT_MEMORY_BTN_BACK = "⬅️ В кабинет"

# --- Опция DUO (ULTRA 1 месяц) ---
TXT_DUO_INTRO = (
    "👫 <b>Доступ на двоих — Опция DUO 🐎⚡️</b>\n\n"
    "Подели ULTRA-кошелёк (⚡ + 💎) с <b>одним партнёром</b>: ты + 1 приглашённый. "
    "У каждого сохраняется индивидуальная ИИ-Память, личные данные HD и личный "
    "«🔮 Совет дня» — но списания идут <b>с твоего общего кошелька</b>.\n\n"
    "Партнёров в DUO: <b>{count} / {limit}</b>."
)
TXT_DUO_NOT_ELIGIBLE = (
    "🔒 <b>Опция DUO</b> — эксклюзив тарифа <b>ULTRA (1 месяц)</b>. "
    "Активируй месячный ULTRA в «🚀 Тарифы», чтобы открыть доступ на двоих."
)
TXT_DUO_ADD_ASK = (
    "👫 Пришли <b>Telegram ID</b> партнёра, с кем хочешь разделить подписку "
    "(только числа). Он должен хотя бы раз запустить @NeuroMule_bot. ✉️"
)
TXT_DUO_ADD_BAD_ID = "⚠️ ID должен быть числом. Пример: <code>123456789</code>."
TXT_DUO_ADD_NOT_REGISTERED = (
    "⚠️ Я не нашёл пользователя с таким ID. Попроси его открыть @NeuroMule_bot и "
    "запустить /start — после этого попробуй ещё раз."
)
TXT_DUO_ADD_OK = (
    "✅ <b>Готово!</b> ID <code>{member_id}</code> подключён к твоей DUO-паре. "
    "Его генерации будут списываться с твоего кошелька 🐎⚡️."
)
TXT_DUO_ADD_FAIL = {
    "self": "⚠️ Нельзя добавить самого себя.",
    "already_linked": "⚠️ Этот пользователь уже в другой DUO-связке.",
    "limit_reached": (
        "⚠️ Достигнут лимит участников. Эта подписка уже разделена на двоих."
    ),
    "not_duo_eligible": (
        "🔒 Опция DUO доступна только при активном тарифе ULTRA (1 месяц)."
    ),
    "not_ultra": "🔒 Опция DUO доступна только при активном тарифе ULTRA (1 месяц).",
}
TXT_DUO_UNLINK_OK = "✅ Партнёр <code>{member_id}</code> отвязан от DUO."
TXT_DUO_BTN_ADD = "➕ Пригласить партнёра"
TXT_DUO_BTN_UNLINK = "🚪 Отвязать ID {member_id}"
TXT_DUO_BTN_BACK = "⬅️ В кабинет"
# deprecated aliases (старые ключи в хендлерах/тестах)
TXT_FAMILY_INTRO = TXT_DUO_INTRO
TXT_FAMILY_NOT_ULTRA = TXT_DUO_NOT_ELIGIBLE
TXT_FAMILY_ADD_ASK = TXT_DUO_ADD_ASK
TXT_FAMILY_ADD_BAD_ID = TXT_DUO_ADD_BAD_ID
TXT_FAMILY_ADD_NOT_REGISTERED = TXT_DUO_ADD_NOT_REGISTERED
TXT_FAMILY_ADD_OK = TXT_DUO_ADD_OK
TXT_FAMILY_ADD_FAIL = TXT_DUO_ADD_FAIL
TXT_FAMILY_UNLINK_OK = TXT_DUO_UNLINK_OK
TXT_FAMILY_BTN_ADD = TXT_DUO_BTN_ADD
TXT_FAMILY_BTN_UNLINK = TXT_DUO_BTN_UNLINK
TXT_FAMILY_BTN_BACK = TXT_DUO_BTN_BACK
TXT_PROFILE_REFRESH_OK = "✨ Баланс обновлен!"
TXT_PROFILE_ALREADY_FRESH = "Данные баланса актуальны."

TXT_HEAVY_MEDIA_INSUFFICIENT_CRYSTALS = (
    "💎 <b>Не хватает Кристаллов для премиум-операции</b>\n\n"
    "Требуется: <b>{need} 💎</b> на «{feature}». На балансе: <b>{have} 💎</b>.\n\n"
    "Тяжёлое медиа (Видео / Музыка / Оживление / Разбор HD) оплачивается "
    "строго Кристаллами — Энергия не списывается.\n\n"
    "🚀 Возьми пакет 💎 и продолжай без пауз 👇"
)
TXT_REFERRAL_CHANNEL_BONUS = (
    "🎉 Твой друг успешно активировал Мула! Тебе начислено +2 💎 Кристалла. "
    "Проверь баланс в меню 👤 Мой профиль."
)
TXT_CABINET_INVITE_BUTTON = "👥 Пригласить друга"
TXT_CABINET_PROMO_BUTTON = TXT_PROFILE_PROMO_BUTTON
TXT_CABINET_CHANNEL_PROMOS = "📢 Канал с промокодами"

INVITE_SWITCH_QUERY_TEMPLATE = (
    "Смотри, нашел крутого бота с GPT-4 и генерацией видео! Заходи: @{bot_username}"
)

TXT_PROMO_ASK = "🎁 Есть подарочный код? Введи его здесь — начислю бонус мгновенно."
TXT_PROMO_GIFT_REDEEMED = (
    "🎉 <b>Подарочный код активирован!</b>\n\n"
    "На твой вечный баланс начислено: <b>{payload}</b>\n"
    "Кристаллы 💎 от подарочных кодов <i>не сгорают</i> — лежат, ждут идеи 🚀"
)
TXT_PROMO_TARIFF_BLOCKED = (
    "⚠️ <b>Код недоступен на твоём тарифе</b>\n\n"
    "Этот подарочный код выдан для пользователей платных пакетов. "
    "Активируй MINI / SMART / ULTRA в меню «🚀 Тарифы» и снова попробуй ввести код."
)
TXT_PROMO_UNKNOWN = "❌ Такого подарочного кода нет. Следи за публикациями в канале."
TXT_PROMO_USED = "❌ Этот подарочный код ты уже активировала."
TXT_PROMO_EXHAUSTED = "❌ Лимит активаций этого подарочного кода исчерпан."

TXT_GEN_STATUS_ACCEPTED = "Мул пошел в облака. Скоро буду с ответом 🐎☁️"
TXT_VIDEO_QUEUE_ACCEPTED = (
    "🎬 <b>Запрос на видео принят в студию NeuroMule 🐎⚡️</b>\n\n"
    "⏱️ Среднее время рендера: <b>1–3 минуты</b>.\n"
    "Я пришлю готовый ролик сразу как Replicate закончит просчёт кадров — "
    "можешь спокойно закрыть чат или продолжить работу."
)
TXT_GEN_JOB_FAILED = "Нейронная подкова разболталась. Сейчас подкуем и попробуем снова!"
TXT_CHAT_AI_UNAVAILABLE = (
    "😵 <b>Нейросеть временно недоступна</b>\n\n"
    "OpenRouter не принял запрос (модель занята или устарел ID в настройках). "
    "⚡ и 💎 за этот ответ <b>не списаны</b> — попробуйте через минуту.\n\n"
    "Если ошибка повторяется — проверьте в <code>.env</code> "
    "<code>FREE_TEXT_MODEL=google/gemini-2.5-flash</code>."
)

TXT_PHOTO_DAILY_LIMIT = (
    "❌ ЛИМИТ ИСЧЕРПАН.\n\n"
    "На бесплатном тарифе доступно {limit} генераций фото в сутки.\n"
    "Загляни в «👤 Мой профиль» — пригласи друга или дождись завтра."
)
TXT_ACCESS_SMART_PLUS = "❌ Видео и Музыка доступны в тарифе SMART и выше"
TXT_UPGRADE_TO_SMART = "❌ Видео и Музыка закрыты на этом тарифе. Открой SMART, чтобы продолжить."
TXT_UPGRADE_TO_ULTRA = "❌ Видео доступно только в тарифе ULTRA. Открой ULTRA, чтобы продолжить."

TXT_RESULT_PHOTO_CAPTION = (
    "✨ ТВОЙ ШЕДЕВР ГОТОВ!\n\n"
    "Нейросеть создала изображение по твоему запросу. Оцени качество детализации! 🚀\n\n"
    "📉 Затраты: {cost} 💎\n"
    "🔋 Остаток кристаллов: {balance} 💎\n\n"
    "Что делаем дальше?\n"
    "🪄 Оживить это фото (Видео) — {animate_cost} 💎\n"
    "🔄 Повторить генерацию — {cost} 💎\n"
    "📥 Скачать в максимальном качестве — PRO\n\n"
    "Поделись результатом с друзьями и получи бонус! 👇"
)
TXT_RESULT_VIDEO_CAPTION = (
    "🎥 ВИДЕО СГЕНЕРИРОВАНО!\n\n"
    "Твоя идея ожила. Проверь результат ниже! 👇\n"
    "⏳ Длительность: 5–10 сек\n"
    "💎 Списано: {cost} 💎\n\n"
    "Хочешь приоритетную обработку без очереди?\n"
    "Оформи тариф ULTRA в разделе «Тарифы»!"
)
TXT_RESULT_MUSIC_CAPTION = (
    "🎧 <b>ТРЕК ЗАПИСАН!</b>\n"
    "📝 Стиль: {style}\n"
    "💎 Кристаллы: {balance} 💎\n"
    "───────────────────\n"
    "🧾 <b>Чек операции @NeuroMule_bot 🐎⚡️:</b>\n"
    "• Списано: {cost} 💎 (Режим: Музыка Suno AI)\n"
    "• Твой остаток: {balance} 💎"
)

# ─── Suno-студия NeuroMule 2026 ──────────────────────────────────────────────
TXT_MUSIC_STUDIO_INTRO = (
    "🎸 <b>Музыкальная студия NeuroMule 🐎⚡️</b>\n\n"
    "Создай студийный трек с реальным вокалом за 1–3 минуты на движке "
    "<b>Suno AI v4</b>. Стоимость одной полноценной записи — <b>15 💎</b>.\n\n"
    "Выбери, как будем писать твой хит 👇"
)
TXT_MUSIC_FREE_BLOCKED_ALERT = (
    "🔒 Доступ ограничен! Музыкальная студия Suno AI закрыта на Бесплатном "
    "тарифе. Активируй MINI, SMART или ULTRA, чтобы открывать VIP-функции "
    "NeuroMule 🐎⚡️!"
)
TXT_MUSIC_INSUFFICIENT_CRYSTALS = (
    "💎 <b>Не хватает Кристаллов для Suno-студии</b>\n\n"
    "Полный трек стоит <b>15 💎</b>, а у тебя сейчас <b>{balance} 💎</b>. "
    "Энергия (⚡) для музыки <i>не используется</i> — это премиум-медиа. "
    "Возьми один из вечных пакетов 💎 и записывай хиты без ограничений 🐎⚡️."
)
TXT_MUSIC_MODE_AI_BTN = "✍️ ИИ пишет текст + Стиль"
TXT_MUSIC_MODE_CUSTOM_BTN = "📝 Мой личный текст песни"
TXT_MUSIC_MODE_INSTRUMENTAL_BTN = "🎹 Только музыка (Минус)"
TXT_MUSIC_ASK_STYLE_AI = (
    "✍️ <b>Режим «ИИ-сценарист + Стиль»</b>\n\n"
    "Опиши стиль трека: жанр, темп (BPM или «медленно»), настроение, язык "
    "вокала, референс-артиста. Я сам напишу слова и сведу студийную запись."
)
TXT_MUSIC_ASK_LYRICS = (
    "📝 <b>Режим «Свой текст»</b>\n\n"
    "Пришли мне сам текст песни (до 1500 символов). После этого я попрошу "
    "описание стиля и сразу запущу запись 🐎⚡️."
)
TXT_MUSIC_ASK_STYLE_AFTER_LYRICS = (
    "🎼 Текст принят! Теперь опиши <b>стиль</b> и настроение: жанр, темп, "
    "вокал, референс-артиста. На этом я закрою сборку и запущу Suno."
)
TXT_MUSIC_ASK_INSTRUMENTAL_STYLE = (
    "🎹 <b>Режим «Только музыка (минус)»</b>\n\n"
    "Опиши инструментал: жанр, темп, инструменты, настроение, референс. "
    "Запишу плотный студийный минус без вокала."
)
TXT_MUSIC_LYRICS_TOO_LONG = (
    "⚠️ Текст песни слишком длинный — сократи до 1500 символов, чтобы Suno "
    "успел нормально его обработать."
)
TXT_MUSIC_UPSELL_SOON = (
    "🚧 Эта фишка появится в ближайшем апдейте NeuroMule 🐎⚡️. Пока мы её "
    "финально полируем — 💎 не списываются, не переживай!"
)
TXT_MUSIC_EXTEND_QUEUED = (
    "⏱ Запрос на продление трека (+1 мин) принят! Suno уже стыкует новый "
    "блок к твоему предыдущему миксу 🐎⚡️."
)
TXT_MUSIC_EXTEND_NO_HISTORY = (
    "⚠️ Не нашёл предыдущий трек, который можно продлить. Сначала запиши "
    "любую композицию в Музстудии 🎸."
)
TXT_MUSIC_PUBLISH_NO_CHANNEL = (
    "📢 Публичная ИИ-радио-витрина пока в настройке. Скоро трек сможет "
    "выкладываться в наш канал в один клик 🐎⚡️."
)
TXT_RESULT_ANIMATE_CAPTION = (
    "✨ ОЖИВЛЕНИЕ ГОТОВО!\n\n"
    "💎 Списано: {cost} 💎\n"
    "🔋 Остаток: {balance} 💎"
)
TXT_ANIMATE_SUCCESS = (
    "🎬 Фотография успешно оживлена! Нейросеть NeuroMule превратила ваш статичный кадр в живое видео."
)
TXT_ANIMATE_SOURCE_CAPTION = "Оживлённый исходник (тест контура)"
TXT_ANIMATE_FAILED = "⚠️ Не удалось оживить фотографию. Попробуйте другой снимок."
TXT_HD_MATCH_DUO_PICKER = (
    "💞 <b>Совместимость HD — выбери партнёра</b>\n\n"
    "У тебя подключена <b>Опция DUO</b> и твой партнёр уже прошёл Полный разбор. "
    "Я могу мгновенно собрать композит — просто выбери, с кем считать совместимость."
)
TXT_HD_MATCH_DUO_PARTNER_NO_DATA = (
    "⚠️ У этого партнёра ещё нет данных рождения. Попроси его сначала пройти "
    "Полный разбор HD, и мы автоматически подтянем его карту в композит 🐎⚡️."
)
TXT_HD_MATCH_FAMILY_PICKER = TXT_HD_MATCH_DUO_PICKER
TXT_HD_MATCH_FAMILY_MEMBER_NO_DATA = TXT_HD_MATCH_DUO_PARTNER_NO_DATA
TXT_VIDEO_REPLICATE_FAILED = (
    "⚠️ <b>Видео-студия NeuroMule временно недоступна.</b>\n\n"
    "Replicate вернул ошибку, поэтому мы автоматически вернули списанные 💎 на твой "
    "баланс. Попробуй ещё раз через минуту или измени описание — иногда промпт "
    "содержит запрещённые модели слова."
)
TXT_VIDEO_REGENERATE_NO_HISTORY = (
    "⚠️ Не нашёл предыдущий видео-запрос. Сначала запусти любую видео-генерацию "
    "из меню «🎬 Создать видео» — после этого кнопка «Сгенерировать заново» оживёт."
)
TXT_VIDEO_REGENERATE_FAILED = (
    "⚠️ <b>Повторная генерация не удалась.</b>\n"
    "Скорее всего не хватает 💎 или поменялся тариф. Загляни в «👤 Личный кабинет» "
    "или докупи Кристаллы в магазине 🐎⚡️."
)
TXT_VIDEO_UPSCALE_SOON = (
    "🔍 Видео-Upscale (5 💎) появится в следующем большом обновлении NeuroMule 🐎⚡️. "
    "Пока этот режим в финальной обкатке у наших ML-инженеров — мы не списываем 💎, "
    "пока качество не дотянем до 8K. Спасибо за доверие!"
)
TXT_ANIMATE_REPLICATE_FAILED = (
    "⚠️ Не удалось оживить фотографию. Убедитесь, что на фото нет сильных размытий."
)
TXT_MUSIC_SUNO_FAILED = (
    "⚠️ <b>Suno AI не смог собрать трек</b>\n\n"
    "Сервис временно перегружен или не смог распознать стиль. "
    "<b>Кристаллы автоматически возвращены на твой баланс</b>. "
    "Попробуй ещё раз через минуту и слегка перефразируй стиль 🐎⚡️."
)
TXT_MUSIC_QUEUE_ACCEPTED = (
    "⏳ Ваш запрос на музыку успешно принят в очередь <b>NeuroMule 2026</b>. "
    "Генерация полноценного трека со студийным сведением и вокалом занимает "
    "от 1 до 3 минут. Пожалуйста, подождите."
)
TXT_ANIMATE_QUEUE_ACCEPTED = (
    "⏳ Фотография получена. NeuroMule добавляет задачу в очередь на оживление…"
)

TXT_BALANCE_LOW_FOOTER = "\n\n⚠️ Внимание: кристаллы заканчиваются! [Пополнить] — раздел «Тарифы»."

TXT_SUPPORT_BTN_FAQ = "❓ Частые вопросы (FAQ)"
TXT_SUPPORT_BTN_GUIDES = "📖 Инструкция и Гайды"
TXT_SUPPORT_BTN_PAYMENT = "💳 Не прошел платеж"
TXT_SUPPORT_BTN_SUBSCRIPTION = "💳 Управление подпиской"
TXT_SUPPORT_BTN_WRITE = "✍️ Отзыв / Вопрос админу"
TXT_SUPPORT_BTN_CLOSE = "❌ Закрыть"
TXT_SUPPORT_BTN_BACK_FAQ = "🔙 Назад в FAQ"
TXT_SUPPORT_BTN_BACK_MAIN = "🔙 Назад в меню"
TXT_SUPPORT_BTN_CHECK_PAYMENT = "🔄 Проверить платеж"
TXT_FAQ_BTN_ENERGY = "⚡️ Что такое Энергия?"
TXT_FAQ_BTN_HD_DIFF = "🔮 Совет дня vs Разбор"
TXT_FAQ_BTN_SLOW_GEN = "⏳ Почему зависла генерация?"
TXT_FAQ_BTN_PROMPTS = "✍️ Как писать промпты?"
TXT_FAQ_BTN_PRIVACY = "🔒 Безопасны ли мои фото?"
TXT_FAQ_BTN_CANCEL_SUB = "💳 Как отключить подписку?"
TXT_FAQ_BTN_HD_SOURCE = "🧬 Откуда данные HD?"
TXT_FAQ_BTN_REFUND = "💎 Вернут кристаллы за брак?"
TXT_FAQ_BTN_STARS = "⭐ Почему в Звездах дороже?"
TXT_FAQ_WRITE_QUESTION_BTN = TXT_SUPPORT_BTN_WRITE

TXT_SUPPORT_MAIN = (
    "🆘 <b>Поддержка и помощь NeuroMule</b>\n\n"
    "Возникли вопросы, что-то пошло не так или хочешь оставить отзыв и научиться "
    "генерировать шедевры как PRO? Мул готов помочь и во всем разобраться! 🐎⚡️\n\n"
    "📄 <b>Документы сервиса:</b> "
    '<a href="{offer_url}">Оферта</a> | '
    '<a href="{privacy_url}">Конфиденциальность</a> | '
    '<a href="{subscription_url}">Условия подписки</a>\n\n'
    "Выбери нужный раздел на кнопках ниже 👇"
)

TXT_SUPPORT_FAQ_MENU = (
    "❓ <b>Частые вопросы (FAQ)</b>\n"
    "Выбери интересующую тему, чтобы получить моментальный ответ:"
)

TXT_FAQ_ANSWER_ENERGY = (
    "⚡️ <b>Что такое Энергия и как она тратится?</b>\n"
    "Энергия — это внутреннее топливо Мула для работы с текстом.\n"
    "• <b>На что она тратится:</b> каждое твое сообщение в разделах «📝 Нейротекст» "
    "или общем «ИИ-Чате» (нейросеть GPT-4o) расходует Энергию.\n"
    "• <b>Как её восстановить:</b> на бесплатном тарифе FREE тебе ежедневно начисляется "
    "30 ⚡️ Энергии (обнуление в 00:00 по МСК). Если энергия закончилась, ты можешь купить "
    "платный тариф с безлимитным чатом в разделе «🚀 Тарифы» или пригласить друзей в меню "
    "«👤 Мой профиль» и получить бонусы!\n"
    "<i>*Обрати внимание: генерация PRO-картинок и видео требует Кристаллы 💎, а не Энергию.</i>"
)

TXT_FAQ_ANSWER_HD_DIFF = (
    "🔮 <b>Чем отличается «Совет дня» от «Полного разбора» по Дизайну Человека?</b>\n"
    "В NeuroMule доступны два формата работы с Хьюман Дизайном (Human Design):\n"
    "1️⃣ <b>🔮 Совет дня (Бесплатно для всех)</b>\n"
    "• Твой ежедневный энергетический прогноз и короткая подсказка на сегодня. Бот анализирует "
    "текущий день и даёт один практичный совет, как правильно принимать решения и избежать стресса. "
    "Доступен 1 раз в сутки.\n"
    "2️⃣ <b>🧬 Полный разбор карты (Требует Кристаллы 💎)</b>\n"
    "• Глубокий, детальный анализ твоей личности на основе даты, времени и города рождения. "
    "Бот рассчитывает твой Бодиграф. Ты узнаешь свой генетический Тип (Манифестор, Генератор, "
    "Проектор, Рефлектор), свой Профиль, Стратегию и Авторитет для безошибочного принятия решений. "
    "Результат сохраняется в твоём профиле навсегда."
)

TXT_FAQ_ANSWER_SLOW_GEN = (
    "⏳ <b>Бот долго молчит, генерация зависла?</b>\n"
    "Не переживай, Мул уже вовсю работает над твоим запросом!\n"
    "• <b>В чем причина:</b> Нейросети для создания видео (Kling AI) и музыки (Suno AI) — "
    "это сложнейшие технологии. На создание одного трека или минутного ролика серверам требуется "
    "от 1 до 3 минут времени (в моменты высокой нагрузки — чуть дольше).\n"
    "• <b>Что делать:</b> Просто подожди. Тебе не нужно отправлять запрос заново "
    "(иначе спишутся лишние кристаллы). Как только шедевр будет готов, Мул мгновенно пришлет "
    "его в этот чат!"
)

TXT_FAQ_ANSWER_PROMPTS = (
    "✍️ <b>На каком языке писать запросы (промпты)?</b>\n"
    "Мул — очень умный и отлично понимает команды на любом языке, включая русский.\n"
    "• <b>Для ИИ-Чата (GPT-4o) и текста:</b> Пиши так, как тебе удобно. Нейросеть идеально "
    "общается на русском языке, улавливает контекст, шутки и профессиональные термины.\n"
    "• <b>Для PRO-фото (Flux Schnell):</b> Бот переведет твой русский запрос автоматически. "
    "Но если ты хочешь получить максимальную детализацию (например, глянцевый журнал, неоновое "
    "освещение, кинокамера), лучше писать промпт на английском языке — так нейросеть поймет "
    "твою задумку на все 100%."
)

TXT_FAQ_ANSWER_PRIVACY = (
    "🔒 <b>Безопасность данных и личных фотографий</b>\n"
    "Мы невероятно серьезно относимся к твоей конфиденциальности.\n"
    "• <b>Твои фото для дипфейков:</b> Все изображения, которые ты загружаешь в бота для создания "
    "ИИ-фото или видео, используются сервером только в момент самой генерации. Они не сохраняются "
    "в открытом доступе и автоматически удаляются сразу после выдачи результата. Никто, кроме "
    "тебя, их не увидит.\n"
    "• <b>Данные рождения (Human Design):</b> Твоя дата, время и место рождения нужны "
    "исключительно алгоритму для расчета Бодиграфа. Они зашифрованы и привязаны только к твоему "
    "личному профилю."
)

TXT_FAQ_ANSWER_CANCEL_SUB = (
    "💳 <b>Как отменить автоматическое продление тарифа?</b>\n"
    "Ты полностью контролируешь свои финансы. Отключить автопродление подписки можно в любой "
    "момент самостоятельно в один клик.\n"
    "• <b>Как это сделать:</b> Перейди в главное меню поддержки ➔ нажми инлайн-кнопку "
    "«💳 Управление подпиской». Там будет доступна кнопка «Отменить подписку».\n"
    "• <b>Что произойдет после отмены:</b> Деньги со следующего месяца списываться не будут. "
    "Твой текущий оплаченный тариф (PREMIUM/VIP) продолжит полноценно работать до самого конца "
    "своего 30-дневного срока, после чего бот бережно переведет тебя на бесплатный тариф FREE."
)

TXT_FAQ_ANSWER_HD_SOURCE = (
    "🧬 <b>Как Мул рассчитывает Дизайн Человека? Это точно?</b>\n"
    "Наш бот не выдумывает ответы «из головы». Внутри NeuroMule интегрированы профессиональные "
    "астрологические и математические алгоритмы расчета Бодиграфа.\n"
    "• <b>Откуда берутся данные:</b> На основе введенного времени и города рождения бот "
    "рассчитывает точное положение планет в минуту твоего появления на свет, сверяясь с "
    "международными базами эфемерид.\n"
    "• <b>Кто делает анализ:</b> Окончательную расшифровку и генерацию «Совета дня» формирует "
    "специально обученная нейросеть, в которую загружены фундаментальные труды по Хьюман Дизайну. "
    "Ты получаешь глубокий, современный и понятный разбор без сложной терминологии."
)

TXT_FAQ_ANSWER_REFUND_CRYSTALS = (
    "💎 <b>Что делать, если генерация ИИ получилась неудачной?</b>\n"
    "Нейросети — это творческий инструмент, и иногда на картинках могут двоиться пальцы, "
    "а видео может смазаться.\n"
    "• <b>Если произошла системная ошибка:</b> Если сервер завис или бот выдал техническую "
    "ошибку, Кристаллы 💎 за эту попытку автоматически возвращаются на твой баланс.\n"
    "• <b>Если не понравился результат:</b> Нейросеть каждый раз создает уникальный контент "
    "на основе твоего промпта. Если результат тебя не устроил, попробуй детальнее описать "
    "задумку. Кристаллы за корректно выполненные сервером генерации не возвращаются, так как "
    "вычислительные мощности ИИ уже были потрачены."
)

TXT_FAQ_ANSWER_STARS_COST = (
    "⭐ <b>Почему оплата в Telegram Stars выходит дороже, чем по карте?</b>\n\n"
    "Telegram Stars — это официальная внутренняя валюта мессенджера, но у неё есть свои "
    "экономические особенности.\n\n"
    "• <b>В чем причина разницы цен:</b> Когда ты покупаешь Звезды внутри приложения, "
    "корпорации Apple (на iPhone) и Google (на Android) закладывают в их стоимость свою "
    "скрытую наценку и комиссию за международный транзит (до 30%). Из-за этого реальная "
    "стоимость одной Звезды при покупке в сторах сильно завышена.\n"
    "• <b>Как сэкономить:</b> Наша платежная система принимает прямые платежи в рублях с "
    "любых банковских карт РФ. Оплата картой напрямую нашему сервису обходится тебе "
    "<b>до 31% выгоднее</b>, так как не содержит никаких скрытых комиссий App Store или "
    "Google Play!\n\n"
    "💡 <i>Выбор за тобой, но Мул рекомендует платить картой напрямую в рублях — это самый "
    "честный, прозрачный и экономичный способ! 🐎</i>"
)

TXT_SUPPORT_GUIDES = (
    "📖 <b>Гайды и Инструкции по боту</b>\n"
    "Мы подготовили подробные иллюстрированные руководства, чтобы твои генерации всегда "
    "получались идеальными:\n"
    "• <a href=\"{instruction_url}\">Полная инструкция по функциям бота</a>\n"
    "• <a href=\"{channel_url}\">Секретные промпты и лайфхаки в нашем канале</a>"
)

TXT_SUPPORT_PAYMENT_ISSUE = (
    "💳 <b>Проблемы с оплатой тарифа</b>\n"
    "Если ты оплатил тариф или кристаллы, но баланс в боте не изменился:\n"
    "1. Подожди 1-2 минуты (иногда платежные шлюзы задерживают ответ).\n"
    "2. Нажми кнопку «🔄 Проверить платеж» ниже, чтобы бот принудительно обновил статус "
    "транзакции.\n"
    "3. Если это не помогло, нажми «Назад» ➔ «Отзыв / Вопрос админу», прикрепи чек, "
    "и мы сразу начислим баланс вручную!"
)

TXT_SUPPORT_MANAGE_SUBSCRIPTION = (
    "💳 <b>Управление подпиской</b>\n\n"
    "Отмена автопродления и правила списаний описаны в "
    '<a href="{subscription_url}">Условиях подписки</a>.\n\n'
    "Дата окончания тарифа — в «👤 Мой профиль». "
    "По индивидуальным вопросам — «✍️ Отзыв / Вопрос админу»."
)

TXT_SUPPORT_SECTION = TXT_SUPPORT_MAIN
TXT_FAQ_SUPPORT = TXT_SUPPORT_MAIN
TXT_SUPPORT_FAQ = TXT_SUPPORT_MAIN


def format_support_text(settings: object) -> str:
    """Главный экран поддержки (HTML, ссылки из settings)."""
    return TXT_SUPPORT_MAIN.format(
        offer_url=getattr(settings, "service_offer_url", ""),
        privacy_url=getattr(settings, "privacy_policy_url", ""),
        subscription_url=getattr(settings, "subscription_terms_url", ""),
    )


def format_faq_support_text(settings: object) -> str:
    """Обратная совместимость."""
    return format_support_text(settings)


def format_start_paywall_text(settings: object) -> str:
    """Hard Paywall на /start (Markdown + ссылки на документы из settings)."""
    return TXT_START_PAYWALL.format(
        offer_url=getattr(settings, "service_offer_url", ""),
        privacy_url=getattr(settings, "privacy_policy_url", ""),
        subscription_url=getattr(settings, "subscription_terms_url", ""),
    )

TXT_SUPPORT_WRITE_ASK = (
    "📝 <b>На связи с командой Мула!</b>\n\n"
    "Напиши свой вопрос, опиши проблему или оставь честный отзыв в ОДНОМ сообщении ниже "
    "(можно прикрепить фото/скриншот). Мы читаем каждое послание и ответим тебе прямо сюда, "
    "в чат с ботом! 👇\n\n"
    "Отмена: /cancel"
)
TXT_SUPPORT_TICKET_ADMIN = (
    "📬 <b>Новое обращение (Вопрос/Отзыв)!</b>\n"
    "👤 От: {user_name}\n"
    "🆔 ID: <code>{user_id}</code>\n\n"
    "💬 <b>Текст:</b>\n{text}"
)
TXT_SUPPORT_TICKET_OK = (
    "✅ Сообщение успешно отправлено администрации! Менеджеры уже изучают его "
    "(или радуются твоему отзыву) и ответят тебе прямо сюда."
)
TXT_SUPPORT_REPLY_USER = (
    "🔔 <b>Ответ техподдержки NeuroMule:</b>\n\n{body}"
)
TXT_FEEDBACK_ASK = TXT_SUPPORT_WRITE_ASK
TXT_FEEDBACK_DELIVERED = TXT_SUPPORT_TICKET_OK
TXT_FEEDBACK_EMPTY = "Отправьте текст или фото с описанием проблемы."
TXT_FEEDBACK_CANCELLED = "Обращение в поддержку отменено."
TXT_FEEDBACK_TICKET_HEADER = (
    "📩 <b>НОВОЕ ОБРАЩЕНИЕ В ПОДДЕРЖКУ</b>\n"
    "👤 Отправитель: {username} (ID: <code>{user_id}</code>)\n"
    "───\n"
)
TXT_FEEDBACK_REPLY_USER = TXT_SUPPORT_REPLY_USER
TXT_FEEDBACK_REPLY_SENT = "🚀 Ответ успешно отправлен пользователю <code>{user_id}</code>!"
TXT_FEEDBACK_REPLY_FAILED = "❌ Не удалось отправить ответ: {error}"
TXT_FEEDBACK_NO_ADMINS = (
    "⚠️ Служба поддержки временно недоступна. Попробуйте позже."
)

TXT_INSTRUCTION = (
    "📍 Инструкция: Как управлять Нейро-Мулом?\n\n"
    "📝 Текст — быстрые ответы и экспертные роли.\n"
    "🎨 Фото — Imagen 4 бесплатно (3/день), PRO (Flux и др.) — за 💎.\n"
    "🎵 Музыка — идеи треков и генерация через музыкальные Системы.\n"
    "🎬 Видео — короткие ролики через видео-Системы.\n"
    "👤 Мой профиль — балансы ⚡️ и 💎.\n"
    "🚀 Тарифы — пакеты ⚡️ для чата и 💎 для HD, фото, видео и музыки.\n\n"
    "⚡️ Энергия — только текстовый ИИ-чат (сброс ежедневно). 💎 Кристаллы не сгорают.\n\n"
    "Готов продолжить? Нажми кнопку «🚀 Тарифы» ниже или в меню чата."
)

TXT_STUB_BUTTON = "Скоро в боте — следи за обновлениями."

TXT_SERVICE_RULES = (
    "Ознакомиться с публичной офертой:\n{offer}\n\n"
    "Политика конфиденциальности:\n{privacy}\n\n"
    "Условия подписки:\n{terms}"
)
TXT_TARIFFS_BLOCK = "Тарифы.\n\n{plans}"

TXT_TARIFFS_BTN_BUNDLE = "💳 Купить Пакет (MINI/SMART/ULTRA)"
TXT_TARIFFS_BTN_CRYSTALS = "💎 Купить только Кристаллы"
TXT_TARIFFS_BTN_TERMS = "📄 Условия подписки"
TXT_TARIFFS_BTN_CLOSE = "❌ Закрыть"
TXT_TARIFFS_BTN_BACK = "🔙 Назад в меню тарифов"

# WARNING: "Безлимитный чат" для SMART — это маркетинговое описание для UI.
# На уровне биллинга списание ⚡/💎 происходит штатно через chat_pipeline.py.
# Не отключать логику списания!
TXT_TARIFFS_MAIN = (
    "🚀 <b>Магазин тарифов NeuroMule 🐎⚡️</b>\n\n"
    "🔮 <b>Совет дня — 0 ₽</b> <i>(Открыт для всех тарифов!)</i>\n"
    "• Получай индивидуальное предсказание ИИ по своей карте абсолютно бесплатно "
    "1 раз в сутки!\n\n"
    "🎁 <b>Тариф FREE — 0 ₽</b> <i>(Активен по умолчанию)</i>\n"
    "• ⚡️ <code>30 ⚡</code> Энергии на день <i>(строго для текстового чата "
    "«Стандарт»)</i>\n"
    "• 🖼️ <code>3 🖼️</code> Базовых фото в день в нейросети <b>Imagen 4</b>\n"
    "• 🔒 <i>Эксперт-чат, Саммари, Таблицы, Подкасты, Музыка и Видео — полностью "
    "закрыты (доступны только за Кристаллы, накопленные за друзей).</i>\n\n"
    "📦 <b>Тариф MINI — {mini_rub} ₽</b> <i>(или {mini_stars} ⭐)</i>\n"
    "• ⚡️ <code>500 ⚡</code> Энергии + <code>10 💎</code> Кристаллов подписки на "
    "30 дней\n"
    "• 🔥 <b>Открыто в пакете:</b> Экспертные роли ИИ, Умная Саммаризация, Анализ "
    "документов и генерация Excel-таблиц!\n"
    "• 💎 <b>PRO-фото &amp; Медиа-студия:</b> Подкасты (20 💎), Музыка Suno (15 💎), "
    "Видео / Оживление фото (20 💎) и Полный разбор Дизайна Человека (70 💎) не входят "
    "в базовый пакет, но <i>полностью открыты</i> для поштучной оплаты за Кристаллы! "
    "Дополнительные фото Flux PRO также доступны по цене 3 💎 за штуку.\n\n"
    "🚀 <b>Тариф SMART (ХИТ 👑) — {smart_rub} ₽</b> <i>(или {smart_stars} ⭐)</i>\n"
    "• ⚡️ <code>1500 ⚡</code> Энергии + <code>35 💎</code> Кристаллов подписки на "
    "30 дней\n"
    "• 💬 <b>Эксклюзив:</b> Полный безлимит на переписку в mini-чате!\n"
    "• 🔥 <b>Открыто в пакете:</b> Модуль создания Аудио-подкастов (TTS) и Музыка "
    "Suno AI включены в подписку без доплат.\n"
    "• 🎬 <b>Тяжелое PRO-медиа:</b> Генерация Видео / Оживление фото (20 💎), "
    "Полный разбор личности HD (70 💎) и Совместимость HD-партнёров (50 💎) не входят "
    "в пакет, но <i>полностью открыты</i> для оплаты за Кристаллы из твоего баланса "
    "подписки или магазина!\n\n"
    "👑 <b>Тариф ULTRA (Киностудия) — {ultra_rub} ₽</b> <i>(или {ultra_stars} ⭐)</i>\n"
    "• ⚡️ <code>7000 ⚡</code> Энергии + <code>120 💎</code> Кристаллов подписки на "
    "30 дней\n"
    "• 🔥 <b>Олл-инклюзив:</b> Высший приоритет, доступ к флагманам GPT-o1 и "
    "Claude 3.5, генерация PRO-видео и пранков в Luma PRO!\n"
    "• 👫 <b>Опция DUO</b> — эксклюзив: доступ на двоих (ты + 1 партнёр)!\n\n"
    "───────────────────\n"
    "💎 <b>Нужны только Кристаллы?</b> <i>(вечные, не сгорают)</i>\n"
    "• <b>10 💎</b> — {c10_rub} ₽ / {c10_stars} ⭐\n"
    "• <b>40 💎</b> — {c40_rub} ₽ / {c40_stars} ⭐\n"
    "• <b>100 💎</b> — {c100_rub} ₽ / {c100_stars} ⭐ <i>(Выгодно!)</i>\n"
    "───────────────────\n"
    "💡 <b>Как сэкономить на покупке до 31%?</b>\n"
    "Оплата <b>Банковской картой (РФ напрямую)</b> — самый выгодный выбор!\n\n"
    "Покупка через Telegram Stars (⭐) со смартфонов iOS и Android содержит "
    "скрытую наценку зарубежных магазинов приложений (App Store и Google Play) "
    "до 30%. Выбирай оплату картой через ЮKassa, чтобы забрать тариф по честной "
    "цене и не переплачивать комиссию корпорациям!\n"
    "───────────────────\n"
    "👇 <b>Выбери свой тариф для мгновенной активации:</b>"
)
TXT_TARIFFS_BUNDLE_MENU = (
    "💳 <b>Выбор тарифа и способа оплаты</b>\n\n"
    "Выбери удобный формат. Оплата картой в рублях напрямую — это самый экономичный "
    "способ без скрытых комиссий магазинов приложений 👇"
)
TXT_TARIFFS_CRYSTALS_SHOP = (
    "💎 <b>Покупка разовых пакетов Кристаллов (вечные)</b>\n\n"
    "Оплата по карте напрямую — самый экономичный способ без переплат за Stars 👇"
)
TXT_TARIFFS_CRYSTALS_FREE_BLOCKED = (
    "❌ <b>Доступ заблокирован</b>\n\n"
    "На тарифе <code>FREE</code> покупка и использование Кристаллов для видео/музыки недоступны. "
    "Накопленные за друзей кристаллы можно тратить только на PRO-фото.\n\n"
    "Чтобы разблокировать полноценную покупку Кристаллов и открыть доступ ко всем тяжелым "
    "ИИ-моделям, активируй любой платный пакет (<b>MINI, SMART или ULTRA</b>)!"
)

TXT_PAY_SHOP_INTRO = (
    "🚀 Тарифы: Выбери свою мощность\n\n"
    "{plans}\n\n"
    "Выбери пакет ниже и способ оплаты:"
)
TXT_PAY_CHOOSE_METHOD = "Выберите способ оплаты: ЮKassa 💳 или Stars ⭐"
TXT_PAY_NO_YOOKASSA = (
    "Оплата картой сейчас недоступна: у бота не настроен PAYMENT_TOKEN. "
    "Выберите Telegram Stars ⭐ или напишите администратору."
)
TXT_PAYMENT_SUCCESS = "✅ Оплата прошла успешно! Вам начислено {amount}"
TXT_PAYMENT_DUPLICATE = "Этот платёж уже был учтён ранее."
TXT_PAYMENT_INVALID = "Не удалось обработать платёж. Напишите в поддержку и приложите скриншот из чека."

# Показывается ТОЛЬКО при точно распознанной ошибке «не хватает Telegram
# Stars» (см. services/billing/stars_payment_hints.py). При обычных
# сетевых сбоях или иных ошибках платёжного провайдера — НЕ выводится.
TXT_STARS_INSUFFICIENT_HINT = (
    "⭐ <b>На твоём счёте недостаточно Telegram Stars.</b>\n\n"
    "💡 <b>Подсказка:</b> оплата <b>картой РФ</b> через кнопку 💳 "
    "<b>Купить</b> — на ~<b>40%</b> выгоднее: без комиссий маркетплейсов "
    "и без валютной конвертации.\n\n"
    "Нажми «Назад» к выбору пакета и выбери <b>RUB</b> на экране способов "
    "оплаты — и получи кристаллы 💎 в один клик."
)
TXT_UNKNOWN_IMAGE_MODEL = "Неизвестная модель"
TXT_ADMIN_DENIED = (
    "❌ Доступ запрещен. У вас нет прав администратора для управления NeuroMule."
)
TXT_ADMIN_PANEL = (
    "🛠️ Добро пожаловать в панель управления NeuroMule. Выберите действие:"
)
TXT_ADMIN_GRANT_PROMPT = (
    "📝 Введите Telegram user ID получателя и количество кристаллов через пробел.\n"
    "Пример: 123456789 50\n\n"
    "Для отмены введите /cancel"
)
TXT_ADMIN_GRANT_CANCELLED = "❌ Начисление отменено."
TXT_ADMIN_GRANT_DONE = "✅ Успешно начислено +{amount} 💎 пользователю {user_id}."
TXT_ADMIN_GRANT_USER_NOTIFY = "✨ Баланс NeuroMule обновлен! Вам начислено {amount} 💎."
TXT_ADMIN_GRANT_INVALID = (
    "❌ Неверный формат. Введите строго ID и количество числами.\n"
    "Пример: 123456789 50 или /cancel"
)
TXT_ADMIN_BROADCAST_PROMPT = (
    "📢 Отправьте текст рассылки или фотографию с подписью. "
    "Она будет мгновенно доставлена всем пользователям NeuroMule.\n\n"
    "Для отмены введите /cancel"
)
TXT_ADMIN_BROADCAST_CANCEL = "❌ Рассылка отменена."
TXT_ADMIN_BROADCAST_RUNNING = "⏳ Запускаю рассылку на {count} пользователей..."
TXT_ADMIN_BROADCAST_EMPTY = "❌ Отправьте текст или фото с подписью."
TXT_ADMIN_BROADCAST_DONE = (
    "📢 <b>РАССЫЛКА ЗАВЕРШЕНА</b>\n\n"
    "✅ Успешно доставлено: {delivered}\n"
    "❌ С ошибкой (заблокировали бота): {errors}"
)


def format_admin_stats_html(stats: dict[str, int | float]) -> str:
    """Текст блока «Статистика» для админ-панели (HTML, без Markdown)."""
    revenue_rub = float(stats.get("revenue_rub", 0)) / 100.0
    return (
        "<b>📊 СТАТИСТИКА ПЛАТФОРМЫ NEUROMUL</b>\n\n"
        f"👥 Всего пользователей в системе: {int(stats.get('total_users', 0))}\n"
        f"📦 Выполнено заказов (payment_events): {int(stats.get('total_orders', 0))}\n\n"
        "<b>💰 Продажи тарифов за СЕГОДНЯ:</b>\n"
        f" ▫️ MINI: {int(stats.get('today_mini', 0))} шт\n"
        f" ▫️ SMART: {int(stats.get('today_smart', 0))} шт\n"
        f" ▫️ ULTRA: {int(stats.get('today_ultra', 0))} шт\n\n"
        "<b>📈 Продажи за ВСЁ ВРЕМЯ:</b>\n"
        f" ▫️ MINI: {int(stats.get('all_mini', 0))} шт\n"
        f" ▫️ SMART: {int(stats.get('all_smart', 0))} шт\n"
        f" ▫️ ULTRA: {int(stats.get('all_ultra', 0))} шт\n\n"
        "<b>💵 Общая выручка компании:</b>\n"
        f" ▫️ В рублях: {revenue_rub:,.2f} ₽\n"
        f" ▫️ В звездах: {int(stats.get('revenue_stars', 0))} ⭐"
    )


def format_admin_payment_notice_html(
    payer_id: int,
    tariff_name: str,
    reward_description: str,
) -> str:
    """Уведомление админам о новом платеже (HTML)."""
    tariff_safe = html_module.escape(tariff_name)
    reward_safe = html_module.escape(reward_description)
    return (
        "💰 <b>НОВЫЙ ПЛАТЕЖ В NEUROMUL!</b>\n\n"
        f"👤 ID Плательщика: <code>{payer_id}</code>\n"
        f"📦 Выбранный тариф: {tariff_safe}\n"
        f"💎 Начислено на баланс: {reward_safe}"
    )


TXT_GEN_STATUS_VIP = "⚡️ VIP-статус подтвержден. Твой запрос обрабатывается вне очереди!"

TXT_VK_START = (
    "{bot_name} (VK). Напиши любой текст — отвечу той же нейросетью, что и в Telegram (@NeuroMule_bot)."
)

EASTER_THANKS_REPLIES = (
    "Рад помочь!",
    "Обращайтесь снова.",
    "Продолжим?",
)

HELP_TRIGGER_WORDS = frozenset({"помощь", "help"})

EASTER_THANKS_TRIGGERS = frozenset(
    {"спасибо", "благодарю", "круто", "ты лучший", "спс", "от души"}
)
