#!/bin/bash

# Configuration
BOT_DIR="/opt/geminibot"
LOG_FILE="/var/log/geminibot/autoupdate.log"
LOCK_FILE="/tmp/geminibot_update.lock"

# Create log directory if it doesn't exist
mkdir -p "$(dirname "$LOG_FILE")"

# Function to log messages
log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# Check if update is already running
if [ -f "$LOCK_FILE" ]; then
    log_message "Update process is already running, exiting"
    exit 0
fi

# Create lock file
touch "$LOCK_FILE"

# Function to clean up lock file
cleanup() {
    rm -f "$LOCK_FILE"
}

# Set up trap to clean up lock file on exit
trap cleanup EXIT

# Change to bot directory
cd "$BOT_DIR" || exit 1

# Check for updates
log_message "Checking for updates..."
git remote update

UPSTREAM=${1:-'@{u}'}
LOCAL=$(git rev-parse @)
REMOTE=$(git rev-parse "$UPSTREAM")
BASE=$(git merge-base @ "$UPSTREAM")

if [ "$LOCAL" = "$REMOTE" ]; then
    log_message "Up-to-date"
elif [ "$LOCAL" = "$BASE" ]; then
    log_message "Found updates, applying..."
    # Run update script
    "$BOT_DIR/scripts/update.sh" >> "$LOG_FILE" 2>&1
    if [ $? -eq 0 ]; then
        log_message "Update completed successfully"
    else
        log_message "Update failed"
    fi
else
    log_message "Diverged from remote, manual intervention required"
fi