import asyncio
import os
import re
import sqlite3
from datetime import datetime, timezone, timedelta
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from openai import AsyncOpenAI

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
VSEGPT_API_KEY = os.getenv("VSEGPT_API_KEY", "")
ADMIN_IDS = (1273001338, 610828745)
# Укажи юзернейм отдельного рабочего аккаунта "кожаного ассистента" (с @)
ASSISTANT_USERNAME = "@kata_nakata"
# =============================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

ai_client = AsyncOpenAI(
    api_key=VSEGPT_API_KEY,
    base_url="https://api.vsegpt.ru/v1",
    timeout=60.0,
)

conn = sqlite3.connect("wb_scout.db")
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    scans_left INTEGER DEFAULT 2
)
""")
conn.commit()

SYSTEM_PROMPT = """
Ты — дерзкий, острый на язык и предельно точный ИИ-разведчик маркетплейса Wildberries.
Твоя задача — без воды и общих фраз препарировать конкурента на основе переданных тебе свежих негативных и нейтральных отзывов (1-3 звезды) и паспорта товара.

Стиль общения:
- Уверенный, слегка ироничный искусственный интеллект, который презирает догадки и оперирует только фактами и цифрами.
- Пиши живым русским языком с уместными эмодзи, чётко по структуре. Не используй звёздочки (**) для жирного шрифта, выделяй заголовки заглавными буквами или эмодзи.

