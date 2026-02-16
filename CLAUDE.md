# Sales Ottenok - WhatsApp Sales Bot

WhatsApp-бот "Алина" — AI-менеджер по продажам для магазина женской обуви и аксессуаров "Оттенок" (Алматы, Казахстан).

## Stack

Python 3.11+ / FastAPI / OpenAI GPT-4o / ChromaDB / GREEN-API (WhatsApp) / Google Drive API / SQLite (aiosqlite) / APScheduler / pandas

## Commands

```bash
python main.py                    # Start bot (uvicorn on port 8080)
python -m knowledge.builder       # Rebuild ChromaDB from documents
pytest tests/ -v                  # Run all tests
```

## Environment Variables

Defined in `config.py`. Key vars (from `.env`):

| Variable | Purpose |
|----------|---------|
| `GREEN_API_INSTANCE_ID`, `GREEN_API_TOKEN` | WhatsApp API |
| `OPENAI_API_KEY` | GPT-4o + embeddings |
| `GOOGLE_DRIVE_PHOTOS_FOLDER_ID` | Root folder with product photos |
| `GOOGLE_CREDENTIALS_FILE` | Path to service account JSON (default: `credentials/google_credentials.json`) |
| `GREEN_API_POLLING` | `1` = polling mode (default), `0` = webhook only |
| `NUDGE_ENABLED` | `1` = auto-nudge enabled (default) |
| `INVENTORY_EXCEL_PATH` | Default: `data/inventory.xlsx` |
| `MANAGER_NUMBERS` | Comma-separated phone numbers for manager commands |

## Architecture

### Module Map

```
main.py                  Entry point. Lifespan: init_db -> load_photo_index -> set_message_handler -> poll/webhook -> nudge_scheduler
config.py                All env vars and constants

ai/
  engine.py              Central orchestrator (1315 lines). generate_response() + handle_message()
  prompts.py             SYSTEM_PROMPT — persona "Алина", 7-step sales dialogue
  rag.py                 ChromaDB search: search_products(), search_scripts()

greenapi/
  client.py              GREEN-API HTTP client: send_text, send_image, send_multiple_images
  webhook.py             POST /webhook router, set_message_handler()
  poller.py              Polling fallback (infinite loop)
  models.py              Pydantic v2 models for webhook payloads

gdrive/
  client.py              Google Drive API: list folders/images, download, build_product_photo_index()
  photo_mapper.py        Photo search with tokenization + Russian brand mapping. find_product_photos()

inventory/
  stock_checker.py       check_product_availability() — tokenized search in Excel
  excel_loader.py        InventoryLoader singleton with 5-min TTL cache

scheduler/
  nudge_scheduler.py     NudgeScheduler (APScheduler) — checks and sends nudges
  nudge_rules.py         Business rules: work hours 9-19, delays, nudge messages

db/
  models.py              init_db() — creates SQLite tables (conversations, clients, sent_photos, handoff_state, client_order_context)
  conversations.py       All DB CRUD: messages, order context, handoff, nudge state

knowledge/
  builder.py             CLI tool: parses .docx + .txt -> embeddings -> ChromaDB
  docx_parser.py         parse_catalog_docx(), parse_scripts_docx()
  chat_parser.py         WhatsApp chat export parser
  embeddings.py          OpenAI batch embeddings + ChromaDB storage
```

### Module Dependencies

```
main.py -> config, greenapi/webhook, greenapi/poller, gdrive/photo_mapper, db/models, ai/engine, scheduler/nudge_scheduler

ai/engine.py (central hub) -> ai/prompts, ai/rag, db/conversations, gdrive/photo_mapper, inventory/stock_checker, greenapi/client, config

greenapi/client.py -> gdrive/client (download_file_bytes for photo upload)
inventory/stock_checker.py -> gdrive/photo_mapper (tokenize_text), inventory/excel_loader
scheduler/nudge_scheduler.py -> scheduler/nudge_rules, db/conversations, greenapi/client
knowledge/builder.py -> knowledge/docx_parser, knowledge/chat_parser, knowledge/embeddings
```

