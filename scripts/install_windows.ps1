# Requires -RunAsAdministrator

# Enable colored output
$host.UI.RawUI.ForegroundColor = "White"

function Write-ColorOutput($ForegroundColor) {
    $fc = $host.UI.RawUI.ForegroundColor
    $host.UI.RawUI.ForegroundColor = $ForegroundColor
    if ($args) {
        Write-Output $args
    }
    $host.UI.RawUI.ForegroundColor = $fc
}

function Test-Command($Command) {
    try { Get-Command $Command -ErrorAction Stop | Out-Null
        return $true
    }
    catch { return $false }
}

# Welcome message
Write-ColorOutput Green "=== GeminiBot Windows Installer ==="
Write-Output ""

# Check if running as administrator
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-ColorOutput Red "Please run this script as Administrator!"
    Exit 1
}

# Check and install Python
Write-ColorOutput Cyan "Checking Python installation..."
if (-not (Test-Command python)) {
    Write-ColorOutput Yellow "Python not found. Installing Python..."
    try {
        winget install Python.Python.3.11
        if (-not $?) { throw "Failed to install Python" }
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    }
    catch {
        Write-ColorOutput Red "Failed to install Python. Please install Python 3.11 or later manually from python.org"
        Exit 1
    }
}

# Check and install PostgreSQL
Write-ColorOutput Cyan "Checking PostgreSQL installation..."
if (-not (Test-Command psql)) {
    Write-ColorOutput Yellow "PostgreSQL not found. Installing PostgreSQL..."
    try {
        winget install PostgreSQL.PostgreSQL
        if (-not $?) { throw "Failed to install PostgreSQL" }
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    }
    catch {
        Write-ColorOutput Red "Failed to install PostgreSQL. Please install PostgreSQL manually from postgresql.org"
        Exit 1
    }
}

# Check and install Git if needed
Write-ColorOutput Cyan "Checking Git installation..."
if (-not (Test-Command git)) {
    Write-ColorOutput Yellow "Git not found. Installing Git..."
    try {
        winget install Git.Git
        if (-not $?) { throw "Failed to install Git" }
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    }
    catch {
        Write-ColorOutput Red "Failed to install Git. Please install Git manually from git-scm.com"
        Exit 1
    }
}

# Create and configure appsettings.json if it doesn't exist
if (-not (Test-Path "appsettings.json")) {
    Write-ColorOutput Cyan "Creating appsettings.json..."
    Write-Output "Please enter the following information:"
    $botToken = Read-Host "Enter your Telegram Bot Token (from @BotFather)"
    $geminiKey = Read-Host "Enter your Gemini API Key"
    $dbPassword = Read-Host "Enter a password for the PostgreSQL database"

    $settings = @{
        "telegram_bot_token" = $botToken
        "gemini_api_key" = $geminiKey
        "database" = @{
            "host" = "localhost"
            "port" = 5432
            "database" = "gemini_bot"
            "user" = "postgres"
            "password" = $dbPassword
        }
    }

    $settings | ConvertTo-Json -Depth 10 | Set-Content "appsettings.json"
}

# Set up virtual environment and install requirements
Write-ColorOutput Cyan "Setting up Python virtual environment..."
if (-not (Test-Path "venv")) {
    python -m venv venv
}
Write-ColorOutput Yellow "Activating virtual environment and installing dependencies..."
. .\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

# Initialize PostgreSQL database
Write-ColorOutput Cyan "Configuring PostgreSQL..."
$settings = Get-Content "appsettings.json" | ConvertFrom-Json
$dbPass = $settings.database.password
$dbName = $settings.database.database

# Create database if it doesn't exist
$env:PGPASSWORD = $dbPass
$result = psql -U postgres -c "SELECT 1 FROM pg_database WHERE datname = '$dbName'" | Select-String "1 row"
if (-not $result) {
    Write-ColorOutput Yellow "Creating database..."
    psql -U postgres -c "CREATE DATABASE $dbName"
}

# Apply database migrations
Write-ColorOutput Cyan "Applying database migrations..."
alembic upgrade head

# Set up auto-update task
Write-ColorOutput Cyan "Setting up auto-update task..."
& "$PSScriptRoot\setup_windows_update.ps1"

# Success message
Write-ColorOutput Green "Installation completed successfully!"
Write-ColorOutput Yellow "You can now start the bot by running: python main.py"

# Create a shortcut on desktop
$WshShell = New-Object -comObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$env:USERPROFILE\Desktop\Start GeminiBot.lnk")
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.Arguments = "-NoExit -Command `"cd '$PWD'; .\venv\Scripts\Activate.ps1; python main.py`""
$Shortcut.WorkingDirectory = $PWD
$Shortcut.Save()

Write-ColorOutput Green "A shortcut has been created on your desktop to start the bot."
Write-Output ""
Write-ColorOutput Cyan "To start the bot:"
Write-Output "1. Double-click the 'Start GeminiBot' shortcut on your desktop"
Write-Output "2. Or open PowerShell in this directory and run:"
Write-Output "   .\venv\Scripts\Activate.ps1"
Write-Output "   python main.py"
Write-Output ""
Write-ColorOutput Yellow "Press any key to exit..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")