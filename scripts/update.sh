#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color
YELLOW='\033[1;33m'

# Function to print colored messages
print_message() {
    echo -e "${GREEN}[*] $1${NC}"
}

print_error() {
    echo -e "${RED}[ERROR] $1${NC}"
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
    print_error "Could not determine the actual user"
    exit 1
fi

# Start ssh-agent and add key for the actual user if needed
print_message "Setting up SSH agent..."
if ! sudo -u $ACTUAL_USER ssh-add -l >/dev/null 2>&1; then
    sudo -u $ACTUAL_USER bash -c 'eval "$(ssh-agent -s)" && ssh-add ~/.ssh/id_ed25519'
fi

# Check SSH connection before proceeding
print_message "Checking SSH connection to GitHub..."
if ! sudo -u $ACTUAL_USER ssh -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
    print_error "SSH connection to GitHub failed!"
    print_error "Please make sure:"
    print_error "1. You have an SSH key: ls -la ~/.ssh"
    print_error "2. Start ssh-agent: eval \$(ssh-agent -s)"
    print_error "3. Add your key: ssh-add ~/.ssh/id_ed25519"
    print_error "4. Your SSH key is added to your GitHub account"
    print_error "5. You can test connection with: ssh -T git@github.com"
    exit 1
fi

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
    print_error ".env file not found in $BOT_DIR!"
    exit 1
fi

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Function to clean old backups
clean_old_backups() {
    print_message "Cleaning old backups..."
    ls -t "$BACKUP_DIR" | tail -n +$((MAX_BACKUPS + 1)) | while read -r backup; do
        rm -f "$BACKUP_DIR/$backup"
        print_warning "Removed old backup: $backup"
    done
}

# Create new backup of critical files
print_message "Creating backup of critical files..."
BACKUP_FILE="$BACKUP_DIR/backup_$(date +%Y%m%d_%H%M%S).tar.gz"
tar -czf "$BACKUP_FILE" -C "$BOT_DIR" \
    .env \
    alembic.ini \
    config.py \
    2>/dev/null || print_warning "Some files were not found for backup"

# Clean old backups
clean_old_backups

# Stop the service
print_message "Stopping the bot service..."
systemctl stop $SERVICE_NAME

# Pull latest changes
print_message "Pulling latest changes from git..."
cd $BOT_DIR
sudo -u $ACTUAL_USER bash -c "SSH_AUTH_SOCK=$SSH_AUTH_SOCK git fetch origin"
# Check if there are any changes
if [ "$(sudo -u $ACTUAL_USER git rev-parse HEAD)" = "$(sudo -u $ACTUAL_USER git rev-parse @{u})" ]; then
    print_warning "No updates available. Already at the latest version."
else
    sudo -u $ACTUAL_USER bash -c "SSH_AUTH_SOCK=$SSH_AUTH_SOCK git reset --hard origin/main"
fi

# Before copying service files, ensure they exist
if [ ! -f "$SCRIPTS_DIR/systemd/geminibot-autoupdate.service" ] || [ ! -f "$SCRIPTS_DIR/systemd/geminibot-autoupdate.timer" ]; then
    print_error "Systemd service files not found in $SCRIPTS_DIR/systemd/"
    exit 1
fi

# After pulling updates, update service files if needed
if [ -f "$SCRIPTS_DIR/systemd/geminibot-autoupdate.service" ]; then
    print_message "Updating systemd service files..."
    cp "$SCRIPTS_DIR/systemd/geminibot-autoupdate.service" /etc/systemd/system/
    cp "$SCRIPTS_DIR/systemd/geminibot-autoupdate.timer" /etc/systemd/system/
    chmod 644 /etc/systemd/system/geminibot-autoupdate.service
    chmod 644 /etc/systemd/system/geminibot-autoupdate.timer
    systemctl daemon-reload
fi

# Update dependencies
print_message "Updating dependencies..."
sudo -u $ACTUAL_USER bash -c "source venv/bin/activate && pip install --require-virtualenv -r requirements.txt"
# Run Alembic migrations
print_message "Applying database migrations..."
sudo -u $ACTUAL_USER bash -c "source venv/bin/activate && alembic upgrade head"
# Apply database migrations

# Start the service
print_message "Starting the bot service..."
systemctl start $SERVICE_NAME

# Check service status
print_message "Checking service status..."
systemctl status $SERVICE_NAME

# Show backup info
BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
print_message "Update complete!"
print_message "Backup created at: $BACKUP_FILE (Size: $BACKUP_SIZE)"
print_message "You can check logs with: journalctl -u $SERVICE_NAME -f"

# Show disk usage warning if running low on space
DISK_USAGE=$(df -h "$BOT_DIR" | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -gt 80 ]; then
    print_warning "Disk usage is at ${DISK_USAGE}%. Consider cleaning up old files."
fi