import os
import sys
import subprocess
import logging
from datetime import datetime
import tarfile
from pathlib import Path
from alembic import command
from alembic.config import Config
import psycopg2
from psycopg2 import OperationalError
import time
# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('update.log')
    ]
)

def is_docker():
    path = '/proc/self/cgroup'
    return os.path.exists('/.dockerenv') or (os.path.exists(path) and any('docker' in line for line in open(path)))

def run_command(command, shell=False):
    try:
        result = subprocess.run(
            command,
            shell=shell,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        return False, e.stderr

def create_backup():
    """Create backup of critical files"""
    backup_dir = Path('backups')
    backup_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_file = backup_dir / f'backup_{timestamp}.tar.gz'
    
    critical_files = ['.env', 'alembic.ini', 'config.py']
    
    with tarfile.open(backup_file, 'w:gz') as tar:
        for file in critical_files:
            if os.path.exists(file):
                tar.add(file)
    
    # Clean old backups (keep last 3)
    backups = sorted(backup_dir.glob('backup_*.tar.gz'))
    for old_backup in backups[:-3]:
        old_backup.unlink()
    
    return True

def wait_for_database(max_attempts=30, delay=2):
    """Wait for database to become available."""
    logging.info("Waiting for database to become available...")
    
    # Read database connection details from .env
    db_params = {}
    try:
        with open('.env', 'r') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    key, value = line.strip().split('=', 1)
                    if key.startswith('DB_'):
                        db_params[key[3:].lower()] = value
    except Exception as e:
        logging.error(f"Failed to read .env file: {e}")
        return False

    for attempt in range(max_attempts):
        try:
            conn = psycopg2.connect(
                dbname=db_params.get('name', 'gemini_bot'),
                user=db_params.get('user', 'postgres'),
                password=db_params.get('password', '123456'),
                host=db_params.get('host', 'localhost')
            )
            conn.close()
            logging.info("Database is available!")
            return True
        except OperationalError as e:
            if attempt < max_attempts - 1:
                logging.info(f"Database not ready yet (attempt {attempt + 1}/{max_attempts}). Waiting {delay} seconds...")
                time.sleep(delay)
            else:
                logging.error(f"Database connection failed after {max_attempts} attempts: {e}")
                return False
    return False

def run_migrations():
    """Runs database migrations using Alembic."""
    try:
        if not os.path.exists('alembic.ini'):
            logging.error("alembic.ini not found. Skipping migrations.")
            return False

        # Wait for database to be available
        if not wait_for_database():
            logging.error("Failed to connect to database. Skipping migrations.")
            return False

        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
        logging.info("Database migrations completed successfully.")
        return True
    except Exception as e:
        logging.error(f"Failed to run database migrations: {e}")
        return False

def update_system():
    logging.info("Starting update process...")
    
    # Get current directory
    bot_dir = Path(__file__).parent.parent.absolute()
    os.chdir(bot_dir)
    
    # Create backup
    if not create_backup():
        logging.error("Backup creation failed")
        return False
    
    # Run database migrations
    if not run_migrations():
        logging.error("Database migrations failed")
        return False
    
    logging.info("Update completed successfully")
    return True

if __name__ == '__main__':
    if update_system():
        sys.exit(0)
    else:
        sys.exit(1)