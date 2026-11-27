import asyncio
import os
import re
import sqlite3
from datetime import datetime, timezone, timedelta
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, StateFilter
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
    ReplyKeyboardRemove,

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
cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, scans_left INTEGER DEFAULT 2, role TEXT DEFAULT NULL)")
conn.commit()


SELLER_PROMPT = (
    "Ты — объективный и точный ИИ-аналитик Wildberries. Оцени товар по свежим отзывам (1-3 звезды за 90 дней) с учётом среднего рейтинга карточки. "
    "ВАЖНО: если средний рейтинг 4.7★ и выше, а жалоб 1-3★ меньше 80 шт. из 1000 — не драматизируй, признай, что товар сильный, а негатив это рабочий шум и точки роста. "
    "Если рейтинг ниже 4.55★ или жалоб больше 100 шт. — жёстко вскрывай системный брак. "
    "Если реальный рейтинг по отзывам ниже витринного рейтинга WB (или ниже 4.6★), в первом пункте обязательно обрати на это внимание: объясни, что высокий балл на витрине держится за счёт старой базы или быстрых пятёрок без текста, а живые отзывы показывают реальное падение качества. "
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
    "Если реальный рейтинг по отзывам ниже витринного рейтинга WB (или ниже 4.6★), в первом пункте обязательно обрати на это внимание: объясни, что высокий балл на витрине держится за счёт старой базы или быстрых пятёрок без текста, а живые отзывы показывают реальное падение качества. "
    "Пиши простым языком с эмодзи, без бизнес-терминов и без звёздочек (**). Структура: "
    "1. ВЕРДИКТ ПЕРЕД ПОКУПКОЙ (брать смело / брать с оговорками / лучше пройти мимо — честно и взвешенно). "
    "2. НА ЧТО ЖАЛУЮТСЯ ПОКУПАТЕЛИ (топ-4 реальных минуса из свежих отзывов в % и с примерами). "
    "3. КОМУ ТОВАР ТОЧНО НЕ ПОДОЙДЁТ (3 конкретных пункта). "
    "4. ЧТО ПРОВЕРИТЬ НА ПВЗ ПРИ ПОЛУЧЕНИИ (3 коротких совета)."
)

BATTLE_SELLER_PROMPT = (
    "Ты — жёсткий и объективный ИИ-аналитик Wildberries. Тебе дали данные и свежие отзывы (1-3★ за 90 дней) по ДВУМ товарам: Товар №1 (наш) и Товар №2 (конкурент). "
    "Сравни их в лоб. Пиши живым русским языком с эмодзи, без звёздочек (**). Структура: "
    "1. СЧЁТ ПО ФАКТАМ (кто объективно сильнее по рейтингу, цене и доле свежего негатива). "
    "2. ПРОБОИНЫ КОНКУРЕНТА (топ-3 слабости Товара №2 из свежих жалоб, на которых можно забрать его клиентов). "
    "3. НАШИ УЯЗВИМОСТИ (честно: где Товар №1 проигрывает Товару №2 и что срочно чинить). "
    "4. ПЛАН ПОБЕДЫ (3 конкретных шага по инфографике, цене или доработке товара, чтобы перебить конкурента)."
)

BATTLE_BUYER_PROMPT = (
    "Ты — честный ИИ-помощник покупателя на Wildberries. Тебе дали два товара-конкурента (Товар №1 и Товар №2) и их свежие отзывы (1-3★ за 90 дней). "
    "Помоги выбрать лучший вариант без воды. Пиши просто, с эмодзи, без бизнес-терминов и без звёздочек (**). Структура: "
    "1. ВЕРДИКТ: КОГО БРАТЬ (Товар №1 или Товар №2, и при каком условии выбрать второй). "
    "2. МИНУСЫ ТОВАРА №1 (главные свежие жалобы в % и с примерами). "
    "3. МИНУСЫ ТОВАРА №2 (главные свежие жалобы в % и с примерами). "
    "4. НА ЧТО СМОТРЕТЬ НА ПВЗ ПРИ ВЫБОРЕ."
)

