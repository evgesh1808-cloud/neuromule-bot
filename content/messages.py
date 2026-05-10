"""Все пользовательские тексты и callback-идентификаторы Telegram-меню."""

# --- callback ids (Telegram inline) ---
CB_CREATE_TEXT = "create_text"
CB_CREATE_IMAGE = "create_image"
CB_CREATE_ANIMATE = "create_animate"
CB_CREATE_VIDEO = "create_video"
CB_CREATE_MUSIC = "create_music"
CB_GEN_IMAGE_PROMPT = "gen_image_prompt"
CB_CABINET_PROMO = "cabinet_promo"
CB_RESULT_ANIMATE = "res_anim"
CB_RESULT_REPEAT_PHOTO = "res_repeat_ph"
CB_RESULT_HD_PRO = "res_hd_pro"
CB_RESULT_PREMIUM = "res_premium"
CB_RESULT_GALLERY = "res_gallery"
CB_RESULT_MP3 = "res_mp3"
CB_RESULT_EDIT_LYRICS = "res_edit_lyrics"
CB_SERVICE_RULES = "service_rules"
CB_BACK_MAIN = "back_main"
CB_BACK_CREATE = "back_create"
CB_IMG_PREFIX = "img:"
CB_ADMIN_STATS = "admin_stats"
CB_ADMIN_BROADCAST = "admin_broadcast"

IMAGE_MODELS: tuple[tuple[str, str], ...] = (
    ("GPT Image 2 (DALL-E 3)", "gpt_image2"),
    ("🌠 Imagen 4", "imagen4"),
    ("Gemini 3.1 Flash — Nano Banana 2", "nano_banana2"),
    ("Gemini 3 Pro — Nano Banana Pro", "nano_banana_pro"),
)

IMAGE_MODEL_IDS = {mid for _, mid in IMAGE_MODELS}

MAIN_MENU_BUTTONS = (
    "🤖 Что умеет бот?",
    "🎨 Создать",
    "👤 Личный кабинет",
    "💳 Тарифы",
    "🆘 Поддержка",
)

CREATE_MENU_BUTTONS = (
    ("📝 Нейротекст", CB_CREATE_TEXT),
    ("🖼 Изображение", CB_CREATE_IMAGE),
    ("🎯 Генерация промпта", CB_GEN_IMAGE_PROMPT),
    ("✨ Оживить фото", CB_CREATE_ANIMATE),
    ("🎬 Видео", CB_CREATE_VIDEO),
    ("🎵 Музыка", CB_CREATE_MUSIC),
    ("⬅️ Назад в главное меню", CB_BACK_MAIN),
)

SUPPORT_TOPICS = (
    "Технические сбои и ошибки",
    "Вопросы оплаты и тарифов",
    "Предложения по улучшению бота",
)

TARIFF_PLANS = (
    "FREE — 30 текстов/день + 3 фото/день",
    "MINI — 500 ⚡️ за 249₽ / 180 ⭐",
    "SMART — 1500 ⚡️ за 549₽ / 400 ⭐",
    "ULTRA — 6000 ⚡️ за 1990₽ / 1450 ⭐",
)

