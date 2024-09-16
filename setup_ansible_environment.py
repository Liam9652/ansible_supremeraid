# This script is a Python program used to set up an Ansible environment.
# It reads configuration settings from a file, checks network connectivity,
# sets up SSH keyless login, downloads Miniconda, and installs the required environment
# and dependencies on remote hosts. The script supports parallel processing for
# setting up multiple hosts and logs detailed information throughout the process.

#!/usr/bin/env python3

import os
import sys
import yaml
import logging
import subprocess
import asyncio
import aiofiles
from pathlib import Path
from datetime import datetime
import getpass
import shutil
import argparse
import configparser
from tqdm import tqdm

# Constants
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / 'group_vars/all/main.yml'


async def load_config():
    if not CONFIG_PATH.exists():
        print(f"Configuration file not found: {CONFIG_PATH}")
        sys.exit(1)
    async with aiofiles.open(CONFIG_PATH, 'r') as file:
        config = yaml.safe_load(await file.read())
    return config['setup']


async def setup_logging(config):
    log_dir = SCRIPT_DIR / Path(config['log_dir']) / 'setup'
    log_dir_ansible = SCRIPT_DIR / Path(config['log_dir']) / 'ansible'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_dir_ansible.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f"setup_ansible_{timestamp}.log"

    logging.basicConfig(level=logging.INFO,
                        format='[%(asctime)s] [%(levelname)s] %(message)s',
                        handlers=[logging.FileHandler(log_file), logging.StreamHandler()])

    return logging.getLogger(__name__)


async def run_command(command, check=True, shell=False):
    process = await asyncio.create_subprocess_shell(
        command if shell else ' '.join(command),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if check and process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode, command, stderr)
    return stdout.decode(), stderr.decode(), process.returncode


async def transfer_files(source_files, destination, use_scp=False):
    host, remote_path = destination.split(':', 1)
    if not await ensure_remote_directory(host, remote_path):
        raise Exception(
            f"Failed to create remote directory {remote_path} on {host}")

    command = ['rsync', '-avz', '--progress'] + source_files + \
        [destination] if not use_scp and shutil.which(
            'rsync') else ['scp', '-r'] + source_files + [destination]

    stdout, stderr, returncode = await run_command(command, check=False)
    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, command, stderr)
    return stdout


async def retry_with_scp(func, *args, **kwargs):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
            if attempt + 1 < max_retries:
                logger.info("Retrying in 5 seconds...")
                await asyncio.sleep(5)
            elif attempt + 1 == max_retries and not kwargs.get('use_scp', False):
                logger.info("Retrying with scp...")
                kwargs['use_scp'] = True
                return await retry_with_scp(func, *args, **kwargs)
            else:
                logger.error(f"All {max_retries} attempts failed")
                raise


async def ensure_remote_directory(host, remote_path):
    check_dir_command = f"ssh {host} '[ -d {remote_path} ] && [ -w {remote_path} ] && echo exists_writable || echo not_exists_or_not_writable'"
    stdout, stderr, returncode = await run_command(check_dir_command, shell=True, check=False)

    if returncode != 0:
        logger.error(f"Failed to check remote directory. Error: {stderr}")
        return False

    if "exists_writable" in stdout:
        logger.info(
            f"Clearing contents of existing directory {remote_path} on {host}...")
        clear_dir_command = f"ssh {host} 'rm -rf {remote_path}/*'"
        _, stderr, returncode = await run_command(clear_dir_command, shell=True, check=False)
        if returncode != 0:
            logger.error(
                f"Failed to clear directory contents. Error: {stderr}")
            return False
    else:
        logger.info(f"Creating remote directory {remote_path} on {host}...")
        create_dir_command = f"ssh {host} 'mkdir -p {remote_path} && chmod 755 {remote_path}'"
        _, stderr, returncode = await run_command(create_dir_command, shell=True, check=False)
        if returncode != 0:
            logger.error(f"Failed to create remote directory. Error: {stderr}")
            return False

    return True


async def check_network(host, config):
    try:
        _, _, returncode = await run_command(['ping', '-c', str(config['ping_count']), '-W', str(config['ping_timeout']), host])
        return returncode == 0
    except Exception as e:
        logger.warning(f"Network check failed for {host}: {str(e)}")
        return False


