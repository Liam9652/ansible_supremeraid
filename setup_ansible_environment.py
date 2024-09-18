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
import aiohttp
import tempfile
import platform
import paramiko

# Constants
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / 'group_vars/all/main.yml'
SSH_OPTIONS = ['-o', 'StrictHostKeyChecking=no',
               '-o', 'UserKnownHostsFile=/dev/null']
SYSTEM = platform.system().lower()
MAX_CONCURRENT_TASKS = 5


async def load_config() -> dict:
    """
    Load the configuration from the YAML file.

    Returns:
        dict: The configuration dictionary.
    """
    if not CONFIG_PATH.exists():
        print(f"Configuration file not found: {CONFIG_PATH}")
        sys.exit(1)
    # Open the file asynchronously and read its contents
    async with aiofiles.open(CONFIG_PATH, 'r') as file:
        config = yaml.safe_load(await file.read())
    # Return the 'setup' section of the configuration
    return config['setup']


async def check_sshpass_availability() -> bool:
    """
    Check if sshpass is installed and available on the system.

    Returns:
        bool: True if sshpass is available, False otherwise
    """
    # Detect if the system is Linux or macOS
    if SYSTEM in ['linux', 'darwin']:
        try:
            # Try to find sshpass in the system's PATH
            await run_command(['which', 'sshpass'])
            return True
        except subprocess.CalledProcessError:
            return False
    return False  # Windows keeps False


async def download_metadata(url):
    """
    Download the metadata file from the given URL and save it to a temporary file.

    Args:
        url (str): The URL of the metadata file to download.

    Returns:
        str: The path to the temporary file containing the metadata.
    """

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            # Check if the request was successful
            if response.status == 200:
                content = await response.text()
                # Create a temporary file
                with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as temp_file:
                    # Write the content to the temporary file
                    temp_file.write(content)
                    # Return the path to the temporary file
                    return temp_file.name
            else:
                raise Exception(
                    f"Failed to download metadata. Status code: {response.status}")


async def setup_logging(config):
    """
    Set up the logging directory and file handler.

    Args:
        config (dict): The configuration dictionary.

    Returns:
        logging.Logger: The logger with the file handler.
    """
    log_dir = SCRIPT_DIR / Path(config['log_dir']) / 'setup'
    log_dir_ansible = SCRIPT_DIR / Path(config['log_dir']) / 'ansible'

    # Create the logging directory if it doesn't exist
    log_dir.mkdir(parents=True, exist_ok=True)
    log_dir_ansible.mkdir(parents=True, exist_ok=True)

    # Generate the log file name with the current timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f"setup_ansible_{timestamp}.log"

    # Configure the logging
    logging.basicConfig(level=logging.INFO,
                        format='[%(asctime)s] [%(levelname)s] %(message)s',
                        handlers=[logging.FileHandler(log_file), logging.StreamHandler()])

    # Return the logger
    return logging.getLogger(__name__)


async def run_command(command, check=True, shell=False):
    """
    Run a command and get the output.

    Args:
        command (list or str): The command to run.
        check (bool): If True, raises CalledProcessError if the command fails.
        shell (bool): If True, the command is run in the shell.

    Returns:
        tuple: (stdout, stderr, returncode)
    """
    if shell:
        if isinstance(command, list):
            command = ' '.join(command)
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
    else:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

    stdout, stderr = await process.communicate()
    if check and process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode, command, stderr)
    return stdout.decode(), stderr.decode(), process.returncode


async def check_command_exists(command):
    """
    Check if a command exists in the system.

    Args:
        command (str): The command to check.

    Returns:
        bool: True if the command exists, False otherwise.
    """
    try:
        # Try to run 'which' command to check if the command exists
        await run_command(['which', command])
        return True
    except subprocess.CalledProcessError:
        # If the command does not exist, return False
        return False


