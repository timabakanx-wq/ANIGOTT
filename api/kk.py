import os, re, json, asyncio, time, logging
from http.server import BaseHTTPRequestHandler
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Update, Message
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from supabase import create_client

BOT_TOKEN = os.environ["KK_BOT_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
WEBHOOK_SECRET = os.environ.get("KK_WEBHOOK_SECRET", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)
logging.basicConfig(level=logging.INFO)

# ===== КЭШ ПРАЙСА (60 сек) =====
_cache = {"prices": None, "aliases": None, "ts": 0}
CACHE_TTL = 60

def get_data():
    now = time.time()
    if _cache["prices"] is not None and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["prices"], _cache["aliases"]
    rows = supabase.table("cards").select("name,rating,price").execute().data
    prices = {}
    for r in rows:
        prices.setdefault(r["name"].strip().lower(), {})[int(r["rating"])] = float(r["price"])
    arows = supabase.table("aliases").select("alias,card_name").execute().data
    aliases = {r["alias"].strip().lower(): r["card_name"].strip().lower() for r in arows}
    _cache.update({"prices": prices, "aliases": aliases, "ts": now})
    return prices, aliases

def normalize_card_name(name, aliases):
    clean = name.strip().lower()
    return aliases.get(clean, clean)

# ===== ДОСТИЖЕНИЯ =====
ACHIEVEMENTS = [
    {"id": "newbie", "name": "Новичок", "desc": "Написал 10 сообщений", "count": 10},
    {"id": "introvert", "name": "Интроверт", "desc": "150 сообщений без зрительного контакта", "count": 150},
    {"id": "sociofob", "name": "Социофоб", "desc": "500 сообщений. Люди пугают.", "count": 500},
    {"id": "amiable", "name": "Дружелюбный", "desc": "Написал 1000 сообщений", "count": 1000},
    {"id": "extrovert", "name": "Экстроверт", "desc": "Написал 2500 сообщений", "count": 2500},
    {"id": "terminally_online", "name": "Хронический онлайн", "desc": "5000 сообщений", "count": 5000},
]

# ===== ФУНКЦИИ БД =====
def increment_message_count(user_id, chat_id):
    return supabase.rpc("increment_message_count", {"p_user_id": user_id, "p_chat_id": chat_id}).execute().data

def get_user_message_count(user_id, chat_id):
    r = supabase.table("user_message_counts").select("message_count").eq("user_id", user_id).eq("chat_id", chat_id).execute().data
    return r[0]["message_count"] if r else 0

def get_top_users(chat_id, limit=10):
    r = supabase.table("user_message_counts").select("user_id,message_count").eq("chat_id", chat_id).order("message_count", desc=True).limit(limit).execute().data
    return [(row["user_id"], row["message_count"]) for row in r]

def get_user_achievements(user_id, chat_id):
    r = supabase.table("achievements").select("achievement_id").eq("user_id", user_id).eq("chat_id", chat_id).execute().data
    return {row["achievement_id"] for row in r}

def get_user_warnings(user_id, chat_id):
    r = supabase.table("achievement_warnings").select("achievement_id,warning_level").eq("user_id", user_id).eq("chat_id", chat_id).execute().data
    return {(row["achievement_id"], row["warning_level"]) for row in r}

def give_achievement(user_id, chat_id, ach_id):
    supabase.table("achievements").insert({"user_id": user_id, "chat_id": chat_id, "achievement_id": ach_id}).execute()

def save_warning(user_id, chat_id, ach_id, level):
    supabase.table("achievement_warnings").insert({"user_id": user_id, "chat_id": chat_id, "achievement_id": ach_id, "warning_level": level}).execute()

def get_achievement_top(chat_id, limit=10):
    r = supabase.rpc("get_achievement_top", {"p_chat_id": chat_id, "p_limit": limit}).execute().data
    return [(row["user_id"], row["ach_count"]) for row in r]

def make_progress_bar(current, target, size=10):
    ratio = min(current / target, 1)
    filled = int(ratio * size)
    return f"{'█' * filled}{'░' * (size - filled)} {int(ratio * 100)}%"

# ===== КОМАНДЫ =====
@router.message(CommandStart())
async def start(message: Message):
    await message.answer(
        f"Привет, <b>{message.from_user.first_name}</b>!\n\n"
        "Формат ставки:\n<code>Ставка\nИмяКарты Уровень Количество</code>\n\n"
        "Пример:\n<code>Ставка\nСукуна 100\nЯто 80 2</code>\n\n"
        "Команды:\n/mystats — твоя статистика"
    )

@router.message(F.text.lower() == "гайд", F.chat.type == "private")
async def guide_private(message: Message):
    await message.answer("<b>Гайд по ставкам</b>\n\n<code>Ставка\nСукуна 100\nЯто 80 2</code>")

@router.message(F.text.lower() == "гайд")
async def guide_group(message: Message):
    await message.answer("Гайд доступен только в личных сообщениях с ботом.")

@router.message()
async def handle_all_messages(message: Message):
    # Сначала проверяем ставки
    if message.text and message.text.lower().startswith(("ставка", "ст")):
        prices, aliases = get_data()
        lines = message.text.split("\n")[1:]
        total = 0.0
        results = []
        pattern = re.compile(r'^(.+?)\s+(\d+)(?:\s+(\d+))?$')
        
        for line in lines:
            if not line.strip():
                continue
            m = pattern.match(line.strip())
            if not m:
                results.append(f"❌ <code>{line}</code>")
                continue
            
            raw_name, level, count = m.groups()
            name = normalize_card_name(raw_name, aliases)
            level = int(level)
            count = int(count) if count else 1
            
            if name not in prices:
                results.append(f"❌ <b>{raw_name}</b> — нет карты")
                continue
            if level not in prices[name]:
                results.append(f"❌ <b>{raw_name} {level}</b> — нет уровня")
                continue
            
            points = prices[name][level] * count
            total += points
            results.append(f"✅ <b>{raw_name} {level}</b> ×{count} = {points:g}")
        
        user = (
            f"@{message.from_user.username}"
            if message.from_user.username
            else message.from_user.first_name
        )
        text = f"💰 <b>Итог {user}</b>: {total:g} пт\n\n" + "\n".join(results)
        await message.reply(text, parse_mode="HTML")
        return  # ВАЖНО: return чтобы не сработал счётчик сообщений
    
    # ... остальная логика (счётчик сообщений, команды и т.д.)

@router.message(Command("top"))
async def top_users(message: Message):
    if message.chat.type == "private":
        return await message.answer("Команда работает только в группе.")
    parts = message.text.split()
    limit = min(int(parts[1]), 50) if len(parts) > 1 and parts[1].isdigit() else 10
    top = get_top_users(message.chat.id, limit)
    if not top:
        return await message.answer("Пока нет данных.")
    lines = [f"🏆 <b>ТОП {len(top)} активных участников по сообщениям:</b>\n"]
    for i, (user_id, count) in enumerate(top, start=1):
        try:
            name = (await message.chat.get_member(user_id)).user.full_name
        except Exception:
            name = f"ID {user_id}"
        lines.append(f"{i}. <b>{name}</b> — {count}")
    await message.answer("\n".join(lines))

@router.message(Command("achtop"))
async def achievement_top(message: Message):
    if message.chat.type == "private":
        return await message.answer("Команда работает только в группе.")
    parts = message.text.split()
    limit = min(int(parts[1]), 30) if len(parts) > 1 and parts[1].isdigit() else 10
    top = get_achievement_top(message.chat.id, limit)
    if not top:
        return await message.answer("Пока никто не получил достижений 😶")
    lines = [f"🏆 <b>ТОП {len(top)} по достижениям</b>\n"]
    for i, (user_id, ach_count) in enumerate(top, start=1):
        try:
            name = (await message.chat.get_member(user_id)).user.full_name
        except Exception:
            name = f"ID {user_id}"
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🔹"
        lines.append(f"{medal} <b>{name}</b> — {ach_count}")
    await message.answer("\n".join(lines))

@router.message(Command("progress"))
async def progress(message: Message):
    if message.chat.type == "private":
        return await message.answer("Команда работает только в группе.")
    user_id, chat_id = message.from_user.id, message.chat.id
    count = get_user_message_count(user_id, chat_id)
    owned = get_user_achievements(user_id, chat_id)
    next_ach, left = None, None
    for ach in ACHIEVEMENTS:
        if ach["id"] not in owned:
            next_ach, left = ach, ach["count"] - count
            break
    if not next_ach:
        return await message.answer("🏆 <b>Максимум достигнут</b>\n\nВсе достижения получены.\nТы легенда чата 💀🔥")
    bar = make_progress_bar(count, next_ach["count"])
    await message.answer(
        f"📈 <b>Прогресс</b>\n\nСледующее достижение:\n<b>{next_ach['name']}</b>\n{next_ach['desc']}\n\n"
        f"{bar}\nОсталось сообщений: <b>{left}</b>"
    )

@router.message(Command("mystats"))
async def mystats(message: Message):
    if message.chat.type == "private":
        return await message.answer("Команда работает только в группе.")
    user_id, chat_id = message.from_user.id, message.chat.id
    count = get_user_message_count(user_id, chat_id)
    owned = get_user_achievements(user_id, chat_id)
    current = None
    for ach in ACHIEVEMENTS:
        if ach["id"] in owned:
            current = ach
    if current:
        level_text = f"🏅 Текущий уровень: <b>{current['name']}</b>\n{current['desc']}"
    else:
        level_text = "🏅 Уровень: <b>Без достижений</b>"
    await message.answer(f"📊 <b>Твоя статистика</b>\n\nСообщений: <b>{count}</b>\n{level_text}")

@router.message(Command("achievements"))
async def my_achievements(message: Message):
    if message.chat.type == "private":
        return
    owned = get_user_achievements(message.from_user.id, message.chat.id)
    if not owned:
        return await message.answer("У тебя пока нет достижений 😶")
    lines = ["🏆 <b>Твои достижения</b>\n"]
    for ach in ACHIEVEMENTS:
        if ach["id"] in owned:
            lines.append(f"✅ <b>{ach['name']}</b> — {ach['desc']}")
    await message.answer("\n".join(lines))

# ===== СЧЁТЧИК СООБЩЕНИЙ =====
@router.message()
async def count_messages(message: Message):
    if message.chat.type == "private":
        return
    if message.from_user is None or message.from_user.is_bot:
        return
    if message.text and message.text.lower().startswith(("ставка", "ст")):
        return
    user_id, chat_id = message.from_user.id, message.chat.id
    count = increment_message_count(user_id, chat_id)
    owned = get_user_achievements(user_id, chat_id)
    warnings = get_user_warnings(user_id, chat_id)
    for ach in ACHIEVEMENTS:
        if count >= ach["count"] and ach["id"] not in owned:
            give_achievement(user_id, chat_id, ach["id"])
            owned.add(ach["id"])
            await message.reply(f"🏆 <b>Достижение получено!</b>\n<b>{ach['name']}</b>\n{ach['desc']}")
    next_ach = None
    for ach in ACHIEVEMENTS:
        if ach["id"] not in owned:
            next_ach = ach
            break
    if next_ach:
        left = next_ach["count"] - count
        for warn in (50, 10):
            if left == warn and (next_ach["id"], warn) not in warnings:
                save_warning(user_id, chat_id, next_ach["id"], warn)
                await message.reply(f"⏳ <b>Почти!</b>\nДо достижения <b>{next_ach['name']}</b> осталось <b>{warn}</b> сообщений 🔥")

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
