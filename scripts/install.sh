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

# Function to check SSH connection to GitHub
check_github_ssh() {
    print_message "Checking SSH connection to GitHub..."
    # Try as the actual user, not as root
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
check_github_ssh

# Check if jq is installed, if not - install it
if ! command -v jq &> /dev/null; then
    print_message "Installing jq..."
    apt-get update && apt-get install -y jq
fi

# Configuration
BOT_DIR="/opt/geminibot"
SERVICE_NAME="geminibot"
GITHUB_REPO="git@github.com:Yaroslavlazarenko/GeminiBot.git"

# Check if .env exists
if [ ! -f ".env" ]; then
    print_error ".env file not found! Please create it first."
    exit 1
fi

# Read configuration from .env
source .env

# Validate configuration
if [[ -z "$DB_NAME" || -z "$DB_USER" || -z "$DB_PASSWORD" || 
      -z "$BOT_TOKEN" || -z "$GEMINI_API_KEY" ]]; then
    print_error "Invalid configuration in .env!"
    exit 1
fi

print_message "Installing system dependencies..."
apt update
DEBIAN_FRONTEND=noninteractive apt install -y python3 python3-pip python3-venv python3-full postgresql postgresql-contrib git openssh-client

# Configure PostgreSQL
print_message "Configuring PostgreSQL..."

# Stop PostgreSQL service
print_message "Stopping PostgreSQL service..."
systemctl stop postgresql

# Get PostgreSQL version and config paths
PG_VERSION=$(ls /etc/postgresql/)
PG_HBA_CONF="/etc/postgresql/$PG_VERSION/main/pg_hba.conf"
PG_CONF="/etc/postgresql/$PG_VERSION/main/postgresql.conf"

# Backup original configuration files
cp "$PG_HBA_CONF" "${PG_HBA_CONF}.backup"
cp "$PG_CONF" "${PG_CONF}.backup"

# First, configure pg_hba.conf to use peer authentication temporarily
cat > "$PG_HBA_CONF" << EOL
local   all             postgres                                peer
local   all             all                                     peer
host    all             all             127.0.0.1/32            md5
host    all             all             ::1/128                 md5
EOL

# Ensure PostgreSQL is listening on localhost
sed -i "s/#listen_addresses = 'localhost'/listen_addresses = 'localhost'/" "$PG_CONF"

# Set proper permissions
chown postgres:postgres "$PG_HBA_CONF"
chmod 640 "$PG_HBA_CONF"
chown postgres:postgres "$PG_CONF"
chmod 640 "$PG_CONF"

# Start PostgreSQL with peer authentication
print_message "Starting PostgreSQL service..."
systemctl start postgresql
sleep 5  # Give PostgreSQL time to start

# Now we can use peer authentication to set up everything
print_message "Configuring PostgreSQL users and database..."

# Create database and set up users using peer authentication
sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD '$DB_PASSWORD';"
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME;" 2>/dev/null || true
sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';" 2>/dev/null || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

# Now switch pg_hba.conf to use md5 authentication
cat > "$PG_HBA_CONF" << EOL
local   all             postgres                                md5
local   all             all                                     md5
host    all             all             127.0.0.1/32            md5
host    all             all             ::1/128                 md5
EOL

# Restart PostgreSQL to apply new authentication method
print_message "Restarting PostgreSQL with new authentication..."
systemctl restart postgresql
sleep 5  # Give PostgreSQL time to restart

# Verify PostgreSQL connection
print_message "Verifying PostgreSQL connection..."
if PGPASSWORD=$DB_PASSWORD psql -h localhost -U $DB_USER -d $DB_NAME -c '\conninfo'; then
    print_message "PostgreSQL connection successful!"
else
    print_error "Failed to connect to PostgreSQL. Please check your configuration."
    exit 1
fi

# Clean up existing installation if present
print_message "Preparing installation directory..."
if [ -d "$BOT_DIR" ]; then
    systemctl stop $SERVICE_NAME || true
    rm -rf "$BOT_DIR"
fi

# Create bot directory and set permissions
mkdir -p $BOT_DIR
chown $ACTUAL_USER:$ACTUAL_USER $BOT_DIR

# Clone repository using the actual user's SSH context
print_message "Cloning repository..."
sudo -u $ACTUAL_USER bash -c "SSH_AUTH_SOCK=$SSH_AUTH_SOCK git clone $GITHUB_REPO $BOT_DIR"

# Copy .env to bot directory
print_message "Copying configuration..."
cp .env $BOT_DIR/
chown $ACTUAL_USER:$ACTUAL_USER $BOT_DIR/.env

# Setup virtual environment
print_message "Setting up Python virtual environment..."
cd $BOT_DIR
sudo -u $ACTUAL_USER python3 -m venv venv
sudo -u $ACTUAL_USER bash -c "source venv/bin/activate && pip install --require-virtualenv -r requirements.txt"

# Create systemd service
print_message "Creating systemd service..."
cat > /etc/systemd/system/$SERVICE_NAME.service << EOL
[Unit]
Description=Gemini Telegram Bot
After=network.target postgresql.service

[Service]
Type=simple
User=$ACTUAL_USER
WorkingDirectory=$BOT_DIR
Environment=PATH=$BOT_DIR/venv/bin
ExecStart=$BOT_DIR/venv/bin/python $BOT_DIR/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOL

chmod 644 /etc/systemd/system/$SERVICE_NAME.service

# Run database migrations
print_message "Running database migrations..."
cd $BOT_DIR
cp .env alembic/
cd alembic
sudo -u $ACTUAL_USER bash -c "source ../venv/bin/activate && alembic upgrade head"
rm .env  # Clean up
cd ..

# Start and enable service
print_message "Starting service..."
systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl start $SERVICE_NAME

# Create log directory for auto-updates
print_message "Setting up auto-update system..."
mkdir -p /var/log/geminibot
chown $ACTUAL_USER:$ACTUAL_USER /var/log/geminibot

# Copy and set permissions for update scripts
cp "$BOT_DIR/scripts/check_updates.sh" "$BOT_DIR/check_updates.sh"
cp "$BOT_DIR/scripts/update.sh" "$BOT_DIR/update.sh"
chmod 755 "$BOT_DIR/check_updates.sh"  # Изменено с +x на 755
chmod 755 "$BOT_DIR/update.sh"         # Изменено с +x на 755
chown $ACTUAL_USER:$ACTUAL_USER "$BOT_DIR/check_updates.sh"
chown $ACTUAL_USER:$ACTUAL_USER "$BOT_DIR/update.sh"

# Install auto-update service and timer
cp "$BOT_DIR/scripts/systemd/geminibot-autoupdate.service" /etc/systemd/system/
cp "$BOT_DIR/scripts/systemd/geminibot-autoupdate.timer" /etc/systemd/system/

# Создаем и настраиваем сервис автообновления с правильными правами
cat > /etc/systemd/system/geminibot-autoupdate.service << EOL
[Unit]
Description=GeminiBot Auto Update Service
After=network.target

[Service]
Type=oneshot
User=$ACTUAL_USER
Group=$ACTUAL_USER
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/check_updates.sh
Environment=HOME=/home/$ACTUAL_USER
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

[Install]
WantedBy=multi-user.target
EOL

chmod 644 /etc/systemd/system/geminibot-autoupdate.service
chmod 644 /etc/systemd/system/geminibot-autoupdate.timer

# Reload systemd and enable auto-update
systemctl daemon-reload
systemctl enable geminibot-autoupdate.timer
systemctl start geminibot-autoupdate.timer

print_message "Auto-update system configured and started"
print_message "You can check auto-update status with: systemctl status geminibot-autoupdate.timer"
print_message "Auto-update logs are available at: /var/log/geminibot/autoupdate.log"

print_message "Installation complete! Service status:"
systemctl status $SERVICE_NAME

print_message "You can check logs with: journalctl -u $SERVICE_NAME -f"
print_warning "If you need to stop the bot: sudo systemctl stop $SERVICE_NAME"
print_warning "If you need to restart the bot: sudo systemctl restart $SERVICE_NAME"