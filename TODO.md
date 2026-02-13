# Sales Ottenok — Task List

```
project_root: /Users/vic/git/Sales_ottenok
python_version: 3.11
framework: FastAPI + uvicorn
entry_point: main.py
central_module: ai/engine.py (1315 lines)
database: SQLite via aiosqlite
```

---

## PHASE 1: Critical Bug Fixes

> Blocker: do not deploy until all Phase 1 tasks are done.

---

### TASK-1.1: Fix mojibake in error fallback message

```
priority: P0
estimate: 0.5h
status: done
depends_on: none
```

**Problem:**
`ai/engine.py:1310` contains double-encoded UTF-8 string. Customer sees garbled text like `"РР·РІРёРЅРёС‚Рµ..."` instead of a Russian apology. The correct version of the same message exists in `greenapi/webhook.py:40`.

**Change specification:**
1. In `ai/engine.py:1310`, replace the mojibake string with: `"Извините, произошла небольшая ошибка. Наш менеджер скоро с вами свяжется!"`
2. Verify the docstring comments at lines 1-3 and 790-793 — they also contain mojibake. Replace with readable Russian.
3. Scan the entire file for other mojibake occurrences (pattern: sequences of `Р` followed by uppercase Latin letters). Fix all found.

**Context files (must read before editing):**
- `ai/engine.py` (full file — scan for all mojibake)
- `greenapi/webhook.py` (reference: correct message at line 40)

**Success criteria:**
- `grep -P 'Р[А-Я]' ai/engine.py` returns zero matches
- Error fallback message in `ai/engine.py:1310` matches the one in `greenapi/webhook.py:40`
- All docstrings and comments in `ai/engine.py` are readable Russian, not garbled

---

### TASK-1.2: Remove false order intent trigger on "адрес"

```
priority: P0
estimate: 1h
status: done
depends_on: none
```

**Problem:**
`ai/engine.py:161-166` — the word `"адрес"` in `_ORDER_INTENT_PATTERNS` causes false checkout when client asks "какой у вас адрес?" (store address inquiry). Similarly, `"оформ"` in `_CHECKOUT_HINTS` is too broad — matches "оформление", "оформления" in informational context.

**Current code at lines 161-166:**
```python
_ORDER_INTENT_PATTERNS = [
    "оформ", "заказ", "беру", "возьму", "покуп", "куплю", "зафикс", "адрес",
]
_CHECKOUT_HINTS = [
    "зафикс", "оформ", "адрес доставки", "напишите, пожалуйста, адрес", "куда отправ",
]
```

**Change specification:**
1. In `_ORDER_INTENT_PATTERNS` (line 161-163): remove `"адрес"`. Optionally add `"адрес доставки"` as a more specific pattern.
2. In `_CHECKOUT_HINTS` (line 164-166): replace `"оформ"` with `"оформить заказ"` and `"оформляем заказ"`.
3. In `_strip_checkout_prompts` (line 501-513): the function drops entire `|||`-separated parts if ANY hint substring matches. This is too aggressive. Add a minimum length check — do not drop parts longer than 120 chars (they likely contain substantive content, not just a checkout prompt).

**Context files (must read before editing):**
- `ai/engine.py:155-198` (constants block)
- `ai/engine.py:486-513` (`_has_order_intent`, `_strip_checkout_prompts`)
- `ai/engine.py:893-960` (where these functions are called in `generate_response`)

**Success criteria:**
- `_has_order_intent("какой у вас адрес?")` returns `False`
- `_has_order_intent("адрес доставки: Алматы, ул. Абая 10")` returns `True`
- `_has_order_intent("хочу оформить заказ")` returns `True`
- `_strip_checkout_prompts("Отличный выбор! Chanel Classic Slingbacks — элегантная модель с открытой пяткой, цена 145 000 ₸. Давайте оформим заказ?")` preserves the long description part
- No existing tests break: `pytest tests/ -v` passes

---

### TASK-1.3: Add per-chat concurrency lock

```
priority: P0
estimate: 2h
status: done
depends_on: none
```

**Problem:**
`greenapi/webhook.py:103` creates `asyncio.create_task()` per message with no locking. Two fast messages from the same client run `generate_response()` concurrently — both read the same `order_ctx`, both call GPT, both write results. Last write wins, first message's extracted fields are lost.

**Change specification:**
1. In `greenapi/webhook.py`, add a module-level lock registry:
```python
_chat_locks: dict[str, asyncio.Lock] = {}
```
2. In `process_incoming_message` (line 32), acquire per-chat lock before calling handler:
```python
async def process_incoming_message(chat_id: str, sender_name: str, text: str):
    if chat_id not in _chat_locks:
        _chat_locks[chat_id] = asyncio.Lock()
    async with _chat_locks[chat_id]:
        handler = _message_handler or _default_echo_handler
        try:
            await handler(chat_id, sender_name, text)
        except Exception as e:
            ...existing error handling...
```
3. Add cleanup: periodically remove locks for chats that haven't sent messages in 1 hour. Simple approach — clear the entire dict every N hours, or limit dict size to 1000 entries (LRU). A pragmatic approach: just let the dict grow — each `asyncio.Lock` is ~200 bytes, even 10K clients = 2MB.

