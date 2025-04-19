# GeminiBot

Telegram бот, использующий Gemini AI для генерации ответов.

## Установка

### Вариант 1: Установка с Docker (рекомендуется)

1. Установите Docker и Docker Compose
2. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/Yaroslavlazarenko/GeminiBot
   cd GeminiBot
   ```
3. Создайте файл .env и заполните его своими значениями:
   ```
   BOT_TOKEN=your_telegram_bot_token
   GEMINI_API_KEY=your_gemini_api_key
   GEMINI_MODEL=gemini-pro
   DB_USER=postgres
   DB_PASSWORD=postgres
   DB_NAME=gemini_bot
   DB_HOST=postgres
   ```
4. Запустите бота:
   ```bash
   docker-compose up -d
   ```

### Вариант 2: Локальная установка

1. Установите Python 3.13:
   - Windows: Установите через [Python.org](https://www.python.org/downloads/) или winget:
     ```bash
     winget install Python.Python.3.13
     ```

2. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/Yaroslavlazarenko/GeminiBot
   cd GeminiBot
   ```

3. Установите зависимости:
   ```bash
   pip install -r requirements.txt
   ```

4. Создайте файл .env как описано выше

5. Запустите бота:
   ```bash
   python main.py
   ```

## Автоматическое обновление

Для настройки автоматического обновления бота выполните следующие команды на сервере:

1. Сделайте скрипт проверки обновлений исполняемым:
```bash
chmod +x /opt/geminibot/check_updates.sh
```

2. Скопируйте сервисные файлы:
```bash
sudo cp /opt/geminibot/geminibot-autoupdate.service /etc/systemd/system/
sudo cp /opt/geminibot/geminibot-autoupdate.timer /etc/systemd/system/
```

3. Включите и запустите сервис автообновления:
```bash
sudo systemctl enable geminibot-autoupdate.timer
sudo systemctl start geminibot-autoupdate.timer
```

Теперь бот будет автоматически проверять наличие обновлений каждые 5 минут и применять их при наличии.

Проверить статус автообновления:
```bash
sudo systemctl status geminibot-autoupdate.timer
```

Просмотреть логи обновлений:
```bash
sudo tail -f /var/log/geminibot/autoupdate.log
```

## Test Update
This is a test update to verify auto-update system (Updated: April 19, 2025)