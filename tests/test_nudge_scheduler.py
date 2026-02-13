"""
Тесты для модуля автоматического дожима (scheduler).
"""

import pytest
from datetime import datetime, timedelta
from scheduler.nudge_rules import (
    is_work_hours,
    calculate_next_nudge_time,
    get_nudge_message,
    is_maybe_response,
    should_nudge_client,
)


def test_is_work_hours():
    """Тест проверки рабочих часов."""
    # Рабочее время (9:00-19:00)
    work_time = datetime(2024, 1, 1, 12, 0)
    assert is_work_hours(work_time) is True

    # Раннее утро
    early_morning = datetime(2024, 1, 1, 7, 0)
    assert is_work_hours(early_morning) is False

    # Поздний вечер
    late_evening = datetime(2024, 1, 1, 20, 0)
    assert is_work_hours(late_evening) is False

    # Граничные случаи
    start_time = datetime(2024, 1, 1, 9, 0)
    assert is_work_hours(start_time) is True

    end_time = datetime(2024, 1, 1, 19, 0)
    assert is_work_hours(end_time) is False  # 19:00 уже нерабочее время


def test_calculate_next_nudge_time_first_nudge():
    """Тест вычисления времени первого дожима (через 3 часа)."""
    # Клиент написал в 14:00
    last_message = datetime(2024, 1, 1, 14, 0)

    next_time = calculate_next_nudge_time(last_message, nudge_count=0)

    # Первый дожим должен быть через 3 часа = 17:00
    assert next_time is not None
    assert next_time.hour == 17
    assert next_time.minute == 0


def test_calculate_next_nudge_time_first_nudge_late():
    """Тест вычисления первого дожима когда 3 часа выпадают на нерабочее время."""
    # Клиент написал в 17:00, через 3 часа будет 20:00 (нерабочее время)
    last_message = datetime(2024, 1, 1, 17, 0)

    next_time = calculate_next_nudge_time(last_message, nudge_count=0)

    # Должен перенести на следующий день в 9:00
    assert next_time is not None
    assert next_time.day == 2  # Следующий день
    assert next_time.hour == 9
    assert next_time.minute == 0


def test_calculate_next_nudge_time_second_nudge():
    """Тест вычисления времени второго дожима (на следующий день в 13:00)."""
    last_message = datetime(2024, 1, 1, 14, 0)

    next_time = calculate_next_nudge_time(last_message, nudge_count=1)

    # Второй дожим должен быть на следующий день в 13:00
    assert next_time is not None
    assert next_time.day == 2
    assert next_time.hour == 13
    assert next_time.minute == 0


def test_calculate_next_nudge_time_max_reached():
    """Тест что после 2 дожимов больше не дожимаем."""
    last_message = datetime(2024, 1, 1, 14, 0)

    next_time = calculate_next_nudge_time(last_message, nudge_count=2)

    assert next_time is None


def test_get_nudge_message():
    """Тест получения текста дожима."""
    message1 = get_nudge_message(1)
    assert "актуальна ли модель" in message1

    message2 = get_nudge_message(2)
    assert "Вчера вы интересовались" in message2


def test_is_maybe_response():
    """Тест определения ответа 'подумаю'."""
    assert is_maybe_response("Я подумаю") is True
    assert is_maybe_response("подумать хочу") is True
    assert is_maybe_response("напишу позже") is True
    assert is_maybe_response("Хорошо, спасибо") is False
    assert is_maybe_response("Да, заказываю") is False


def test_should_nudge_client_basic(test_client_data):
    """Тест базовой логики дожима."""
    # Клиент не ответил, прошло 4 часа, рабочее время
    now = datetime.now()
    if not is_work_hours(now):
        # Пропускаем тест если сейчас нерабочее время
        pytest.skip("Test requires work hours")

    should = should_nudge_client(
        last_client_message_at=test_client_data["last_client_message_at"],
        last_bot_message_at=test_client_data["last_bot_message_at"],
        nudge_count=test_client_data["nudge_count"],
        handoff_enabled=test_client_data["handoff_enabled"],
        last_client_text=test_client_data["last_client_text"],
    )

    # Должен дожать если прошло больше 3 часов
    assert should is True


def test_should_nudge_client_handoff_enabled(test_client_data):
    """Тест что не дожимаем если включен handoff."""
    should = should_nudge_client(
        last_client_message_at=test_client_data["last_client_message_at"],
        last_bot_message_at=test_client_data["last_bot_message_at"],
        nudge_count=0,
        handoff_enabled=True,  # Включен handoff
        last_client_text=test_client_data["last_client_text"],
    )

    assert should is False


def test_should_nudge_client_max_nudges_reached(test_client_data):
    """Тест что не дожимаем если достигнут лимит."""
    should = should_nudge_client(
        last_client_message_at=test_client_data["last_client_message_at"],
        last_bot_message_at=test_client_data["last_bot_message_at"],
        nudge_count=2,  # Уже 2 дожима
        handoff_enabled=False,
        last_client_text=test_client_data["last_client_text"],
    )

    assert should is False


def test_should_nudge_client_client_replied(test_client_data):
    """Тест что не дожимаем если клиент ответил после бота."""
    should = should_nudge_client(
        last_client_message_at=datetime.now(),  # Клиент только что ответил
        last_bot_message_at=test_client_data["last_bot_message_at"],
        nudge_count=0,
        handoff_enabled=False,
        last_client_text="Спасибо",
    )

    assert should is False
