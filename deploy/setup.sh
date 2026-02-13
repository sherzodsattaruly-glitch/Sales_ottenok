#!/bin/bash
# Автоматическая установка Sales Ottenok Bot на Ubuntu/Debian VPS

set -e

echo "============================================"
echo "Sales Ottenok Bot - Автоматическая установка"
echo "============================================"

# Проверка прав root
if [ "$EUID" -ne 0 ]; then
    echo "❌ Запустите скрипт с sudo"
    exit 1
fi

# Обновление системы
echo "📦 Обновление системы..."
apt-get update
apt-get upgrade -y

# Установка зависимостей
echo "📦 Установка зависимостей..."
apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3-pip \
    nginx \
    certbot \
    python3-certbot-nginx \
    git \
    sqlite3

# Создание пользователя
echo "👤 Создание пользователя ottenok..."
if ! id "ottenok" &>/dev/null; then
    useradd -r -m -s /bin/bash ottenok
fi

# Создание директории проекта
PROJECT_DIR="/opt/sales_ottenok"
echo "📁 Создание директории $PROJECT_DIR..."
mkdir -p $PROJECT_DIR
chown -R ottenok:ottenok $PROJECT_DIR

# Копирование файлов (предполагается что скрипт запускается из корня проекта)
echo "📋 Копирование файлов..."
if [ -f "requirements.txt" ]; then
    cp -r . $PROJECT_DIR/
    chown -R ottenok:ottenok $PROJECT_DIR
else
    echo "⚠️  Скрипт должен быть запущен из корня проекта sales_ottenok"
    exit 1
fi

# Создание виртуального окружения
echo "🐍 Создание виртуального окружения..."
sudo -u ottenok python3.11 -m venv $PROJECT_DIR/.venv
sudo -u ottenok $PROJECT_DIR/.venv/bin/pip install --upgrade pip
sudo -u ottenok $PROJECT_DIR/.venv/bin/pip install -r $PROJECT_DIR/requirements.txt

# Создание директории для логов
echo "📝 Создание директории логов..."
mkdir -p /var/log/sales_ottenok
chown -R ottenok:ottenok /var/log/sales_ottenok

# Создание директории данных
echo "💾 Создание директории данных..."
mkdir -p $PROJECT_DIR/data/chroma_db
mkdir -p $PROJECT_DIR/data/knowledge_base
chown -R ottenok:ottenok $PROJECT_DIR/data

# Настройка systemd
echo "⚙️  Настройка systemd сервиса..."
cp $PROJECT_DIR/deploy/systemd/sales_ottenok.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable sales_ottenok

# Настройка nginx
echo "🌐 Настройка nginx..."
cp $PROJECT_DIR/deploy/nginx/sales_ottenok.conf /etc/nginx/sites-available/
ln -sf /etc/nginx/sites-available/sales_ottenok.conf /etc/nginx/sites-enabled/
nginx -t

echo ""
echo "============================================"
echo "✅ Установка завершена!"
echo "============================================"
echo ""
echo "📝 Следующие шаги:"
echo ""
echo "1. Отредактируйте $PROJECT_DIR/.env"
echo "   Укажите GREEN_API_INSTANCE_ID, GREEN_API_TOKEN, OPENAI_API_KEY"
echo ""
echo "2. Создайте Excel файл data/inventory.xlsx с колонками:"
echo "   product_name | size | color | quantity | price"
echo "   (см. README.md для примера)"
echo ""
echo "3. Соберите базу знаний (положите .docx в data/knowledge_base/):"
echo "   sudo -u ottenok .venv/bin/python -m knowledge.builder"
echo ""
echo "4. Настройте Google Drive credentials в credentials/google_credentials.json"
echo ""
echo "5. Запустите сервис:"
echo "   sudo systemctl start sales_ottenok"
echo ""
echo "6. Проверьте статус:"
echo "   sudo systemctl status sales_ottenok"
echo "   sudo journalctl -u sales_ottenok -f"
echo ""
echo "7. Настройте SSL (замените yourdomain.com на ваш домен):"
echo "   sudo certbot --nginx -d yourdomain.com"
echo ""
echo "8. Настройте webhook в GREEN-API:"
echo "   https://yourdomain.com/webhook"
echo ""
echo "============================================"