**Context files (must read before editing):**
- `greenapi/webhook.py` (full file — 106 lines)
- `greenapi/poller.py` (check if poller has same issue — it likely calls the same handler)

**Success criteria:**
- `_chat_locks` dict exists in `greenapi/webhook.py`
- `process_incoming_message` acquires lock for `chat_id` before calling handler
- Two concurrent calls to `process_incoming_message` with the same `chat_id` execute sequentially (second waits for first)
- Two concurrent calls with different `chat_id`s execute in parallel (no blocking)
- Poller path also uses the same lock mechanism if it calls the handler directly

---

### TASK-1.4: Fix is_answering_missing_field detection

```
priority: P1
estimate: 1.5h
status: done
depends_on: none
```

**Problem:**
`ai/engine.py:869,972-978` — `missing_order_fields` is computed AFTER merging extracted fields into `order_ctx` (line 861). So if client provides city, city is already merged into `order_ctx`, `_build_missing_fields` no longer includes `city`, and the loop at 975 never matches. Result: `is_answering_missing_field` stays `False`, bot sends photos when client just answered "Алматы".

**Current flow (lines 838-978 simplified):**
```
extracted_fields = _extract_order_fields(...)        # line 838
order_ctx = _merge_order_context(order_ctx, extracted_fields)  # line 861 — city is now merged
missing_order_fields = _build_missing_fields(order_ctx, ...)   # line 869 — city is NOT missing anymore
...
for field in missing_order_fields:  # line 975 — city not in list
    if extracted_fields.get(field): # never matches for city
```

**Change specification:**
1. Compute `pre_merge_missing` BEFORE the merge at line 861:
```python
# Before merge — capture what WAS missing
color_required_pre = await _is_color_required(order_ctx.get("product", ""))
pre_merge_missing = _build_missing_fields(order_ctx, color_required_pre)
```
2. After merge, use `pre_merge_missing` for the `is_answering_missing_field` check at line 972-978:
```python
is_answering_missing_field = False
if pre_merge_missing and extracted_fields:
    for field in pre_merge_missing:
        if extracted_fields.get(field):
            is_answering_missing_field = True
            break
```
3. Keep the existing `missing_order_fields` (post-merge) for all other uses (order guard prompt, question forcing at line 903+).

**Context files (must read before editing):**
- `ai/engine.py:790-980` (`generate_response` — the relevant section)
- `ai/engine.py:441-465` (`_build_missing_fields`)
- `ai/engine.py:716-731` (`_is_color_required`)

**Success criteria:**
- When client's message provides a field that was previously missing (e.g., city="Алматы"), `is_answering_missing_field` is `True`
- When `is_answering_missing_field` is `True`, photo search stages at lines 981-1071 are skipped (existing behavior — they check `not is_answering_missing_field`)
- When client sends a message that does NOT answer a missing field, photo search works as before
- No existing tests break

---

### TASK-1.5: Fix GPT response text used as photo search query

```
priority: P1
estimate: 1h
status: done
depends_on: none
```

**Problem:**
`ai/engine.py:1035` passes the entire GPT response text to `find_product_photos(product_name=assistant_text)`. A multi-sentence response like "Отличный выбор! Chanel Classic Slingbacks — элегантная модель..." produces dozens of tokens, matching unrelated product photos.

**Current code at lines 1032-1039:**
```python
# Stage 3: search by GPT response text
if not photos and not is_answering_missing_field:
    try:
        found_photos = await find_product_photos(product_name=assistant_text)
```

**Change specification:**
Option A (preferred — minimal change): Extract only the first product-like name from `assistant_text` instead of passing the whole text. Use the existing `_extract_product_name_from_result` pattern or a simple regex for known brand patterns.

Implementation:
```python
if not photos and not is_answering_missing_field:
    # Extract product name from GPT response instead of using full text
    gpt_product = _extract_product_mention(assistant_text)  # new helper
    if gpt_product:
        try:
            found_photos = await find_product_photos(product_name=gpt_product)
```

The `_extract_product_mention(text)` helper should:
1. Search for known brand names (from `_MODEL_QUERY_IGNORE_TOKENS` brand list or from `BRAND_MAPPING` in `gdrive/photo_mapper.py`)
2. Extract the brand + following 1-3 words as product name
3. Return `None` if no brand found

Option B (simpler): Use only `primary_product_match` or `rag_product_name` (already computed at lines 820, 842) instead of `assistant_text`.

**Context files (must read before editing):**
- `ai/engine.py:1032-1039` (the stage 3 code)
- `ai/engine.py:182-192` (`_MODEL_QUERY_IGNORE_TOKENS` — contains brand names)
- `ai/engine.py:234-248` (`_extract_product_name_from_result` — existing pattern)
- `gdrive/photo_mapper.py:50-77` (`BRAND_MAPPING` — Russian to English brand names)

