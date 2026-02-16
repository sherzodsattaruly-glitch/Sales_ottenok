# Sales Ottenok - WhatsApp Sales Bot

AI-менеджер по продажам для магазина женской обуви и аксессуаров "Оттенок" с автоматическим дожимом клиентов.

## Возможности

- **Прием сообщений** через GREEN-API (webhook или polling)
- **RAG система** - отвечает только из базы знаний, не выдумывает
- **Проверка наличия** товаров из Excel перед оформлением заказа
- **Поиск и отправка фото** из Google Drive (1-3 фото с фильтрацией по цвету)
- **Автоматический дожим** клиентов:
  - Дожим #1: через 3 часа после последнего сообщения (только 9:00-19:00)
  - Дожим #2: на следующий день в 13:00 (если клиент не ответил или сказал "подумаю")
- **Сбор данных заказа** (товар, город, размер, цвет, адрес)
- **Оформление заказа** когда все данные собраны и наличие подтверждено
- **Передача менеджеру** (handoff) для сложных случаев

## Стек технологий

- **Python 3.11+**
- **FastAPI** - веб-сервер для webhook
- **GREEN-API** - интеграция с WhatsApp
- **OpenAI GPT-4o** - генерация ответов
- **ChromaDB** - векторная БД для RAG
- **Google Drive API** - хранение фото товаров
- **SQLite** - хранение диалогов и состояния
- **APScheduler** - автоматический дожим клиентов
- **pandas + openpyxl** - работа с Excel наличием

## Установка и запуск (локально)

### 1. Клонируйте проект

```bash
cd sales_ottenok
```

### 2. Создайте виртуальное окружение

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/Mac
source .venv/bin/activate
```

### 3. Установите зависимости

```bash
pip install -r requirements.txt
```

### 4. Настройте `.env`

Отредактируйте файл `.env` и укажите ваши credentials:

```env
# Green API
GREEN_API_INSTANCE_ID=ваш_instance_id
GREEN_API_TOKEN=ваш_токен

# OpenAI
OPENAI_API_KEY=sk-proj-...

# Google Drive
GOOGLE_DRIVE_PHOTOS_FOLDER_ID=ваш_folder_id

# Остальные настройки оставьте по умолчанию
```

### 5. Подготовьте данные

#### a) Создайте Excel файл с наличием

Создайте `data/inventory.xlsx` с колонками:

| product_name | size | color | quantity | price |
|--------------|------|-------|----------|-------|
| Chanel Jumbo | -    | черные| 2        | 45000₸|

#### b) Соберите базу знаний (документы Word)

Положите `.docx` файлы в `data/knowledge_base/` и запустите:

```bash
python -m knowledge.builder
```

#### c) Настройте Google Drive credentials

1. Скачайте `google_credentials.json` из Google Cloud Console
2. Поместите в `credentials/google_credentials.json`
3. При первом запуске пройдите OAuth авторизацию

### 6. Запустите бота

```bash
python main.py
```

Бот будет доступен на `http://localhost:8080`

### 7. Настройте webhook в GREEN-API

В личном кабинете GREEN-API укажите:

```
Webhook URL: https://ваш-домен.com/webhook
```

Или используйте polling (включен по умолчанию в `.env`).

## Deployment на VPS (Ubuntu/Debian)

### Автоматическая установка

```bash
chmod +x deploy/setup.sh
sudo ./deploy/setup.sh
```

Скрипт автоматически:
- Установит Python 3.11+
- Создаст пользователя и виртуальное окружение
- Настроит systemd сервис
- Настроит nginx reverse proxy

### Ручная установка

#### 1. Скопируйте проект на сервер

```bash
scp -r sales_ottenok/ user@server:/opt/sales_ottenok
```

#### 2. Установите зависимости

```bash
cd /opt/sales_ottenok
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### 3. Настройте systemd

```bash
sudo cp deploy/systemd/sales_ottenok.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sales_ottenok
sudo systemctl start sales_ottenok
```

#### 4. Настройте nginx

```bash
sudo cp deploy/nginx/sales_ottenok.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/sales_ottenok.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

#### 5. Настройте SSL (Let's Encrypt)

```bash
sudo certbot --nginx -d yourdomain.com
```

## Тестирование

### Запуск всех тестов

```bash
pytest tests/ -v
```

### Запуск конкретных тестов

```bash
# Тесты проверки наличия
pytest tests/test_stock_checker.py -v

# Тесты дожима
pytest tests/test_nudge_scheduler.py -v
```

## Структура проекта

```
sales_ottenok/
├── ai/                    # AI engine (RAG + GPT)
├── greenapi/              # GREEN-API интеграция
├── gdrive/                # Google Drive API
├── inventory/             # Проверка наличия из Excel
├── scheduler/             # Автоматический дожим
├── db/                    # SQLite БД
├── knowledge/             # Обработка базы знаний
├── data/                  # Данные (БД, ChromaDB, Excel)
├── deploy/                # Deployment скрипты
├── tests/                 # Тесты
├── main.py                # Точка входа
├── config.py              # Конфигурация
└── requirements.txt       # Зависимости
```

## Логика автоматического дожима

### Дожим #1 (через 3 часа)

- **Условие**: Клиент не ответил после последнего сообщения бота
- **Время**: Через 3 часа (только в рабочее время 9:00-19:00)
- **Текст**: *"Хотела уточнить, актуальна ли модель? Если есть вопросы - с радостью подскажу"*

### Дожим #2 (на следующий день в 13:00)

- **Условие**: Клиент не ответил ИЛИ сказал "подумаю" после первого дожима
- **Время**: На следующий день в 13:00
- **Текст**: *"Добрый день! Вчера вы интересовались моделью из рекламы. Напомню: у нас в магазине есть примерка и возможность возврата - вы ничем не рискуете."*

### Остановка дожима

- Клиент ответил (кроме "подумаю") → сброс счетчика
- Включен handoff (передача менеджеру) → дожим останавливается
- Отправлено 2 дожима → больше не дожимаем

## Проверка наличия товара

Перед оформлением заказа бот автоматически проверяет наличие в `data/inventory.xlsx`:

1. **Товар в наличии** → оформляет заказ
2. **Товар закончился** → сообщает клиенту, предлагает альтернативы
3. **Товар не найден** → предлагает похожие варианты

Обновите Excel файл → бот автоматически обновит данные (кэш 5 минут).

## Команды менеджера

Отправьте боту от номера менеджера:

```
/handoff on 77001234567   # Включить передачу для клиента
/handoff off 77001234567  # Выключить передачу
/handoff status 77001234567  # Проверить статус
```

## Логи

```bash
# Просмотр логов
tail -f data/sales_ottenok.log

# Логи systemd (на VPS)
sudo journalctl -u sales_ottenok -f
```

## Troubleshooting

### Бот не отвечает на сообщения

1. Проверьте webhook в GREEN-API
2. Проверьте логи: `tail -f data/sales_ottenok.log`
3. Убедитесь что polling включен: `GREEN_API_POLLING=1` в `.env`

### Дожимы не отправляются

1. Проверьте `NUDGE_ENABLED=1` в `.env`
2. Проверьте логи scheduler: grep "nudge" в логах
3. Проверьте БД: `sqlite3 data/ottenok.db "SELECT * FROM clients"`

### Excel файл не загружается

1. Проверьте путь: `INVENTORY_EXCEL_PATH=data/inventory.xlsx`
2. Проверьте формат колонок: `product_name | size | color | quantity | price`
3. Установите openpyxl: `pip install openpyxl==3.1.2`

## Лицензия

MIT

## Автор

Создано для магазина "Оттенок"