TOP_SELLER_PROMPT = (
    "Ты — жёсткий и объективный ИИ-аналитик Wildberries. Тебе дали данные и свежие отзывы (1-3★ за 90 дней) по НЕСКОЛЬКИМ товарам (от 3 до 5 шт.) из одной ниши. "
    "Сделай рейтинг карточек и разбор ниши. Пиши живым русским языком с эмодзи, без звёздочек (**). Структура: "
    "1. РЕЙТИНГ КАРТОЧЕК (от 1-го до последнего места по качеству, рейтингу, цене и чистоте свежих отзывов — с коротким вердиктом по каждому). "
    "2. ОБЩИЕ БОЛИ НИШИ (топ-3 системные проблемы, на которые жалуются клиенты у всех конкурентов). "
    "3. КАК ЗАБРАТЬ РЫНОК (3 конкретных шага по товару и инфографике, чтобы обойти лидера рейтинга)."
)

TOP_BUYER_PROMPT = (
    "Ты — честный ИИ-помощник покупателя на Wildberries. Покупатель выбирает лучший товар из нескольких вариантов (от 3 до 5 шт.) по их цене, рейтингу и свежим отзывам (1-3★ за 90 дней). "
    "Помоги сделать безошибочный выбор без воды. Пиши просто, с эмодзи, без бизнес-терминов и без звёздочек (**). Структура: "
    "1. ПЬЕДЕСТАЛ ПОБЕДИТЕЛЕЙ (расставь все товары от 1-го места до последнего: кого брать смело, кто запасной вариант, а кого точно отмести). "
    "2. ГЛАВНЫЕ МИНУСЫ КАЖДОГО ВАРИАНТА (коротко по пунктам из реальных жалоб). "
    "3. ЧТО ПРОВЕРИТЬ НА ПВЗ ПРИ ПОЛУЧЕНИИ."
)


class ScanForm(StatesGroup):
    waiting_article = State()
    waiting_battle = State()
    waiting_feedback = State()


def seller_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Пробить товар по артикулу"), KeyboardButton(text="⚖️ Сравнить товары (2–5 шт.)")],
            [KeyboardButton(text="💳 Баланс и пополнение"), KeyboardButton(text="🤖 Хочу такого бота себе")],
            [KeyboardButton(text="🔄 Сменить режим (сейчас: Селлер)"), KeyboardButton(text="💬 Отзывы и предложения")],
        ],
        resize_keyboard=True,
    )


def buyer_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Пробить товар по артикулу"), KeyboardButton(text="⚖️ Сравнить товары (2–5 шт.)")],
            [KeyboardButton(text="💳 Баланс и пополнение"), KeyboardButton(text="🔄 Сменить режим (сейчас: Покупатель)")],
            [KeyboardButton(text="💬 Отзывы и предложения")],
        ],
        resize_keyboard=True,
    )



def get_menu_for_role(role: str | None):
    return buyer_menu() if role == "buyer" else seller_menu()


def role_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Я селлер / менеджер WB", callback_data="set_role_seller")],
            [InlineKeyboardButton(text="🛍 Я покупатель (выбираю товар)", callback_data="set_role_buyer")],
        ]
    )


def buy_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡ 1 разбор — 20 ⭐", callback_data="buy_1")],
            [InlineKeyboardButton(text="🔥 10 разборов — 150 ⭐", callback_data="buy_10")],
            [InlineKeyboardButton(text="🚀 30 разборов — 350 ⭐", callback_data="buy_30")],
            [InlineKeyboardButton(text="💳 Оплатить картой РФ / СБП (скоро)", callback_data="contact_human")],
        ]
    )


def confirm_battle_keyboard(articles: list[int]):
    joined = "-".join(str(a) for a in articles[:5])
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚖️ Всё равно сравнить", callback_data=f"force_battle_{joined}")],
            [InlineKeyboardButton(text="🔄 Ввести другие артикулы", callback_data="retry_battle")],
        ]
    )


