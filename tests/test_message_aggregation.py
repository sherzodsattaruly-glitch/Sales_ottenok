"""
Tests for message aggregation (debounce buffering) in webhook.py.
"""

import asyncio

import pytest
from unittest.mock import AsyncMock


@pytest.fixture(autouse=True)
def clear_buffers():
    """Clear all module-level buffer state before and after each test."""
    from greenapi import webhook

    webhook._message_buffers.clear()
    webhook._buffer_sender.clear()
    for task in webhook._buffer_timers.values():
        task.cancel()
    webhook._buffer_timers.clear()
    webhook._chat_locks.clear()
    yield
    webhook._message_buffers.clear()
    webhook._buffer_sender.clear()
    for task in webhook._buffer_timers.values():
        task.cancel()
    webhook._buffer_timers.clear()
    webhook._chat_locks.clear()


@pytest.mark.asyncio
async def test_aggregation_combines_messages(monkeypatch):
    """Three rapid messages should be combined into one with newlines."""
    monkeypatch.setattr("greenapi.webhook.MESSAGE_AGGREGATION_DELAY", 0.3)

    from greenapi.webhook import process_incoming_message, set_message_handler

    handler = AsyncMock()
    set_message_handler(handler)

    await process_incoming_message("test@c.us", "Alice", "здравствуйте")
    await process_incoming_message("test@c.us", "Alice", "можете показать")
    await process_incoming_message("test@c.us", "Alice", "какие есть туфли?")

    # Handler should NOT have been called yet
    handler.assert_not_called()

    # Wait for flush
    await asyncio.sleep(0.5)

    handler.assert_called_once_with(
        "test@c.us", "Alice", "здравствуйте\nможете показать\nкакие есть туфли?"
    )


@pytest.mark.asyncio
async def test_single_message_delayed(monkeypatch):
    """A single message should be processed after the aggregation delay."""
    monkeypatch.setattr("greenapi.webhook.MESSAGE_AGGREGATION_DELAY", 0.2)

    from greenapi.webhook import process_incoming_message, set_message_handler

    handler = AsyncMock()
    set_message_handler(handler)

    await process_incoming_message("single@c.us", "Bob", "привет")

    handler.assert_not_called()
    await asyncio.sleep(0.4)

    handler.assert_called_once_with("single@c.us", "Bob", "привет")


@pytest.mark.asyncio
async def test_manager_command_immediate(monkeypatch):
    """Manager /handoff commands should be processed immediately."""
    monkeypatch.setattr("greenapi.webhook.MESSAGE_AGGREGATION_DELAY", 1.0)
    monkeypatch.setattr("greenapi.webhook.MANAGER_CHAT_IDS", {"manager@c.us"})

    from greenapi.webhook import process_incoming_message, set_message_handler

    handler = AsyncMock()
    set_message_handler(handler)

    await process_incoming_message("manager@c.us", "Manager", "/handoff on 77001234567")

    handler.assert_called_once_with("manager@c.us", "Manager", "/handoff on 77001234567")


@pytest.mark.asyncio
async def test_bot_command_immediate(monkeypatch):
    """Manager /bot commands should be processed immediately."""
    monkeypatch.setattr("greenapi.webhook.MESSAGE_AGGREGATION_DELAY", 1.0)
    monkeypatch.setattr("greenapi.webhook.MANAGER_CHAT_IDS", {"manager@c.us"})

    from greenapi.webhook import process_incoming_message, set_message_handler

    handler = AsyncMock()
    set_message_handler(handler)

    await process_incoming_message("manager@c.us", "Manager", "/bot on")

    handler.assert_called_once_with("manager@c.us", "Manager", "/bot on")


@pytest.mark.asyncio
async def test_aggregation_disabled(monkeypatch):
    """When delay is 0, messages should be processed immediately."""
    monkeypatch.setattr("greenapi.webhook.MESSAGE_AGGREGATION_DELAY", 0)

    from greenapi.webhook import process_incoming_message, set_message_handler

    handler = AsyncMock()
    set_message_handler(handler)

    await process_incoming_message("imm@c.us", "Charlie", "hello")

    handler.assert_called_once_with("imm@c.us", "Charlie", "hello")


@pytest.mark.asyncio
async def test_sender_name_from_first(monkeypatch):
    """sender_name should be taken from the first message in the batch."""
    monkeypatch.setattr("greenapi.webhook.MESSAGE_AGGREGATION_DELAY", 0.2)

    from greenapi.webhook import process_incoming_message, set_message_handler

    handler = AsyncMock()
    set_message_handler(handler)

    await process_incoming_message("name@c.us", "FirstName", "msg1")
    await process_incoming_message("name@c.us", "ChangedName", "msg2")

    await asyncio.sleep(0.4)

    handler.assert_called_once()
    assert handler.call_args[0][1] == "FirstName"


@pytest.mark.asyncio
async def test_independent_chat_buffers(monkeypatch):
    """Different chat_ids should have independent buffers."""
    monkeypatch.setattr("greenapi.webhook.MESSAGE_AGGREGATION_DELAY", 0.2)

    from greenapi.webhook import process_incoming_message, set_message_handler

    handler = AsyncMock()
    set_message_handler(handler)

    await process_incoming_message("chat1@c.us", "A", "hello")
    await process_incoming_message("chat2@c.us", "B", "world")

    await asyncio.sleep(0.4)

    assert handler.call_count == 2


@pytest.mark.asyncio
async def test_timer_resets(monkeypatch):
    """Each new message should reset the timer."""
    monkeypatch.setattr("greenapi.webhook.MESSAGE_AGGREGATION_DELAY", 0.3)

    from greenapi.webhook import process_incoming_message, set_message_handler

    handler = AsyncMock()
    set_message_handler(handler)

    await process_incoming_message("reset@c.us", "X", "msg1")
    await asyncio.sleep(0.2)  # < 0.3, timer not fired yet
    await process_incoming_message("reset@c.us", "X", "msg2")

    # At t=0.2 from msg2, original timer would have fired but was reset
    await asyncio.sleep(0.15)
    handler.assert_not_called()

    # At t=0.3+ from msg2, new timer fires
    await asyncio.sleep(0.2)
    handler.assert_called_once_with("reset@c.us", "X", "msg1\nmsg2")
