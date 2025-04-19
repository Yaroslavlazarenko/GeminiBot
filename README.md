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

## Конфигурация (appsettings.json)
```json
{
  "telegram_bot_token": "YOUR_BOT_TOKEN",
  "gemini_api_key": "YOUR_GEMINI_KEY",
  "database": {
    "host": "localhost",
    "port": 5432,
    "database": "gemini_bot",
    "user": "postgres",
    "password": "your_password"
  }
}
```

## Команды бота

- `/settings` - Общие настройки
- `/group_settings` - Настройки группы
- `/clear` - Очистить историю диалога
- `/mute`, `/unmute` - Управление ответами бота

## Поддержка

При проблемах:
1. Проверьте логи
2. Создайте issue на GitHub
3. Убедитесь в актуальности версии

## Лицензия

MIT License. См. `LICENSE`.