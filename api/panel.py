import os, json, asyncio, logging
from http.server import BaseHTTPRequestHandler
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Update, Message, InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB, CallbackQuery
from aiogram.filters import Command
from supabase import create_client

BOT_TOKEN = os.environ["PANEL_BOT_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
WEBHOOK_SECRET = os.environ.get("PANEL_WEBHOOK_SECRET", "")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)
logging.basicConfig(level=logging.INFO)

def is_admin(uid):
    return uid in ADMIN_IDS

# ===== ПАРСЕРЫ МУЛЬТИ-ВВОДА =====
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
    if not name:
        return None
    return rating, name, price

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

# ===== ОПЕРАЦИИ С БД =====
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
    if rows:
        return rows[0]["price"]
    return None

def export_all():
    rows = supabase.table("cards").select("name,rating,price").order("name").execute().data
    return rows

# ===== КОМАНДЫ =====
MAIN_KB = IKM(inline_keyboard=[[IKB(text="💰 Работа с картами", callback_data="cards_help")]])

@router.message(Command("start"))
async def cmd_start(m: Message):
    if not is_admin(m.from_user.id):
        return
    await m.answer("🎛 <b>Панель управления (облако)</b>\n\nЦены хранятся в базе.\nИзменения применяются в течение 60 сек без перезапуска.", reply_markup=MAIN_KB)

@router.callback_query(F.data == "cards_help")
async def cb_cards(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return
    text = (
        "💰 <b>Работа с картами</b>\n\n"
        "<b>Изменить цену</b> (мульти):\n<code>/edit\n90 Адам 60\n100 Сукуна 12</code>\n\n"
        "<b>Добавить карту</b> (мульти):\n<code>/append\n89 НоваяКарта 15\n80 Тест 5</code>\n\n"
        "<b>Удалить</b> (мульти):\n<code>/delete\nСукуна\n100 Адам</code>\n\n"
        "<b>Посмотреть</b> (мульти):\n<code>/price\n100 Сукуна</code>\n\n"
        "<b>Весь прайс:</b> <code>/export</code>\n\n"
        "📌 Формат строки: <b>рейтинг имя цена</b>\n"
        "⚡ Изменения применяются в течение 60 сек."
    )
    await c.message.edit_text(text)
    await c.answer()

@router.message(Command("edit"))
async def cmd_edit(m: Message):
    if not is_admin(m.from_user.id):
        return
    data = parse_data_lines(m.text)
    if not data:
        return await m.answer("❌ Формат: <code>/edit\n90 Адам 60</code>")
    results = []
    for line in data:
        parsed = parse_edit_line(line)
        if not parsed:
            results.append(f"❌ <code>{line}</code> — формат: рейтинг имя цена")
            continue
        rating, name, price = parsed
        results.append(edit_price(rating, price, name))
    await m.answer("💰 <b>/edit:</b>\n\n" + "\n".join(results) + "\n\n⚡ Применится в течение 60 сек.")

@router.message(Command("append"))
async def cmd_append(m: Message):
    if not is_admin(m.from_user.id):
        return
    data = parse_data_lines(m.text)
    if not data:
        return await m.answer("❌ Формат: <code>/append\n89 НоваяКарта 15</code>")
    results = []
    for line in data:
        parsed = parse_edit_line(line)
        if not parsed:
            results.append(f"❌ <code>{line}</code> — формат: рейтинг имя цена")
            continue
        rating, name, price = parsed
        results.append(append_card(rating, price, name))
    await m.answer("💰 <b>/append:</b>\n\n" + "\n".join(results) + "\n\n⚡ Применится в течение 60 сек.")

@router.message(Command("delete"))
async def cmd_delete(m: Message):
    if not is_admin(m.from_user.id):
        return
    data = parse_data_lines(m.text)
    if not data:
        return await m.answer("❌ Формат:\n<code>/delete Имя</code> — карту\n<code>/delete 100 Имя</code> — рейтинг")
    results = []
    for line in data:
        rating, name = parse_delete_line(line)
        if not name:
            results.append(f"❌ <code>{line}</code> — не указано имя")
            continue
        results.append(delete_card(name, rating))
    await m.answer("🗑 <b>/delete:</b>\n\n" + "\n".join(results))

@router.message(Command("price"))
async def cmd_price(m: Message):
    if not is_admin(m.from_user.id):
        return
    data = parse_data_lines(m.text)
    if not data:
        return await m.answer("❌ Формат: <code>/price\n100 Сукуна</code>")
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

@router.message(Command("export"))
async def cmd_export(m: Message):
    if not is_admin(m.from_user.id):
        return
    rows = export_all()
    if not rows:
        return await m.answer("Прайс пуст.")
    lines = [f"📋 <b>Весь прайс ({len(rows)} записей):</b>\n"]
    for r in rows:
        lines.append(f"{r['name']} | {r['rating']} | {r['price']:g}")
    text = "\n".join(lines)
    # Telegram ограничивает сообщение 4096 символами — режем на части
    for i in range(0, len(text), 4000):
        await m.answer(text[i:i+4000])

# ===== ВЕБХУК =====
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
            asyncio.run(dp.feed_update(bot, update))
        except Exception as e:
            logging.error(f"Update error: {e}")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')
