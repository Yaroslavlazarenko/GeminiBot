# 🤖 GeminiBot

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)

Мощный Telegram бот на основе Gemini AI с поддержкой голосовых сообщений, групповых чатов и автоматического обновления.

## ✨ Возможности

- 🗣️ Отвечает на текстовые и голосовые сообщения
- 👥 Поддерживает личные и групповые чаты
- ⚙️ Настраиваемые параметры для групп и пользователей
- 🔄 Автоматическое обновление (Windows/Linux/Docker)
- 🔒 Безопасное хранение настроек
- 📊 Поддержка PostgreSQL для хранения данных

## 📋 Предварительные требования

1. **Токены доступа:**
   - Telegram Bot Token ([получить у @BotFather](https://t.me/BotFather))
   - Gemini API Key ([получить в Google AI Studio](https://makersuite.google.com/app/apikey))

2. **Для запуска требуется одно из:**
   - Docker и Docker Compose
   - Python 3.11+ и PostgreSQL
   - Или просто следуйте инструкции для вашей системы

## 🚀 Установка

### 1️⃣ Docker (рекомендуется)

1. **Установите Docker:**
   - [Docker Desktop для Windows](https://docs.docker.com/desktop/install/windows-install/)
   - [Docker для Linux](https://docs.docker.com/engine/install/)
   - [Docker Compose](https://docs.docker.com/compose/install/)

2. **Запустите бота:**
   ```bash
   # Клонируйте репозиторий
   git clone https://github.com/Yaroslavlazarenko/GeminiBot.git
   cd GeminiBot

   # Создайте файл .env с вашими данными
   echo "BOT_TOKEN=your_token" > .env
   echo "GEMINI_API_KEY=your_key" >> .env

   # Запустите бота
   docker-compose up -d
   ```

### 2️⃣ Windows

1. **Простая установка (рекомендуется):**
   - Скачайте и распакуйте бота
   - Откройте PowerShell от администратора в папке бота
   - Выполните:
     ```powershell
     powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
     ```

2. **Всё остальное установщик сделает автоматически:**
   - ⚙️ Установит Python, PostgreSQL и Git
   - 📝 Создаст конфигурационный файл
   - 🔄 Настроит автообновление
   - 🚀 Создаст ярлык для запуска

### 3️⃣ Linux (Ubuntu/Debian)

1. **Автоматическая установка:**
   ```bash
   git clone git@github.com:Yaroslavlazarenko/GeminiBot.git
   cd GeminiBot
   chmod +x scripts/install.sh
   sudo scripts/install.sh
   ```

2. **Установщик настроит всё необходимое:**
   - 📦 Установит зависимости
   - 🔄 Создаст systemd сервисы
   - 🕒 Настроит автообновление

## ⚙️ Управление

### Docker
```bash
# Просмотр логов
docker-compose logs -f

# Перезапуск
docker-compose restart

# Обновление
docker-compose pull && docker-compose up -d
```

### Windows
- 🚀 Запуск: Используйте ярлык на рабочем столе
- 📊 Логи: Смотрите файл `bot.log`
- 🔄 Обновления: Автоматические (каждый час)

### Linux
```bash
# Статус бота
sudo systemctl status geminibot

# Просмотр логов
journalctl -u geminibot -f

# Управление сервисом
sudo systemctl start|stop|restart geminibot
```

## 🔄 Автообновление

Бот поддерживает автоматическое обновление на всех платформах:

### Docker
- ✅ Включено по умолчанию через Watchtower
- 🕒 Проверка обновлений каждый час
- 📊 Логи: `docker-compose logs -f watchtower`

### Windows
- ✅ Настраивается автоматически при установке
- 🕒 Проверка обновлений каждый час
- 📊 Логи в файле `update.log`

### Linux
- ✅ Настраивается через systemd timer
- 🕒 Проверка каждые 5 минут
- 📊 Логи: `/var/log/geminibot/autoupdate.log`

## 📝 Конфигурация

Все настройки хранятся в `appsettings.json`:

```json
{
  "telegram_bot_token": "your_token",
  "gemini_api_key": "your_key",
  "database": {
    "host": "localhost",
    "port": 5432,
    "database": "gemini_bot",
    "user": "postgres",
    "password": "your_password"
  }
}
```

## 🤝 Поддержка

При возникновении проблем:
1. Проверьте логи вашей платформы
2. Создайте issue в репозитории на GitHub
3. Убедитесь, что используете последнюю версию

## 📜 Лицензия

Распространяется под MIT лицензией. См. `LICENSE` для деталей.