def extract_all_articles(text: str) -> list[int]:
    if not text:
        return []
    found: list[int] = []

    def strip_url(m):
        found.append(int(m.group(1)))
        return " "

    rem = re.sub(r"catalog/(\d+)", strip_url, text)
    rem = re.sub(r"\b(\d{1,3})[ \t]+(\d{3})[ \t]+(\d{3,4})\b", r"\1\2\3", rem)
    for d in re.findall(r"\b\d{6,12}\b", rem):
        found.append(int(d))

    return list(dict.fromkeys(found))


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
    elif vol <= 3269: guess = 19
    elif vol <= 3485: guess = 20
    elif vol <= 3701: guess = 21
    elif vol <= 3917: guess = 22
    elif vol <= 4133: guess = 23
    elif vol <= 4349: guess = 24
    elif vol <= 4565: guess = 25
    else:
        return [f"{b:02d}" for b in range(26, 70)]

    nums = []
    for delta in range(-4, 5):
        b = guess + delta
        if 1 <= b <= 30:
            nums.append(f"{b:02d}")
    return nums



async def fetch_wb_data(article: int) -> dict | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://wildberries.ru",
        "Referer": f"https://wildberries.ru/catalog/{article}/detail.aspx",
    }
    vol = article // 100000
    part = article // 1000

    async def get_json(url: str) -> tuple[int, dict | None]:
        for attempt in range(2):
            try:
                from curl_cffi.requests import AsyncSession
                async with AsyncSession(impersonate="chrome", timeout=15) as s:
                    r = await s.get(url, headers=headers)
                    if r.status_code == 429 and attempt == 0:
                        await asyncio.sleep(0.4)
                        continue
                    if r.status_code == 200:
                        return r.status_code, r.json()
                    return r.status_code, None
            except ImportError:
                pass
            except Exception:
                return 0, None

            try:
                async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as session:
                    async with session.get(url) as resp:
                        if resp.status == 429 and attempt == 0:
                            await asyncio.sleep(0.4)
                            continue
                        if resp.status == 200:
                            return resp.status, await resp.json(content_type=None)
                        return resp.status, None
            except Exception:
                return 0, None
        return 0, None


    name = "Товар WB"
    brand = "Бренд WB"
    root_id = article
    price_str = "по карте WB"
    card_rating = None
    category = ""

    for api_ver in ("v4", "v2"):
        detail_url = f"https://wb.ru{api_ver}/detail?appType=1&curr=rub&dest=-1255987&spp=30&ab_testing=false&nm={article}"
        st, d_json = await get_json(detail_url)
        if st == 200 and d_json:
            products = d_json.get("data", {}).get("products") or d_json.get("products") or []
            if products:
                prod = next(iter(products))
                name = prod.get("name") or name
                brand = prod.get("brand") or brand
                root_id = prod.get("root") or prod.get("imt_id") or root_id
                card_rating = prod.get("reviewRating") or prod.get("rating")
                category = prod.get("entity") or category
                sizes = prod.get("sizes") or []
                if sizes:
                    first_size = next(iter(sizes))
                    p_info = first_size.get("price") or {}
                    total_kop = p_info.get("product") or p_info.get("basic")
                    if total_kop:
                        price_str = f"{int(total_kop // 100):,} ₽".replace(",", " ")
                break

    if name == "Товар WB" or root_id == article or price_str == "по карте WB":
        async def check_basket(b: str):
            base_info = f"https://basket-{b}.wbbasket.ru/vol{vol}/part{part}/{article}/info"
            st_c, data_c = await get_json(f"{base_info}/ru/card.json")
            if st_c == 200 and data_c:
                _, hist_data = await get_json(f"{base_info}/price-history.json")
                return data_c, hist_data
            return None

        results = await asyncio.gather(*(check_basket(b) for b in candidate_baskets(vol)))
        found_pair = next((r for r in results if r), None)
        if found_pair:
            c_data, hist_data = found_pair
            name = c_data.get("imt_name", name)
            brand = c_data.get("selling", {}).get("brand_name", brand)
            category = c_data.get("subj_name") or category
            found_imt = c_data.get("imt_id") or c_data.get("data", {}).get("imt_id")
            if found_imt:
                root_id = found_imt
        if price_str == "по карте WB":
            host = "search" + ".wb.ru"
            search_url = f"https://{host}/exactmatch/ru/common/v9/search?appType=1&curr=rub&dest=-1257786&query={article}"
            st_s, data_s = await get_json(search_url)
            if st_s == 200 and data_s:
                s_prods = data_s.get("data", {}).get("products") or []
                matched = next((p for p in s_prods if p.get("id") == article), None)
                if matched:
                    if not card_rating and matched.get("reviewRating"):
                        card_rating = float(matched["reviewRating"])
                    for sz in matched.get("sizes", []):
                        pr = (sz.get("price") or {}).get("product") or (sz.get("price") or {}).get("basic")
                        if pr:
                            price_str = f"~{int(pr // 100):,} ₽".replace(",", " ")
                            break
        if price_str == "по карте WB" and isinstance(hist_data, list) and hist_data:
            last_rec = hist_data[-1]
            rub_kop = (last_rec.get("price") or {}).get("RUB")
            if rub_kop and rub_kop >= 10000:
                price_str = f"~{int(rub_kop // 100):,} ₽".replace(",", " ")




    reviews_list = []
    candidate_ids = list(dict.fromkeys([root_id, article]))
    for cid in candidate_ids:
        for ver in ("v2", "v1"):
            for sub in ("feedbacks1.", "feedbacks2.", "feedbacks3.", "feedbacks4.", "feedbacks5.", "feedbacks."):
                fb_url = f"https://{sub}wb.ru/feedbacks/{ver}/{cid}"
                st, fb_json = await get_json(fb_url)
                if st == 200 and fb_json:
                    raw_fbs = fb_json.get("feedbacks") or []
                    if raw_fbs:
                        reviews_list = raw_fbs
                        break
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
        "category": category.strip().lower(),
        "price": price_str,
        "rating": avg_rating,
        "card_rating": f"{card_rating:.2f}" if card_rating else "",
        "total_feedbacks": len(reviews_list),
        "analyzed_count": len(filtered_reviews),
        "neg_1_2": neg_1_2,
        "neu_3": neu_3,
        "reviews_text": "\n".join(filtered_reviews),
        "debug": f"root={root_id} | detail_st={st}",
    }