**Success criteria:**
- `find_product_photos` at stage 3 receives a short product name (1-5 words), not the full GPT response
- If no product name can be extracted from GPT response, stage 3 is skipped entirely
- Photo search still works for stages 1, 2, 4, 5 (unchanged)
- No existing tests break

---

### TASK-1.6: Reset order fields on product switch

```
priority: P1
estimate: 1.5h
status: done
depends_on: none
```

**Problem:**
`ai/engine.py:399-409` — `_merge_order_context` only overwrites fields with non-empty values. When client switches from "Chanel Jumbo" to "Jimmy Choo Azia", the `product` field updates but `size`, `color`, `address` from Chanel remain. Client ends up ordering the wrong size/color.

**Current code at lines 399-409:**
```python
def _merge_order_context(base: dict, updates: dict) -> dict:
    merged = _sanitize_order_context(base)
    incoming = _sanitize_order_context(updates)
    for key in ("city", "product", "size", "color", "address"):
        if incoming.get(key):
            merged[key] = incoming[key]
    ...
```

**Change specification:**
1. In `_merge_order_context`, detect product switch: if `incoming["product"]` is non-empty AND `merged["product"]` is non-empty AND they differ significantly (not just case/whitespace), then reset `size`, `color`, `address` in `merged` before applying the update.
2. "Differ significantly" — use tokenized comparison: if `tokenize_text(incoming["product"])` shares less than 50% tokens with `tokenize_text(merged["product"])`, it's a product switch.
3. Do NOT reset `city` — city is independent of product.
4. Do NOT reset `product_type` — it will be re-inferred from the new product.

```python
def _merge_order_context(base: dict, updates: dict) -> dict:
    merged = _sanitize_order_context(base)
    incoming = _sanitize_order_context(updates)

    # Detect product switch
    if incoming.get("product") and merged.get("product"):
        old_tokens = tokenize_text(merged["product"])
        new_tokens = tokenize_text(incoming["product"])
        if old_tokens and new_tokens:
            overlap = old_tokens & new_tokens
            similarity = len(overlap) / max(len(old_tokens), len(new_tokens))
            if similarity < 0.5:
                # Product switch — reset dependent fields
                merged["size"] = ""
                merged["color"] = ""
                merged["address"] = ""

    for key in ("city", "product", "size", "color", "address"):
        if incoming.get(key):
            merged[key] = incoming[key]
    ...
```

**Context files (must read before editing):**
- `ai/engine.py:388-409` (`_sanitize_order_context`, `_merge_order_context`)
- `gdrive/photo_mapper.py` — `tokenize_text` function (already imported at engine.py:27)
- `ai/engine.py:861-867` (where merge is called in `generate_response`)

**Success criteria:**
- Switching from "Chanel Jumbo" to "Jimmy Choo Azia" resets `size`, `color`, `address`
- Refining the same product (e.g., "Chanel Jumbo" → "Chanel Jumbo Classic Flap") does NOT reset fields
- `city` is never reset on product switch
- `tokenize_text` import already exists (line 27) — no new imports needed
- No existing tests break

---

### TASK-1.7: Save client messages during handoff

```
priority: P1
estimate: 0.5h
status: done
depends_on: none
```

**Problem:**
`ai/engine.py:1256-1258` — when handoff is enabled, bot returns without saving client's message. When manager resolves the issue and disables handoff, conversation history is missing all messages the client sent during handoff. GPT has no context of what happened.

**Current code at lines 1255-1258:**
```python
if await get_handoff_state(chat_id):
    logger.info(f"Handoff enabled for {chat_id}; bot skipped reply.")
    return
```

**Change specification:**
Replace lines 1255-1258 with:
```python
if await get_handoff_state(chat_id):
    # Save message to history even though bot won't reply
    await save_message(chat_id, "user", text, sender_name)
    await update_last_client_message(chat_id, text)
    logger.info(f"Handoff enabled for {chat_id}; saved message, bot skipped reply.")
    return
```

`save_message` and `update_last_client_message` are already imported (lines 17, 25).

**Context files (must read before editing):**
- `ai/engine.py:1223-1315` (`handle_message` function)
- `ai/engine.py:15-26` (imports — verify `save_message` and `update_last_client_message` are imported)
- `db/conversations.py:9-48` (`save_message` signature)
- `db/conversations.py:315-333` (`update_last_client_message` signature)

**Success criteria:**
- When handoff is enabled and client sends a message, message is saved to `conversations` table with `role="user"`
- `clients.last_client_message_at` is updated
- `clients.last_client_text` is updated
- Bot still does NOT reply (returns after saving)
- No existing tests break

---

### TASK-1.8: Make _strip_checkout_prompts less aggressive

```
priority: P2
estimate: 1h
status: done
depends_on: TASK-1.2
```

**Problem:**
`ai/engine.py:501-513` drops entire `|||`-separated response parts if any `_CHECKOUT_HINTS` substring matches. Combined with TASK-1.2 fixes to `_CHECKOUT_HINTS`, this task ensures substantive content is not dropped.

**Change specification:**
After TASK-1.2 is done (which fixes `_CHECKOUT_HINTS` patterns), add a length guard in `_strip_checkout_prompts`:

