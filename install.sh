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
    if ! ssh -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
        print_error "SSH connection to GitHub failed!"
        print_error "Please make sure:"
        print_error "1. You have an SSH key: ls -la ~/.ssh"
        print_error "2. Your SSH key is added to ssh-agent: ssh-add -l"
        print_error "3. Your SSH key is added to your GitHub account"
        print_error "4. You can test connection with: ssh -T git@github.com"
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

# Check if appsettings.json exists
if [ ! -f "appsettings.json" ]; then
    print_error "appsettings.json not found! Please create it first."
    exit 1
fi

# Read configuration from appsettings.json
DB_NAME=$(jq -r '.database.name' appsettings.json)
DB_USER=$(jq -r '.database.user' appsettings.json)
DB_PASSWORD=$(jq -r '.database.password' appsettings.json)
BOT_TOKEN=$(jq -r '.bot.token' appsettings.json)
GEMINI_API_KEY=$(jq -r '.gemini.api_key' appsettings.json)

# Validate configuration
if [[ "$DB_NAME" == "null" || "$DB_USER" == "null" || "$DB_PASSWORD" == "null" || 
      "$BOT_TOKEN" == "null" || "$GEMINI_API_KEY" == "null" ]]; then
    print_error "Invalid configuration in appsettings.json!"
    exit 1
fi

print_message "Installing system dependencies..."
apt update
apt install -y python3 python3-pip python3-venv postgresql postgresql-contrib git openssh-client

# Create and configure PostgreSQL database
print_message "Configuring PostgreSQL..."
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME;"
fi

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
    sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';"
fi
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

# Create bot directory and set permissions
print_message "Creating bot directory..."
mkdir -p $BOT_DIR
chown $ACTUAL_USER:$ACTUAL_USER $BOT_DIR

# Clone or update repository
print_message "Cloning/updating repository..."
if [ -d "$BOT_DIR/.git" ]; then
    cd $BOT_DIR
    sudo -u $ACTUAL_USER git pull
else
    # Use sudo -u $ACTUAL_USER to run git clone as the actual user
    sudo -u $ACTUAL_USER git clone $GITHUB_REPO $BOT_DIR
fi

# Copy appsettings.json to bot directory
print_message "Copying configuration..."
cp appsettings.json $BOT_DIR/
chown $ACTUAL_USER:$ACTUAL_USER $BOT_DIR/appsettings.json

# Setup virtual environment
print_message "Setting up Python virtual environment..."
cd $BOT_DIR
sudo -u $ACTUAL_USER python3 -m venv venv
sudo -u $ACTUAL_USER /bin/bash -c "source venv/bin/activate && pip install -r requirements.txt"

# Create .env file
print_message "Creating environment file..."
cat > $BOT_DIR/.env << EOL
bot_token=$BOT_TOKEN
gemini_api_key=$GEMINI_API_KEY
gemini_model=$(jq -r '.gemini.model' appsettings.json)
db_user=$DB_USER
db_password=$DB_PASSWORD
db_name=$DB_NAME
db_host=$(jq -r '.database.host' appsettings.json)
EOL

chown $ACTUAL_USER:$ACTUAL_USER $BOT_DIR/.env
chmod 600 $BOT_DIR/.env

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
ExecStart=$BOT_DIR/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOL

chmod 644 /etc/systemd/system/$SERVICE_NAME.service

# Run database migrations
print_message "Running database migrations..."
cd $BOT_DIR
sudo -u $ACTUAL_USER /bin/bash -c "source venv/bin/activate && alembic upgrade head"

# Start and enable service
print_message "Starting service..."
systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl start $SERVICE_NAME

print_message "Installation complete! Service status:"
systemctl status $SERVICE_NAME

print_message "You can check logs with: journalctl -u $SERVICE_NAME -f"
print_warning "If you need to stop the bot: sudo systemctl stop $SERVICE_NAME"
print_warning "If you need to restart the bot: sudo systemctl restart $SERVICE_NAME"