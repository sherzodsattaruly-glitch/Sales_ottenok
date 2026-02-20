"""AI engine — GPT-4o с function calling. Вся логика продаж в промпте."""

import json
import logging
from openai import AsyncOpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL
import db
import services
from greenapi_client import send_text, send_photos

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# ── Tools (function calling) ─────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_stock",
            "description": "Проверить наличие товара в каталоге. Вызови перед оформлением заказа.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "description": "Название товара"},
                    "size": {"type": "string", "description": "Размер (если известен)"},
                    "color": {"type": "string", "description": "Цвет (если известен)"},
                },
                "required": ["product"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_photos",
            "description": "Найти и отправить фото товара клиенту. Вызови когда клиент спрашивает 'покажите', 'какие есть', или при первом упоминании товара.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "description": "Название товара"},
                    "color": {"type": "string", "description": "Цвет (если указан)"},
                },
                "required": ["product"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_order",
            "description": "Оформить заказ. Вызови ТОЛЬКО когда собраны ВСЕ данные: товар, размер (для обуви), цвет, город, адрес. Клиент должен подтвердить.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string"},
                    "size": {"type": "string", "description": "Размер (пустая строка для сумок)"},
                    "color": {"type": "string"},
                    "city": {"type": "string"},
                    "address": {"type": "string", "description": "Адрес доставки"},
                },
                "required": ["product", "city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "handoff_to_manager",
            "description": "Передать диалог менеджеру. Вызови если: сложный случай, жалоба, возврат, или не можешь помочь.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Причина передачи"},
                },
                "required": ["reason"],
            },
        },
    },
]

# ── System Prompt ────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """Ты — Алина, менеджер по продажам магазина "Ottenok" (женская обувь и аксессуары люкс-класса). Ты пишешь в WhatsApp.

ФОРМАТ ОТВЕТА:
- Каждое отдельное сообщение разделяй символами |||
- Одно сообщение = 1-3 предложения максимум
- Обычно отправляй 2-4 сообщения за раз

СТИЛЬ ОБЩЕНИЯ:
- Коротко и по делу, как в мессенджере
- НЕ пиши длинные полотна
- Эмодзи умеренно: ✨ ☺️ ✅ ❤️ 😊 🤍 (1-2 на весь ответ)
- НЕ используй маркдаун, жирный текст, списки, заголовки
- Обращайся на "вы", но без официоза
- Только русский язык

О МАГАЗИНЕ:
- Бутик тихого люкса в Алматы — не байеры, есть физический магазин
- Изделия люкс-уровня с тех же фабрик, что производят мировые бренды
- Ограниченные партии — эксклюзивность
- Цена в 2-3 раза ниже чем в люкс-магазинах
- Примерка, обмен и возврат
- Адрес: Егизбаева 7/2, г. Алматы
- Ежедневно с 10:00 до 22:00
- Telegram: https://t.me/kzottenokkz
- Оплата: перевод Халык, счёт на оплату, рассрочка Каспи

ПОРЯДОК ДИАЛОГА:
1. Приветствие → коротко о магазине
2. Презентация товара → вызови get_photos. НЕ спрашивай город сразу!
3. Помощь с выбором → отвечай на вопросы (цена, качество, размеры)
4. Сбор города → ТОЛЬКО когда клиент определился ("хочу эту", "беру")
5. Размер и цвет → для обуви. У сумок НЕТ размера!
6. Адрес → когда всё остальное собрано
7. Оформление → вызови check_stock, потом submit_order

ПРАВИЛА:
- Если клиент спрашивает цену — ОБЯЗАТЕЛЬНО назови из каталога + фраза про выгоду
- Если "дорого" → отработай возражение (качество, гарантия, примерка)
- Если клиент не ответил на твой вопрос и задал свой → СНАЧАЛА ответь на его вопрос, ПОТОМ повтори свой
- НЕ спрашивай два вопроса за раз
- НЕ повторяй приветствие если уже здоровалась
- НЕ выдумывай товары, которых нет в каталоге
- Если клиент хочет приехать на примерку — НЕ собирай данные заказа, дай адрес
- "какой у вас адрес?" = вопрос про адрес магазина, НЕ заказ
- Если не можешь помочь → handoff_to_manager

ДОЖИМ НА ПОКУПКУ:
- Алматы: "оформите онлайн, заберите в магазине или отправим через Яндекс/InDrive"
- Другой город: "оформите онлайн, отправим Казпочтой"