```python
def _strip_checkout_prompts(text: str) -> str:
    if not text:
        return text
    parts = [p.strip() for p in text.split("|||") if p.strip()]
    kept = []
    for p in parts:
        low = p.lower()
        # Only drop SHORT parts that are purely checkout prompts
        # Long parts likely contain product descriptions + checkout hint
        if len(p) < 120 and any(h in low for h in _CHECKOUT_HINTS):
            continue
        kept.append(p)
    if not kept:
        return ""
    return "|||".join(kept)
```

**Context files (must read before editing):**
- `ai/engine.py:501-513` (`_strip_checkout_prompts`)
- `ai/engine.py:164-166` (`_CHECKOUT_HINTS` — after TASK-1.2 changes)
- `ai/engine.py:898-900` (where `_strip_checkout_prompts` is called)

**Success criteria:**
- Short checkout-only messages like "Давайте оформим заказ?" are stripped
- Long product descriptions containing a checkout hint are NOT stripped
- `_strip_checkout_prompts("")` returns `""`
- `_strip_checkout_prompts("Давайте оформим заказ?")` returns `""`
- `_strip_checkout_prompts("Chanel Classic Slingbacks — элегантная модель с открытой пяткой, натуральная кожа, цена 145 000 ₸. Давайте оформим заказ?")` preserves the text
- No existing tests break

---

### TASK-1.9: Add TTL to _COLOR_REQUIREMENT_CACHE

```
priority: P2
estimate: 0.5h
status: done
depends_on: none
```

**Problem:**
`ai/engine.py:160` — `_COLOR_REQUIREMENT_CACHE` is a plain dict that persists for the entire process lifetime. If photos change on Google Drive (new colors added), the cache never reflects it.

**Current code at line 160:**
```python
_COLOR_REQUIREMENT_CACHE: dict[str, bool] = {}
```

**Change specification:**
1. Change the cache to store `(bool, float)` tuples where float is `time.time()`:
```python
import time  # add to imports if not present

_COLOR_REQUIREMENT_CACHE: dict[str, tuple[bool, float]] = {}
_COLOR_CACHE_TTL = 1800  # 30 minutes
```
2. In `_is_color_required` (lines 716-731), check TTL before returning cached value:
```python
async def _is_color_required(product_name: str) -> bool:
    key = (product_name or "").strip().lower()
    if not key:
        return False
    cached = _COLOR_REQUIREMENT_CACHE.get(key)
    if cached is not None:
        value, ts = cached
        if time.time() - ts < _COLOR_CACHE_TTL:
            return value
    # ...existing lookup logic...
    _COLOR_REQUIREMENT_CACHE[key] = (result, time.time())
    return result
```

**Context files (must read before editing):**
- `ai/engine.py:160` (cache declaration)
- `ai/engine.py:716-731` (`_is_color_required` function)
- `ai/engine.py:6-9` (imports — check if `time` is imported)

**Success criteria:**
- Cache entries expire after 30 minutes
- Fresh entries are returned from cache (no API call)
- Expired entries trigger a fresh lookup
- No existing tests break

---

### TASK-1.10: Add type validation on LLM JSON extraction

```
priority: P2
estimate: 0.5h
status: done
depends_on: none
```

**Problem:**
`ai/engine.py:617-624` — parsed JSON from LLM has no type validation. If GPT returns `"size": 38` (int), calling `.strip()` later in `_sanitize_order_context` (line 390) will crash with `AttributeError: 'int' object has no attribute 'strip'`.

**Current code at lines 617-624:**
```python
return {
    "city": parsed.get("city") or "",
    "product": parsed.get("product") or "",
    "product_type": parsed.get("product_type") or "",
    "size": parsed.get("size") or "",
    "color": parsed.get("color") or "",
    "address": parsed.get("address") or "",
    "ready_to_order": bool(parsed.get("ready_to_order", False)),
}
```

**Change specification:**
Wrap string fields with `str()`:
```python
return {
    "city": str(parsed.get("city") or ""),
    "product": str(parsed.get("product") or ""),
    "product_type": str(parsed.get("product_type") or ""),
    "size": str(parsed.get("size") or ""),
    "color": str(parsed.get("color") or ""),
    "address": str(parsed.get("address") or ""),
    "ready_to_order": bool(parsed.get("ready_to_order", False)),
}
```

**Context files (must read before editing):**
- `ai/engine.py:588-636` (`_extract_order_fields`)
- `ai/engine.py:388-396` (`_sanitize_order_context` — calls `.strip()`)

**Success criteria:**
- `_extract_order_fields` always returns string values for string fields, even if LLM returns int/float/list/dict
- `_sanitize_order_context` never crashes on `.strip()` with non-string input
- `"size": 38` from LLM becomes `"size": "38"` in returned dict
- No existing tests break

---

## PHASE 2: Observability

> Blocker: do not deploy until at least 2.1, 2.3, 2.4 are done.

---

### TASK-2.1: Add log rotation

```
priority: P0
estimate: 0.5h
status: done
depends_on: none
```

**Problem:**
`main.py:27` uses `FileHandler` — log file grows forever, will fill disk on VPS.

