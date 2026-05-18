"""Все пользовательские тексты и callback-идентификаторы Telegram-меню."""

import html as html_module

# --- callback ids (Telegram inline) ---
CB_CREATE_TEXT = "create_text"
CB_CREATE_IMAGE = "create_image"
CB_CREATE_ANIMATE = "create_animate"
CB_CREATE_VIDEO = "create_video"
CB_CREATE_MUSIC = "create_music"
CB_UPSCALE_START = "upscale_start"
CB_HD_PREMIUM_BUY = "hd_premium_buy"
CB_HD_FREE_ADVICE = "hd_free_advice"
CB_MATCH_START = "match_start"
CB_HD_REPORT_PREFIX = "hd_report:"
CB_HD_REPORT_MONEY = "hd_report:money"
CB_HD_REPORT_LOVE = "hd_report:love"
CB_HD_REPORT_ENERGY = "hd_report:energy"
CB_HD_REPORT_PLAN = "hd_report:plan"
CB_HD_REPORT_PDF = "hd_report:pdf"
CB_CABINET_PROMO = "cabinet_promo"
CB_SHOW_INSTRUCTION = "show_instruction"
CB_CHECK_SUBSCRIPTION = "check_subscription"
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
CB_ADMIN_GIVE_CRYSTALS = "admin_give_crystals"
CB_ADMIN_START_BROADCAST = "admin_start_broadcast"
# Алиасы для обратной совместимости со старыми callback_data.
CB_ADMIN_GRANT_CRYSTALS = CB_ADMIN_GIVE_CRYSTALS
CB_ADMIN_BROADCAST = CB_ADMIN_START_BROADCAST
CB_TEXT_ROLE_PREFIX = "text_role:"

BTN_DAILY_ADVICE = "🔮 Совет дня"
BTN_PROFILE = "👤 Профиль"
BTN_HD_SECTION = "🧬 Дизайн Человека"
BTN_CREATE = "🎨 Создать"
BTN_TARIFFS = "🚀 Тарифы"
BTN_SUPPORT = "🙋‍♂️ FAQ / Поддержка"
BTN_SUPPORT_LEGACY = "🆘 Поддержка"

USER_MAIN_MENU_BUTTONS = (
    BTN_DAILY_ADVICE,
    BTN_PROFILE,
    BTN_HD_SECTION,
    BTN_CREATE,
    BTN_TARIFFS,
    BTN_SUPPORT,
)
INSTRUCTION_INLINE_BUTTON_LABEL = "📍 Инструкция"
ADMIN_MAIN_MENU_BUTTON = "⚙️ Админ-панель"

TEXT_ROLES: tuple[tuple[str, str], ...] = (
    ("🔘 Стандарт", "standard"),
    ("🎓 Академик", "academic"),
    ("🎭 Психолог", "psychologist"),
    ("🗣️ Спикер (TED)", "speaker"),
    ("📱 Блогер", "blogger"),
    ("📉 Аналитик", "analyst"),
    ("🧙 Сказочник", "storyteller"),
)
PREMIUM_TEXT_ROLE_IDS = {role_id for _, role_id in TEXT_ROLES if role_id != "standard"}

IMAGE_MODELS: tuple[tuple[str, str], ...] = (
    ("🎨 Flux Schnell (FREE)", "flux-schnell"),
    ("GPT Image 2 (DALL-E 3)", "gpt_image2"),
    ("🌠 Imagen 4", "imagen4"),
    ("Gemini 3.1 Flash — Nano Banana 2", "nano_banana2"),
    ("Gemini 3 Pro — Nano Banana Pro", "nano_banana_pro"),
)

IMAGE_MODEL_IDS = {mid for _, mid in IMAGE_MODELS}

MAIN_MENU_BUTTONS = USER_MAIN_MENU_BUTTONS  # legacy alias; используется только в фильтре общего чата.

CB_HD_SECTION = "hd_section"
CB_HD_REPORT_OPEN = "hd_report_open"

