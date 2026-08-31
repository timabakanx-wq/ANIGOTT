import os, json, asyncio, logging
from http.server import BaseHTTPRequestHandler
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Update, Message, InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB, CallbackQuery
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from supabase import create_client

BOT_TOKEN = os.environ["PANEL_BOT_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
WEBHOOK_SECRET = os.environ.get("PANEL_WEBHOOK_SECRET", "")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
logging.basicConfig(level=logging.INFO)

def is_admin(uid):
    return uid in ADMIN_IDS

# Меню
MAIN_KB = IKM(inline_keyboard=[
    [IKB(text="📋 Весь прайс", callback_data="m_export"),
     IKB(text="🔍 Найти карту", callback_data="m_search_help")],
    [IKB(text="➕ Добавить карту", callback_data="m_append_help"),
     IKB(text="✏️ Изменить цену", callback_data="m_edit_help")],
    [IKB(text="🗑 Удалить", callback_data="m_delete_help"),
     IKB(text="ℹ️ Помощь", callback_data="m_help")],
])

BACK_KB = IKM(inline_keyboard=[[IKB(text="◀️ В главное меню", callback_data="m_back")]])

MENU_TEXTS = {
    "m_search_help": (
        "🔍 <b>Найти карту</b>\n\n"
        "Отправь команду:\n"
        "<code>/price 100 Сукуна</code>\n\n"
        "Можно сразу несколько:\n"
        "<code>/price\n100 Сукуна\n90 Адам\n88 Риас</code>"
    ),
    "m_append_help": (
        "➕ <b>Добавить карту</b>\n\n"
        "Формат: <b>рейтинг имя цена</b>\n\n"
        "Одна карта:\n<code>/append 89 НоваяКарта 15</code>\n\n"
        "Несколько сразу:\n"
        "<code>/append\n89 Карта1 15\n80 Карта2 5\n100 Карта3 20</code>\n\n"
        "💡 Если карта уже есть — добавится новый рейтинг."
    ),
    "m_edit_help": (
        "✏️ <b>Изменить цену</b>\n\n"
        "Формат: <b>рейтинг имя цена</b>\n\n"
        "Одна:\n<code>/edit 100 Сукуна 25</code>\n\n"
        "Несколько:\n"
        "<code>/edit\n90 Адам 60\n100 Сукуна 12</code>"
    ),
    "m_delete_help": (
        "🗑 <b>Удалить</b>\n\n"
        "Удалить карту целиком:\n<code>/delete Сукуна</code>\n\n"
        "Удалить только рейтинг:\n<code>/delete 100 Сукуна</code>\n\n"
        "Несколько:\n<code>/delete\nСукуна\n100 Адам</code>"
    ),
    "m_help": (
        "ℹ️ <b>Справка</b>\n\n"
        "Все команды:\n"
        "• <code>/price</code> — найти цену карты\n"
        "• <code>/edit</code> — изменить цену\n"
        "• <code>/append</code> — добавить карту\n"
        "• <code>/delete</code> — удалить карту\n"
        "• <code>/export</code> — весь прайс списком\n"
        "• <code>/count</code> — сколько карт в базе\n\n"
        "📌 Формат строки везде: <b>рейтинг имя цена</b>\n"
        "📌 Имя может быть с пробелами: <code>80 Демон Мицури 8000</code>\n"
        "⚡ Изменения применяются в течение 60 сек без перезапуска."
    ),
}

# Операции с БД
def edit_price(rating, price, name):
    name_lower = name.strip().lower()
    existing = supabase.table("cards").select("id").eq("name", name_lower).eq("rating", rating).execute().data
    if not existing:
        return f"❌ {name} {rating} — нет карты (используй /append)"
    supabase.table("cards").update({"price": price}).eq("name", name_lower).eq("rating", rating).execute()
    return f"✅ {name} {rating} → {price:g} пт"

def append_card(rating, price, name):
    name_lower = name.strip().lower()
    existing = supabase.table("cards").select("id").eq("name", name_lower).eq("rating", rating).execute().data
    if existing:
        supabase.table("cards").update({"price": price}).eq("name", name_lower).eq("rating", rating).execute()
        return f"🔄 {name} {rating} уже была → цена обновлена на {price:g}"
    supabase.table("cards").insert({"name": name_lower, "rating": rating, "price": price}).execute()
    return f"✅ Новая карта: {name} {rating} = {price:g} пт"

def delete_card(name, rating=None):
    name_lower = name.strip().lower()
    if rating is None:
        result = supabase.table("cards").delete().eq("name", name_lower).execute()
        n = len(result.data)
        return f"🗑 '{name}' удалена ({n} записей)" if n else f"❌ '{name}' не найдена"
    result = supabase.table("cards").delete().eq("name", name_lower).eq("rating", rating).execute()
    return f"🗑 У '{name}' удалён рейтинг {rating}" if result.data else f"❌ {name} {rating} не найден"

def find_price(name, rating):
    name_lower = name.strip().lower()
    rows = supabase.table("cards").select("rating,price").eq("name", name_lower).eq("rating", rating).execute().data
    return rows[0]["price"] if rows else None

def get_count():
    rows = supabase.table("cards").select("name", count="exact", head=True).execute()
    return rows.count

# Парсеры
def parse_data_lines(text):
    lines = text.split("\n")
    first = lines[0].strip().split(maxsplit=1)
    data = []
    if len(first) > 1:
        data.append(first[1].strip())
    data.extend(l.strip() for l in lines[1:] if l.strip())
    return data

def parse_edit_line(line):
    parts = line.split()
    if len(parts) < 3:
        return None
    try:
        rating, price = int(parts[0]), float(parts[-1])
    except ValueError:
        return None
    name = " ".join(parts[1:-1]).strip()
    return (rating, name, price) if name else None

def parse_delete_line(line):
    parts = line.split()
    if not parts:
        return None, None
    try:
        rating = int(parts[0])
        name = " ".join(parts[1:]).strip()
        return (rating, name) if name else (None, None)
    except ValueError:
        return None, line.strip()

# Создаём бота и dispatcher
# Глобальный event loop — создаётся ОДИН раз и НИКОГДА не закрывается
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

@router.message(Command("start"))
async def cmd_start(m: Message):
    if not is_admin(m.from_user.id):
        return
    await m.answer(
        "🎛 <b>Панель управления ценами</b>\n\n"
        "Выбери действие кнопками ниже 👇\n"
        "или сразу пиши команду (см. ℹ️ Помощь)",
        reply_markup=MAIN_KB
    )

@router.callback_query(F.data.startswith("m_"))
async def cb_menu(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return
    data = c.data
    if data == "m_back":
        await c.message.edit_text("🎛 <b>Главное меню</b>", reply_markup=MAIN_KB)
        await c.answer()
        return
    if data == "m_export":
        await c.answer("Формирую прайс... ⏳")
        rows = supabase.table("cards").select("name,rating,price").order("name").execute().data
        if not rows:
            await c.message.answer("Прайс пуст.", reply_markup=BACK_KB)
            return
        lines = [f"📋 <b>Прайс ({len(rows)} записей)</b>\n"]
        for r in rows:
            lines.append(f"• {r['name']} | {r['rating']} | {r['price']:g}")
        text = "\n".join(lines)
        for i in range(0, len(text), 4000):
            await c.message.answer(text[i:i+4000], reply_markup=BACK_KB if i == 0 else None)
        await c.answer()
        return
    if data in MENU_TEXTS:
        await c.message.edit_text(MENU_TEXTS[data], reply_markup=BACK_KB)
        await c.answer()
        return
    await c.answer()

@router.message(Command("count"))
async def cmd_count(m: Message):
    if not is_admin(m.from_user.id):
        return
    n = get_count()
    await m.answer(f"📊 В базе <b>{n}</b> записей карт.")

@router.message(Command("edit"))
async def cmd_edit(m: Message):
    if not is_admin(m.from_user.id):
        return
    data = parse_data_lines(m.text)
    if not data:
        return await m.answer("❌ Формат:\n<code>/edit\n90 Адам 60\n100 Сукуна 12</code>")
    results = []
    for line in data:
        parsed = parse_edit_line(line)
        if not parsed:
            results.append(f"❌ <code>{line}</code> — формат: рейтинг имя цена")
            continue
        rating, name, price = parsed
        results.append(edit_price(rating, price, name))
    await m.answer("✏️ <b>Результат:</b>\n\n" + "\n".join(results) + "\n\n⚡ Применится в течение 60 сек.")

@router.message(Command("append"))
async def cmd_append(m: Message):
    if not is_admin(m.from_user.id):
        return
    data = parse_data_lines(m.text)
    if not data:
        return await m.answer("❌ Формат:\n<code>/append\n89 НоваяКарта 15\n80 Тест 5</code>")
    results = []
    for line in data:
        parsed = parse_edit_line(line)
        if not parsed:
            results.append(f"❌ <code>{line}</code> — формат: рейтинг имя цена")
            continue
        rating, name, price = parsed
        results.append(append_card(rating, price, name))
    await m.answer("➕ <b>Результат:</b>\n\n" + "\n".join(results) + "\n\n⚡ Применится в течение 60 сек.")

@router.message(Command("delete"))
async def cmd_delete(m: Message):
    if not is_admin(m.from_user.id):
        return
    data = parse_data_lines(m.text)
    if not data:
        return await m.answer("❌ Формат:\n<code>/delete Сукуна</code> — карту\n<code>/delete 100 Сукуна</code> — рейтинг")
    results = []
    for line in data:
        rating, name = parse_delete_line(line)
        if not name:
            results.append(f"❌ <code>{line}</code> — не указано имя")
            continue
        results.append(delete_card(name, rating))
    await m.answer("🗑 <b>Результат:</b>\n\n" + "\n".join(results))

@router.message(Command("price"))
async def cmd_price(m: Message):
    if not is_admin(m.from_user.id):
        return
    data = parse_data_lines(m.text)
    if not data:
        return await m.answer("❌ Формат:\n<code>/price\n100 Сукуна</code>")
    results = []
    for line in data:
        parts = line.split(maxsplit=1)
        if len(parts) < 2:
            results.append(f"❌ <code>{line}</code>")
            continue
        try:
            rating = int(parts[0])
        except ValueError:
            results.append(f"❌ <code>{line}</code> — рейтинг не число")
            continue
        price = find_price(parts[1], rating)
        if price is not None:
            results.append(f"🔍 <b>{parts[1]} {rating}</b> — <b>{price:g} пт</b>")
        else:
            results.append(f"❌ <b>{parts[1]} {rating}</b> не найден")
    await m.answer("\n".join(results))

# Вебхук
class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if WEBHOOK_SECRET:
            if self.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != WEBHOOK_SECRET:
                self.send_response(403)
                self.end_headers()
                return
        
        try:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            update = Update(**json.loads(body))
            
            # Создаём новый event loop для каждого вызова
            LOOP.run_until_complete(dp.feed_update(bot, update))
        except Exception as e:
            logging.error(f"Update error: {e}")
        
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')
    
    def log_message(self, format, *args):
        pass