def get_user_scans(user_id: int) -> int:
    cursor.execute("SELECT scans_left FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    return next(iter(row), 0) if row else 0


def get_user_role(user_id: int) -> str | None:
    cursor.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    val = next(iter(row), None) if row else None
    return val if val in ("seller", "buyer") else None


@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, scans_left, role) VALUES (?, ?, 2, NULL)",
        (message.from_user.id, message.from_user.username),
    )
    conn.commit()
    if message.from_user.id not in ADMIN_IDS:
        try:
            await bot.send_message(1273001338, f"🆕 Новый юзер: @{message.from_user.username} (ID: {message.from_user.id})")
        except Exception:
            pass
    scans = get_user_scans(message.from_user.id)
    temp_msg = await message.answer("⏳", reply_markup=ReplyKeyboardRemove())
    await temp_msg.delete()
    await message.answer(
        "🤖 На связи ИИ-разведчик Wildberries!\n\n"
        "За 15 секунд анализирую свежие отзывы (1–3★ за 90 дней), реальные цены и скрытые минусы товаров:\n"
        "• кинь 1 артикул — сделаю полный разбор карточки;\n"
        "• кинь 2–5 артикулов — сравню в лоб и выберу лучший вариант.\n\n"
        f"🎁 На балансе: {scans} бесплатных проверки.\n\n"
        "Чтобы я открыл нужный кабинет и настроил формат отчётов, выбери свою роль 👇",
        reply_markup=role_keyboard(),
    )