# Шесть инструментов в сетке 2×2×2; кнопка «Назад» добавляется в platforms.telegram_bot.create_menu().
CREATE_MENU_GRID = (
    ("📝 Нейротекст", CB_CREATE_TEXT),
    ("🖼️ Изображение", CB_CREATE_IMAGE),
    ("✨ Оживить фото", CB_CREATE_ANIMATE),
    ("🎸 Музыка", CB_CREATE_MUSIC),
    ("🎬 Видео", CB_CREATE_VIDEO),
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
TXT_HD_ALREADY_PURCHASED = (
    "<b>Разбор уже у тебя есть.</b>\n\n"
    "Открой разделы отчёта или проверь совместимость с партнёром — кнопки ниже."
)

# Подписи inline-кнопок (Дизайн человека / разделы отчёта)
TXT_HD_INLINE_FULL_REPORT = "🗺️ Полный разбор личности — {cost} 💎"
TXT_HD_INLINE_VIEW_REPORT = "🗺️ Посмотреть мой разбор"
TXT_HD_INLINE_COMPATIBILITY = "👩‍❤️‍👨 Совместимость"
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
    "• ⚡️ 30 Энергии — обновляются ежедневно в 00:00 и не накапливаются\n"
    "• 📝 Доступ к роли «Стандарт»\n"
    "• 🎨 До 3 обычных фото в день\n"
    "• ❌ Кристаллы, Видео и Музыка — недоступны\n"
    "Стоимость: 0 ₽",
    "📦 ПАКЕТ: MINI\n"
    "Для тех, кто ценит комфорт и качество\n"
    "• ⚡️ 500 Энергии\n"
    "• 💎 10 Кристаллов\n"
    "• ✅ Доступ ко всем Экспертным ролям: Психолог, Аналитик, Блогер и другие\n"
    "• 🖼 Фото Хорошего качества — 100⚡️\n"
    "Стоимость: 290 ₽ / 210 ⭐",
    "🚀 ПАКЕТ: SMART (ХИТ)\n"
    "Оптимальный маршрут для активных создателей\n"
    "• ⚡️ 1500 Энергии + 🔥 Безлимитный текст на mini-моделях\n"
    "• 💎 35 Кристаллов\n"
    "• ✅ Все Экспертные роли + приоритет в очереди\n"
    "• 🎸 Музыка: Suno V3.5\n"
    "Стоимость: 690 ₽ / 490 ⭐",
    "👑 ПАКЕТ: ULTRA (КИНОСТУДИЯ)\n"
    "Максимум выносливости. Искусство без границ\n"
    "• ⚡️ 7000 Энергии\n"
    "• 💎 120 Кристаллов\n"
    "• ✅ Доступ к самым мощным ИИ: GPT-o1 / Claude 3.5\n"
    "• 🎬 Видео: Luma Dream Machine\n"
    "Стоимость: 1990 ₽ / 1450 ⭐",
    "📍 НУЖНЫ ТОЛЬКО КРИСТАЛЛЫ?\n"
    "• 10 💎 — 199 ₽ / 145 ⭐\n"
    "• 40 💎 — 490 ₽ / 355 ⭐\n"
    "• 100 💎 — 990 ₽ / 720 ⭐ (Выгодно!)\n\n"
    "Энергия в платных пакетах не сгорает в конце дня и доступна до полного использования.",
)