async def transfer_files(source_files, destination, host_vars, use_scp=False, password=None, scp_options=None, sshpass_available=False):
    """
    Transfer files to a remote host using either scp or rsync.

    This function will first create the remote directory if it does not exist.
    If sshpass is available, it will use it to forward the password to the
    scp or rsync command. Otherwise, it will use paramiko to transfer the
    files.

    Args:
        source_files (list): The list of files to transfer.
        destination (str): The destination in the format of "host:remote_path".
        host_vars (dict): The host variables, including the username.
        use_scp (bool, optional): Whether to use scp or rsync. Defaults to False.
        password (str, optional): The password for the host. Defaults to None.
        scp_options (list, optional): The options to pass to the scp command. Defaults to None.
        sshpass_available (bool, optional): Whether sshpass is available. Defaults to False.

    Returns:
        str: The stdout of the command.
    """
    user = host_vars.get('ansible_user', 'root')
    host, remote_path = destination.split(':', 1)
    if not await ensure_remote_directory(host, host_vars, remote_path, password, sshpass_available):
        raise Exception(
            f"Failed to create remote directory {remote_path} on {user}@{host}")

    if sshpass_available:
        # use scp to transfer files
        if use_scp or not shutil.which('rsync'):
            base_command = ['scp', '-r']
            if scp_options:
                base_command.extend(scp_options)
            if password:
                base_command = ['sshpass', '-p', password] + base_command
            command = base_command + source_files + [f"{user}@{destination}"]
        else:
            command = ['rsync', '-avz', '--progress'] + \
                source_files + [f"{user}@{destination}"]
            if password:
                command = ['sshpass', '-p', password] + command

        stdout, stderr, returncode = await run_command(command, check=False)
        if returncode != 0:
            raise subprocess.CalledProcessError(returncode, command, stderr)
    else:
        # use paramiko to transfer files
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(host, username=user, password=password)
            with ssh.open_sftp() as sftp:
                for source_file in source_files:
                    remote_file = os.path.join(
                        remote_path, os.path.basename(source_file))
                    sftp.put(source_file, remote_file)
            stdout = "Files transferred successfully using paramiko."
        except Exception as e:
            raise Exception(
                f"Failed to transfer files using paramiko: {str(e)}")
        finally:
            ssh.close()

    return stdout