# --- тексты ---
TXT_IMAGE_INTRO = (
    "Для работы с фотографиями:\n\n"
    "• GPT Image 2 (DALL-E 3)\n"
    "• Imagen 4\n"
    "• Gemini 3.1 Flash — Nano Banana 2\n"
    "• Gemini 3 Pro — Nano Banana Pro\n\n"
    "Выбери модель ниже, затем опиши желаемое изображение текстом."
)
TXT_SELECT_TOOL = "Выбери инструмент:"
TXT_BACK_TO_MAIN = "Главное меню."
TXT_BACK_TO_TOOLS = "Назад к инструментам"
TXT_LOW_ENERGY = (
    "⚡ Энергии не хватает.\n\n"
    "Пополни баланс в «Тарифах» или пригласи друга из «Личного кабинета» — так больше людей узнают про бота."
)
TXT_INSUFFICIENT_BALANCE = (
    "⚠️ Упс! На вашем балансе недостаточно средств для выполнения операции. "
    "Пожалуйста, пополните счет 💳 или оплатите тарифный план 🚀, "
    "чтобы продолжить пользоваться всеми возможностями!"
)
TXT_CHAT_RATE_LIMIT = (
    "⏳ Слишком много сообщений подряд. Подождите немного и напишите снова — так мы защищаем сервис от перегрузки."
)
TXT_CHAT_EMPTY = "Напишите текст сообщения — пустое сообщение не обрабатываю."
TXT_CHAT_CONTEXT_TOO_LARGE = (
    "Сообщение слишком длинное для одного запроса к модели. Сократите текст или разбейте на части."
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
    "<code>GPT-4o</code>, <code>Flux</code>, <code>Kling AI</code> и <code>Suno</code> в одном месте. 🚀\n\n"
    "Твой текущий статус: <b>FREE</b> 🎁"
)
TXT_START_FIRST_MEET_NEED_CHANNEL_2 = (
    "• 📝 <code>{text_daily_limit}</code> текстовых запросов/день\n"
    "• 🎨 <code>{photo_daily_limit}</code> генерации фото/день (<code>Flux Schnell</code>)\n\n"
    "Чтобы снять ограничения и начать творить, подпишись на наш "
    '<a href="{channel_url}">официальный канал</a>. '
    "Там мы делимся лайфхаками, секретными промптами и дарим промокоды."
)

# Подписка ок — одно сообщение + главное меню (тот же стиль, что «START_MESSAGE» в подсказках).
TXT_START_FIRST_MEET_OK = (
    "🌟 <b>NeuroMule</b> в сети. Процессоры заряжены и готовы к твоим идеям!\n\n"
    "🤖 Добро пожаловать в мир безграничных возможностей!\n\n"
    "Твой доступ к самым мощным нейросетям планеты уже открыт: "
    "<code>GPT-4o</code>, <code>Flux</code>, <code>Kling AI</code> и <code>Suno</code> в одном месте. 🚀\n\n"
    "Твой текущий статус: <b>FREE</b> 🎁\n\n"
    "• 📝 <code>{text_daily_limit}</code> текстовых запросов/день\n"
    "• 🎨 <code>{photo_daily_limit}</code> генерации фото/день (<code>Flux Schnell</code>)\n\n"
    'В <a href="{channel_url}">официальном канале</a> мы делимся лайфхаками, секретными промптами и дарим промокоды — '
    "заглядывай и включи уведомления 🔔\n\n"
    "<b>Выбирай действие в меню ниже</b> 👇"
)
TXT_ABOUT_BOT = (
    "Помогаю с текстами (нейросеть), изображениями (несколько моделей), "
    "оживлением фото, видео и музыкой, а также генерацией промпта по твоему описанию кадра "
    "(готовый EN + коротко RU для вставки в генератор). "
    "Мультиплатформенный бот @NeuroMule_bot."
)
TXT_GEN_IMAGE_PROMPT_HINT = (
    "Опиши обычным текстом, какую фотографию или сцену хочешь: настроение, стиль, свет, ракурс, детали.\n\n"
    "Пример: «киношный портрет в золотом часе, мягкий боке, тёплые тона».\n\n"
    "Я соберу промпт (EN + коротко RU). Дальше открой «Изображение», выбери модель и вставь EN-часть; "
    "если нужен референс по лицу или другой эффект — напиши это в описании, и это попадёт в промпт."
)
TXT_GEN_IMAGE_PROMPT_NEED_TEXT = "Напиши описание текстом — так я смогу собрать промпт."
TXT_CREATE_TEXT_HINT = "Напиши задачу текстом — отвечу в чате."
TXT_CREATE_IMAGE_AFTER_MODEL = "Опиши изображение: стиль, детали, формат."
TXT_CREATE_ANIMATE_HINT = "Пришли фото для оживления."
TXT_CREATE_VIDEO_HINT = "Опиши сцену для видео."
TXT_CREATE_MUSIC_HINT = "Опиши трек: жанр, темп, настроение."
TXT_PHOTO_PROCESS = (
    "Принял запрос. Модель: {model}. Генерация будет подключена к API.\n\n{wait_note}"
)
TXT_VIDEO_PROCESS = "Видео-задача принята. Обработка подключается.\n\n{wait_note}"
TXT_ANIMATE_PROCESS = "Фото принято. Оживление подключается.\n\n{wait_note}"
TXT_MUSIC_PROCESS = "Трек в работе. Подключение аудио-API.\n\n{wait_note}"
TXT_CABINET_TEMPLATE = (
    "🗂 ЛИЧНЫЙ КАБИНЕТ\n\n"
    "🆔 ID: {user_id}\n"
    "⚡ Энергия: {energy}\n"
    "📦 Тариф: {tariff}\n"
    "👥 Приглашено друзей: {invites}\n\n"
    "🔗 Твоя реферальная ссылка:\n{ref_link}\n\n"
    "Промокоды — в канале (включи уведомления 🔔), ввод кода — кнопка ниже."
)
TXT_CABINET_INVITE_BUTTON = "👥 Пригласить друга"
TXT_CABINET_PROMO_BUTTON = "🎟 Промокод"
TXT_CABINET_CHANNEL_PROMOS = "📢 Канал с промокодами"