**Current code at lines 22-29:**
```python
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/sales_ottenok.log", encoding="utf-8"),
    ],
)
```

**Change specification:**
Replace `FileHandler` with `RotatingFileHandler`:
```python
from logging.handlers import RotatingFileHandler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            "data/sales_ottenok.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        ),
    ],
)
```

**Context files (must read before editing):**
- `main.py:1-30` (imports and logging setup)

**Success criteria:**
- `RotatingFileHandler` is used instead of `FileHandler`
- `maxBytes=10*1024*1024` (10 MB per file)
- `backupCount=5` (keeps 5 rotated files)
- `encoding="utf-8"` preserved
- Bot starts without errors: `python -c "from main import app"`

---

### TASK-2.2: Add chat_id to all log messages in engine and webhook

```
priority: P1
estimate: 2h
status: done
depends_on: none
```

**Problem:**
Log messages from different client conversations interleave with no way to correlate them. Some log lines include `chat_id`, many don't.

**Change specification:**
1. In `ai/engine.py`, for every `logger.info/warning/error` call inside `generate_response` and `handle_message`, ensure `chat_id` is included as the first part of the message. Pattern: `logger.info("[%s] message text", chat_id)`.
2. In `greenapi/webhook.py`, same pattern for `process_incoming_message`.
3. Do NOT change the global logging format — just prepend `chat_id` to message strings where it is available.
4. Do NOT modify functions that don't have access to `chat_id` (pure utility functions).

**Context files (must read before editing):**
- `ai/engine.py` (full file — search for all `logger.` calls)
- `greenapi/webhook.py` (full file)
- `greenapi/poller.py` (check if it also logs without chat_id)

**Success criteria:**
- Every `logger.*` call inside `generate_response` and `handle_message` includes `chat_id`
- Format is consistent: `[{chat_id}] message text`
- Functions without `chat_id` parameter are not modified
- No existing tests break

---

### TASK-2.3: Add Telegram error notifications

```
priority: P0
estimate: 3h
status: done
depends_on: none
```

**Problem:**
No one is notified when the bot errors. Owner learns about problems from customer complaints.

**Change specification:**

1. Create new file `notifications.py` in project root:
```python
"""
Telegram error notification with throttling.
Sends alerts to owner when bot encounters errors.
"""
import asyncio
import logging
import time
from typing import Optional
import httpx
from config import TELEGRAM_ALERT_BOT_TOKEN, TELEGRAM_ALERT_CHAT_ID

logger = logging.getLogger(__name__)

_last_sent: dict[str, float] = {}
_THROTTLE_SECONDS = 600  # 10 minutes per error type

async def notify_error(error_type: str, message: str) -> None:
    """Send error alert to Telegram. Throttled to 1 per error_type per 10 min."""
    if not TELEGRAM_ALERT_BOT_TOKEN or not TELEGRAM_ALERT_CHAT_ID:
        return
    now = time.time()
    if now - _last_sent.get(error_type, 0) < _THROTTLE_SECONDS:
        return
    _last_sent[error_type] = now
    text = f"⚠️ Sales Ottenok Error\n\nType: {error_type}\n{message[:1000]}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_ALERT_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_ALERT_CHAT_ID, "text": text},
            )
    except Exception as e:
        logger.warning(f"Failed to send Telegram alert: {e}")
```

2. Add to `config.py`:
```python
# Telegram alerts
TELEGRAM_ALERT_BOT_TOKEN = os.getenv("TELEGRAM_ALERT_BOT_TOKEN", "")
TELEGRAM_ALERT_CHAT_ID = os.getenv("TELEGRAM_ALERT_CHAT_ID", "")
```

3. Add `httpx` to `requirements.txt` (if not already present).

4. Wire into error handlers in:
   - `ai/engine.py:1305-1313` (handle_message catch-all): call `await notify_error("handle_message", f"chat_id={chat_id} error={e}")`
   - `greenapi/webhook.py:37-42` (process_incoming_message catch): call `await notify_error("webhook", f"chat_id={chat_id} error={e}")`

**Context files (must read before editing):**
- `config.py` (full file — add new env vars)
- `ai/engine.py:1305-1313` (error handler in `handle_message`)
- `greenapi/webhook.py:32-42` (error handler in `process_incoming_message`)
- `requirements.txt` (check if `httpx` is already listed)

**Success criteria:**
- `notifications.py` exists and exports `notify_error(error_type, message)`
- Config has `TELEGRAM_ALERT_BOT_TOKEN` and `TELEGRAM_ALERT_CHAT_ID`
- Error handlers in `engine.py` and `webhook.py` call `notify_error`
- Throttling works: same error type within 10 min doesn't send twice
- If `TELEGRAM_ALERT_BOT_TOKEN` is empty, function returns silently (no crash)
- `httpx` is in `requirements.txt`
- Bot starts without errors even when Telegram vars are not set

---

### TASK-2.4: Expand /health endpoint

```
priority: P1
estimate: 1.5h
status: done
depends_on: none
```

