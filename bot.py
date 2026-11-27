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
cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, scans_left INTEGER DEFAULT 2, role TEXT DEFAULT 'seller')")
try:
    cursor.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'seller'")
except Exception:
    pass
conn.commit()

SELLER_PROMPT = (
    "Ты — объективный и точный ИИ-аналитик Wildberries. Оцени товар по свежим отзывам (1-3 звезды за 90 дней) с учётом среднего рейтинга карточки. "
    "ВАЖНО: если средний рейтинг 4.7★ и выше, а жалоб 1-3★ меньше 80 шт. из 1000 — не драматизируй, признай, что товар сильный, а негатив это рабочий шум и точки роста. "
    "Если рейтинг ниже 4.55★ или жалоб больше 100 шт. — жёстко вскрывай системный брак. "
    "Пиши живым русским языком с эмодзи, без звёздочек (**). Структура: "
    "1. ВЕРДИКТ РАЗВЕДКИ (объективная оценка: сильный лидер с мелкими нюансами или проблемная карточка). "
    "2. ТОП-5 ПРИЧИН НЕГАТИВА И ВОЗВРАТОВ (в % от выборки жалоб с деталями). "
    "3. 3 ГОТОВЫХ ТРИГГЕРА ДЛЯ ОБЛОЖКИ ИНФОГРАФИКИ (как закрыть эти возражения). "
    "4. ЧЕК-ЛИСТ ДОРАБОТКИ ТОВАРА И ОПИСАНИЯ (3 шага)."
)

BUYER_PROMPT = (
    "Ты — честный и адекватный ИИ-помощник покупателя на Wildberries. Твоя задача — дать объективный совет перед покупкой по свежим отзывам (1-3 звезды за 90 дней) и среднему рейтингу. "
    "ВАЖНО: не пугай покупателя зря! Если средний рейтинг 4.7★ и выше, а негативных отзывов за 90 дней меньше 80 из 1000 — честно скажи, что товар в целом хороший и брать можно, но предупреди о главных нюансах. "
    "Отговаривай от покупки только если рейтинг ниже 4.55★ или в отзывах массовый свежий брак. "
    "Пиши простым языком с эмодзи, без бизнес-терминов и без звёздочек (**). Структура: "
    "1. ВЕРДИКТ ПЕРЕД ПОКУПКОЙ (брать смело / брать с оговорками / лучше пройти мимо — честно и взвешенно). "
    "2. НА ЧТО ЖАЛУЮТСЯ ПОКУПАТЕЛИ (топ-4 реальных минуса из свежих отзывов в % и с примерами). "
    "3. КОМУ ТОВАР ТОЧНО НЕ ПОДОЙДЁТ (3 конкретных пункта). "
    "4. ЧТО ПРОВЕРИТЬ НА ПВЗ ПРИ ПОЛУЧЕНИИ (3 коротких совета)."
)


class ScanForm(StatesGroup):
    waiting_article = State()

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Пробить товар по артикулу")],
            [KeyboardButton(text="📊 Режим: Селлер"), KeyboardButton(text="🛍 Режим: Покупатель")],
            [KeyboardButton(text="💳 Баланс и пополнение"), KeyboardButton(text="🤖 Хочу такого бота себе")],
        ],
        resize_keyboard=True,
    )

def role_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Я селлер / менеджер WB", callback_data="set_role_seller")],
            [InlineKeyboardButton(text="🛍 Я покупатель (проверить товар)", callback_data="set_role_buyer")],
        ]
    )
def buy_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡ 1 разбор — 20 ⭐", callback_data="buy_1")],
            [InlineKeyboardButton(text="🔥 10 разборов — 150 ⭐", callback_data="buy_10")],
            [InlineKeyboardButton(text="🚀 30 разборов — 350 ⭐", callback_data="buy_30")],
            [InlineKeyboardButton(text="💳 Оплатить картой РФ / СБП (написать ассистенту)", callback_data="contact_human")],
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
        return int(next(iter(digits)))
    return None


def candidate_baskets(vol: int) -> list[str]:
    if vol <= 143: guess = 1
    elif vol <= 287: guess = 2
    elif vol <= 431: guess = 3
    elif vol <= 719: guess = 4
    elif vol <= 1007: guess = 5
    elif vol <= 1061: guess = 6
    elif vol <= 1115: guess = 7
    elif vol <= 1169: guess = 8
    elif vol <= 1313: guess = 9
    elif vol <= 1601: guess = 10
    elif vol <= 1655: guess = 11
    elif vol <= 1919: guess = 12
    elif vol <= 2045: guess = 13
    elif vol <= 2189: guess = 14
    elif vol <= 2405: guess = 15
    elif vol <= 2621: guess = 16
    elif vol <= 2837: guess = 17
    elif vol <= 3053: guess = 18
    else:
        guess = 19 + (vol - 3054) // 216

    nums = []
    for delta in (0, -1, 1, -2, 2, -3, 3, -4, 4, -5, 5):
        b = guess + delta
        if b >= 1:
            nums.append(f"{b:02d}")
    return nums


