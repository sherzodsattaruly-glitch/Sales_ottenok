# Sales Ottenok v2 — WhatsApp Sales Bot

AI-менеджер по продажам для магазина "Оттенок". GPT-4o с function calling, без лишней сложности.

## Что изменилось vs v1

| | v1 | v2 |
|---|---|---|
| Строк кода | ~4000 | ~1500 |
| Файлов Python | 21 | 7 |
| AI логика | Regex + RAG + ChromaDB | GPT-4o function calling |
| Каталог | Excel + Google Sheets | Google Sheets (один источник) |
| Intent detection | 50+ regex паттернов | GPT решает сам |
| Field extraction | Regex + JSON parse | GPT structured output |
| Дожим | APScheduler | asyncio loop |

## Архитектура

```
main.py      — FastAPI, webhook/polling, voice
chat.py      — message routing, aggregation, locks, handoff
ai.py        — GPT-4o + function calling (tools)
services.py  — Google Sheets, Google Drive, Telegram, N8N
db.py        — SQLite (история, клиенты, заказы)
nudge.py     — автоматический дожим
config.py    — конфигурация из .env
```

## GPT Tools (function calling)

| Tool | Когда вызывается |
|------|-----------------|
| `check_stock` | Перед оформлением заказа |
| `get_photos` | Клиент спрашивает "покажите", "какие есть" |
| `submit_order` | Все данные собраны, клиент подтвердил |
| `handoff_to_manager` | Сложный случай, жалоба, возврат |

## Установка

```bash
cp .env.example .env
# Заполни .env

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python main.py
```

## Структура данных

### Google Sheets (каталог)

| product_name | price | sizes | colors | quantity |
|---|---|---|---|---|
| Chanel Jumbo | 145000₸ | 36,37,38 | черный,бежевый | 3 |

### Команды менеджера

```
/handoff on 77001234567   — передать клиента менеджеру
/handoff off 77001234567  — вернуть боту
/bot on                   — включить бота
/bot off                  — выключить бота
Emoji реакция в чате      — включить бота обратно
```

## Деплой

```bash
# Тот же systemd/nginx что и v1, просто замени путь
python main.py
# Сервер на :8080, /health для мониторинга
```