**Problem:**
`main.py:69-71` returns `{"status": "ok"}` without checking anything. Coolify/monitoring can only tell if the process is alive, not if it's functional.

**Current code at lines 69-71:**
```python
@app.get("/health")
async def health():
    return {"status": "ok"}
```

**Change specification:**
```python
import time as _time

_start_time = _time.time()

@app.get("/health")
async def health():
    checks = {"uptime_seconds": int(_time.time() - _start_time)}

    # Check SQLite
    try:
        async with aiosqlite.connect(SQLITE_DB_PATH) as db:
            await db.execute("SELECT 1")
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"

    # Check photo index
    from gdrive.photo_mapper import _photo_index
    checks["photo_index_products"] = len(_photo_index) if _photo_index else 0

    # Check ChromaDB
    try:
        from ai.rag import chroma_client
        collections = chroma_client.list_collections()
        checks["chromadb_collections"] = len(collections)
    except Exception as e:
        checks["chromadb_collections"] = f"error: {e}"

    all_ok = checks.get("db") == "ok" and checks.get("photo_index_products", 0) > 0
    checks["status"] = "ok" if all_ok else "degraded"

    return checks
```

Add needed imports to `main.py`.

**Context files (must read before editing):**
- `main.py` (full file)
- `config.py:30` (`SQLITE_DB_PATH`)
- `gdrive/photo_mapper.py` (find the module-level `_photo_index` variable)
- `ai/rag.py` (find the `chroma_client` variable)

**Success criteria:**
- `GET /health` returns JSON with: `status`, `uptime_seconds`, `db`, `photo_index_products`, `chromadb_collections`
- `status` is `"ok"` when DB is accessible and photo index has products
- `status` is `"degraded"` when any check fails
- Health check does not crash — each check has its own try/except
- Response time < 500ms (no heavy operations)

---

### TASK-2.5: Add admin conversation review endpoints

```
priority: P1
estimate: 3h
status: done
depends_on: none
```

**Problem:**
No way to review what the bot said to clients without SSH-ing into the server and querying SQLite.

**Change specification:**

1. Add to `config.py`:
```python
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
```

2. Create new file `admin/routes.py`:
```python
"""
Admin endpoints for conversation review.
Protected by ADMIN_API_KEY header.
"""
import logging
from fastapi import APIRouter, Header, HTTPException
import aiosqlite
from config import SQLITE_DB_PATH, ADMIN_API_KEY

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


async def _verify_key(x_api_key: str = Header(...)):
    if not ADMIN_API_KEY or x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.get("/conversations")
async def list_conversations(x_api_key: str = Header(...), limit: int = 50):
    await _verify_key(x_api_key)
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT chat_id, name, last_message_at, message_count,
                      last_client_text, nudge_count, nudge_state
               FROM clients ORDER BY last_message_at DESC LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


@router.get("/conversations/{chat_id}")
async def get_conversation(chat_id: str, x_api_key: str = Header(...), limit: int = 100):
    await _verify_key(x_api_key)
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT role, content, sender_name, created_at
               FROM conversations WHERE chat_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (chat_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in reversed(rows)]


@router.get("/orders/{chat_id}")
async def get_order_context(chat_id: str, x_api_key: str = Header(...)):
    await _verify_key(x_api_key)
    async with aiosqlite.connect(SQLITE_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM client_order_context WHERE chat_id = ?""",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else {}
```

3. In `main.py`, add:
```python
from admin.routes import router as admin_router
app.include_router(admin_router)
```

4. Create `admin/__init__.py` (empty file).

**Context files (must read before editing):**
- `main.py` (full file — where to add router)
- `config.py` (full file — where to add ADMIN_API_KEY)
- `db/conversations.py` (reference for DB schema and query patterns)
- `db/models.py` (table schemas — `conversations`, `clients`, `client_order_context`)

**Success criteria:**
- `GET /admin/conversations` with valid `X-Api-Key` header returns list of clients
- `GET /admin/conversations/{chat_id}` returns message history for a chat
- `GET /admin/orders/{chat_id}` returns order context for a chat
- Missing or invalid `X-Api-Key` returns 401
- Empty `ADMIN_API_KEY` in config disables all admin endpoints (returns 401)
- No existing tests break

---

## PHASE 3: Testing Infrastructure

> Recommended before deployment. Can be done in parallel with Phase 4.

---

### TASK-3.1: Extract order logic from engine.py into ai/order_manager.py

```
priority: P1
estimate: 4h
status: pending
depends_on: TASK-1.2, TASK-1.6, TASK-1.8, TASK-1.10
```

**Problem:**
`ai/engine.py` is a 1315-line monolith. Pure business logic functions (no I/O) are mixed with async orchestration. Can't unit test without mocking everything.