INVITE_SWITCH_QUERY_TEMPLATE = (
    "Смотри, нашел крутого бота с GPT-4 и генерацией видео! Заходи: @{bot_username}"
)

TXT_PROMO_ASK = "Есть секретный код? Введи его здесь"
TXT_PROMO_REDEEMED = "✅ Промокод принят! Начислено +{bonus} ⚡"
TXT_PROMO_UNKNOWN = "❌ Такого промокода нет. Следи за публикациями в канале."
TXT_PROMO_USED = "❌ Этот промокод ты уже активировала."
TXT_PROMO_EXHAUSTED = "❌ Лимит активаций этого промокода исчерпан."

TXT_GEN_STATUS_ACCEPTED = "🚀 Запрос принят! Нейросеть уже творит. Ожидание: 1-3 минуты"
TXT_GEN_JOB_FAILED = "⚠️ Не удалось завершить генерацию. Попробуй ещё раз чуть позже."

TXT_PHOTO_DAILY_LIMIT = (
    "❌ ЛИМИТ ИСЧЕРПАН.\n\n"
    "На бесплатном тарифе доступно {limit} генераций фото в сутки.\n"
    "Загляни в «Личный кабинет» — пригласи друга или дождись завтра."
)
TXT_ACCESS_SMART_PLUS = "❌ Видео и Музыка доступны в тарифе SMART и выше"
TXT_UPGRADE_TO_SMART = "❌ Видео и Музыка закрыты на этом тарифе. Открой SMART, чтобы продолжить."
TXT_UPGRADE_TO_ULTRA = "❌ Видео доступно только в тарифе ULTRA. Открой ULTRA, чтобы продолжить."

TXT_RESULT_PHOTO_CAPTION = (
    "✨ ТВОЙ ШЕДЕВР ГОТОВ!\n\n"
    "Нейросеть создала изображение по твоему запросу. Оцени качество детализации! 🚀\n\n"
    "📉 Затраты: {cost} ⚡\n"
    "🔋 Остаток баланса: {balance} ⚡\n\n"
    "Что делаем дальше?\n"
    "🪄 Оживить это фото (Видео) — {animate_cost} ⚡\n"
    "🔄 Повторить генерацию — {cost} ⚡\n"
    "📥 Скачать в максимальном качестве — PRO\n\n"
    "Поделись результатом с друзьями и получи бонус! 👇"
)
TXT_RESULT_VIDEO_CAPTION = (
    "🎥 ВИДЕО СГЕНЕРИРОВАНО!\n\n"
    "Твоя идея ожила. Проверь результат ниже! 👇\n"
    "⏳ Длительность: 5–10 сек\n"
    "⚡ Списано: {cost} ⚡\n\n"
    "Хочешь приоритетную обработку без очереди?\n"
    "Оформи тариф ULTRA в разделе «Тарифы»!"
)
TXT_RESULT_MUSIC_CAPTION = (
    "🎧 ТРЕК ЗАПИСАН!\n\n"
    "Твой персональный хит готов к прослушиванию. Включай на полную! 🔥\n\n"
    "📝 Стиль: {style}\n"
    "⚡ Баланс: {balance} ⚡"
)
TXT_RESULT_ANIMATE_CAPTION = (
    "✨ ОЖИВЛЕНИЕ ГОТОВО!\n\n"
    "⚡ Списано: {cost} ⚡\n"
    "🔋 Остаток: {balance} ⚡"
)

