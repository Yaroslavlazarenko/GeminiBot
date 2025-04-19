import os
import sys
import subprocess
import logging
from datetime import datetime
import tarfile
from pathlib import Path
from alembic import command
from alembic.config import Config
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

def update_system():
    logging.info("Starting update process...")
    
    # Get current directory
    bot_dir = Path(__file__).parent.parent.absolute()
    os.chdir(bot_dir)
    
    # Create backup
    if not create_backup():
        logging.error("Backup creation failed")
        return False
    
    
    def run_upgrade():
        """Runs database migrations using Alembic."""
        try:
            alembic_cfg = Config("alembic.ini")
            command.upgrade(alembic_cfg, "head")
            logging.info("Database upgrade completed successfully.")
        except Exception as e:
            logging.error(f"Failed to upgrade database: {e}")

    
    if os.path.exists('alembic.ini'):
        run_upgrade()
    
    logging.info("Update completed successfully")
    return True

if __name__ == '__main__':
    if update_system():
        sys.exit(0)
    else:
        sys.exit(1)