**Change specification:**
1. Create `ai/order_manager.py` and move these functions from `ai/engine.py`:
   - `_normalize_product_type` (line 199)
   - `_infer_product_type_from_text` (line 212)
   - `_sanitize_order_context` (line 388)
   - `_merge_order_context` (line 399)
   - `_contains_order_confirm` (line 412)
   - `_strip_order_confirm` (line 421)
   - `_build_missing_fields` (line 441)
   - `_question_for_missing` (line 468)
   - `_has_question` (line 482)
   - `_has_order_intent` (line 486)
   - `_asks_for_field` (line 491)
   - `_assistant_already_requests_missing` (line 497)
   - `_strip_checkout_prompts` (line 501)
   - Constants: `_ORDER_CONFIRM_TEXT`, `_SIZE_REQUIRED_TYPES`, `_ORDER_INTENT_PATTERNS`, `_CHECKOUT_HINTS`, `_FIELD_PROMPT_HINTS`, `_PRODUCT_COLOR_OVERRIDES`

2. In `ai/engine.py`, replace the moved functions with imports:
```python
from ai.order_manager import (
    _merge_order_context, _build_missing_fields, _has_order_intent,
    _contains_order_confirm, _strip_order_confirm, _strip_checkout_prompts,
    _normalize_product_type, _infer_product_type_from_text,
    _ORDER_CONFIRM_TEXT, _SIZE_REQUIRED_TYPES,
    ...
)
```