async def retry_with_scp(func, *args, **kwargs):
    """
    Retry a function that calls ssh or scp with exponential backoff.

    Args:
        func (function): The function to retry.
        *args (list): The arguments to pass to `func`.
        *kwargs (dict): The keyword arguments to pass to `func`.

    Kwargs:
        use_scp (bool): Whether to use scp instead of ssh. Defaults to False.

    Returns:
        The result of the successful call to `func`.

    Raises:
        Exception: If all retries fail.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            # If the exception is due to a network error, retry with exponential backoff
            if "Connection refused" in str(e) or "ConnectionResetError" in str(e):
                logger.warning(
                    f"Attempt {attempt + 1} failed due to network error: {str(e)}")
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
            # If the exception is not due to a network error, re-raise immediately
            else:
                raise


async def ensure_remote_directory(host, host_vars, remote_path, password=None, sshpass_available=False):
    """
    Ensure that a remote directory exists and is writable.

    Args:
        host (str): The hostname or IP address of the host to check.
        host_vars (dict): The host variables, including the username.
        remote_path (str): The path of the directory to check.
        password (str, optional): The password for the host. Defaults to None.
        sshpass_available (bool, optional): Whether sshpass is available. Defaults to False.

    Returns:
        bool: True if the directory exists and is writable, False otherwise.
    """
    user = host_vars.get('ansible_user', 'root')

    if sshpass_available:
        logger.info(
            f"Check if remote directory {remote_path} exists and is writable using sshpass...")
        check_dir_command = f"ssh {' '.join(SSH_OPTIONS)} {user}@{host} '[ -d {remote_path} ] && [ -w {remote_path} ] && echo exists_writable || echo not_exists_or_not_writable'"
        if password:
            check_dir_command = f"sshpass -p {password} " + check_dir_command

        stdout, stderr, returncode = await run_command(check_dir_command, shell=True, check=False)

        if returncode != 0:
            logger.error(f"Failed to check remote directory. Error: {stderr}")
            return False

        if "exists_writable" in stdout:
            logger.info(
                f"Clearing contents of existing directory {remote_path} on {user}@{host}...")
            clear_dir_command = f"ssh {' '.join(SSH_OPTIONS)} {user}@{host} 'rm -rf {remote_path}/*'"
            if password:
                clear_dir_command = f"sshpass -p {password} " + \
                    clear_dir_command
            _, stderr, returncode = await run_command(clear_dir_command, shell=True, check=False)
            if returncode != 0:
                logger.error(
                    f"Failed to clear directory contents. Error: {stderr}")
                return False
        else:
            logger.info(
                f"Creating remote directory {remote_path} on {user}@{host}...")
            create_dir_command = f"ssh {' '.join(SSH_OPTIONS)} {user}@{host} 'mkdir -p {remote_path} && chmod 755 {remote_path}'"
            if password:
                create_dir_command = f"sshpass -p {password} " + \
                    create_dir_command
            _, stderr, returncode = await run_command(create_dir_command, shell=True, check=False)
            if returncode != 0:
                logger.error(
                    f"Failed to create remote directory. Error: {stderr}")
                return False

    else:
        logger.info(
            f"Check if remote directory {remote_path} exists and is writable using paramiko...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(host, username=user, password=password)
            # check if remote directory exists and is writable
            stdin, stdout, stderr = ssh.exec_command(
                f'[ -d {remote_path} ] && [ -w {remote_path} ] && echo exists_writable || echo not_exists_or_not_writable')
            output = stdout.read().decode()
            if 'exists_writable' in output:
                # clear contents of existing directory
                ssh.exec_command(f'rm -rf {remote_path}/*')
            else:
                # create remote directory
                ssh.exec_command(
                    f'mkdir -p {remote_path} && chmod 755 {remote_path}')
        except Exception as e:
            logger.error(
                f"Failed to ensure remote directory via paramiko: {e}")
            return False
        finally:
            ssh.close()

    return True


async def check_network(host, config):
    """
    Checks if a host is reachable via ping.

    Args:
        host (str): The hostname or IP address of the host to check.
        config (dict): The configuration dictionary.

    Returns:
        bool: True if the host is reachable, False otherwise.
    """
    try:
        # Run a ping command with the specified count and timeout
        _, _, returncode = await run_command(['ping', '-c', str(config['ping_count']), '-W', str(config['ping_timeout']), host])
        # If the ping command returns 0, the host is reachable
        return returncode == 0
    except Exception as e:
        # If the ping command fails, log a warning and return False
        logger.warning(f"Network check failed for {host}: {str(e)}")
        return False


async def setup_ssh_keyless(hosts, password, sshpass_available):
    """
    Sets up SSH keyless authentication on the target hosts.

    This function copies the local SSH key to the target hosts using sshpass
    or paramiko. It assumes that the target hosts are reachable via SSH.

    Args:
        hosts (list): A list of tuples containing (host, host_vars).
        password (str): The common password for all hosts.
        sshpass_available (bool): Whether sshpass is available.

    Returns:
        None
    """
    logger.info("Setting up SSH keyless authentication...")

    await check_ssh_key()

    for host, host_vars in tqdm(hosts, desc="Setting up SSH keyless auth"):
        user = host_vars.get('ansible_user', 'root')
        try:
            logger.info(f"Copying SSH key to {user}@{host}...")
            # Remove the host from the known hosts file
            remove_known_host = f"ssh-keygen -R {host} || true"
            await run_command(remove_known_host, shell=True)

            if sshpass_available:
                # Use sshpass to copy ssh key
                copy_id = f"sshpass -p {password} ssh-copy-id -o StrictHostKeyChecking=no {user}@{host}"
                _, stderr, returncode = await run_command(copy_id, shell=True, check=False)
            else:
                # Use paramiko to copy ssh key
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                try:
                    ssh.connect(host, username=user, password=password)
                    stdin, stdout, stderr = ssh.exec_command(
                        'mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys', get_pty=True)
                    stdin.write(open(os.path.expanduser(
                        '~/.ssh/id_rsa.pub')).read())
                    stdin.channel.shutdown_write()
                    returncode = stdout.channel.recv_exit_status()
                finally:
                    ssh.close()

            if returncode == 0:
                logger.info(f"SSH key successfully copied to {user}@{host}")
            else:
                logger.error(
                    f"Failed to copy SSH key to {user}@{host}: {stderr}")

        except Exception as e:
            logger.error(
                f"Failed to set up SSH keyless authentication for {user}@{host}: {str(e)}")


async def download_miniconda(config):
    """
    Downloads the Miniconda installer from the internet and saves it to the
    specified path on the target host.

    Args:
        config (dict): The configuration dictionary.

    Returns:
        Path: The path to the Miniconda installer on the target host.
    """
    miniconda_installer = f"Miniconda3-{config['miniconda_version']}-Linux-x86_64.sh"
    graid_path = Path(config['graid_path'].replace(
        "{{ ansible_env.HOME }}", os.environ.get('HOME', '')))
    installer_path = graid_path / miniconda_installer
    graid_path.mkdir(parents=True, exist_ok=True)

    # Download the Miniconda installer if it doesn't exist
    if not installer_path.exists():
        logger.info(f"Downloading Miniconda installer to {installer_path}...")
        try:
            # Use wget to download the installer
            await run_command(['wget', f"https://repo.anaconda.com/miniconda/{miniconda_installer}", '-O', str(installer_path)])
            logger.info(
                f"Miniconda installer downloaded successfully to {installer_path}")
        except Exception as e:
            logger.error(f"Failed to download Miniconda installer: {str(e)}")
            raise
    else:
        logger.info(f"Miniconda installer already exists at {installer_path}")

    # Check if the installer was downloaded successfully
    if not installer_path.exists():
        raise FileNotFoundError(
            f"Miniconda installer not found at {installer_path}")

    return installer_path


async def create_remote_setup_script(config, graid_path):
    """
    Creates a remote setup script on the target host.

    The script will:
    1. Create a directory for the Graid environment
    2. Install Miniconda if not already present
    3. Create a conda environment for Graid if not already present
    4. Create a script to run commands in the Graid environment

    Args:
        config (dict): The configuration dictionary.
        graid_path (Path): The path to the Graid environment.

    Returns:
        Path: The path to the remote setup script.
    """
    remote_setup_script = f"""