# --- тексты ---
TXT_IMAGE_INTRO = (
    "Для работы с фотографиями:\n\n"
    "• Flux Schnell — 3 бесплатные генерации в день\n"
    "• GPT Image 2 (DALL-E 3) — PRO за 💎\n"
    "• Imagen 4 — PRO за 💎\n"
    "• Gemini 3.1 Flash — Nano Banana 2 — PRO за 💎\n"
    "• Gemini 3 Pro — Nano Banana Pro — PRO за 💎\n\n"
    "Выбери модель ниже, затем опиши желаемое изображение текстом."
)
TXT_SECTION_INTRO = "Нейроны на старте! Какой инструмент задействуем для этого маршрута?"
TXT_SELECT_TOOL = TXT_SECTION_INTRO
TXT_BACK_TO_MAIN = "Твой маршрут проложен! ⚡️"
TXT_BACK_TO_TOOLS = "Назад к инструментам"
TXT_LOW_ENERGY = (
    "⚡ Энергии не хватает.\n\n"
    "Пополни баланс в «Тарифах» или пригласи друга в разделе «👤 Мой профиль» — так больше людей узнают про бота."
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

# /start — единое приветствие (HTML), показывается всегда, без проверки подписки.
TXT_START_WELCOME = (
    "Мул на связи! Нейроны на старте и готовы к твоим идеям! 🐎⚡️\n"
    "🤖 Добро пожаловать в единую точку доступа к самым мощным нейросетям планеты: "
    "<code>GPT-4o</code>, <code>Flux</code>, <code>Kling AI</code> и <code>Suno</code> в одном месте.\n\n"
    "Твой текущий статус: <b>FREE</b> 🎁\n"
    "• ⚡️ 30 Энергии (обновляются ежедневно в 00:00)\n"
    "• 🎨 3 Обычных фото в день (10⚡️ за генерацию)\n\n"
    "В канале @mulendeeva_ai мы делимся лайфхаками, секретными промптами и дарим промокоды — "
    "подписывайся! 🔔\n\n"
    "Выбирай направление в меню ниже 👇"
)
TXT_START_MAIN_MENU_PROMPT = "Главное меню доступно ниже."

# Сохранён для обратной совместимости в тестах/импортах: формат идентичен старому.
TXT_START_FIRST_MEET_OK = TXT_START_WELCOME

# Шлюз подписки на канал (мягкая проверка при нажатии любых кнопок, кроме /start).
TXT_CHANNEL_GATE = (
    "Мул готов к выходу, но путь закрыт! 🚧 "
    "Чтобы активировать нейроны и использовать бесплатные лимиты, подпишись на наш канал. "
    "Там мы делимся секретами и дарим бонусы!"
)
TXT_CHANNEL_GATE_SUBSCRIBE_BTN = "📢 Подписаться"
TXT_CHANNEL_GATE_CHECK_BTN = "✅ Проверить подписку"
TXT_CHANNEL_GATE_OK = "✅ Подписка подтверждена. Доступ открыт!"
TXT_CHANNEL_GATE_FAIL = (
    "Подписка пока не найдена. Подпишись на канал и снова нажми «✅ Проверить подписку»."
)
TXT_ABOUT_BOT = (
    "Помогаю с текстами (нейросеть), изображениями (несколько моделей), "
    "оживлением фото, видео и музыкой. "
    "Мультиплатформенный бот @NeuroMule_bot."
)
TXT_CREATE_TEXT_HINT = (
    "📝 Нейротекст: сначала выбери режим (Стандарт или экспертный), затем одним сообщением опиши задачу — "
    "что нужно получить на выходе, тон и ограничения по объёму."
)
TXT_TEXT_ROLE_SELECTED = "Режим «{role}» включен. Напиши задачу для Нейронов."
TXT_PREMIUM_ROLE_LOCKED = "Для активации этого экспертного режима Нейронам нужна подзарядка. 🚀 Загляни в Тарифы!"
TXT_CREATE_IMAGE_AFTER_MODEL = (
    "Опиши изображение одним сообщением: стиль, ключевые объекты, фон, освещение и формат (квадрат / вертикаль)."
)
TXT_CREATE_ANIMATE_HINT = "Пришли одним сообщением фото (как файл или сжатое изображение), которое нужно оживить."
TXT_UPSCALE_HINT = "Пришли фото, и я улучшу его четкость до максимума. Стоимость: 1 💎."
TXT_UPSCALE_PROCESSING = "Прокладываю кратчайший путь через нейроны... Списываю 1 💎 и готовлю UPSCALE."
TXT_UPSCALE_SUCCESS = "Доставлено в лучшем виде! 📦\n\n🔍 UPSCALE готов.\nСписано: 1 💎\nОстаток: {balance} 💎"
TXT_UPSCALE_FAILED = "Нейронная подкова разболталась. Сейчас подкуем и попробуем снова!"
TXT_CREATE_VIDEO_HINT = (
    "Опиши сцену для видео: герой, действие, атмосфера, длительность в секундах (если важно), стиль (кино / мультик)."
)
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
TXT_HD_DAILY_ADVICE_CTA = (
    "💎 Усиль свой магнетизм в NeuroMul: загляни в раздел Создать — "
    "там визуал, музыка и инструменты для твоего бренда."
)

# Кнопка «Назад» из подменю Дизайна человека к списку инструментов
TXT_HD_BACK_TO_TOOLS = "⬅️ Назад"
TXT_HD_FREE_ADVICE_PROCESSING = "Готовлю бесплатный совет дня через Gemini."
TXT_HD_FREE_ADVICE_USED = (
    "<b>Совет дня уже получен.</b>\n"
    "Новый совет будет доступен завтра."
)
TXT_HD_FREE_ADVICE_USED_ALERT = "Совет дня уже получен. Загляни завтра."
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
    "👩‍❤️‍👨 Совместимость стоит 50 💎.\n\n"
    "Пришли данные второго человека одним сообщением: дата рождения, точное время и город."
)
TXT_MATCH_ASK_BOTH = (
    "👩‍❤️‍👨 Совместимость стоит 50 💎.\n\n"
    "У меня ещё нет твоих данных Бодиграфа. Пришли данные обоих людей одним сообщением:\n"
    "Вы: дата рождения, точное время и город\n"
    "Партнер: дата рождения, точное время и город"
)
TXT_MATCH_PROCESSING = "Считаю наложение карт через эфемериды и готовлю анализ совместимости через Gemini."
TXT_MATCH_INSUFFICIENT_CRYSTALS = "Для совместимости нужно 50 💎. Пополни кристаллы в «🚀 Тарифы»."
TXT_MATCH_FAILED = "Не удалось подготовить совместимость. Списание возвращено, попробуй позже."
TXT_MATCH_EMPTY_DATA = "Пришли данные второго человека текстом."
TXT_PHOTO_PROCESS = (
    "Принял запрос. Модель: {model}. Генерация будет подключена к API.\n\n{wait_note}"
)
TXT_VIDEO_PROCESS = "Видео-задача принята. Обработка подключается.\n\n{wait_note}"
TXT_ANIMATE_PROCESS = "Фото принято. Оживление подключается.\n\n{wait_note}"
TXT_MUSIC_PROCESS = "Трек в работе. Подключение аудио-API.\n\n{wait_note}"
TXT_CABINET_TEMPLATE = (
    "👤 Мой профиль\n\n"
    "🆔 Твой ID: {user_id}\n"
    "📦 Текущий тариф: {tariff}\n"
    "💰 Баланс: ⚡️ {energy} | 💎 {crystals}\n\n"
    "👥 Приглашено друзей: {invites}\n"
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

TXT_GEN_STATUS_ACCEPTED = "Мул пошел в облака. Скоро буду с ответом 🐎☁️"
TXT_VIDEO_QUEUE_ACCEPTED = (
    "⏳ Ваш запрос на видео принят в очередь NeuroMul. Робот начал просчёт кадров…"
)
TXT_GEN_JOB_FAILED = "Нейронная подкова разболталась. Сейчас подкуем и попробуем снова!"

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
    "🎧 ТРЕК ЗАПИСАН!\n\n"
    "Твой персональный хит готов к прослушиванию. Включай на полную! 🔥\n\n"
    "📝 Стиль: {style}\n"
    "💎 Кристаллы: {balance} 💎"
)
TXT_RESULT_ANIMATE_CAPTION = (
    "✨ ОЖИВЛЕНИЕ ГОТОВО!\n\n"
    "💎 Списано: {cost} 💎\n"
    "🔋 Остаток: {balance} 💎"
)
TXT_ANIMATE_SUCCESS = (
    "🎬 Фотография успешно оживлена! Нейросеть NeuroMul превратила ваш статичный кадр в живое видео."
)
TXT_ANIMATE_SOURCE_CAPTION = "Оживлённый исходник (тест контура)"
TXT_ANIMATE_FAILED = "⚠️ Не удалось оживить фотографию. Попробуйте другой снимок."
TXT_VIDEO_REPLICATE_FAILED = (
    "⚠️ К сожалению, не удалось сгенерировать видео. Попробуйте изменить описание."
)
TXT_ANIMATE_REPLICATE_FAILED = (
    "⚠️ Не удалось оживить фотографию. Убедитесь, что на фото нет сильных размытий."
)
TXT_MUSIC_SUNO_FAILED = "⚠️ Нам не удалось подобрать мотив. Попробуйте перефразировать стиль музыки."
TXT_MUSIC_QUEUE_ACCEPTED = "⏳ Стиль принят. NeuroMul отправляет запрос в Suno и собирает трек…"
TXT_ANIMATE_QUEUE_ACCEPTED = (
    "⏳ Фотография получена. NeuroMul добавляет задачу в очередь на оживление…"
)

