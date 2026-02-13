"""
Бизнес-логика для автоматического дожима клиентов.
Определяет правила отправки дожимов.
"""

from datetime import datetime, timedelta
from typing import Optional
import re

# Рабочие часы для отправки дожимов (9:00 - 19:00)
WORK_HOURS_START = 9
WORK_HOURS_END = 19

# Задержка для первого дожима
NUDGE_DELAY_FIRST = timedelta(hours=3)

# Максимальное количество дожимов
MAX_NUDGE_COUNT = 2

# Тексты дожимов
NUDGE_MESSAGES = {
    1: "Хотела уточнить, актуальна ли модель? Если есть вопросы - с радостью подскажу",
    2: "Добрый день! Вчера вы интересовались моделью из рекламы. Напомню: у нас в магазине есть примерка и возможность возврата - вы ничем не рискуете."
}

# Паттерны для определения "подумаю"
_MAYBE_PATTERNS = [
    r"\bподума(ю|ть|й|ем)\b",
    r"\bпоже\b",
    r"\bпотом\b",
    r"\bпозднее\b",
    r"\bзавтра\b",
]


def is_work_hours(dt: datetime) -> bool:
    """
    Проверить, находится ли время в рабочих часах (9:00 - 19:00).

    Args:
        dt: Время для проверки

    Returns:
        True если время в рабочих часах
    """
    return WORK_HOURS_START <= dt.hour < WORK_HOURS_END


def calculate_next_nudge_time(last_message_at: datetime, nudge_count: int) -> Optional[datetime]:
    """
    Вычислить время следующего дожима.

    Логика:
    - nudge_count=0: через 3 часа (с коррекцией на рабочие часы)
    - nudge_count=1: на следующий день в 13:00
    - nudge_count>=2: None (больше не дожимаем)

    Args:
        last_message_at: Время последнего сообщения клиента
        nudge_count: Текущее количество отправленных дожимов

    Returns:
        Время следующего дожима или None если больше не дожимаем
    """
    if nudge_count >= MAX_NUDGE_COUNT:
        return None

    if nudge_count == 0:
        # Первый дожим: через 3 часа, но только в рабочее время
        next_time = last_message_at + NUDGE_DELAY_FIRST

        # Проверяем, попадает ли в рабочие часы
        if not is_work_hours(next_time):
            # Если нет, переносим на следующий рабочий день в WORK_HOURS_START
            if next_time.hour < WORK_HOURS_START:
                # Слишком рано - переносим на сегодня в 9:00
                next_time = next_time.replace(hour=WORK_HOURS_START, minute=0, second=0, microsecond=0)
            else:
                # Слишком поздно - переносим на завтра в 9:00
                next_time = (next_time + timedelta(days=1)).replace(
                    hour=WORK_HOURS_START, minute=0, second=0, microsecond=0
                )

        return next_time

    elif nudge_count == 1:
        # Второй дожим: на следующий день в 13:00
        next_time = (last_message_at + timedelta(days=1)).replace(
            hour=13, minute=0, second=0, microsecond=0
        )
        return next_time

    return None


def get_nudge_message(nudge_count: int, product_name: str = "") -> str:
    """
    Получить текст дожима.

    Args:
        nudge_count: Номер дожима (1 или 2)
        product_name: Название товара для персонализации (опционально)

    Returns:
        Текст сообщения
    """
    base_message = NUDGE_MESSAGES.get(nudge_count, "")

    # Можно добавить персонализацию в будущем
    # Например: if product_name:
    #     return base_message.replace("моделью", f"моделью {product_name}")

    return base_message


def is_maybe_response(text: str) -> bool:
    """
    Проверить, является ли ответ клиента "подумаю"/"позже" и т.д.

    Args:
        text: Текст сообщения клиента

    Returns:
        True если клиент говорит "подумаю"
    """
    if not text:
        return False

    text_lower = text.lower()

    for pattern in _MAYBE_PATTERNS:
        if re.search(pattern, text_lower):
            return True

    return False


def should_nudge_client(
    last_client_message_at: datetime,
    last_bot_message_at: datetime,
    nudge_count: int,
    handoff_enabled: bool,
    last_client_text: str = ""
) -> bool:
    """
    Проверить, нужно ли отправить дожим клиенту.

    Условия для отправки дожима:
    - handoff_enabled=False (диалог не передан менеджеру)
    - nudge_count < MAX_NUDGE_COUNT
    - Клиент не ответил после последнего сообщения бота
    - Сейчас рабочее время
    - Прошло достаточно времени согласно calculate_next_nudge_time

    Args:
        last_client_message_at: Время последнего сообщения клиента
        last_bot_message_at: Время последнего сообщения бота
        nudge_count: Текущее количество дожимов
        handoff_enabled: Передан ли диалог менеджеру
        last_client_text: Текст последнего сообщения клиента (для проверки "подумаю")

    Returns:
        True если нужно отправить дожим
    """
    # Если диалог передан менеджеру - не дожимаем
    if handoff_enabled:
        return False

    # Если уже отправили максимум дожимов - не дожимаем
    if nudge_count >= MAX_NUDGE_COUNT:
        return False

    # Если клиент написал после бота - не дожимаем
    if last_client_message_at >= last_bot_message_at:
        return False

    # Если сейчас не рабочее время - не дожимаем
    now = datetime.now()
    if not is_work_hours(now):
        return False

    # Вычисляем время следующего дожима
    next_nudge_time = calculate_next_nudge_time(last_client_message_at, nudge_count)

    if next_nudge_time is None:
        return False

    # Если время пришло - дожимаем
    if now >= next_nudge_time:
        return True

    return False
