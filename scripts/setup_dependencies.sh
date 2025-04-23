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

print_message "Installing system dependencies for OpenCV and other requirements..."

# Update package list
apt-get update

# Install OpenCV dependencies
print_message "Installing OpenCV dependencies..."
apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgl1-mesa-dev \
    libglu1-mesa-dev \
    freeglut3-dev \
    mesa-common-dev

# Install other system dependencies
print_message "Installing other system dependencies..."
apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-full \
    postgresql \
    postgresql-contrib \
    git \
    openssh-client \
    jq

# If we're in the bot directory, reinstall OpenCV
if [ -d "/opt/geminibot" ]; then
    print_message "Reinstalling OpenCV in virtual environment..."
    cd /opt/geminibot
    if [ -d "venv" ]; then
        sudo -u $SUDO_USER bash -c "
            source venv/bin/activate && \
            pip uninstall -y opencv-python opencv-python-headless && \
            pip install opencv-python-headless
        "
    fi
fi

print_message "System dependencies installed successfully!" 