#!/usr/bin/env python3

import os
import sys
import yaml
import logging
import subprocess
import concurrent.futures
from pathlib import Path
from datetime import datetime


SCRIPT_DIR = Path(__file__).resolve().parent

# Load configuration from group_vars/all/main.yml
def load_config():
    config_path = SCRIPT_DIR / 'group_vars/all/main.yml'
    if not config_path.exists():
        print(f"Configuration file not found: {config_path}")
        sys.exit(1)
    with open(config_path, 'r') as file:
        return yaml.safe_load(file)

config = load_config()

# Resolve graid_path
graid_path = Path(config['graid_path'].replace("{{ ansible_env.HOME }}", os.environ.get('HOME', '')))

# Set up logging
log_dir = SCRIPT_DIR / Path(config['log_dir'])
log_dir.mkdir(exist_ok=True)
log_file = log_dir / f"setup_ansible_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(level=logging.INFO,
                    format='[%(asctime)s] [%(levelname)s] %(message)s',
                    handlers=[logging.FileHandler(log_file), logging.StreamHandler()])

logger = logging.getLogger(__name__)

def run_command(command, check=True, shell=False):
    try:
        result = subprocess.run(command, check=check, shell=shell, text=True, capture_output=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {e.cmd}")
        logger.error(f"Error output: {e.stderr}")
        if check:
            raise

def check_network(host):
    try:
        result = subprocess.run(['ping', '-c', str(config['ping_count']), '-W', str(config['ping_timeout']), host],
                                check=True, text=True, capture_output=True)
        logger.debug(f"Ping result for {host}:\n{result.stdout}")
        return True
    except subprocess.CalledProcessError as e:
        logger.warning(f"Network check failed for {host}")
        logger.warning(f"Ping output:\n{e.output}")
        return False

def retry(func, *args, **kwargs):
    for attempt in range(config['max_retries']):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
            if attempt + 1 < config['max_retries']:
                logger.info(f"Retrying in {config['retry_delay']} seconds...")
                subprocess.run(['sleep', str(config['retry_delay'])])
            else:
                logger.error(f"All {config['max_retries']} attempts failed")
                raise

def download_miniconda():
    miniconda_installer = f"Miniconda3-{config['miniconda_version']}-Linux-x86_64.sh"
    installer_path = graid_path / miniconda_installer
    graid_path.mkdir(parents=True, exist_ok=True)
    if not installer_path.exists():
        logger.info(f"Downloading Miniconda installer to {installer_path}...")
        try:
            run_command(['wget', f"https://repo.anaconda.com/miniconda/{miniconda_installer}", '-O', str(installer_path)])
            logger.info(f"Miniconda installer downloaded successfully to {installer_path}")
        except Exception as e:
            logger.error(f"Failed to download Miniconda installer: {str(e)}")
            raise
    else:
        logger.info(f"Miniconda installer already exists at {installer_path}")
    
    if not installer_path.exists():
        raise FileNotFoundError(f"Miniconda installer not found at {installer_path}")
    
    return installer_path

def setup_remote_host(host):
    miniconda_installer = f"Miniconda3-{config['miniconda_version']}-Linux-x86_64.sh"
    installer_path = graid_path / miniconda_installer
    logger.info(f"Setting up {host}...")

    if not check_network(host):
        logger.warning(f"Skipping setup for {host} due to network issues")
        return False

    remote_setup_script = f"""
#!/usr/bin/env bash
set -e
set -x

echo "Starting remote setup script"
echo "Current directory: $(pwd)"
echo "Graid path: {graid_path}"

# Create Graid package directory
mkdir -p {graid_path}
echo "Graid directory created/verified"

if ! command -v conda &> /dev/null; then
    echo "Conda not found, installing Miniconda"
    bash {installer_path} -b -p {graid_path}/miniconda
    {graid_path}/miniconda/bin/conda init bash
    {graid_path}/miniconda/bin/conda init zsh
    export PATH="{graid_path}/miniconda/bin:$PATH"
else
    echo "Conda already installed"
fi

sleep 5
# export PATH="{graid_path}/miniconda/bin:$PATH"
echo "Conda version: $(conda --version)"

if ! conda env list | grep -q {config['conda_env_name']}; then
    echo "Creating conda environment {config['conda_env_name']}"
    conda create -n {config['conda_env_name']} python={config['python_version']} -y
else
    echo "Conda environment {config['conda_env_name']} already exists"
fi

echo "Creating run_in_graid_env.sh script"
cat << EOT > {graid_path}/run_in_graid_env.sh
#!/bin/bash
# export PATH="/root/graid_package/miniconda/bin:$PATH"
source {graid_path}/miniconda/bin/activate {config['conda_env_name']}
python --version
EOT
chmod +x {graid_path}/run_in_graid_env.sh

echo "Installing Ansible in the environment"
conda run -n {config['conda_env_name']} pip install ansible

echo "Remote setup completed successfully"
echo "{graid_path}/run_in_graid_env.sh"

"""

    remote_setup_path = graid_path / 'remote_setup.sh'
    with open(remote_setup_path, 'w') as f:
        f.write(remote_setup_script)

    try:
        miniconda_installer = download_miniconda()
        logger.info(f"Using Miniconda installer: {miniconda_installer}")
        logger.info(f"Remote setup script: {remote_setup_path}")
        
        retry(run_command, ['rsync', '-avz', '--progress', str(miniconda_installer), str(remote_setup_path), f"{host}:{graid_path}/"])
        
        # Execute remote setup script and capture the output
        ssh_command = f"bash -x {graid_path}/remote_setup.sh"
        result = subprocess.run(['ssh', host, ssh_command], capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"Remote setup script execution failed on {host}")
            logger.error(f"Exit code: {result.returncode}")
            logger.error(f"STDOUT: {result.stdout}")
            logger.error(f"STDERR: {result.stderr}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False
        
        # Extract the Python interpreter path from the output
        remote_python_path = graid_path / "miniconda" / "envs" / config['conda_env_name'] / "bin" / "python"

        logger.info(f"Python interpreter path on {host}: {remote_python_path}")

        # Update Ansible inventory
        # inventory_path =  str(Path(config['inventory_file']))
        inventory_path = Path(SCRIPT_DIR).parent / config['inventory_file']
        # print(inventory_path, Path(SCRIPT_DIR).parents,Path(config['inventory_file']) )
        with open(inventory_path, 'r') as f:
            lines = f.readlines()
        with open(inventory_path, 'w') as f:
            for line in lines:
                if line.strip().startswith(host):
                    f.write(f"{host} ansible_python_interpreter={remote_python_path}\n")
                else:
                    f.write(line)
        
        logger.info(f"Setup completed for {host}")
        return True
    except Exception as e:
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return False

def run_update_link_script():
    update_script_path = SCRIPT_DIR / 'update_download_link.py'
    if not update_script_path.exists():
        logger.error(f"Update driver links script not found: {update_script_path}")
        return

    logger.info("Running update driver links script...")
    try:
        result = subprocess.run([sys.executable, str(update_script_path)], 
                                check=True, text=True, capture_output=True)
        logger.info(f"Update driver links script output:\n{result.stdout}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Update driver links script failed: {e}")
        logger.error(f"Error output: {e.stderr}")

def main():
    logger.info("Preparing setup...")
    miniconda_installer = download_miniconda()

    logger.info("Reading inventory file...")
    
    inventory_path = Path(SCRIPT_DIR).parent / config['inventory_file']
    # print(SCRIPT_DIR)
    # if not inventory_path.exists():
        
    #     alternative_path = SCRIPT_DIR.parent / Path(config['inventory_file']).relative_to('ansible_supremeraid')
    #     print(alternative_path)
    #     if alternative_path.exists():
    #         inventory_path = alternative_path
    #     else:
    #         logger.error(f"Inventory file not found: {inventory_path}")
    #         logger.error(f"Also tried: {alternative_path}")
    #         sys.exit(1)

    logger.info(f"Using inventory file: {inventory_path}")

    hosts = []
    current_group = None
    with open(inventory_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('[') and line.endswith(']'):
                current_group = line[1:-1]
                logger.info(f"Found group: {current_group}")
            elif line and not line.startswith('#'):
                host = line.split()[0]
                hosts.append(host)
                logger.info(f"Added host: {host} (Group: {current_group})")

    logger.info(f"Total hosts found: {len(hosts)}")

    if not hosts:
        logger.error("No hosts found in the inventory file.")
        sys.exit(1)

    logger.info(f"Starting setup for {len(hosts)} hosts...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=config['max_parallel_hosts']) as executor:
        future_to_host = {executor.submit(setup_remote_host, host): host for host in hosts}
        for future in concurrent.futures.as_completed(future_to_host):
            host = future_to_host[future]
            try:
                success = future.result()
                if success:
                    logger.info(f"Setup successful for {host}")
                else:
                    logger.warning(f"Setup failed for {host}")
            except Exception as e:
                logger.error(f"Setup for {host} raised an exception: {str(e)}")

    try:
        (graid_path / 'remote_setup.sh').unlink()
    except OSError:
        logger.warning("Failed to remove local temporary file")

    logger.info("Setup complete for all hosts.")
    logger.info("Update driver link.....")
    run_update_link_script()

    logger.info("All tasks completed.")
    # logger.info("IMPORTANT: The Ansible inventory file has been updated with the correct Python interpreter paths.")
    # logger.info(f"Use 'conda activate {config['conda_env_name']}' before running Ansible commands.")

if __name__ == "__main__":
    # run_update_link_script()
    main()