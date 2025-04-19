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

# Configuration
BOT_DIR="/opt/geminibot"
SERVICE_NAME="geminibot"
BACKUP_DIR="$BOT_DIR/backups"
MAX_BACKUPS=3

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Function to clean old backups
clean_old_backups() {
    # List all backup files sorted by date (oldest first) and remove all but the last MAX_BACKUPS
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
sudo -u $SUDO_USER git stash  # Stash any local changes
sudo -u $SUDO_USER git pull

# Update dependencies
print_message "Updating dependencies..."
sudo -u $SUDO_USER /bin/bash -c "source venv/bin/activate && pip install -r requirements.txt"

# Apply database migrations
print_message "Applying database migrations..."
sudo -u $SUDO_USER /bin/bash -c "source venv/bin/activate && alembic upgrade head"

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