#!/usr/bin/env bash
set -e
set -x

# Create the Graid directory if it doesn't exist
echo "Starting remote setup script"
echo "Current directory: $(pwd)"
echo "Graid path: {graid_path}"
mkdir -p {graid_path}
echo "Graid directory created/verified"

# Install Miniconda if it's not already installed
if ! command -v conda &> /dev/null; then
    echo "Conda not found, installing Miniconda"
    bash {graid_path}/Miniconda3-{config['miniconda_version']}-Linux-x86_64.sh -b -p {graid_path}/miniconda
    # Initialize conda for bash and zsh
    {graid_path}/miniconda/bin/conda init bash
    {graid_path}/miniconda/bin/conda init zsh
    # Add the conda bin directory to the PATH
    export PATH="{graid_path}/miniconda/bin:$PATH"
else
    echo "Conda already installed"
fi

# Verify that conda is installed correctly
sleep 5
echo "Conda version: $(conda --version)"

# Create the conda environment if it doesn't already exist
if ! conda env list | grep -q {config['conda_env_name']}; then
    echo "Creating conda environment {config['conda_env_name']}"
    conda create -n {config['conda_env_name']} python={config['python_version']} -y
else
    echo "Conda environment {config['conda_env_name']} already exists"
fi

# Create a script to run commands in the Graid environment
echo "Creating run_in_graid_env.sh script"
cat << EOT > {graid_path}/run_in_graid_env.sh
#!/bin/bash
# Activate the conda environment
source {graid_path}/miniconda/bin/activate {config['conda_env_name']}
# Verify that the environment is activated
python --version
EOT
chmod +x {graid_path}/run_in_graid_env.sh

