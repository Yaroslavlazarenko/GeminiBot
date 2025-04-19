#!/bin/bash

# Устанавливаем режим выхода при любой ошибке
# Это важно, чтобы скрипт останавливался, если какая-либо команда не выполнится
set -e
set -o pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color
YELLOW='\033[1;33m'
BLUE='\033[0;34m'

# Function to print colored messages
print_message() {
    echo -e "${GREEN}[*] $1${NC}"
}

print_error() {
    echo -e "${RED}[ERROR] $1${NC}" >&2 # Print errors to stderr
    # Добавляем дополнительный вывод в журнал, если работаем как сервис
    if systemd-detect-virt >/dev/null 2>&1 && [ "$1" != "Please run this script as root (with sudo)" ] && [ "$1" != "Could not determine the actual user" ]; then
        logger -t "$SERVICE_NAME-update" "ERROR: $1"
    fi
}

print_warning() {
    echo -e "${YELLOW}[!] $1${NC}"
}

# Check if script is run as root
if [ "$EUID" -ne 0 ]; then
    print_error "Please run this script as root (with sudo)"
    exit 1
fi

# Get actual user (not root)
ACTUAL_USER=$SUDO_USER
if [ -z "$ACTUAL_USER" ]; then
    print_error "Could not determine the actual user. Make sure you run this with sudo (e.g., sudo ./update.sh)."
    exit 1
fi
ACTUAL_USER_HOME=$(sudo -u "$ACTUAL_USER" printenv HOME)
if [ -z "$ACTUAL_USER_HOME" ]; then
     ACTUAL_USER_HOME=$(getent passwd "$ACTUAL_USER" | cut -d: -f6)
fi


# Start ssh-agent and add key for the actual user if needed
# Note: This is tricky when run as root. We need the SSH_AUTH_SOCK from the actual user's session.
# For scripts run by systemd timer, the user's ssh-agent might not be available directly.
# A more robust approach for automated updates might involve a dedicated SSH key without a passphrase,
# or handling the agent differently (e.g., via a user systemd unit).
# For a simple script run manually via sudo, relying on SUDO_SSH_ASKPASS or SSH_ASKPASS might work,
# but SSH_AUTH_SOCK is better. Let's try to preserve SSH_AUTH_SOCK if present.

# Preserve SSH_AUTH_SOCK if it exists in the sudo environment
if [ -n "$SSH_AUTH_SOCK" ]; then
    export SSH_AUTH_SOCK="$SSH_AUTH_SOCK"
    print_message "Using existing SSH_AUTH_SOCK: $SSH_AUTH_SOCK"
else
    print_warning "SSH_AUTH_SOCK not found in environment. SSH commands might fail if agent is needed."
    print_warning "Consider running 'sudo -E' if SSH agent forwarding is configured."
fi

# Check SSH connection before proceeding
# Run as the actual user
print_message "Checking SSH connection to GitHub..."
# Use SSH_AUTH_SOCK explicitly in the sudo -u command if available
if [ -n "$SSH_AUTH_SOCK" ]; then
    if ! sudo -u "$ACTUAL_USER" env SSH_AUTH_SOCK="$SSH_AUTH_SOCK" ssh -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
        print_error "SSH connection to GitHub failed!"
        print_error "Please ensure the SSH agent is running for user '$ACTUAL_USER' and your key is added."
        print_error "Try manually running: eval \$(ssh-agent -s) && ssh-add ~/.ssh/id_ed25519"
        print_error "And verify with: ssh -T git@github.com"
        exit 1
    fi
else
     if ! sudo -u "$ACTUAL_USER" ssh -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
        print_error "SSH connection to GitHub failed!"
        print_error "SSH_AUTH_SOCK was not available. Please ensure the SSH agent is running for user '$ACTUAL_USER' and your key is added."
        print_error "Try manually running: eval \$(ssh-agent -s) && ssh-add ~/.ssh/id_ed25519"
        print_error "And verify with: ssh -T git@github.com"
        exit 1
    fi
fi
print_message "SSH connection to GitHub successful."


# Configuration
BOT_DIR="/opt/geminibot"
SERVICE_NAME="geminibot"
SCRIPTS_DIR="$BOT_DIR/scripts"
BACKUP_DIR="$BOT_DIR/backups"
MAX_BACKUPS=3

# Check if bot directory exists
if [ ! -d "$BOT_DIR" ]; then
    print_error "Bot directory $BOT_DIR not found! Please run scripts/install.sh first."
    exit 1
fi

# Check if .env exists
if [ ! -f "$BOT_DIR/.env" ]; then
    print_error ".env file not found in $BOT_DIR! This file is required for configuration."
    exit 1
fi

