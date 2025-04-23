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
apt-get install -y libgl1-mesa-glx libglib2.0-0

# Install other system dependencies
apt-get install -y python3 python3-pip python3-venv python3-full postgresql postgresql-contrib git openssh-client jq

print_message "System dependencies installed successfully!" 