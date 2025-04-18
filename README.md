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