async def fetch_wb_data(article: int) -> dict | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://www.wildberries.ru",
        "Referer": f"https://wildberries.ru{article}/detail.aspx",
    }
    timeout = aiohttp.ClientTimeout(total=15)
    vol = article // 100000
    part = article // 1000

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        name = "Товар WB"
        brand = "Бренд WB"
        root_id = article
        price_str = "по карте WB"
        card_rating = None

        detail_url = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1255987&spp=30&ab_testing=false&nm={article}"
        try:
            async with session.get(detail_url) as d_resp:
                if d_resp.status == 200:
                    d_json = await d_resp.json(content_type=None)
                    products = d_json.get("data", {}).get("products") or d_json.get("products") or []
                    if products:
                        prod = next(iter(products))
                        name = prod.get("name") or name
                        brand = prod.get("brand") or brand
                        root_id = prod.get("root") or prod.get("imt_id") or root_id
                        card_rating = prod.get("reviewRating") or prod.get("rating")
                        sizes = prod.get("sizes") or []
                        if sizes:
                            first_size = next(iter(sizes))
                            p_info = first_size.get("price") or {}
                            total_kop = p_info.get("product") or p_info.get("basic")
                            if total_kop:
                                price_str = f"{int(total_kop // 100):,} ₽".replace(",", " ")
        except Exception:
            pass

        if name == "Товар WB" or root_id == article:
            for basket in candidate_baskets(vol):
                card_cdn = f"https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{article}/info/ru/card.json"
                try:
                    async with session.get(card_cdn) as resp:
                        if resp.status == 200:
                            c_data = await resp.json(content_type=None)
                            name = c_data.get("imt_name", name)
                            brand = c_data.get("selling", {}).get("brand_name", brand)
                            found_imt = c_data.get("imt_id") or c_data.get("data", {}).get("imt_id")
                            if found_imt:
                                root_id = found_imt
                            break
                except Exception:
                    continue

        reviews_list = []
        candidate_ids = list(dict.fromkeys([root_id, article]))
        for cid in candidate_ids:
            for ver in ("v2", "v1"):
                for sub in ("feedbacks1.", "feedbacks2."):
                    fb_url = f"https://{sub}wb.ru/feedbacks/{ver}/{cid}"
                    try:
                        async with session.get(fb_url) as fb_resp:
                            if fb_resp.status == 200:
                                fb_json = await fb_resp.json(content_type=None)
                                raw_fbs = fb_json.get("feedbacks") or []
                                if raw_fbs:
                                    reviews_list = raw_fbs
                                    break
                    except Exception:
                        continue
                if reviews_list:
                    break
            if reviews_list:
                break

        if not reviews_list and name == "Товар WB":
            return None

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=90)
        filtered_reviews = []
        neg_1_2 = 0
        neu_3 = 0
        all_vals = []

        for fb in reviews_list:
            val = fb.get("productValuation", 5)
            all_vals.append(val)
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

        avg_rating = card_rating if card_rating else (round(sum(all_vals) / len(all_vals), 2) if all_vals else "—")

        return {
            "article": article,
            "name": name,
            "brand": brand,
            "price": price_str,
            "rating": avg_rating,
            "total_feedbacks": len(reviews_list),
            "analyzed_count": len(filtered_reviews),
            "neg_1_2": neg_1_2,
            "neu_3": neu_3,
            "reviews_text": "\n".join(filtered_reviews),
        }


def get_user_scans(user_id: int) -> int:
    cursor.execute("SELECT scans_left FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    return next(iter(row), 0) if row else 0


def get_user_role(user_id: int) -> str:
    cursor.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    val = next(iter(row), "seller") if row else "seller"
    return val if val in ("seller", "buyer") else "seller"

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, scans_left, role) VALUES (?, ?, 2, 'seller')", (message.from_user.id, message.from_user.username))
    conn.commit()
    if message.from_user.id not in ADMIN_IDS:
        try:
            await bot.send_message(1273001338, f"🆕 Новый юзер: @{message.from_user.username} (ID: {message.from_user.id})")
        except Exception:
            pass
    scans = get_user_scans(message.from_user.id)
    await message.answer(
        "🤖 На связи ИИ-разведчик Wildberries!\n\n"
        "За 15 секунд вскрываю свежие отзывы (1–3★ за 90 дней) и показываю реальную картину о любом товаре.\n"
        f"🎁 На балансе: {scans} бесплатных проверки.\n\n"
        "Выбери, кто ты, чтобы я настроил формат отчёта под тебя:",
        reply_markup=role_keyboard(),
    )
    await message.answer("Или сразу жми кнопку в меню ниже и кидай артикул WB 👇", reply_markup=main_menu())