# Check if venv exists
if [ ! -d "$BOT_DIR/venv" ]; then
    print_error "Virtual environment not found in $BOT_DIR/venv! Please run scripts/install.sh first."
    exit 1
fi

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Function to clean old backups
clean_old_backups() {
    print_message "Cleaning old backups (keeping $MAX_BACKUPS)..."
    # Ensure directory exists and contains files before processing
    if [ -d "$BACKUP_DIR" ] && [ "$(ls -A "$BACKUP_DIR")" ]; then
        ls -t "$BACKUP_DIR" | tail -n +$((MAX_BACKUPS + 1)) | while read -r backup; do
            print_warning "Removing old backup: $backup"
            rm -f "$BACKUP_DIR/$backup"
        done
    else
        print_message "No backups to clean."
    fi
}

# Create new backup of critical files
print_message "Creating backup of critical files..."
BACKUP_FILE="$BACKUP_DIR/backup_$(date +%Y%m%d_%H%M%S).tar.gz"
# Use bash -c for sudo to handle potential globs or commands needing user context
# Use find to reliably list files for tar
sudo -u "$ACTUAL_USER" bash -c "
    cd \"$BOT_DIR\"
    find .env alembic.ini config.py -maxdepth 1 -print 2>/dev/null | tar -czf \"$BACKUP_FILE\" --no-mode-permissions --no-owner -T -
" || print_warning "Some files were not found for backup or tar failed. Check $BACKUP_FILE."

# Check if backup file was created successfully and is not empty
if [ ! -s "$BACKUP_FILE" ]; then
    print_warning "Backup file $BACKUP_FILE was not created successfully or is empty!"
fi


# Clean old backups
clean_old_backups

# Stop the service
print_message "Stopping the bot service '$SERVICE_NAME'..."
systemctl stop "$SERVICE_NAME" || print_warning "Service '$SERVICE_NAME' might not have been running."

# Pull latest changes
print_message "Pulling latest changes from git..."
# Navigate to the bot directory as the actual user for git operations
cd "$BOT_DIR" || { print_error "Failed to change directory to $BOT_DIR"; exit 1; }

# Determine if we need to pull
LOCAL_COMMIT=$(sudo -u "$ACTUAL_USER" git rev-parse HEAD)
REMOTE_COMMIT=$(sudo -u "$ACTUAL_USER" git rev-parse @{u} 2>/dev/null)

# Fetch latest state from remote first
if [ -n "$SSH_AUTH_SOCK" ]; then
    sudo -u "$ACTUAL_USER" env SSH_AUTH_SOCK="$SSH_AUTH_SOCK" git fetch origin
else
    sudo -u "$ACTUAL_USER" git fetch origin
fi

# Re-check remote commit after fetch
REMOTE_COMMIT=$(sudo -u "$ACTUAL_USER" git rev-parse @{u} 2>/dev/null)

if [ "$LOCAL_COMMIT" = "$REMOTE_COMMIT" ]; then
    print_warning "No updates available. Already at the latest version ($LOCAL_COMMIT)."
else
    print_message "Local commit: $LOCAL_COMMIT"
    print_message "Remote commit: $REMOTE_COMMIT"
    print_message "Resetting to latest version..."
    # Perform hard reset as the actual user
    if [ -n "$SSH_AUTH_SOCK" ]; then
        sudo -u "$ACTUAL_USER" env SSH_AUTH_SOCK="$SSH_AUTH_SOCK" git reset --hard origin/main
    else
        sudo -u "$ACTUAL_USER" git reset --hard origin/main
    fi
    print_message "Git reset complete."
fi

# After pulling updates, update systemd service files if needed
# Check if service files exist in the updated scripts directory
if [ -f "$SCRIPTS_DIR/systemd/geminibot-autoupdate.service" ] && [ -f "$SCRIPTS_DIR/systemd/geminibot-autoupdate.timer" ]; then
    print_message "Updating systemd service and timer files..."
    cp "$SCRIPTS_DIR/systemd/geminibot-autoupdate.service" /etc/systemd/system/
    cp "$SCRIPTS_DIR/systemd/geminibot-autoupdate.timer" /etc/systemd/system/
    chmod 644 /etc/systemd/system/geminibot-autoupdate.service
    chmod 644 /etc/systemd/system/geminibot-autoupdate.timer
    systemctl daemon-reload
    print_message "Systemd configuration reloaded."
else
    print_warning "Systemd service files not found in $SCRIPTS_DIR/systemd/. Skipping systemd update."
fi


# Update dependencies
print_message "Updating dependencies..."
# Run pip install as the actual user inside the virtual environment
# Use 'set -e' in the subshell to catch pip errors
if ! sudo -u "$ACTUAL_USER" bash -c "
    set -e # Exit on error within this subshell
    source \"$BOT_DIR/venv/bin/activate\"
    echo \"Running pip install -r requirements.txt...\" # Add inner message
    pip install --require-virtualenv -r requirements.txt