TXT_BALANCE_LOW_FOOTER = "\n\n⚠️ Внимание: кристаллы заканчиваются! [Пополнить] — раздел «Тарифы»."

TXT_FAQ_ADMIN_CONTACT = "👤 Связаться с администратором"

TXT_FAQ_SUPPORT = (
    "🙋‍♂️ <b>ЧАСТО ЗАДАВАЕМЫЕ ВОПРОСЫ И ПОДДЕРЖКА</b>\n\n"
    "🧬 <b>В чём разница между Советом дня и Полным разбором?</b>\n\n"
    "🌌 <b>Бесплатный совет дня</b> — ваш ежедневный навигатор. NeuroMul рассчитывает его "
    "каждое утро и показывает короткий фокус энергии, актуальный именно сегодня. "
    "Завтра совет сменится новым.\n\n"
    "👑 <b>Полный разбор HD Premium (70 💎)</b> — фундаментальная «книга» о вашей личности "
    "(до ~4000 токенов), которую покупают <b>один раз навсегда</b>. Бот рассчитывает карту "
    "планет по швейцарским эфемеридам, определяет открытые и закрытые центры бодиграфа "
    "и генерирует отчёт по 4 сферам: Деньги, Отношения, Энергия и стратегический план.\n\n"
    "Дополнительно — PDF для скачивания и навсегда разблокируется модуль "
    "<b>Совместимости с партнёром</b> (50 💎). Повторно платить за чтение своих разделов "
    "не нужно!\n\n"
    "────────────────────────\n"
    "Остались технические вопросы, пожелания или проблемы с зачислением баланса? "
    "Нажмите кнопку ниже, чтобы написать службе поддержки напрямую."
)