@dp.callback_query(F.data.startswith("set_role_"))
async def cb_set_role(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    role = "buyer" if callback.data.endswith("buyer") else "seller"
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, scans_left, role) VALUES (?, ?, 2, ?)",
        (callback.from_user.id, callback.from_user.username, role),
    )
    cursor.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, callback.from_user.id))
    conn.commit()

    if role == "buyer":
        text = (
            "✅ Открыт кабинет Покупателя!\n\n"
            "Теперь просто отправь в чат от 1 до 5 артикулов (или ссылок WB) — даже без нажатия кнопок:\n"
            "• 1 товар — честно скажу, стоит ли брать и что проверить на ПВЗ;\n"
            "• 2–5 товаров — расставлю по местам и подскажу лучший выбор."
        )
    else:
        text = (
            "✅ Открыт кабинет Селлера / менеджера WB!\n\n"
            "Теперь просто отправь в чат от 1 до 5 артикулов (или ссылок WB) — даже без нажатия кнопок:\n"
            "• 1 товар — вскрою боли за 90 дней и дам триггеры для инфографики;\n"
            "• 2–5 товаров — сравню с конкурентами и дам план победы в нише."
        )
    await callback.message.answer(text, reply_markup=get_menu_for_role(role))


@dp.message(F.text.startswith("🔄 Сменить режим"))
async def switch_role_btn(message: types.Message, state: FSMContext):
    await state.clear()
    current = get_user_role(message.from_user.id)
    new_role = "buyer" if current == "seller" else "seller"
    cursor.execute("UPDATE users SET role = ? WHERE user_id = ?", (new_role, message.from_user.id))
    conn.commit()
    label = "🛍 Покупатель (честная проверка перед покупкой)" if new_role == "buyer" else "📊 Селлер / менеджер (анализ конкурентов и триггеры)"
    await message.answer(
        f"✅ Режим переключён: {label}.\nОтправляй от 1 до 5 артикулов или ссылок на товары WB!",
        reply_markup=get_menu_for_role(new_role),
    )


@dp.message(F.text.in_(["🔍 Пробить товар по артикулу", "🔍 Пробить конкурента по артикулу"]))
async def ask_article(message: types.Message, state: FSMContext):
    role = get_user_role(message.from_user.id)
    if not role:
        await message.answer("Сначала выбери свою роль, чтобы я настроил формат отчётов:", reply_markup=role_keyboard())
        return
    if message.from_user.id not in ADMIN_IDS and get_user_scans(message.from_user.id) <= 0:
        await message.answer("Бесплатный лимит исчерпан. Выбирай пакет ниже:", reply_markup=buy_keyboard())
        return
    await message.answer("Кидай артикул товара WB (например, 283675449) или прямую ссылку на карточку:")
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
        f"🤖 Нужен такой же ИИ-бот под ваш магазин на WB/Ozon или под конкретную задачу?\n\n"
        f"Напишите нашему менеджеру: {ASSISTANT_USERNAME} — обсудим логику, сроки и запуск под ключ."
    )