async def setup_ssh_keyless(hosts, password):
    logger.info("Setting up SSH keyless authentication...")
    await check_ssh_key()
    await install_sshpass()

    for host in tqdm(hosts, desc="Setting up SSH keyless auth"):
        try:
            logger.info(f"Copying SSH key to {host}...")
            remove_known_host = f"ssh-keygen -R {host}"
            copy_id = f"sshpass -p {password} ssh-copy-id -o StrictHostKeyChecking=no root@{host}"
            await run_command(remove_known_host, shell=True)
            _, stderr, returncode = await run_command(copy_id, shell=True, check=False)

            if returncode == 0:
                logger.info(f"SSH key successfully copied to {host}")
            else:
                logger.error(f"Failed to copy SSH key to {host}: {stderr}")

        except Exception as e:
            logger.error(
                f"Failed to set up SSH keyless authentication for {host}: {str(e)}")


async def download_miniconda(config):
    miniconda_installer = f"Miniconda3-{config['miniconda_version']}-Linux-x86_64.sh"
    graid_path = Path(config['graid_path'].replace(
        "{{ ansible_env.HOME }}", os.environ.get('HOME', '')))
    installer_path = graid_path / miniconda_installer
    graid_path.mkdir(parents=True, exist_ok=True)
    if not installer_path.exists():
        logger.info(f"Downloading Miniconda installer to {installer_path}...")
        try:
            await run_command(['wget', f"https://repo.anaconda.com/miniconda/{miniconda_installer}", '-O', str(installer_path)])
            logger.info(
                f"Miniconda installer downloaded successfully to {installer_path}")
        except Exception as e:
            logger.error(f"Failed to download Miniconda installer: {str(e)}")
            raise
    else:
        logger.info(f"Miniconda installer already exists at {installer_path}")

    if not installer_path.exists():
        raise FileNotFoundError(
            f"Miniconda installer not found at {installer_path}")

    return installer_path


async def create_remote_setup_script(config, graid_path):
    remote_setup_script = f"""
#!/usr/bin/env bash
set -e
set -x

echo "Starting remote setup script"
echo "Current directory: $(pwd)"
echo "Graid path: {graid_path}"

mkdir -p {graid_path}
echo "Graid directory created/verified"

if ! command -v conda &> /dev/null; then
    echo "Conda not found, installing Miniconda"
    bash {graid_path}/Miniconda3-{config['miniconda_version']}-Linux-x86_64.sh -b -p {graid_path}/miniconda
    {graid_path}/miniconda/bin/conda init bash
    {graid_path}/miniconda/bin/conda init zsh
    export PATH="{graid_path}/miniconda/bin:$PATH"
else
    echo "Conda already installed"
fi

sleep 5
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
source {graid_path}/miniconda/bin/activate {config['conda_env_name']}
python --version
EOT
chmod +x {graid_path}/run_in_graid_env.sh

echo "Installing Ansible in the environment"
conda run -n {config['conda_env_name']} pip install ansible
conda run -n {config['conda_env_name']} pip install pexpect

echo "Remote setup completed successfully"
echo "{graid_path}/run_in_graid_env.sh"
"""
    remote_setup_path = graid_path / 'remote_setup.sh'
    async with aiofiles.open(remote_setup_path, 'w') as f:
        await f.write(remote_setup_script)
    return remote_setup_path


async def setup_remote_host(host, config):
    graid_path = Path(config['graid_path'].replace(
        "{{ ansible_env.HOME }}", os.environ.get('HOME', '')))
    logger.info(f"Setting up {host}...")

    if not await check_network(host, config):
        logger.warning(f"Skipping setup for {host} due to network issues")
        return False

    try:
        miniconda_installer = await download_miniconda(config)
        remote_setup_path = await create_remote_setup_script(config, graid_path)

        await retry_with_scp(transfer_files,
                             [str(miniconda_installer),
                              str(remote_setup_path)],
                             f"{host}:{graid_path}/")

        ssh_command = f"bash -x {graid_path}/remote_setup.sh"
        stdout, stderr, returncode = await run_command(['ssh', host, ssh_command])
        if returncode != 0:
            logger.error(f"Remote setup script execution failed on {host}")
            logger.error(f"Exit code: {returncode}")
            logger.error(f"STDOUT: {stdout}")
            logger.error(f"STDERR: {stderr}")
            return False

        remote_python_path = graid_path / "miniconda" / \
            "envs" / config['conda_env_name'] / "bin" / "python"
        logger.info(f"Python interpreter path on {host}: {remote_python_path}")

        inventory_path = Path(SCRIPT_DIR).parent / config['inventory_file']
        await update_ansible_inventory(host, remote_python_path, inventory_path)

        logger.info(f"Setup completed for {host}")
        return True
    except Exception as e:
        logger.error(f"Setup failed for {host}: {str(e)}")
        return False


