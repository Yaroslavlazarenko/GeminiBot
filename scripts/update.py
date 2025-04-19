import os
import sys
import subprocess
import logging
import shutil
import platform
from datetime import datetime
import tarfile
import json
from pathlib import Path

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
    
    # Check for git updates
    success, output = run_command(['git', 'fetch', 'origin'])
    if not success:
        logging.error(f"Failed to fetch updates: {output}")
        return False
    
    # Check if we're behind remote
    success, local_commit = run_command(['git', 'rev-parse', 'HEAD'])
    success, remote_commit = run_command(['git', 'rev-parse', '@{u}'])
    
    if not success or local_commit.strip() == remote_commit.strip():
        logging.info("Already up to date")
        return True
    
    # Pull updates
    success, output = run_command(['git', 'pull', 'origin', 'main'])
    if not success:
        logging.error(f"Failed to pull updates: {output}")
        return False
    
    # Update dependencies
    if platform.system() == 'Windows':
        pip_cmd = [sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt']
    else:
        pip_cmd = ['pip3', 'install', '-r', 'requirements.txt']
    
    success, output = run_command(pip_cmd)
    if not success:
        logging.error(f"Failed to update dependencies: {output}")
        return False
    
    # Apply database migrations
    if os.path.exists('alembic.ini'):
        success, output = run_command(['alembic', 'upgrade', 'head'])
        if not success:
            logging.error(f"Failed to apply migrations: {output}")
            return False
    
    logging.info("Update completed successfully")
    return True

if __name__ == '__main__':
    if update_system():
        sys.exit(0)
    else:
        sys.exit(1)