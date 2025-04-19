# GeminiBot

Telegram бот на основе Gemini AI, который может отвечать на сообщения, обрабатывать голосовые сообщения и работать в групповых чатах.

## Требования

- Telegram Bot Token (получите у [@BotFather](https://t.me/BotFather))
- Gemini API Key (получите в [Google AI Studio](https://makersuite.google.com/app/apikey))

## Способы установки

### 1. Установка с Docker (рекомендуется)

Самый простой способ запустить бота - использовать Docker.

1. Установите Docker и Docker Compose:
   - [Docker для Windows](https://docs.docker.com/desktop/install/windows-install/)
   - [Docker для Linux](https://docs.docker.com/engine/install/)
   - [Docker Compose](https://docs.docker.com/compose/install/)

2. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/Yaroslavlazarenko/GeminiBot.git
   cd GeminiBot
   ```

3. Отредактируйте `appsettings.json`, указав ваши значения для:
   - Telegram Bot Token
   - Gemini API Key
   - Пароль базы данных

4. Запустите бота:
   ```bash
   docker-compose up -d
   ```

Бот запустится в фоновом режиме. Логи можно посмотреть командой:
```bash
docker-compose logs -f
```

### 2. Установка на Windows

1. Установите Python 3.11 или выше:
   - Скачайте с [Python.org](https://www.python.org/downloads/)
   - Или используйте winget:
     ```bash
     winget install Python.Python.3.13
     ```

2. Установите PostgreSQL:
   - Скачайте с [официального сайта](https://www.postgresql.org/download/windows/)
   - Или используйте winget:
     ```bash
     winget install PostgreSQL.PostgreSQL
     ```

3. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/Yaroslavlazarenko/GeminiBot.git
   cd GeminiBot
   ```

4. Создайте виртуальное окружение и установите зависимости:
   ```bash
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

5. Отредактируйте в `appsettings.json`:
   - В database.host: localhost
   - Укажите ваши значения для Telegram Bot Token и Gemini API Key

6. Примените миграции базы данных:
   ```bash
   alembic upgrade head
   ```

7. Запустите бота:
   ```bash
   python main.py
   ```

### 3. Установка на Linux (Ubuntu/Debian)

1. Клонируйте репозиторий:
   ```bash
   git clone git@github.com:Yaroslavlazarenko/GeminiBot.git
   cd GeminiBot
   ```

2. Отредактируйте `appsettings.json`, указав ваши значения для токенов и настроек базы данных.

3. Сделайте скрипт установки исполняемым и запустите его:
   ```bash
   chmod +x install.sh
   sudo ./install.sh
   ```

Скрипт автоматически:
- Установит все необходимые зависимости
- Настроит PostgreSQL
- Создаст и настроит виртуальное окружение Python
- Применит миграции базы данных
- Создаст и запустит systemd сервис
- Настроит автоматическое обновление

#### Управление ботом на Linux

Основные команды:
```bash
# Просмотр статуса
sudo systemctl status geminibot

# Просмотр логов
journalctl -u geminibot -f

# Перезапуск бота
sudo systemctl restart geminibot

# Остановка бота
sudo systemctl stop geminibot

# Запуск бота
sudo systemctl start geminibot
```

#### Автоматическое обновление на Linux

Бот автоматически проверяет наличие обновлений каждые 5 минут. Вы можете:

- Проверить статус автообновления:
  ```bash
  sudo systemctl status geminibot-autoupdate.timer
  ```

- Посмотреть логи обновлений:
  ```bash
  sudo tail -f /var/log/geminibot/autoupdate.log
  ```

## Возможности бота

- Отвечает на текстовые сообщения используя Gemini AI
- Обрабатывает голосовые сообщения (преобразует в текст и отвечает)
- Работает в личных чатах и группах
- Поддерживает настройки для групп и пользователей
- Имеет систему автоматического обновления (для Linux)

## Поддержка

При возникновении проблем, создайте issue в репозитории проекта на GitHub.