TXT_BALANCE_LOW_FOOTER = "\n\n⚠️ Внимание: баланс заканчивается! [Пополнить] — раздел «Тарифы»."

TXT_SUPPORT_FAQ = (
    "🆘 ПОДДЕРЖКА\n\n"
    "Чат с командой (FAQ, ошибки, оплата):\n"
    "👉 @{support_bot}\n\n"
    "Частые вопросы:\n"
    "• Как купить энергию? — открой «Тарифы» в главном меню или напиши в @{support_bot}.\n"
    "• Почему видео долго? — рендер идёт 1–3 минуты, в часы пик очередь может быть дольше; "
    "статус «запрос принят» значит задача уже в работе.\n"
    "• Промокоды — только в нашем канале, включи уведомления, чтобы не пропустить."
)

TXT_STUB_BUTTON = "Скоро в боте — следи за обновлениями."

TXT_SERVICE_RULES = (
    "Ознакомиться с публичной офертой:\n{offer}\n\n"
    "Политика конфиденциальности:\n{privacy}\n\n"
    "Условия подписки:\n{terms}"
)
TXT_TARIFFS_BLOCK = "Тарифы.\n\n{plans}"
TXT_PAY_SHOP_INTRO = (
    "💳 Магазин тарифов NeuroMule\n\n"
    "🔥 Скидка 30% при оплате картой\n\n"
    "Выберите пакет и способ оплаты:\n{plans}"
)
TXT_PAY_CHOOSE_METHOD = "Выберите способ оплаты: ЮKassa 💳 или Stars ⭐"
TXT_PAY_NO_YOOKASSA = (
    "Оплата картой сейчас недоступна: у бота не настроен PAYMENT_TOKEN. "
    "Выберите Telegram Stars ⭐ или напишите администратору."
)
TXT_PAYMENT_SUCCESS = "✅ Оплата прошла успешно! Вам начислено {amount} ⚡️"
TXT_PAYMENT_DUPLICATE = "Этот платёж уже был учтён ранее."
TXT_PAYMENT_INVALID = "Не удалось обработать платёж. Напишите в поддержку и приложите скриншот из чека."
TXT_UNKNOWN_IMAGE_MODEL = "Неизвестная модель"
TXT_ADMIN_DENIED = "Доступ запрещен."
TXT_ADMIN_PANEL = "Скрытая админ-панель. Выберите действие:"
TXT_ADMIN_BROADCAST_PROMPT = "Отправь сообщение для рассылки (текст или фото с подписью). Отмена: /cancel"
TXT_ADMIN_BROADCAST_DONE = "Рассылка завершена. Доставлено: {ok}, ошибок: {fail}."
TXT_GEN_STATUS_VIP = "⚡️ VIP-статус подтвержден. Твой запрос обрабатывается вне очереди!"

TXT_VK_START = (
    "{bot_name} (VK). Напиши любой текст — отвечу той же нейросетью, что и в Telegram (@NeuroMule_bot)."
)

EASTER_THANKS_REPLIES = (
    "Рад помочь!",
    "Обращайтесь снова.",
    "Продолжим?",
)

EASTER_THANKS_TRIGGERS = frozenset(
    {"спасибо", "благодарю", "круто", "ты лучший", "спс", "от души"}
)