## Message Flow

```
GREEN-API -> POST /webhook (or poller) -> parse WebhookPayload -> extract text
  -> handle_message() [ai/engine.py:1223]
    -> check /handoff commands (manager)
    -> check handoff state (skip if enabled)
    -> generate_response() [ai/engine.py:790]
      1. Save message to DB, reset nudge state
      2. Load order context from DB
      3. Parallel RAG: search_products() + search_scripts()
      4. Extract order fields via separate GPT call (JSON mode) [ai/engine.py:588]
      5. Build system prompt with order guard
      6. Call GPT-4o for response
      7. Post-process: strip duplicate greetings, validate order confirmation
      8. Multi-stage photo search (5 strategies) [ai/engine.py:~900-1100]
      9. Save assistant message to DB
    -> Split response by "|||" separator
    -> Send text parts + photos via GREEN-API
```

## Order State Machine

Tracked in `client_order_context` table. Fields: `city`, `product`, `product_type`, `size`, `color`, `address`.

- `product_type` in {shoes, bag, accessory, other}
- `size` required only for shoes
- `color` required only if product has multiple color photos in Drive
- `address` asked LAST (after all other fields)
- Order confirmed only when ALL required fields present AND inventory check passes
- Field extraction: separate GPT call with JSON mode (`_extract_order_fields`)

## Nudge State Machine

Tracked in `clients` table fields: `nudge_count`, `nudge_state`, `last_nudge_at`.

- Client stops responding -> after 3h (work hours 9-19): nudge #1
- Still no response -> next day at 13:00: nudge #2
- Client replies -> `reset_nudge_state()`, nudge_count=0
- Manager `/handoff on` or nudge_count >= 2 -> stop nudging

## Data Files

### In git (source of truth)

| File | Purpose |
|------|---------|
| `data/inventory.xlsx` | Product inventory (product_name, size, color, quantity, price) |
| `data/knowledge_base/catalog/*.docx` | Product catalog documents |
| `data/knowledge_base/scripts/*.docx` | Sales scripts |
| `data/knowledge_base/chats/*/*.txt` | WhatsApp chat exports (text only) |

### Auto-generated (NOT in git, .gitignore'd)

| File | Rebuild command |
|------|----------------|
| `data/chroma_db/` | `python -m knowledge.builder` |
| `data/ottenok.db` | Created on startup by `init_db()` |
| `data/photo_index.json` | Created on startup by `load_photo_index()` |
| `data/sales_ottenok.log` | Runtime log |

## Key Implementation Details

- **Response separator**: `"|||"` splits response into multiple WhatsApp messages
- **Brand mapping**: Russian brand names mapped to English in `gdrive/photo_mapper.py:50-77` (e.g., "шанель" -> "chanel")
- **Photo search**: 5-stage fallback in `generate_response()` — user message -> order context -> RAG metadata -> GPT response text -> last assistant message
- **Photo selection**: Color variety mode (1 per color) or filtered by requested color. Max 3 per message, 6 per showcase.
- **Inventory cache**: 5-min TTL, auto-reloads from Excel
- **Manager commands**: `/handoff on|off|status {phone}` — sent from manager WhatsApp number
- **Retry**: GREEN-API calls decorated with `@retry_async(max_retries=3)`

## Tests

```bash
pytest tests/test_stock_checker.py -v    # Inventory checking tests
pytest tests/test_nudge_scheduler.py -v  # Nudge logic tests
```

## Deploy

- `deploy/setup.sh` — automated VPS setup (Ubuntu/Debian)
- `deploy/systemd/sales_ottenok.service` — systemd unit
- `deploy/nginx/sales_ottenok.conf` — nginx reverse proxy config
