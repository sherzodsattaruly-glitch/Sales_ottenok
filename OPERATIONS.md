# Sales Ottenok — Operations Runbook

Инструкция для AI-ассистента по управлению production-сервером.

## Сервер

| Параметр | Значение |
|----------|----------|
| Хост | `root@45.139.29.234` |
| Проект | `/opt/sales_ottenok` |
| Сервис | `sales_ottenok.service` (systemd) |
| Пользователь процесса | `ottenok` |
| Python | `/opt/sales_ottenok/.venv/bin/python` |
| Порт | `8080` (uvicorn, без nginx) |
| Git remote | `origin` → GitHub |
| Ветка | `v2-rewrite` |
| Логи | `/var/log/sales_ottenok/stderr.log`, `stdout.log` |

## Безопасные операции (только чтение)

Эти команды можно выполнять без подтверждения пользователя.

### Проверка здоровья

```bash
# Статус systemd-сервиса
ssh root@45.139.29.234 "systemctl status sales_ottenok"

# HTTP health check
ssh root@45.139.29.234 "curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/health"

# Процесс и потребление ресурсов
ssh root@45.139.29.234 "ps aux | grep 'main.py' | grep -v grep"
```

### Просмотр логов

```bash
# Последние 100 строк stderr (основной лог приложения)
ssh root@45.139.29.234 "tail -100 /var/log/sales_ottenok/stderr.log"

# Последние 50 строк stdout (HTTP-запросы uvicorn)
ssh root@45.139.29.234 "tail -50 /var/log/sales_ottenok/stdout.log"

# Поиск ошибок в логах (последние 500 строк)
ssh root@45.139.29.234 "tail -500 /var/log/sales_ottenok/stderr.log | grep -i 'error\|exception\|traceback\|critical'"

# Поиск WARNING
ssh root@45.139.29.234 "tail -500 /var/log/sales_ottenok/stderr.log | grep -i 'warning'"

# Размер лог-файлов
ssh root@45.139.29.234 "ls -lh /var/log/sales_ottenok/"
```

### Состояние git на сервере

```bash
# Текущий коммит и ветка
ssh root@45.139.29.234 "cd /opt/sales_ottenok && git log --oneline -3 && echo '---' && git branch --show-current"

# Есть ли локальные изменения
ssh root@45.139.29.234 "cd /opt/sales_ottenok && git status --short"

# Сравнение с remote (без fetch)
ssh root@45.139.29.234 "cd /opt/sales_ottenok && git log HEAD..origin/v2-rewrite --oneline 2>/dev/null || echo 'нужен git fetch для сравнения'"
```

### Диагностика

```bash
# Диск
ssh root@45.139.29.234 "df -h / && du -sh /opt/sales_ottenok/data/ /var/log/sales_ottenok/"

# Память и CPU
ssh root@45.139.29.234 "free -h && echo '---' && uptime"

# Открытые порты приложения
ssh root@45.139.29.234 "ss -tlnp | grep 8080"

# Содержимое .env (без секретов)
ssh root@45.139.29.234 "cd /opt/sales_ottenok && grep -v -E 'KEY|TOKEN|PASSWORD|SECRET|CREDENTIALS' .env"
```

## Операции деплоя (требуют подтверждения пользователя)

**ВАЖНО:** Перед любой из этих операций спроси у пользователя подтверждение.

### Выкатка нового релиза

Полная последовательность:

```bash
# 1. Проверить что сервис работает ДО обновления
ssh root@45.139.29.234 "systemctl is-active sales_ottenok && curl -sf http://localhost:8080/health > /dev/null && echo 'OK'"

# 2. Забрать изменения из git
ssh root@45.139.29.234 "cd /opt/sales_ottenok && git fetch origin && git log HEAD..origin/v2-rewrite --oneline"
```

Покажи пользователю список коммитов, которые будут применены. После подтверждения:

```bash
# 3. Применить изменения
ssh root@45.139.29.234 "cd /opt/sales_ottenok && git pull origin v2-rewrite"

# 4. Установить новые зависимости (если requirements.txt изменился)
ssh root@45.139.29.234 "cd /opt/sales_ottenok && .venv/bin/pip install -r requirements.txt"

# 5. Перезапустить сервис
ssh root@45.139.29.234 "systemctl restart sales_ottenok"

# 6. Подождать 5 секунд и проверить здоровье
ssh root@45.139.29.234 "sleep 5 && systemctl is-active sales_ottenok && curl -sf http://localhost:8080/health > /dev/null && echo 'DEPLOY OK' || echo 'DEPLOY FAILED'"

# 7. Проверить логи на ошибки после старта
ssh root@45.139.29.234 "tail -30 /var/log/sales_ottenok/stderr.log"
```

### Перезапуск сервиса (без обновления кода)

```bash
ssh root@45.139.29.234 "systemctl restart sales_ottenok"
ssh root@45.139.29.234 "sleep 5 && systemctl status sales_ottenok --no-pager -l | head -15"
```

### Просмотр логов в реальном времени

```bash
# Следить за логами (прервать по Ctrl+C / таймауту)
ssh root@45.139.29.234 "timeout 30 tail -f /var/log/sales_ottenok/stderr.log"
```

## Чего НЕЛЬЗЯ делать

- **НЕ** редактировать файлы на сервере напрямую (код, .env, конфиги)
- **НЕ** запускать `git reset`, `git checkout`, `git stash` — только `git pull`
- **НЕ** останавливать сервис (`systemctl stop`) без явной просьбы пользователя
- **НЕ** удалять логи, данные, БД (`rm`, `truncate`)
- **НЕ** менять systemd unit-файл
- **НЕ** устанавливать/удалять системные пакеты
- **НЕ** менять настройки firewall, ssh, пользователей
- **НЕ** трогать `.env` файл

## Типичные сценарии

### «Проверь, всё ли работает»

1. `systemctl status sales_ottenok` — active (running)?
2. `curl -s http://localhost:8080/health` — 200?
3. `tail -100 /var/log/sales_ottenok/stderr.log | grep -i error` — ошибки?
4. Если всё ОК — сообщи пользователю. Если нет — покажи найденные проблемы.

### «Задеплой последний код»

1. Проверить здоровье (сценарий выше)
2. `git fetch` + показать новые коммиты
3. Спросить подтверждение
4. `git pull` → `pip install -r requirements.txt` → `systemctl restart` → проверка здоровья
5. Показать результат и логи после старта

### «Посмотри логи за последний час»

```bash
ssh root@45.139.29.234 "awk -v d=\$(date -u -d '1 hour ago' '+%Y-%m-%d %H:%M') '\$0 >= d' /var/log/sales_ottenok/stderr.log | tail -200"
```

### «Сервис упал / не отвечает»

1. `systemctl status sales_ottenok` — что показывает?
2. `journalctl -u sales_ottenok --no-pager -n 50` — systemd логи
3. `tail -100 /var/log/sales_ottenok/stderr.log` — последние логи перед падением
4. Показать пользователю, предложить `systemctl restart sales_ottenok`
