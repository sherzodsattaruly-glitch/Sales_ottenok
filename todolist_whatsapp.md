# TODO: WhatsApp Order Notification + Soft Handoff

<context>
project: Sales_ottenok — WhatsApp sales bot "Алина" for women's shoes & accessories store "Оттенок"
problem: Bot and managers share ONE WhatsApp number. When bot completes an order, managers have no way to know which chat is ready for takeover.
solution: (1) Send client contact card + order details to a WhatsApp group. (2) Auto-detect manager typing in client chat and silence bot.
stack: Python 3.11, FastAPI, GREEN-API (WhatsApp), SQLite (aiosqlite), asyncio
green_api_docs: SendContact endpoint = POST {BASE_URL}/sendContact/{TOKEN}, body = {"chatId": str, "contact": {"phoneContact": int, "firstName": str}}
green_api_docs: outgoingMessageReceived = webhook type for messages typed manually from phone (NOT via API)
green_api_docs: outgoingAPIMessageReceived = webhook type for messages sent via API (bot's own messages) — must be IGNORED
green_api_docs: group chat_id format = "phone-timestamp@g.us"
</context>

---

## TASK-1: Add send_contact() to GREEN-API client

<task>
id: TASK-1
status: done
effort: 1h
depends_on: none
</task>

<instructions>
Add async function send_contact(chat_id, phone_contact, first_name, last_name="") to greenapi/client.py.
Copy the exact pattern from send_text() function in the same file.
Endpoint: POST {BASE_URL}/sendContact/{GREEN_API_TOKEN}
Request body: {"chatId": chat_id, "contact": {"phoneContact": phone_contact, "firstName": first_name, "lastName": last_name}}
phone_contact type is int (phone number without + sign).
Decorate with @retry_async(max_retries=3, delay=1.0).
Use httpx.AsyncClient(timeout=30).
Log successful send with logger.info.
</instructions>

<required_context>
greenapi/client.py — read full file, copy pattern from send_text() at lines 42-55
</required_context>

<modify>
greenapi/client.py — add send_contact() function after send_text()
</modify>

<success_criteria>
- Function send_contact exists in greenapi/client.py with signature: async def send_contact(chat_id: str, phone_contact: int, first_name: str, last_name: str = "") -> dict
- Decorated with @retry_async(max_retries=3, delay=1.0)
- Sends POST to {BASE_URL}/sendContact/{GREEN_API_TOKEN} with correct JSON payload
- Returns parsed JSON response
- Logs successful send
</success_criteria>

---

## TASK-2: Add ORDER_NOTIFICATION_GROUP_ID config variable

<task>
id: TASK-2
status: done
effort: 0.5h
depends_on: none
</task>

<instructions>
Add one line to config.py:
ORDER_NOTIFICATION_GROUP_ID = os.getenv("ORDER_NOTIFICATION_GROUP_ID", "")
Place it near other integration-related vars (near N8N_ORDER_WEBHOOK_URL).
</instructions>

<required_context>
config.py — read full file, find where N8N_ORDER_WEBHOOK_URL is defined
</required_context>

<modify>
config.py — add one line
</modify>

<success_criteria>
- ORDER_NOTIFICATION_GROUP_ID is importable from config
- Defaults to empty string when env var not set
</success_criteria>

---

## TASK-3: Create order notification function

<task>
id: TASK-3
status: done
effort: 2h
depends_on: TASK-1, TASK-2
</task>

<instructions>
Create new file integrations/order_notifications.py with function notify_order_to_group(chat_id, order_ctx, sender_name).

Logic:
1. If ORDER_NOTIFICATION_GROUP_ID is empty — return silently (no error)
2. Extract phone from chat_id: strip "@c.us" suffix, convert to int
3. Call send_contact(ORDER_NOTIFICATION_GROUP_ID, phone_int, sender_name or "Клиент")
4. Build order details text message:
   "Новый заказ от {sender_name or phone}:\nТовар: {product}\nРазмер: {size}\nЦвет: {color}\nГород: {city}\nАдрес: {address}"
   Only include size/color lines if values are non-empty.
5. Call send_text(ORDER_NOTIFICATION_GROUP_ID, details_text)
6. Wrap everything in try/except — catch all exceptions, log with logger.error, never raise
7. If phone parsing fails — log warning, skip contact card, still send text message
</instructions>

<required_context>
integrations/n8n.py — copy fire-and-forget async pattern from notify_order_confirmed()
greenapi/client.py — understand send_contact() and send_text() signatures
config.py — import ORDER_NOTIFICATION_GROUP_ID
</required_context>

<create>
integrations/order_notifications.py
</create>

<success_criteria>
- Function notify_order_to_group exists and is importable from integrations.order_notifications
- Sends contact card with client phone number and name to group
- Sends formatted order details text to group
- Returns silently when ORDER_NOTIFICATION_GROUP_ID is empty
- Handles invalid chat_id format gracefully (logs warning, skips contact card, still sends text)
- All exceptions caught and logged — function never raises
</success_criteria>

---

## TASK-4: Wire notification into order confirmation flow

<task>
id: TASK-4
status: done
effort: 1h
depends_on: TASK-3
</task>

<instructions>
In ai/engine.py:
1. Add import at top: from integrations.order_notifications import notify_order_to_group
2. Find the order confirmation block where _ORDER_CONFIRM_TEXT is appended to assistant_text (around line 898-902).
   Look for the line: asyncio.create_task(notify_order_confirmed(chat_id, order_ctx, sender_name))
3. Add right after it: asyncio.create_task(notify_order_to_group(chat_id, order_ctx, sender_name))
4. Do NOT add any handoff logic here — bot continues responding normally
</instructions>

<required_context>
ai/engine.py — read lines 860-920 (order confirmation block in generate_response())
ai/order_manager.py — understand _contains_order_confirm() and _ORDER_CONFIRM_TEXT
integrations/n8n.py — see how notify_order_confirmed is called as asyncio.create_task
</required_context>

<modify>
ai/engine.py — add import + one asyncio.create_task() line
</modify>

<success_criteria>
- notify_order_to_group is called as asyncio.create_task when order is confirmed
- Called with arguments: (chat_id, order_ctx, sender_name)
- Placed right next to existing notify_order_confirmed() call
- Bot still responds to client normally after notification — no handoff triggered
</success_criteria>

---

## TASK-5: Implement soft handoff — detect manager manual messages

<task>
id: TASK-5
status: done
effort: 3h
depends_on: none
</task>

<instructions>
When a manager manually types a message in a client's chat from the phone, auto-enable handoff so bot stops responding.

### Step 5a: greenapi/webhook.py
1. Add imports: from config import MANAGER_CHAT_IDS and from db.conversations import set_handoff_state
2. Create async helper function _handle_outgoing_message(body: dict) -> None:
   - Extract chat_id from body.get("senderData", {}).get("chatId", "")
   - If not chat_id: return
   - If "@g.us" in chat_id: return (skip group chats)
   - If chat_id in MANAGER_CHAT_IDS: return (skip manager's own chats)
   - await set_handoff_state(chat_id, True)
   - logger.info(f"[{chat_id}] Outgoing message detected (manager takeover), enabling handoff")
   - Wrap in try/except, log errors
3. In the webhook handler function, BEFORE the "incomingMessageReceived" check, add:
   if type_webhook == "outgoingMessageReceived":
       asyncio.create_task(_handle_outgoing_message(body))
       return Response(status_code=200)
4. Do NOT handle "outgoingAPIMessageReceived" — those are bot's own API messages

### Step 5b: greenapi/poller.py
1. Import _handle_outgoing_message from greenapi.webhook
2. In the polling loop, BEFORE the "incomingMessageReceived" check, add:
   if type_webhook == "outgoingMessageReceived":
       await _handle_outgoing_message(body)
       if receipt_id is not None:
           await delete_notification(receipt_id)
       continue

### Step 5c: Manual configuration (not code)
Enable outgoingMessageWebhook in GREEN-API instance settings:
POST /waInstance{id}/setSettings/{token} with {"outgoingMessageWebhook": true}
Or enable via GREEN-API dashboard.
</instructions>

<required_context>
greenapi/webhook.py — read full file, understand webhook handler flow and typeWebhook check
greenapi/poller.py — read full file, understand polling loop and typeWebhook check
db/conversations.py — read set_handoff_state() function (around line 120) and get_handoff_state()
config.py — read MANAGER_CHAT_IDS definition
ai/engine.py — read handle_message() (around line 1179) to see existing handoff check with get_handoff_state
</required_context>

<modify>
greenapi/webhook.py — add _handle_outgoing_message() function + handle outgoingMessageReceived in handler
greenapi/poller.py — handle outgoingMessageReceived in polling loop, delegate to _handle_outgoing_message
</modify>

<success_criteria>
- outgoingMessageReceived type is handled in both webhook.py and poller.py
- outgoingAPIMessageReceived type is NOT handled (bot's own messages must be ignored)
- Group chats (@g.us) are skipped — no handoff for group messages
- Manager chat IDs are skipped — no handoff when manager chats with another manager
- set_handoff_state(chat_id, True) is called for client chats when manager types manually
- After handoff enabled, bot stops responding (existing get_handoff_state check in handle_message handles this)
- /handoff off {phone} command still works to re-enable bot (existing logic, no changes needed)
- All exceptions caught and logged in _handle_outgoing_message
</success_criteria>

---

## TASK-6: End-to-end verification

<task>
id: TASK-6
status: done
effort: 2h
depends_on: TASK-1, TASK-2, TASK-3, TASK-4, TASK-5
</task>

<instructions>
1. Add ORDER_NOTIFICATION_GROUP_ID=your-group-id@g.us to .env
2. Enable outgoingMessageWebhook: true in GREEN-API settings
3. Start bot: python main.py
4. Test order notification:
   - Complete a full order flow (provide city, product, size if needed, color if needed, address)
   - Verify contact card appears in WhatsApp group
   - Verify order details text appears in group
   - Tap contact card — verify it opens client chat
   - Verify bot still responds to client after order
5. Test soft handoff:
   - Manually type a message in client chat from phone
   - Send another client message — verify bot does NOT respond
   - Send /handoff off {phone} — verify bot responds again
6. Test edge cases:
   - Remove ORDER_NOTIFICATION_GROUP_ID from .env — verify no errors
   - Type in WhatsApp group — verify no handoff triggered
   - Verify bot's own API-sent messages do NOT trigger handoff
</instructions>

<required_context>
all modified files: config.py, greenapi/client.py, integrations/order_notifications.py, ai/engine.py, greenapi/webhook.py, greenapi/poller.py
</required_context>

<success_criteria>
- Complete order triggers contact card + details in WhatsApp group
- Contact card is tappable and opens correct client chat
- Manager typing in client chat triggers automatic handoff
- Bot's own messages do not trigger handoff
- /handoff off restores bot responses
- No errors in data/sales_ottenok.log during normal operation
- Empty ORDER_NOTIFICATION_GROUP_ID does not cause errors
</success_criteria>
