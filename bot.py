import asyncio
import io
import os
import random
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
from aiogram.utils.media_group import MediaGroupBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from openai import AsyncOpenAI

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
VSEGPT_API_KEY = os.getenv("VSEGPT_API_KEY", "")
ADMIN_IDS = (1273001338, 610828745)
DECKS_DIR = "decks"
# =============================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

ai_client = AsyncOpenAI(
    api_key=VSEGPT_API_KEY,
    base_url="https://api.vsegpt.ru/v1",
    timeout=45.0
)

conn = sqlite3.connect("taro_bot.db")
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    free_readings INTEGER DEFAULT 1
)
""")
conn.commit()

TAROT_DECK = [
    "Шут", "Маг", "Верховная Жрица", "Императрица", "Император", "Иерофант",
    "Влюблённые", "Колесница", "Сила", "Отшельник", "Колесо Фортуны", "Справедливость",
    "Повешенный", "Смерть", "Умеренность", "Дьявол", "Башня", "Звезда", "Луна",
    "Солнце", "Суд", "Мир",
    "Туз Жезлов", "Двойка Жезлов", "Тройка Жезлов", "Четвёрка Жезлов", "Пятёрка Жезлов",
    "Шестёрка Жезлов", "Семёрка Жезлов", "Восьмёрка Жезлов", "Девятка Жезлов", "Десятка Жезлов",
    "Паж Жезлов", "Рыцарь Жезлов", "Королева Жезлов", "Король Жезлов",
    "Туз Кубков", "Двойка Кубков", "Тройка Кубков", "Четвёрка Кубков", "Пятёрка Кубков",
    "Шестёрка Кубков", "Семёрка Кубков", "Восьмёрка Кубков", "Девятка Кубков", "Десятка Кубков",
    "Паж Кубков", "Рыцарь Кубков", "Королева Кубков", "Король Кубков",
    "Туз Мечей", "Двойка Мечей", "Тройка Мечей", "Четвёрка Мечей", "Пятёрка Мечей",
    "Шестёрка Мечей", "Семёрка Мечей", "Восьмёрка Мечей", "Девятка Мечей", "Десятка Мечей",
    "Паж Мечей", "Рыцарь Мечей", "Королева Мечей", "Король Мечей",
    "Туз Пентаклей", "Двойка Пентаклей", "Тройка Пентаклей", "Четвёрка Пентаклей", "Пятёрка Пентаклей",
    "Шестёрка Пентаклей", "Семёрка Пентаклей", "Восьмёрка Пентаклей", "Девятка Пентаклей", "Десятка Пентаклей",
    "Паж Пентаклей", "Рыцарь Пентаклей", "Королева Пентаклей", "Король Пентаклей"
]

SYSTEM_PROMPT = """
Ты — чуткий, глубокий и мудрый таролог-психолог. Твоя задача — трактовать расклад Таро мягко, бережно, без запугивания, фатализма и шаблонных фраз из учебников.
Используй карты как инструмент саморефлексии и поиска опоры.
Структура ответа:
1. Общая атмосфера расклада (2-3 предложения).
2. Значение каждой выпавшей карты именно в контексте вопроса человека и их связка друг с другом.
3. Практический совет или мягкое напутствие.
Пиши живым, тёплым русским языком, разбивай текст на абзацы с уместными эмодзи. Не используй звёздочки для жирного шрифта.
"""

class Form(StatesGroup):
    waiting_ai_question = State()
    waiting_live_request = State()

def get_today_deck_path():
    if not os.path.exists(DECKS_DIR):
        return None
    decks = sorted([
        os.path.join(DECKS_DIR, d) for d in os.listdir(DECKS_DIR)
        if os.path.isdir(os.path.join(DECKS_DIR, d))
    ])
    if not decks:
        return None
    day_of_year = datetime.now().timetuple().tm_yday
    return decks[day_of_year % len(decks)]

def prepare_card_image(deck_path, card_index, is_reversed):
    if not deck_path:
        return None
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        img_path = os.path.join(deck_path, f"{card_index:02d}{ext}")
        if os.path.exists(img_path):
            with Image.open(img_path) as img:
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                if is_reversed:
                    img = img.rotate(180)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=90)
                buf.seek(0)
                return BufferedInputFile(buf.read(), filename=f"card_{card_index:02d}.jpg")
    return None

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔮 Быстрый расклад с ИИ (24/7)")],
            [KeyboardButton(text="✋ Личный расклад от мастера (днём)")],
            [KeyboardButton(text="🕸 Авторские ловцы снов")]
        ],
        resize_keyboard=True
    )

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, free_readings) VALUES (?, ?, 1)",
        (message.from_user.id, message.from_user.username)
    )
    conn.commit()
    await message.answer(
        "Добро пожаловать в Мастерскую Таро и Снов ✨\n\n"
        "Здесь можно получить мгновенный разбор от ИИ-таролога (обученного на авторских записях мастера) "
        "или записаться на живой глубокий расклад на реальной колоде.\n\n"
        "🎁 У тебя есть 1 бесплатный тестовый расклад на 3 карты. Выбирай раздел в меню:",
        reply_markup=main_menu()
    )

@dp.message(F.text == "🔮 Быстрый расклад с ИИ (24/7)")
async def ai_reading_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        cursor.execute("SELECT free_readings FROM users WHERE user_id = ?", (message.from_user.id,))
        row = cursor.fetchone()
        free_left = next(iter(row), 0) if row else 0

        if free_left <= 0:
            await message.answer(
                "Бесплатный тестовый расклад уже использован ✨\n\n"
                "Полный разбор на 3 карты от ИИ-таролога стоит 190 ₽."
            )
            return

    await message.answer("Сформулируй и напиши свой вопрос (одной фразой или подробнее, как чувствуешь):")
    await state.set_state(Form.waiting_ai_question)

@dp.message(Form.waiting_ai_question)
async def process_ai_question(message: types.Message, state: FSMContext):
    question = message.text
    await state.clear()
    wait_msg = await message.answer("Тасую колоду дня и настраиваюсь на твой вопрос... 🕯")

    drawn_indices = random.sample(range(len(TAROT_DECK)), 3)
    deck_path = get_today_deck_path()

    cards_lines = []
    media_group = MediaGroupBuilder()
    images_found = 0

    for idx in drawn_indices:
        card_name = TAROT_DECK[idx]
        is_rev = random.choice([False, True])
        pos_text = "перевёрнутое положение" if is_rev else "прямое положение"
        cards_lines.append(f"— {card_name} ({pos_text})")

        card_file = prepare_card_image(deck_path, idx, is_rev)
        if card_file:
            images_found += 1
            caption = f"🃏 {card_name} ({pos_text})" if images_found == 1 else None
            media_group.add_photo(media=card_file, caption=caption)

    cards_text = "\n".join(cards_lines)

    try:
        response = await ai_client.chat.completions.create(
            model="deepseek/deepseek-v4.1-flash-1017-alt-lowcost",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Вопрос человека: {question}\n\nВыпали карты:\n{cards_text}"}
            ],
            temperature=0.7
        )
        if isinstance(response, str):
            raise Exception(f"Текст от сервера: {response}")

        first_choice = next(iter(response.choices))
        interpretation = first_choice.message.content

        if message.from_user.id not in ADMIN_IDS:
            cursor.execute("UPDATE users SET free_readings = free_readings - 1 WHERE user_id = ?", (message.from_user.id,))
            conn.commit()

        if images_found == 3:
            await wait_msg.delete()
            await message.answer_media_group(media=media_group.build())
            full_text = (
                f"🃏 Выпавшие карты:\n{cards_text}\n\n{interpretation}\n\n"
                f"Консультация носит информационно-рекомендательный характер."
            )
            is_first = False
        else:
            full_text = (
                f"🃏 Твой расклад:\n{cards_text}\n\n{interpretation}\n\n"
                f"Консультация носит информационно-рекомендательный характер."
            )
            is_first = True

        while len(full_text) > 0:
            if len(full_text) <= 3000:
                chunk = full_text
                full_text = ""
            else:
                split_pos = full_text.rfind("\n", 0, 3000)
                if split_pos == -1:
                    split_pos = full_text.rfind(" ", 0, 3000)
                if split_pos == -1:
                    split_pos = 3000
                chunk = full_text[:split_pos]
                full_text = full_text[split_pos:].lstrip()

            if is_first:
                await wait_msg.edit_text(chunk)
                is_first = False
            else:
                await asyncio.sleep(0.4)
                await message.answer(chunk)

    except Exception as e:
        print("ОШИБКА ИИ:", e)
        await wait_msg.edit_text(f"Ошибка: {str(e)[:500]}")

@dp.message(F.text == "✋ Личный расклад от мастера (днём)")
async def live_reading_start(message: types.Message, state: FSMContext):
    await message.answer(
        "Живой расклад выполняется мастером вручную на реальной колоде в дневное время "
        "(ответ приходит в виде фото карт и подробного голосового сообщения).\n\n"
        "Стоимость: 1 500 ₽.\n"
        "Опиши кратко свою ситуацию или вопрос, чтобы мы передали заявку мастеру:"
    )
    await state.set_state(Form.waiting_live_request)

@dp.message(Form.waiting_live_request)
async def process_live_request(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Заявка принята ✨ Мы свяжемся с тобой в ближайшее время для подтверждения окошка и отправки реквизитов для оплаты."
    )


    for admin_id in ADMIN_IDS:
        if admin_id != 0:
            await bot.send_message(
                admin_id,
                f"📥 Новая заявка на живой расклад от @{message.from_user.username} (ID: {message.from_user.id}):\n\n{message.text}"
            )

@dp.message(F.text == "🕸 Авторские ловцы снов")
async def dreamcatchers_info(message: types.Message):
    await message.answer(
        "Каждый ловец снов плетётся вручную в единственном экземпляре на основе из натуральных веток 🌿\n\n"
        "Посмотреть актуальные работы в наличии и заказать индивидуальный оберег можно в нашем канале мастерской."
    )

async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
