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

print_message "Installing system dependencies..."

# Update package list
apt-get update

# Check if ffmpeg is installed
if ! command -v ffmpeg &> /dev/null || ! command -v ffprobe &> /dev/null; then
    print_message "Installing ffmpeg and its dependencies..."
    apt-get update
    apt-get install -y \
        ffmpeg \
        libavcodec-dev \
        libavformat-dev \
        libavutil-dev \
        libswscale-dev \
        libavfilter-dev \
        libavdevice-dev \
        libpostproc-dev \
        libswresample-dev
    
    # Verify installation immediately after
    if ! command -v ffmpeg &> /dev/null || ! command -v ffprobe &> /dev/null; then
        print_error "FFmpeg installation failed. Please check the error messages above."
        print_error "You can try installing manually with:"
        print_error "sudo apt-get install ffmpeg libavcodec-dev libavformat-dev libavutil-dev libswscale-dev libavfilter-dev libavdevice-dev libpostproc-dev libswresample-dev"
        exit 1
    fi
else
    print_message "ffmpeg is already installed"
fi

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

# If we're in the bot directory, update Python dependencies
if [ -d "/opt/geminibot" ]; then
    print_message "Updating Python dependencies in virtual environment..."
    cd /opt/geminibot
    if [ -d "venv" ]; then
        sudo -u $SUDO_USER bash -c "
            source venv/bin/activate && \
            pip install -r requirements.txt
        "
    fi
fi

# Verify ffmpeg installation
if command -v ffmpeg &> /dev/null && command -v ffprobe &> /dev/null; then
    print_message "ffmpeg installation verified successfully"
    ffmpeg -version | head -n 1
    ffprobe -version | head -n 1
else
    print_error "ffmpeg installation failed. Please check the error messages above."
    exit 1
fi

print_message "System dependencies installed successfully!" 