"; then
    print_error "Dependency update failed! Exiting."
    exit 1 # Exit the main script on dependency failure
fi
print_message "Dependencies updated successfully."

# Apply database migrations
print_message "Applying database migrations..."
# Navigate to the bot directory first, then check alembic files
cd "$BOT_DIR" || { print_error "Failed to change directory to $BOT_DIR"; exit 1; }

# Ensure alembic directory and ini exist after git pull
if [ ! -d "$BOT_DIR/alembic" ] || [ ! -f "$BOT_DIR/alembic.ini" ]; then
    print_error "Alembic directory or alembic.ini not found in $BOT_DIR after update. Cannot apply migrations."
    exit 1
fi


# Ensure .env is accessible for alembic's env.py
# env.py is usually run from the alembic directory, so copy .env there temporarily.
ENV_FILE="$BOT_DIR/.env"
ALEMBIC_DIR="$BOT_DIR/alembic"
ALEMBIC_ENV_COPY="$ALEMBIC_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    print_message "Copying .env to $ALEMBIC_DIR/ for migration..."
    # Make copy failure fatal if the source file exists
    if ! cp "$ENV_FILE" "$ALEMBIC_ENV_COPY"; then
        print_error "Failed to copy $ENV_FILE to $ALEMBIC_ENV_COPY! Cannot apply migrations."
        exit 1
    fi
else
    # This case should be caught earlier by the script's initial .env check, but double-check
    print_error ".env file not found at $ENV_FILE! Migrations require database configuration."
    exit 1
fi

# Run the migration command as the actual user inside the virtual environment
# Use a subshell for the activation and command
# Capture output for better debugging if needed, but for simplicity, let it flow
print_message "Executing alembic upgrade head as user '$ACTUAL_USER' from '$BOT_DIR'..."
if sudo -u "$ACTUAL_USER" bash -c "
    set -e # Exit on error within the subshell
    source \"$BOT_DIR/venv/bin/activate\"
    # Run alembic from BOT_DIR, assuming alembic.ini is there
    echo \"Running alembic upgrade head from $BOT_DIR...\" # Debug message
    cd \"$BOT_DIR\" # Ensure we are in BOT_DIR, just in case bash -c starts elsewhere
    alembic upgrade head
"; then
    print_message "Database migrations applied successfully."
else
    # The set -e and if condition should catch alembic non-zero exit
    print_error "Database migration failed! Check the output above for details."
    # Clean up the copied .env even on failure
    if [ -f "$ALEMBIC_ENV_COPY" ]; then
        print_message "Cleaning up copied .env file from $ALEMBIC_DIR/..."
        rm -f "$ALEMBIC_ENV_COPY"
    fi
    exit 1 # Exit the main script
fi

# Clean up the copied .env file
if [ -f "$ALEMBIC_ENV_COPY" ]; then
    print_message "Cleaning up copied .env file from $ALEMBIC_DIR/..."
    rm -f "$ALEMBIC_ENV_COPY"
fi

# Start the service
print_message "Starting the bot service '$SERVICE_NAME'..."
systemctl start "$SERVICE_NAME" || { print_error "Failed to start service '$SERVICE_NAME'!"; exit 1; }
print_message "Service start command issued."

# Check service status (give it a moment to start)
print_message "Checking service status (waiting 5 seconds)..."
sleep 5
systemctl status "$SERVICE_NAME" --no-pager || print_warning "Could not display service status. Check logs manually."


# Show backup info
BACKUP_SIZE_KB=$(stat -c%s "$BACKUP_FILE" 2>/dev/null)
if [ -n "$BACKUP_SIZE_KB" ]; then
    if [ "$BACKUP_SIZE_KB" -gt 1024 ]; then
       BACKUP_SIZE="$(du -h "$BACKUP_FILE" | cut -f1)"
    else
       BACKUP_SIZE="$BACKUP_SIZE_KB bytes"
    fi
    print_message "Backup created at: $BACKUP_FILE (Size: $BACKUP_SIZE)"
else
    print_warning "Could not determine size of backup file: $BACKUP_FILE"
fi


print_message "Update complete!"
print_message "You can check service logs with: journalctl -u $SERVICE_NAME -f"

# Show disk usage warning if running low on space
# Check usage of the filesystem where BOT_DIR resides
DISK_USAGE=$(df -h "$BOT_DIR" | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -gt 80 ]; then
    print_warning "Disk usage on filesystem containing $BOT_DIR is at ${DISK_USAGE}%. Consider cleaning up old files (e.g., logs, backups)."
fi

exit 0 # Indicate success