@dp.callback_query(F.data.startswith("set_role_"))
async def cb_set_role(callback: types.CallbackQuery):
    await callback.answer()
    role = "buyer" if callback.data.endswith("buyer") else "seller"
    cursor.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, callback.from_user.id))
    conn.commit()
    label = "🛍 Покупатель (честная проверка перед покупкой)" if role == "buyer" else "📊 Селлер / менеджер (анализ конкурентов и триггеры)"
    await callback.message.answer(f"✅ Режим переключён: {label}.\nТеперь кидай артикул или ссылку на товар WB!", reply_markup=main_menu())

@dp.message(F.text.in_(["📊 Режим: Селлер", "🛍 Режим: Покупатель"]))
async def switch_role_btn(message: types.Message):
    role = "buyer" if "Покупатель" in message.text else "seller"
    cursor.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, message.from_user.id))
    conn.commit()
    label = "🛍 Покупатель (честная проверка перед покупкой)" if role == "buyer" else "📊 Селлер / менеджер (анализ конкурентов)"
    await message.answer(f"✅ Включён режим: {label}.\nКидай артикул или ссылку на товар WB!", reply_markup=main_menu())

@dp.message(F.text.in_(["🔍 Пробить товар по артикулу", "🔍 Пробить конкурента по артикулу"]))
async def ask_article(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS and get_user_scans(message.from_user.id) <= 0:
        await message.answer("Бесплатный лимит исчерпан. Выбирай пакет ниже:", reply_markup=buy_keyboard())
        return
    await message.answer("Кидай артикул товара WB (например, 207509051) или прямую ссылку на карточку:")
    await state.set_state(ScanForm.waiting_article)


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
    pack = callback.data.replace("buy_", "")
    tariffs = {
        "1": ("1 разбор конкурента WB", "Мгновенное пополнение на 1 глубокий скан карточки", 20, "pack_1"),
        "10": ("Пакет «На нишу» (10 разборов)", "10 глубоких сканов карточек конкурентов WB", 150, "pack_10"),
        "30": ("Пакет «Профи» (30 разборов)", "30 глубоких сканов карточек конкурентов WB", 350, "pack_30"),
    }
    if pack not in tariffs:
        return

    title, desc, stars_amount, payload = tariffs[pack]
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
async def successful_payment(message: types.Message, state: FSMContext):
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
        f"• Цена: {wb_info['price']} | Средний рейтинг: {wb_info['rating']}★\n"
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
        user_role = get_user_role(message.from_user.id)
        active_prompt = BUYER_PROMPT if user_role == "buyer" else SELLER_PROMPT
        response = await ai_client.chat.completions.create(
            model="deepseek/deepseek-v4.1-flash-1017-alt-lowcost",
            messages=[
                {"role": "system", "content": active_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
        )

        first_choice = next(iter(response.choices))
        analysis = first_choice.message.content

        if message.from_user.id not in ADMIN_IDS:
            cursor.execute("UPDATE users SET scans_left = scans_left - 1 WHERE user_id = ?", (message.from_user.id,))
            conn.commit()
            try:
                await bot.send_message(1273001338, f"🔍 Разбор WB: {article} от @{message.from_user.username}")
            except Exception:
                pass

        await wait_msg.delete()
        full_report = f"{passport}\n\n{analysis}"

        if len(full_report) <= 4000:
            await message.answer(full_report, reply_markup=main_menu())
        else:
            await message.answer(passport)
            await message.answer(analysis[:4000], reply_markup=main_menu())

    except Exception as e:
        await wait_msg.edit_text(f"⚠️ Сбой нейроядра при анализе ({e}). Попытка не списана, попробуй через минуту.")

@dp.message(F.text == "/stats")
async def admin_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = next(iter(cursor.fetchone()), 0)
    cursor.execute("SELECT COUNT(*) FROM users WHERE scans_left < 2")
    active_users = next(iter(cursor.fetchone()), 0)
    cursor.execute("SELECT COUNT(*) FROM users WHERE scans_left > 2")
    paid_users = next(iter(cursor.fetchone()), 0)
    await message.answer(
        f"📊 СТАТИСТИКА БОТА:\n"
        f"• Всего запустили /start: {total_users}\n"
        f"• Сделали хотя бы 1 разбор: {active_users}\n"
        f"• Пополнили баланс: {paid_users}"
    )

async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
