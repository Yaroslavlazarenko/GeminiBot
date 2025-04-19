#!/bin/bash

# Activate virtual environment
source /opt/geminibot/venv/bin/activate

# Run database migrations
alembic upgrade head

# Start the bot
/opt/geminibot/venv/bin/python /opt/geminibot/main.py