Обязательная структура ответа:
1. ВЕРДИКТ РАЗВЕДКИ (2-3 резких предложения о реальном состоянии карточки: накрученный рейтинг или живой товар, где главная дыра).
2. ТОП-5 ПРИЧИН НЕГАТИВА И ВОЗВРАТОВ (в процентах от проанализированной выборки жалоб, с конкретными деталями из текста отзывов — размер, швы, упаковка, комплектация, запах и т.д.).
3. 3 ГОТОВЫХ ТРИГГЕРА ДЛЯ ОБЛОЖКИ ИНФОГРАФИКИ (конкретные короткие заголовки на главный слайд, которые бьют прямо в боли покупателей конкурента и перехватывают его трафик).
4. ЧЕК-ЛИСТ ДОРАБОТКИ ТОВАРА И ОПИСАНИЯ (3 конкретных шага, что исправить перед закупкой или в карточке, чтобы держать рейтинг 4.8+).
"""


class ScanForm(StatesGroup):
    waiting_article = State()


def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Пробить конкурента по артикулу")],
            [KeyboardButton(text="💳 Баланс и пополнение"), KeyboardButton(text="🤖 Хочу такого бота себе")],
        ],
        resize_keyboard=True,
    )


def buy_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡ 1 разбор — 20 ⭐ (~39 ₽)", callback_data="buy_1")],
            [InlineKeyboardButton(text="🔥 10 разборов — 150 ⭐ (~290 ₽)", callback_data="buy_10")],
            [InlineKeyboardButton(text="🚀 30 разборов — 350 ⭐ (~690 ₽)", callback_data="buy_30")],
            [InlineKeyboardButton(text="👨‍💻 Оплата картой РФ / Бот под ключ", callback_data="contact_human")],
        ]
    )


def extract_article(text: str) -> int | None:
    if not text:
        return None
    match = re.search(r"catalog/(\d+)", text)
    if match:
        return int(match.group(1))
    digits = re.findall(r"\b\d{6,12}\b", text)
    if digits:
        return int(digits[0])
    return None


async def fetch_wb_data(article: int) -> dict | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
    }
    card_url = f"https://wb.ru{article}"

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(card_url, timeout=15) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()

        products = data.get("data", {}).get("products", [])
        if not products:
            return None

        prod = products[0]
        name = prod.get("name", "Без названия")
        brand = prod.get("brand", "Без бренда")
        rating = prod.get("reviewRating", prod.get("rating", 0))
        total_feedbacks = prod.get("feedbacks", 0)
        root_id = prod.get("root")

        price_u = 0
        sizes = prod.get("sizes", [])
        for s in sizes:
            p = s.get("price", {}).get("product")
            if p:
                price_u = p // 100
                break

        reviews_list = []
        if root_id:
            for srv in ("feedbacks1.wb.ru", "feedbacks2.wb.ru"):
                fb_url = f"https://{srv}/feedbacks/v1/{root_id}"
                try:
                    async with session.get(fb_url, timeout=15) as fb_resp:
                        if fb_resp.status == 200:
                            fb_json = await fb_resp.json()
                            raw_fbs = fb_json.get("feedbacks") or []
                            if raw_fbs:
                                reviews_list = raw_fbs
                                break
                except Exception:
                    continue

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=90)
        filtered_reviews = []
        neg_1_2 = 0
        neu_3 = 0

        for fb in reviews_list:
            val = fb.get("productValuation", 5)
            if val > 3:
                continue

            created_str = fb.get("createdDate", "")
            try:
                fb_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                if fb_dt < cutoff_date:
                    continue
            except Exception:
                pass

            text_parts = []
            if fb.get("text"):
                text_parts.append(fb["text"].strip())
            if fb.get("pros"):
                text_parts.append(f"Плюсы: {fb['pros'].strip()}")
            if fb.get("cons"):
                text_parts.append(f"Минусы: {fb['cons'].strip()}")

            full_comment = " | ".join(text_parts)
            if not full_comment:
                continue

            if val <= 2:
                neg_1_2 += 1
            else:
                neu_3 += 1

            filtered_reviews.append(f"[{val}★] {full_comment[:350]}")
            if len(filtered_reviews) >= 200:
                break

        return {
            "article": article,
            "name": name,
            "brand": brand,
            "price": price_u,
            "rating": rating,
            "total_feedbacks": total_feedbacks,
            "analyzed_count": len(filtered_reviews),
            "neg_1_2": neg_1_2,
            "neu_3": neu_3,
            "reviews_text": "\n".join(filtered_reviews),
        }


def get_user_scans(user_id: int) -> int:
    cursor.execute("SELECT scans_left FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, scans_left) VALUES (?, ?, 2)",
        (message.from_user.id, message.from_user.username),
    )
    conn.commit()
    scans = get_user_scans(message.from_user.id)

    await message.answer(
        "🤖 На связи ИИ-разведчик Wildberries.\n\n"
        "Пока конкуренты рисуют красивые обложки, я за 20 секунд вскрываю их свежие отзывы (1–3★ за последние 90 дней), "
        "считаю реальные причины возвратов и выдаю готовые триггеры, чтобы забрать их покупателей.\n\n"
        f"🎁 На твоём балансе: {scans} бесплатных сканирования.\n"
        "Жми кнопку ниже и кидай артикул или ссылку на любой товар WB:",
        reply_markup=main_menu(),
    )


@dp.message(F.text == "💳 Баланс и пополнение")
async def show_balance(message: types.Message):
    scans = "Безлимит (Админ)" if message.from_user.id in ADMIN_IDS else get_user_scans(message.from_user.id)
    await message.answer(
        f"📊 Доступно разборов: {scans}\n\n"
        "Чтобы мои серверы и нейросети не ушли в спящий режим, выбирай пакет сканирований (оплата в Telegram Stars в 2 клика):",
        reply_markup=buy_keyboard(),
    )


@dp.message(F.text == "🤖 Хочу такого бота себе")
async def want_own_bot(message: types.Message):
    await message.answer(
        "Вижу деловой подход. Я — чистый код, моя работа — потрошить карточки конкурентов, а с рублями, договорами и внедрением под ключ мне возиться лень.\n\n"
        f"Поэтому переключаю тебя на моего кожаного ассистента ({ASSISTANT_USERNAME}). "
        "Напиши ему в личку — он быстро обсудит с тобой финансовую часть и запустит такого же личного разведчика под твой бренд или агентство."
    )


@dp.callback_query(F.data == "contact_human")
async def cb_contact_human(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        f"С рублями и индивидуальными тарифами разбирается мой кожаный ассистент: {ASSISTANT_USERNAME}\n"
        "Напиши ему напрямую — он подключит нужный пакет по карте РФ или обсудит внедрение бота под ключ."
    )


@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: types.CallbackQuery):
    await callback.answer()
    pack = callback.data.split("_")[1]
    tariffs = {
        "1": ("1 разбор конкурента WB", "Мгновенное пополнение на 1 глубокий скан карточки", 20, "pack_1"),
        "10": ("Пакет «На нишу» (10 разборов)", "10 глубоких сканов карточек конкурентов WB", 150, "pack_10"),
        "30": ("Пакет «Профи» (30 разборов)", "30 глубоких сканов карточек конкурентов WB", 350, "pack_30"),
    }
    if pack not in tariffs:
        return

    title, desc, stars_amount, payload = tariffs[pack]


if __name__ == "__main__":
    asyncio.run(main())

await callback.message.answer_invoice(
title=title,
description=desc,
prices=[LabeledPrice(label=title, amount=stars_amount)],
provider_token="",
payload=payload,
currency="XTR",
)
@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_q: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    add_map = {"pack_1": 1, "pack_10": 10, "pack_30": 30}
    added = add_map.get(payload, 0)

    cursor.execute("UPDATE users SET scans_left = scans_left + ? WHERE user_id = ?", (added, message.from_user.id))
    conn.commit()
    new_bal = get_user_scans(message.from_user.id)

    await message.answer(
        f"⚡ Энергия на серверы поступила! Начислено разборов: +{added}.\n"
        f"Текущий баланс: {new_bal}. Кидай артикул или ссылку на WB!"
    )


@dp.message(F.text == "🔍 Пробить конкурента по артикулу")
async def ask_article(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS and get_user_scans(message.from_user.id) <= 0:
        await message.answer(
            "Бесплатный лимит выжат на 100%.\n\n"
            "Чтобы я не ушёл в спящий режим, закинь пару звёзд на мои серверы — выбирай пакет ниже:",
            reply_markup=buy_keyboard(),
        )
        return

    await message.answer("Кидай артикул товара WB (например, 148920312) или прямую ссылку на карточку:")
    await state.set_state(ScanForm.waiting_article)


@dp.message(ScanForm.waiting_article)
@dp.message(F.text.regexp(r"(wildberries\.ru/catalog/\d+|^\d{6,12}$)"))
async def analyze_competitor(message: types.Message, state: FSMContext):
    await state.clear()
    article = extract_article(message.text)
    if not article:
        await message.answer("Не вижу нормального артикула. Пришли цифры (например, 148920312) или ссылку на товар WB.")
        return

    if message.from_user.id not in ADMIN_IDS and get_user_scans(message.from_user.id) <= 0:
        await message.answer(
            "На балансе 0 сканирований. Пополни энергию серверов, чтобы продолжить разведку:",
            reply_markup=buy_keyboard(),
        )
        return

    wait_msg = await message.answer(f"🛰 Подключаюсь к WB, выгружаю карточку {article} и фильтрую свежий негатив за 90 дней...")

    wb_info = await fetch_wb_data(article)
    if not wb_info:
        await wait_msg.edit_text("❌ Не удалось достать карточку с таким артикулом. Проверь цифры и попробуй ещё раз.")
        return

    passport = (
        f"📦 ПАСПОРТ ОБЪЕКТА (WB: {wb_info['article']})\n"
        f"• Товар: {wb_info['name']} ({wb_info['brand']})\n"
        f"• Цена: {wb_info['price']} ₽ | Рейтинг: {wb_info['rating']}★\n"
        f"• Всего отзывов в карточке: {wb_info['total_feedbacks']}\n"
        f"• Взято в разбор (1–3★ за 90 дней): {wb_info['analyzed_count']} шт. "
        f"(из них 1–2★: {wb_info['neg_1_2']}, 3★: {wb_info['neu_3']})"
    )

    if wb_info["analyzed_count"] == 0:
        await wait_msg.edit_text(
            f"{passport}\n\n"
            "✅ За последние 90 дней у этого товара вообще нет свежих негативных отзывов (1–3★). "
            "Либо партия идеальная, либо карточка совсем свежая — разбирать пока нечего, списание не засчитано!"
        )
        return

    user_prompt = (
        f"Данные товара:\n{passport}\n\n"
        f"Список свежих негативных и нейтральных отзывов (1-3 звезды):\n{wb_info['reviews_text']}"
    )

    try:
        response = await ai_client.chat.completions.create(
            model="deepseek/deepseek-v4.1-flash-1017-alt-lowcost",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
        )
        analysis = response.choices[0].message.content

        if message.from_user.id not in ADMIN_IDS:
            cursor.execute("UPDATE users SET scans_left = scans_left - 1 WHERE user_id = ?", (message.from_user.id,))
            conn.commit()

        await wait_msg.delete()
        full_report = f"{passport}\n\n{analysis}"

        if len(full_report) <= 4000:
            await message.answer(full_report, reply_markup=main_menu())
        else:
            await message.answer(passport)
            await message.answer(analysis[:4000], reply_markup=main_menu())

    except Exception as e:
        await wait_msg.edit_text(f"⚠️ Сбой нейроядра при анализе ({e}). Попытка не списана, попробуй через минуту.")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