async def update_ansible_inventory(host, remote_python_path, inventory_path):
    async with aiofiles.open(inventory_path, 'r') as f:
        lines = await f.readlines()
    async with aiofiles.open(inventory_path, 'w') as f:
        for line in lines:
            if line.strip().startswith(host):
                await f.write(f"{host} ansible_python_interpreter={remote_python_path}\n")
            else:
                await f.write(line)


async def run_update_link_script():
    update_script_path = SCRIPT_DIR / 'update_download_link.py'
    if not update_script_path.exists():
        logger.error(
            f"Update driver links script not found: {update_script_path}")
        return

    logger.info("Running update driver links script...")
    try:
        stdout, stderr, returncode = await run_command([sys.executable, str(update_script_path)])
        logger.info(f"Update driver links script output:\n{stdout}")
    except Exception as e:
        logger.error(f"Update driver links script failed: {e}")
        logger.error(f"Error output: {stderr}")


async def read_inventory(config):
    inventory_path = Path(SCRIPT_DIR).parent / config['inventory_file']
    logger.info(f"Using inventory file: {inventory_path}")

    hosts = []
    current_group = None
    async with aiofiles.open(inventory_path, 'r') as f:
        async for line in f:
            line = line.strip()
            if line.startswith('[') and line.endswith(']'):
                current_group = line[1:-1]
                logger.info(f"Found group: {current_group}")
            elif line and not line.startswith('#'):
                host = line.split()[0]
                hosts.append(host)
                logger.info(f"Added host: {host} (Group: {current_group})")

    logger.info(f"Total hosts found: {len(hosts)}")
    return hosts


async def install_sshpass():
    logger.info("Checking and installing sshpass...")

    # Determine the package manager
    if await run_command(['which', 'apt'], check=False)[2] == 0:
        install_cmd = ['sudo', 'apt', 'update', '&&',
                       'sudo', 'apt', 'install', '-y', 'sshpass']
    elif await run_command(['which', 'yum'], check=False)[2] == 0:
        install_cmd = ['sudo', 'yum', 'install', '-y', 'sshpass']
    elif await run_command(['which', 'zypper'], check=False)[2] == 0:
        install_cmd = ['sudo', 'zypper', 'install', '-y', 'sshpass']
    else:
        logger.error(
            "Unable to determine package manager. Please install sshpass manually.")
        return

    try:
        await run_command(install_cmd, shell=True)
        logger.info("sshpass installed successfully.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to install sshpass: {e}")
        sys.exit(1)


async def check_ssh_key():
    key_path = os.path.expanduser('~/.ssh/id_rsa')
    if not os.path.exists(key_path):
        logger.info("SSH key not found. Generating a new one...")
        await run_command(['ssh-keygen', '-t', 'rsa', '-N', '', '-f', key_path])
        logger.info("SSH key generated successfully.")
    else:
        logger.info("Existing SSH key found.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--password", help="SSH password")
    args = parser.parse_args()

    config = await load_config()
    global logger
    logger = await setup_logging(config)

    logger.info("Preparing setup...")
    hosts = await read_inventory(config)

    if not hosts:
        logger.error("No hosts found in the inventory file.")
        sys.exit(1)

    password = args.password or os.environ.get('SSH_PASSWORD') or getpass.getpass(
        "Enter the common password for all hosts: ")

    await setup_ssh_keyless(hosts, password)

    logger.info(f"Starting setup for {len(hosts)} hosts...")

    tasks = [setup_remote_host(host, config) for host in hosts]
    results = []
    with tqdm(total=len(hosts), desc="Setting up hosts") as pbar:
        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)
            pbar.update(1)
            if result:
                pbar.set_postfix_str("Success", refresh=True)
            else:
                pbar.set_postfix_str("Failed", refresh=True)

    success_count = sum(results)
    logger.info(
        f"Setup completed. Successful: {success_count}, Failed: {len(hosts) - success_count}")

    graid_path = Path(config['graid_path'].replace(
        "{{ ansible_env.HOME }}", os.environ.get('HOME', '')))
    try:
        (graid_path / 'remote_setup.sh').unlink()
    except OSError:
        logger.warning("Failed to remove local temporary file")

    logger.info("Setup complete for all hosts.")
    logger.info("Updating driver link...")
    await run_update_link_script()

    logger.info("All tasks completed.")

if __name__ == "__main__":
    if sys.version_info >= (3, 7):
        # Python 3.7 and above
        asyncio.run(main())
    else:
        # Python 3.6
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
        loop.close()