TXT_SUPPORT_FAQ = TXT_FAQ_SUPPORT

TXT_INSTRUCTION = (
    "📍 Инструкция: Как управлять Нейро-Мулом?\n\n"
    "📝 Текст — быстрые ответы и экспертные роли.\n"
    "🎨 Фото — генерация изображений и бесплатный Flux Schnell.\n"
    "🎵 Музыка — идеи треков и генерация через музыкальные Системы.\n"
    "🎬 Видео — короткие ролики через видео-Системы.\n"
    "👤 Мой профиль — балансы ⚡️ и 💎.\n"
    "🚀 Тарифы — пополнение энергии и кристаллов.\n\n"
    "⚡️ Энергия обновляется ежедневно. 💎 Кристаллы не сгорают.\n\n"
    "Готов продолжить? Нажми кнопку «🚀 Тарифы» ниже или в меню чата."
)

TXT_STUB_BUTTON = "Скоро в боте — следи за обновлениями."

TXT_SERVICE_RULES = (
    "Ознакомиться с публичной офертой:\n{offer}\n\n"
    "Политика конфиденциальности:\n{privacy}\n\n"
    "Условия подписки:\n{terms}"
)
TXT_TARIFFS_BLOCK = "Тарифы.\n\n{plans}"
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
TXT_UNKNOWN_IMAGE_MODEL = "Неизвестная модель"
TXT_ADMIN_DENIED = (
    "❌ Доступ запрещен. У вас нет прав администратора для управления NeuroMul."
)
TXT_ADMIN_PANEL = (
    "🛠️ Добро пожаловать в панель управления NeuroMul. Выберите действие:"
)
TXT_ADMIN_GRANT_PROMPT = (
    "📝 Введите Telegram user ID получателя и количество кристаллов через пробел.\n"
    "Пример: 123456789 50\n\n"
    "Для отмены введите /cancel"
)
TXT_ADMIN_GRANT_CANCELLED = "❌ Начисление отменено."
TXT_ADMIN_GRANT_DONE = "✅ Успешно начислено +{amount} 💎 пользователю {user_id}."
TXT_ADMIN_GRANT_USER_NOTIFY = "✨ Баланс NeuroMul обновлен! Вам начислено {amount} 💎."
TXT_ADMIN_GRANT_INVALID = (
    "❌ Неверный формат. Введите строго ID и количество числами.\n"
    "Пример: 123456789 50 или /cancel"
)
TXT_ADMIN_BROADCAST_PROMPT = (
    "📢 Отправьте текст рассылки или фотографию с подписью. "
    "Она будет мгновенно доставлена всем пользователям NeuroMul.\n\n"
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