echo "Remote setup completed successfully"
echo "{graid_path}/run_in_graid_env.sh"
"""
    remote_setup_path = graid_path / 'remote_setup.sh'
    async with aiofiles.open(remote_setup_path, 'w') as f:
        await f.write(remote_setup_script)
    return remote_setup_path


async def setup_remote_host(host, host_vars, config, password=None, use_keyless=False, sshpass_available=False):
    """
    Sets up a remote host with the required environment.

    Args:
        host (str): The hostname of the host to set up.
        host_vars (dict): The host variables for the host.
        config (dict): The configuration dictionary.
        password (str, optional): The password for the host. Defaults to None.
        use_keyless (bool, optional): Whether to use keyless SSH. Defaults to False.
        sshpass_available (bool, optional): Whether sshpass is available. Defaults to False.

    Returns:
        bool: True if the setup was successful, False otherwise.
    """
    user = host_vars.get('ansible_user', 'root')
    graid_path = Path(config['graid_path'].replace(
        "{{ ansible_env.HOME }}", os.environ.get('HOME', '')))
    logger.info(f"Setting up {user}@{host}...")

    # Check if the host is reachable
    if not await check_network(host, config):
        logger.warning(f"Skipping setup for {host} due to network issues")
        return False

    try:
        # Download the Miniconda installer
        miniconda_installer = await download_miniconda(config)

        # Create the remote setup script
        remote_setup_path = await create_remote_setup_script(config, graid_path)

        # Transfer the Miniconda installer and the remote setup script to the host
        await retry_with_scp(transfer_files,
                             [str(miniconda_installer),
                              str(remote_setup_path)],
                             f"{host}:{graid_path}/",
                             host_vars,
                             use_scp=True,
                             password=password,
                             scp_options=SSH_OPTIONS,
                             sshpass_available=sshpass_available)

        # Run the remote setup script
        ssh_command = f"bash -x {graid_path}/remote_setup.sh"
        if sshpass_available and password and not use_keyless:
            ssh_command = ['sshpass', '-p', password, 'ssh'] + \
                SSH_OPTIONS + [f"{user}@{host}", ssh_command]
        else:
            ssh_command = ['ssh'] + SSH_OPTIONS + \
                [f"{user}@{host}", ssh_command]
        if isinstance(ssh_command, list):
            ssh_command = ' '.join(ssh_command)

        if sshpass_available or use_keyless:
            stdout, stderr, returncode = await run_command(ssh_command, shell=True)
        else:
            stdout, stderr, returncode = await ssh_connect(host, user, password, ssh_command)

        if returncode != 0:
            logger.error(
                f"Remote setup script execution failed on {user}@{host}")
            logger.error(f"Exit code: {returncode}")
            logger.error(f"STDOUT: {stdout}")
            logger.error(f"STDERR: {stderr}")
            return False

        # Update the Ansible inventory file with the new remote Python interpreter path
        remote_python_path = graid_path / "miniconda" / \
            "envs" / config['conda_env_name'] / "bin" / "python"
        logger.info(f"Python interpreter path on {host}: {remote_python_path}")

        inventory_path = Path(SCRIPT_DIR).parent / config['inventory_file']
        await update_ansible_inventory(host, host_vars, remote_python_path, inventory_path)

        logger.info(f"Setup completed for {user}@{host}")
        return True
    except Exception as e:
        logger.error(f"Setup failed for {user}@{host}: {str(e)}")
        return False


async def update_ansible_inventory(host, host_vars, remote_python_path, inventory_path):
    """
    Updates the Ansible inventory file with the new remote Python interpreter path.

    Args:
        host (str): The hostname of the host to update.
        host_vars (dict): The host variables for the host.
        remote_python_path (Path): The path of the remote Python interpreter.
        inventory_path (Path): The path of the Ansible inventory file.
    """
    async with aiofiles.open(inventory_path, 'r') as f:
        lines = await f.readlines()
    async with aiofiles.open(inventory_path, 'w') as f:
        for line in lines:
            if line.strip().startswith(host):
                # Update the Python interpreter path for this host
                parts = line.split()
                new_parts = [
                    host, f"ansible_python_interpreter={remote_python_path}"]
                for part in parts[1:]:
                    # Preserve other host variables
                    if not part.startswith('ansible_python_interpreter='):
                        new_parts.append(part)
                await f.write(' '.join(new_parts) + '\n')
            else:
                # Don't change other hosts
                await f.write(line)


async def run_update_link_script(config):
    """
    Runs the update driver links script.

    The script reads the metadata file and updates the Ansible
    inventory file with the new download links.

    Args:
        config (dict): The configuration dictionary.

    Returns:
        None
    """
    update_script_path = SCRIPT_DIR / 'update_download_link.py'
    if not update_script_path.exists():
        logger.error(
            f"Update driver links script not found: {update_script_path}")
        return

    logger.info("Running update driver links script...")
    try:
        metadata_path = config.get('metadata_file', 'metadata.json')
        yaml_path = Path(config.get('download_urls_yaml',
                         'group_vars/all/download_urls.yml'))

        # Check if metadata_path is a URL
        if metadata_path.startswith('http://') or metadata_path.startswith('https://'):
            logger.info(f"Downloading metadata from URL: {metadata_path}")
            metadata_path = await download_metadata(metadata_path)
            logger.info(
                f"Metadata downloaded to temporary file: {metadata_path}")
        else:
            metadata_path = Path(metadata_path)
            if not metadata_path.exists():
                raise FileNotFoundError(
                    f"Local metadata file not found: {metadata_path}")
            logger.info(f"Using local metadata file: {metadata_path}")

        command = [
            sys.executable,
            str(update_script_path),
            '--metadata', str(metadata_path),
            '--yaml', str(yaml_path)
        ]

        stdout, stderr, returncode = await run_command(command)
        logger.info(f"Update driver links script output:\n{stdout}")
        if returncode != 0:
            logger.error(
                f"Update driver links script failed with return code {returncode}")
            logger.error(f"Error output: {stderr}")
    except Exception as e:
        logger.error(f"Update driver links script failed: {e}")
    finally:
        # Clean up the temporary file if it was created
        if 'metadata_path' in locals() and metadata_path.startswith('/tmp/'):
            try:
                os.unlink(metadata_path)
                logger.info(
                    f"Temporary metadata file removed: {metadata_path}")
            except Exception as e:
                logger.warning(
                    f"Failed to remove temporary metadata file: {e}")


async def read_inventory(config):
    """
    Reads the Ansible inventory file and returns a list of hosts
    with their respective variables.

    The inventory file is expected to be in the format:

    [group1]
    host1 ansible_user=root
    host2 ansible_user=liam

    [group2]
    host3 ansible_group=group2
    host4 ansible_user=liam ansible_group=group2

    :param config: The configuration dictionary.
    :return: A list of tuples, where each tuple contains the host name
             and a dictionary of its variables.
    """
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
                parts = line.split()
                host = parts[0]
                # Default user and group
                host_vars = {'ansible_user': 'root', 'group': current_group}

                for part in parts[1:]:
                    if '=' in part:
                        key, value = part.split('=', 1)
                        host_vars[key] = value

                hosts.append((host, host_vars))
                logger.info(f"Added host: {host} with vars: {host_vars}")

    logger.info(f"Total hosts found: {len(hosts)}")
    return hosts


async def install_sshpass() -> bool:
    """
    Checks if sshpass is installed, and if not, attempts to install it.

    On Linux systems, it uses the package manager to install sshpass.
    On macOS, it uses Homebrew to install sshpass.
    If installation fails, it prompts the user to either exit the script or
    continue without sshpass.
    """
    logger.info("Checking and installing sshpass...")

    if SYSTEM == 'linux':
        # Determine the package manager
        if await check_command_exists('apt'):  # For Debian-based systems
            install_cmd = 'sudo apt update && sudo apt install -y sshpass'
        elif await check_command_exists('yum'):  # For RHEL-based systems
            install_cmd = 'sudo yum install -y sshpass'
        elif await check_command_exists('zypper'):  # For OpenSUSE
            install_cmd = 'sudo zypper install -y sshpass'
        elif await check_command_exists('dnf'):  # For newer Fedora versions
            install_cmd = 'sudo dnf install -y sshpass'
        elif await check_command_exists('pacman'):  # For Arch-based systems
            install_cmd = 'sudo pacman -S --noconfirm sshpass'
        else:
            logger.error(
                "Unable to determine package manager. Please install sshpass manually.")
            return await handle_sshpass_installation_failure()

        try:
            await run_command(install_cmd, shell=True)
            logger.info("sshpass installed successfully.")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install sshpass: {e}")
            return await handle_sshpass_installation_failure()

    elif SYSTEM == 'darwin':
        logger.info("Installing sshpass on macOS using Homebrew...")
        try:
            await run_command(['brew', 'install', 'sshpass'])
            logger.info("sshpass installed successfully.")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install sshpass: {e}")
            logger.info(
                "Please install Homebrew and try again, or proceed without sshpass.")
            return await handle_sshpass_installation_failure()
    else:
        logger.warning(
            "sshpass is not available on this system. Using alternative method for SSH.")
        return False

    return True


async def handle_sshpass_installation_failure() -> bool:
    """
    Handles the case where sshpass installation fails.

    Prompts the user to either exit the script or continue without sshpass.
    """
    while True:
        choice = input(
            "Do you want to exit the script (e) or continue without sshpass (c)? ").lower()
        if choice == 'e':
            logger.info("Exiting script as per user request.")
            sys.exit(1)
        elif choice == 'c':
            logger.info(
                "Continuing without sshpass. Some features may be limited.")
            return False
        else:
            print("Invalid choice. Please enter 'e' to exit or 'c' to continue.")


async def ssh_connect(host, username, password, command):
    """
    Connects to a host using SSH and executes a command.

    Args:
        host (str): The hostname or IP address of the host to connect to.
        username (str): The username to use for the SSH connection.
        password (str): The password to use for the SSH connection.
        command (str): The command to execute on the remote host.

    Returns:
        tuple: A tuple containing the stdout, stderr, and exit status of the command.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        # Connect to the host using SSH
        client.connect(host, username=username, password=password)
        # Execute the command
        stdin, stdout, stderr = client.exec_command(command)
        # Return the stdout, stderr, and exit status
        return stdout.read().decode(), stderr.read().decode(), stdout.channel.recv_exit_status()
    finally:
        # Close the SSH connection
        client.close()