@dp.callback_query(F.data == "contact_human")
async def cb_contact_human(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "💳 Автоматическая оплата картой РФ и через СБП (ЮKassa) сейчас на финальном подключении!\n\n"
        "А пока пополнить баланс за 5 секунд можно через Telegram Stars кнопками выше 👆"
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


async def run_single_analysis(message: types.Message, user_id: int, username: str | None, article: int, role: str):
    if user_id not in ADMIN_IDS and get_user_scans(user_id) <= 0:
        await message.answer(
            "На балансе 0 сканирований. Пополни энергию серверов, чтобы продолжить разведку:",
            reply_markup=buy_keyboard(),
        )
        return

    wait_msg = await message.answer(f"🛰 Подключаюсь к WB, выгружаю карточку {article} и фильтрую свежий негатив за 90 дней...")

    try:
        wb_info = await fetch_wb_data(article)
    except Exception as e:
        await wait_msg.edit_text(f"⚠️ Ошибка при запросе к WB: {e}")
        return

    if not wb_info:
        await wait_msg.edit_text("❌ Не удалось достать карточку с таким артикулом. Проверь цифры и попробуй ещё раз.")
        return

    rating_line = (
        f"• Витрина WB: {wb_info['card_rating']}★ | Реальный (по отзывам): {wb_info['rating']}★\n"
        if wb_info.get("card_rating") and wb_info["card_rating"] != wb_info["rating"]
        else f"• Реальный рейтинг (по отзывам): {wb_info['rating']}★\n"
    )

    passport = (
        f"📦 ПАСПОРТ ОБЪЕКТА (WB: {wb_info['article']})\n"
        f"• Товар: {wb_info['name']} ({wb_info['brand']})\n"
        f"• Цена: {wb_info['price']}\n"
        f"{rating_line}"
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
        active_prompt = BUYER_PROMPT if role == "buyer" else SELLER_PROMPT
        response = await ai_client.chat.completions.create(
            model="deepseek/deepseek-v4.1-flash-1017-alt-lowcost",
            messages=[
                {"role": "system", "content": active_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
        )

        analysis = next(iter(response.choices)).message.content

        if user_id not in ADMIN_IDS:
            cursor.execute("UPDATE users SET scans_left = scans_left - 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            try:
                await bot.send_message(1273001338, f"🔍 Разбор WB: {article} от @{username}")
            except Exception:
                pass

        await wait_msg.delete()
        full_report = f"{passport}\n\n{analysis}"
        menu = get_menu_for_role(role)

        if len(full_report) <= 4000:
            await message.answer(full_report, reply_markup=menu)
        else:
            await message.answer(passport)
            await message.answer(analysis[:4000], reply_markup=menu)

    except Exception as e:
        await wait_msg.edit_text(f"⚠️ Сбой нейроядра при анализе ({e}). Попытка не списана, попробуй через минуту.")


@dp.message(F.text.in_(["⚔️ Битва артикулов", "⚖️ Сравнить товары (2–5 шт.)"]))
async def ask_battle_articles(message: types.Message, state: FSMContext):
    role = get_user_role(message.from_user.id)
    if not role:
        await message.answer("Сначала выбери свою роль, чтобы я настроил формат отчётов:", reply_markup=role_keyboard())
        return
    if message.from_user.id not in ADMIN_IDS and get_user_scans(message.from_user.id) < 2:
        await message.answer(
            "⚖️ Для сравнения нужно минимум 2 проверки на балансе (по 1 проверке на каждый артикул).\n"
            "Пополни баланс ниже:",
            reply_markup=buy_keyboard(),
        )
        return
    await message.answer(
        "⚖️ Режим сравнения и выбора лучшего включён!\n\n"
        "Пришли от 2 до 5 артикулов (или ссылок WB) через пробел, запятую или с новой строки:\n"
        "• 2 товара — сделаю глубокое сравнение 1 на 1;\n"
        "• 3–5 товаров — расставлю по местам и помогу выбрать лучший вариант!"
    )
    await state.set_state(ScanForm.waiting_battle)


async def run_battle_analysis(message: types.Message, user_id: int, username: str | None, articles: list[int], role: str, skip_cat_check: bool = False, state: FSMContext | None = None):
    articles = articles[:5]
    cost = len(articles)

    if user_id not in ADMIN_IDS and get_user_scans(user_id) < cost:
        await message.answer(
            f"Для анализа {cost} товаров нужно {cost} проверок на балансе (сейчас: {get_user_scans(user_id)}):",
            reply_markup=buy_keyboard(),
        )
        return

    arts_str = ", ".join(str(a) for a in articles)
    wait_msg = await message.answer(f"⚖️ Загружаю {cost} карточек ({arts_str}) и фильтрую свежие отзывы за 90 дней...")

    try:
        wb_items = await asyncio.gather(*(fetch_wb_data(a) for a in articles))
    except Exception as e:
        await wait_msg.edit_text(f"⚠️ Ошибка при запросе к WB: {e}")
        return

    missing = [str(articles[i]) for i, item in enumerate(wb_items) if not item]
    if missing:
        await wait_msg.edit_text(
            f"❌ Не удалось достать карточки: {', '.join(missing)}. Проверь артикулы и попробуй снова (попытки не списаны)."
        )
        return

    categories = {item.get("category", "") for item in wb_items if item.get("category", "")}
    if not skip_cat_check and len(categories) > 1:
        if state:
            await state.update_data(warned_pair=sorted(articles))
            await state.set_state(ScanForm.waiting_battle)
        lines = [f"{i+1}️⃣ {it['name']} — «{it.get('category') or 'без категории'}»" for i, it in enumerate(wb_items)]
        await wait_msg.edit_text(
            "⚠️ Похоже, в списке товары из разных категорий:\n"
            + "\n".join(lines)
            + f"\n\nВсё равно сравниваем их (спишется {cost} проверки) или заменишь список?",
            reply_markup=confirm_battle_keyboard(articles),
        )
        return

    header_title = "⚖️ СРАВНЕНИЕ ДВУХ ТОВАРОВ WB" if cost == 2 else f"🏆 ВЫБОР ЛУЧШЕГО ИЗ {cost} ТОВАРОВ WB"
    passport_blocks = [header_title]
    reviews_blocks = []
    max_rev_lines = 150 if cost == 2 else 65

    for idx, wb in enumerate(wb_items, start=1):
        passport_blocks.append(
            f"{idx}️⃣ ТОВАР №{idx} ({wb['article']}): {wb['name']} ({wb['brand']})\n"
            f"• Цена: {wb['price']} | Рейтинг: {wb['rating']}★ | Жалоб 1–3★ (90 дн.): {wb['analyzed_count']} из {wb['total_feedbacks']}"
        )
        trimmed_rev = "\n".join((wb["reviews_text"] or "Свежих жалоб нет").splitlines()[:max_rev_lines])
        reviews_blocks.append(f"--- ОТЗЫВЫ ТОВАРА №{idx} ({wb['name']}) ---\n{trimmed_rev}")

    passport = "\n\n".join(passport_blocks)
    user_prompt = f"{passport}\n\n" + "\n\n".join(reviews_blocks)

    try:
        if cost == 2:
            active_prompt = BATTLE_BUYER_PROMPT if role == "buyer" else BATTLE_SELLER_PROMPT
        else:
            active_prompt = TOP_BUYER_PROMPT if role == "buyer" else TOP_SELLER_PROMPT

        response = await ai_client.chat.completions.create(
            model="deepseek/deepseek-v4.1-flash-1017-alt-lowcost",
            messages=[
                {"role": "system", "content": active_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
        )

        analysis = next(iter(response.choices)).message.content

        if user_id not in ADMIN_IDS:
            cursor.execute("UPDATE users SET scans_left = scans_left - ? WHERE user_id = ?", (cost, user_id))
            conn.commit()
            try:
                await bot.send_message(1273001338, f"⚖️ Сравнение ({cost} шт.): {arts_str} от @{username}")
            except Exception:
                pass

        await wait_msg.delete()
        full_report = f"{passport}\n\n{analysis}"
        menu = get_menu_for_role(role)
        if len(full_report) <= 4000:
            await message.answer(full_report, reply_markup=menu)
        else:
            await message.answer(passport)
            await message.answer(analysis[:4000], reply_markup=menu)

    except Exception as e:
        await wait_msg.edit_text(f"⚠️ Сбой нейроядра при сравнении ({e}). Попытки не списаны.")


@dp.callback_query(F.data.startswith("force_battle_"))
async def cb_force_battle(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    role = get_user_role(callback.from_user.id) or "seller"
    raw_ids = callback.data.replace("force_battle_", "").split("-")
    articles = [int(x) for x in raw_ids if x.isdigit()]
    await callback.message.delete()
    await run_battle_analysis(
        message=callback.message,
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        articles=articles,
        role=role,
        skip_cat_check=True,
        state=state,
    )


@dp.callback_query(F.data == "retry_battle")
async def cb_retry_battle(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(warned_pair=None)
    await state.set_state(ScanForm.waiting_battle)
    await callback.message.edit_text("🔄 Ок, попытки не списаны! Пришли от 2 до 5 новых артикулов WB через пробел или с новой строки:")

@dp.message(F.text == "💬 Отзывы и предложения")
async def ask_feedback(message: types.Message, state: FSMContext):
    await state.set_state(ScanForm.waiting_feedback)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_feedback")]]
    )
    await message.answer(
        "💬 Напиши одним сообщением, если нашёл ошибку или хочешь предложить идею для улучшения бота.\n\n"
        "Я сразу передам это команде 👇",
        reply_markup=cancel_kb,
    )


@dp.callback_query(F.data == "cancel_feedback")
async def cb_cancel_feedback(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text("👌 Отменено. Можешь продолжать работу с артикулами!")


@dp.message(ScanForm.waiting_feedback)
async def process_feedback(message: types.Message, state: FSMContext):
    await state.clear()
    role = get_user_role(message.from_user.id)
    user_text = message.text or message.caption or "[медиафайл без текста]"
    header = (
        f"📩 ОТЗЫВ / ИДЕЯ В БОТЕ WB\n"
        f"От: @{message.from_user.username or 'без_ника'} (ID: {message.from_user.id})\n"
        f"Роль: {role or 'не выбрана'}\n\n"
    )
    for recipient_id in set((1273001338, *ADMIN_IDS)):
        try:
            await bot.send_message(recipient_id, header + user_text)
        except Exception:
            pass

    await message.answer(
        "✅ Спасибо! Сообщение передано. Если нужно — обязательно добавим это в ближайшем обновлении!",
        reply_markup=get_menu_for_role(role),
    )


@dp.message(F.text)
async def smart_articles_router(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    role = get_user_role(message.from_user.id)

    if not role:
        await message.answer(
            "Сначала выбери свою роль, чтобы я открыл нужный кабинет и настроил формат отчётов 👇",
            reply_markup=role_keyboard(),
        )
        return

    articles = extract_all_articles(message.text)

    if not articles:
        if current_state == ScanForm.waiting_battle:
            await message.answer("Нужно минимум ДВА разных артикула WB (от 2 до 5 шт.) через пробел или с новой строки. Попробуй ещё раз:")
        elif current_state == ScanForm.waiting_article:
            await message.answer("Не вижу нормального артикула. Пришли цифры (например, 283675449) или ссылку на товар WB:")
        else:
            await message.answer(
                "Отправь от 1 до 5 артикулов (или ссылок на товары WB) через пробел или с новой строки — я сразу сделаю разбор!",
                reply_markup=get_menu_for_role(role),
            )
        return

    if len(articles) == 1:
        if current_state == ScanForm.waiting_battle:
            await message.answer("Ты в режиме сравнения — нужно минимум ДВА разных артикула WB (от 2 до 5 шт.). Пришли второй артикул или нажми кнопку в меню:")
            return
        await state.clear()
        await run_single_analysis(
            message=message,
            user_id=message.from_user.id,
            username=message.from_user.username,
            article=articles[0],
            role=role,
        )
        return

    if len(articles) > 5:
        await message.answer("ℹ️ Взял первые 5 артикулов из списка (максимум за один раз).")
        articles = articles[:5]

    data = await state.get_data()
    already_warned = data.get("warned_pair") == sorted(articles)
    await state.clear()

    await run_battle_analysis(
        message=message,
        user_id=message.from_user.id,
        username=message.from_user.username,
        articles=articles,
        role=role,
        skip_cat_check=already_warned,
        state=state,
    )


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