3. Keep `_extract_order_fields` in `engine.py` (it's async, calls OpenAI).
4. Keep `_is_color_required` in `engine.py` (it's async, calls Google Drive).

**Context files (must read before editing):**
- `ai/engine.py` (full file — identify all functions and their dependencies)
- All constants defined at lines 156-196
- All pure functions at lines 199-529

**Success criteria:**
- `ai/order_manager.py` exists with all specified functions
- `ai/engine.py` imports from `ai/order_manager` instead of defining them locally
- All functions in `order_manager.py` are synchronous (no async, no I/O)
- `python -c "from ai.engine import handle_message"` succeeds (no import errors)
- `pytest tests/ -v` passes (no regressions)
- `ai/engine.py` is ~300 lines shorter

---

### TASK-3.2: Write unit tests for order logic

```
priority: P1
estimate: 4h
status: pending
depends_on: TASK-3.1
```

**Problem:**
Zero test coverage on order management logic — the core of the business flow.

**Change specification:**
Create `tests/test_order_manager.py` with tests:

1. `_merge_order_context`:
   - Merge with empty base → updates applied
   - Merge with existing base → only non-empty updates overwrite
   - Product switch → size/color/address reset (TASK-1.6 behavior)
   - Same product refinement → fields preserved
   - City not reset on product switch

2. `_build_missing_fields`:
   - All empty → returns `["city", "product"]` (no size/color/address yet)
   - Shoes product with city → returns `["size"]` (not address yet)
   - All basic fields present → returns `["address"]`
   - All fields present → returns `[]`
   - Bag product → size not required
   - Color required + no color → returns `["color"]`

3. `_has_order_intent`:
   - `"хочу оформить заказ"` → True
   - `"какой у вас адрес?"` → False (after TASK-1.2 fix)
   - `"адрес доставки: Алматы"` → True (if pattern added)
   - `"сколько стоит?"` → False

4. `_strip_checkout_prompts`:
   - Short checkout message → stripped
   - Long product description with checkout hint → preserved (TASK-1.8)
   - Empty string → empty string

5. `_normalize_product_type`:
   - `"shoes"` → `"shoes"`, `"обувь"` → `"shoes"`, `"bag"` → `"bag"`, `"unknown"` → `""`

6. `_infer_product_type_from_text`:
   - `"Chanel Classic Slingbacks"` → `"shoes"`
   - `"Chanel Jumbo Classic Flap"` → `"bag"`
   - `"hello"` → `""`

Add `pytest` and `pytest-asyncio` to `requirements.txt` if not present.

**Context files (must read before editing):**
- `ai/order_manager.py` (created in TASK-3.1)
- `tests/conftest.py` (existing test fixtures)
- `tests/test_stock_checker.py` (reference for test style)

**Success criteria:**
- `pytest tests/test_order_manager.py -v` passes with all tests green
- At least 20 test cases covering the above scenarios
- Tests are pure (no mocks, no I/O, no async)
- Tests validate TASK-1.2 and TASK-1.6 fixes are working correctly

---

### TASK-3.3: Write integration tests for generate_response

```
priority: P2
estimate: 4h
status: pending
depends_on: TASK-3.1, TASK-1.3, TASK-1.4
```

**Problem:**
`generate_response` (the main orchestrator) has zero tests. It's the most critical function in the project.

**Change specification:**
Create `tests/test_engine_integration.py` with mocked external dependencies:

1. **Fixtures:**
   - Mock `openai_client.chat.completions.create` to return canned responses
   - Mock `search_products` and `search_scripts` to return canned RAG results
   - Mock `find_product_photos` to return canned photo results
   - Use in-memory SQLite for DB (override `SQLITE_DB_PATH` in config)
   - Call `init_db()` in fixture setup

2. **Test scenarios:**
   - New client greeting → response contains greeting, no checkout prompts
   - Client asks "какой у вас адрес?" → does NOT trigger order flow (TASK-1.2 fix)
   - Client provides city → `is_answering_missing_field` is True, no photos sent (TASK-1.4 fix)
   - Client switches product → old size/color reset (TASK-1.6 fix)
   - Order completion → all fields present → `_ORDER_CONFIRM_TEXT` in response
   - Handoff enabled → message saved but no response generated (TASK-1.7 fix)

3. Add `pytest-asyncio` to `requirements.txt`.

**Context files (must read before editing):**
- `ai/engine.py` (full file — understand `generate_response` flow)
- `db/models.py` (table schemas for in-memory DB setup)
- `tests/conftest.py` (existing fixtures)
- `config.py` (env vars to override in tests)

**Success criteria:**
- `pytest tests/test_engine_integration.py -v` passes
- At least 6 test scenarios from the list above
- Tests use `pytest-asyncio` for async functions
- Tests use in-memory SQLite (not real database file)
- External APIs are fully mocked (no real OpenAI/Google Drive calls)
- Tests complete in < 10 seconds

---

## PHASE 4: Containerization & Deployment

---

### TASK-4.1: Create Dockerfile

```
priority: P1
estimate: 2h
status: pending
depends_on: none
```

**Change specification:**
Create `Dockerfile` in project root:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user
RUN useradd -r -m appuser && \
    mkdir -p /app/data && \
    chown -R appuser:appuser /app/data

USER appuser

EXPOSE 8080

CMD ["python", "main.py"]
```

Create `.dockerignore`:
```
.git
.env
__pycache__
*.pyc
.venv
data/chroma_db
data/ottenok.db
data/photo_index.json
data/sales_ottenok.log
credentials/
.claude/
```

**Context files (must read before editing):**
- `main.py` (entry point, port)
- `requirements.txt` (dependencies)
- `.gitignore` (reference for what to exclude)
- `config.py` (env vars that need to be passed)

**Success criteria:**
- `docker build -t sales-ottenok .` succeeds
- `docker run --env-file .env -p 8080:8080 -v ./data:/app/data -v ./credentials:/app/credentials sales-ottenok` starts the bot
- `/health` endpoint is accessible at localhost:8080
- Image size < 500MB
- Non-root user inside container

---

### TASK-4.2: Create docker-compose.yml for local dev

```
priority: P2
estimate: 1h
status: pending
depends_on: TASK-4.1
```

**Change specification:**
Create `docker-compose.yml`:

```yaml
services:
  bot:
    build: .
    ports:
      - "8080:8080"
    env_file:
      - .env
    volumes:
      - ./data:/app/data
      - ./credentials:/app/credentials
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s
```

**Context files (must read before editing):**
- `Dockerfile` (created in TASK-4.1)
- `config.py` (all env vars)
- `main.py` (port number)

**Success criteria:**
- `docker compose up` builds and starts the bot
- `docker compose ps` shows the bot as healthy
- `curl localhost:8080/health` returns JSON with status
- `docker compose down && docker compose up` restarts cleanly
- Data persists in `./data` between restarts

---

## PHASE 5: N8N Integration (Optional)

---

### TASK-5.1: Add N8N order notification webhook

```
priority: P2
estimate: 2h
status: pending
depends_on: TASK-2.3
```

**Problem:**
When an order is confirmed, the owner has no notification with order details.

**Change specification:**

1. Add to `config.py`:
```python
N8N_ORDER_WEBHOOK_URL = os.getenv("N8N_ORDER_WEBHOOK_URL", "")
```

2. Create `integrations/__init__.py` (empty) and `integrations/n8n.py`:
```python
"""N8N webhook integration for order notifications."""
import logging
import httpx
from config import N8N_ORDER_WEBHOOK_URL

logger = logging.getLogger(__name__)

async def notify_order_confirmed(chat_id: str, order_ctx: dict, sender_name: str = "") -> None:
    """Fire-and-forget webhook to N8N when order is confirmed."""
    if not N8N_ORDER_WEBHOOK_URL:
        return
    payload = {
        "event": "order_confirmed",
        "chat_id": chat_id,
        "sender_name": sender_name,
        "product": order_ctx.get("product", ""),
        "size": order_ctx.get("size", ""),
        "color": order_ctx.get("color", ""),
        "city": order_ctx.get("city", ""),
        "address": order_ctx.get("address", ""),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(N8N_ORDER_WEBHOOK_URL, json=payload)
    except Exception as e:
        logger.warning(f"Failed to notify N8N: {e}")
```

3. In `ai/engine.py`, after order confirmation at ~line 951 (where `_ORDER_CONFIRM_TEXT` is added):
```python
from integrations.n8n import notify_order_confirmed
...
# After line 951, inside the `else` block (product available):
asyncio.create_task(notify_order_confirmed(chat_id, order_ctx, sender_name))
```

**Context files (must read before editing):**
- `config.py` (add env var)
- `ai/engine.py:920-960` (order confirmation block)
- `requirements.txt` (httpx should already be added in TASK-2.3)

**Success criteria:**
- When `N8N_ORDER_WEBHOOK_URL` is set, order confirmation fires async POST
- When `N8N_ORDER_WEBHOOK_URL` is empty, no request is made
- Webhook failure does not break the order flow (fire-and-forget)
- Payload contains all order fields + chat_id + sender_name