async def check_ssh_key():
    """
    Checks if an SSH key exists in the default location, and generates one if it doesn't.
    """
    key_path = os.path.expanduser('~/.ssh/id_rsa')
    if not os.path.exists(key_path):
        # If the key doesn't exist, generate a new one
        logger.info("SSH key not found. Generating a new one...")
        await run_command(['ssh-keygen', '-t', 'rsa', '-N', '', '-f', key_path])
        logger.info("SSH key generated successfully.")
    else:
        # If the key exists, just log a message
        logger.info("Existing SSH key found.")


async def main():
    """
    The main entry point of the script. It sets up the logging, reads the inventory
    file, checks for and installs sshpass, and then sets up each host in parallel.
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--password", help="SSH password")
    args = parser.parse_args()

    config = await load_config()
    global logger
    logger = await setup_logging(config)

    logger.info("Preparing setup...")

    logger.info("Checking and installing sshpass..")
    sshpass_available = await install_sshpass()

    hosts = await read_inventory(config)

    if not hosts:
        logger.error("No hosts found in the inventory file.")
        sys.exit(1)

    # Password handling logic
    if args.password:
        password = args.password
        setup_keyless = False
    else:
        setup_choice = input(
            "Do you want to set up SSH keyless authentication? (y/n): ").lower()
        if setup_choice == 'y':
            password = getpass.getpass(
                "Enter the common password for all hosts (for initial setup): ")
            setup_keyless = True
        else:
            password = getpass.getpass(
                "Enter the common password for all hosts: ")
            setup_keyless = False

    if setup_keyless:
        await setup_ssh_keyless(hosts, password, sshpass_available)
        password = None  # After keyless setup, we don't need the password

    async def setup_host_with_semaphore(host, host_vars):
        """
        Sets up a host using the semaphore to limit the number of concurrent tasks.
        """
        async with semaphore:
            return await setup_remote_host(host, host_vars, config, password=password, use_keyless=setup_keyless,
                                           sshpass_available=sshpass_available)

    logger.info(f"Starting setup for {len(hosts)} hosts...")
    tasks = [setup_host_with_semaphore(host, host_vars)
             for host, host_vars in hosts]
    results = await asyncio.gather(*tasks)

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
    await run_update_link_script(config)

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
