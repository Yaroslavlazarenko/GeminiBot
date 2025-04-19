# GeminiBot

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)

Telegram бот на базе Gemini AI с поддержкой голосовых сообщений и групповых чатов.

## Возможности

- Обработка текстовых и голосовых сообщений
- Поддержка групповых чатов с отдельными настройками
- Контекстное понимание диалога
- Автоматическое обновление на всех платформах
- База данных PostgreSQL для хранения настроек и истории
- Форматированные ответы с HTML

## Требования

- Telegram Bot Token ([@BotFather](https://t.me/BotFather))
- Gemini API Key ([Google AI Studio](https://makersuite.google.com/app/apikey))
- Python 3.11+
- PostgreSQL 14+
- 512MB RAM
- 1GB места на диске

## Установка

### Docker
```bash
git clone https://github.com/Yaroslavlazarenko/GeminiBot.git
cd GeminiBot
cp .env.example .env
# Отредактируйте .env
docker-compose up -d
```

### Windows
1. Скачайте и распакуйте бота
2. Откройте PowerShell (от администратора)
3. Выполните:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
```

### Linux
```bash
git clone git@github.com:Yaroslavlazarenko/GeminiBot.git
cd GeminiBot
chmod +x scripts/install.sh
sudo scripts/install.sh
```

## Управление

### Docker
```bash
docker-compose logs -f     # Логи
docker-compose restart     # Перезапуск
docker-compose pull && docker-compose up -d   # Обновление
```

### Windows
- Запуск: Ярлык "Start GeminiBot" на рабочем столе
- Логи: `bot.log`
- Обновление: Автоматическое (каждый час)

### Linux
```bash
sudo systemctl status geminibot     # Статус
sudo systemctl restart geminibot    # Перезапуск
journalctl -u geminibot -f         # Логи
```

## Конфигурация (.env)

Создайте файл `.env` в корневой директории проекта со следующим содержимым:

```env
BOT_TOKEN=your_telegram_bot_token
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash-preview-04-17

DB_USER=postgres
DB_PASSWORD=your_database_password
DB_NAME=gemini_bot
DB_HOST=localhost
```

Где:
- `BOT_TOKEN` - токен вашего Telegram бота от @BotFather
- `GEMINI_API_KEY` - ваш API ключ от Google Gemini
- `GEMINI_MODEL` - модель Gemini для использования
- `DB_USER` - пользователь PostgreSQL
- `DB_PASSWORD` - пароль для PostgreSQL
- `DB_NAME` - имя базы данных
- `DB_HOST` - хост базы данных

## Лицензия

MIT License. См. `LICENSE`.