СКРИПТЫ:
- "Дорого" → "Понимаю🤍 Важно учитывать, что вы платите не просто за бренд, а за качество, контроль и отсутствие неприятных сюрпризов. Большинство наших клиентов возвращаются из-за этого"
- Предзаказ → "Эту модель собираем по предзаказу. 50% предоплата, остальное при получении. Размер/цвет резервируем за вами"
- Адрес/график → "Работаем ежедневно с 10:00 до 22:00, Егизбаева 7/2 ||| https://2gis.kz/almaty/geo/70000001107511471"
- Каталог → "Наш телеграм канал с актуальными ценами ||| https://t.me/kzottenokkz"

КАТАЛОГ ТОВАРОВ:
{catalog}
"""


# ── Tool execution ───────────────────────────────────────────

async def execute_tool(chat_id: str, name: str, args: dict) -> str:
    """Выполнить tool call и вернуть результат."""
    logger.info(f"[{chat_id}] Tool: {name}({json.dumps(args, ensure_ascii=False)[:200]})")

    if name == "check_stock":
        result = await services.check_stock(
            args.get("product", ""),
            args.get("size", ""),
            args.get("color", ""),
        )
        return json.dumps(result, ensure_ascii=False)

    elif name == "get_photos":
        product = args.get("product", "")
        color = args.get("color", "")
        product_key = f"{product}_{color}".lower().strip("_")

        # Проверяем, не отправляли ли уже
        if await db.has_sent_photos(chat_id, product_key):
            return json.dumps({"sent": False, "reason": "Фото этого товара уже отправлялись клиенту"})

        photos = await services.find_photos(product, color)
        if photos:
            await send_photos(chat_id, photos)
            await db.mark_photos_sent(chat_id, product_key)
            return json.dumps({"sent": True, "count": len(photos)})
        else:
            return json.dumps({"sent": False, "reason": "Фото не найдены"})

    elif name == "submit_order":
        order = {
            "product": args.get("product", ""),
            "size": args.get("size", ""),
            "color": args.get("color", ""),
            "city": args.get("city", ""),
            "address": args.get("address", ""),
            "client_phone": chat_id.replace("@c.us", ""),
        }
        await db.save_order_state(chat_id, order)
        await services.notify_order(order)
        await services.send_order_to_n8n(order)
        return json.dumps({"success": True, "order": order}, ensure_ascii=False)

    elif name == "handoff_to_manager":
        await db.set_handoff(chat_id, True)
        reason = args.get("reason", "")
        await services.notify_error("handoff", f"chat_id={chat_id} reason={reason}")
        return json.dumps({"success": True, "message": "Диалог передан менеджеру"})

    return json.dumps({"error": f"Unknown tool: {name}"})


# ── Main generate ────────────────────────────────────────────

async def generate_response(chat_id: str, user_text: str, sender_name: str = "") -> str:
    """Сгенерировать ответ на сообщение клиента. Возвращает текст (разделённый |||)."""

    # Сохраняем сообщение
    await db.save_message(chat_id, "user", user_text, sender_name)

    # Загружаем историю
    history = await db.get_history(chat_id)

    # Каталог для промпта
    catalog = await services.get_catalog()
    catalog_text = services.format_catalog_for_prompt(catalog)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(catalog=catalog_text)

    # Формируем messages
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    # Цикл tool calling (максимум 5 итераций)
    for _ in range(5):
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.7,
            max_tokens=1000,
        )

        choice = response.choices[0]

        # Если есть tool calls — выполняем
        if choice.message.tool_calls:
            messages.append(choice.message)
            for tc in choice.message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                result = await execute_tool(chat_id, tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
            continue

        # Готовый ответ
        assistant_text = choice.message.content or ""
        if assistant_text:
            await db.save_message(chat_id, "assistant", assistant_text)
        return assistant_text

    # Fallback
    fallback = "Извините, произошла ошибка. Наш менеджер скоро с вами свяжется!"
    await db.save_message(chat_id, "assistant", fallback)
    return fallback


# ── Whisper (voice) ──────────────────────────────────────────

async def transcribe_voice(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """Транскрибировать голосовое через Whisper."""
    ext = "ogg"
    if "mp4" in mime_type or "m4a" in mime_type:
        ext = "m4a"
    elif "wav" in mime_type:
        ext = "wav"

    from io import BytesIO
    buf = BytesIO(audio_bytes)
    buf.name = f"voice.{ext}"

    response = await client.audio.transcriptions.create(
        model="whisper-1",
        file=buf,
        language="ru",
    )
    return response.text or ""
