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
import time
import fcntl
from contextlib import contextmanager
import random
import textwrap


# Constants
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / 'group_vars/all/main.yml'
SSH_OPTIONS = ['-o', 'StrictHostKeyChecking=no',
               '-o', 'UserKnownHostsFile=/dev/null']
SYSTEM = platform.system().lower()
MAX_CONCURRENT_TASKS = 5


@contextmanager
def acquire_lock(lock_file):
    lockf = None
    try:
        lockf = open(lock_file, 'w')
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield lockf
    except IOError:
        if lockf:
            lockf.close()
        raise
    finally:
        if lockf:
            fcntl.flock(lockf, fcntl.LOCK_UN)
            lockf.close()
            try:
                os.remove(lock_file)
            except OSError:
                pass


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
            # Change all the elements in the list to strings and join them
            command = ' '.join(map(str, command))
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
    else:
        if isinstance(command, str):
            # If the command is a string, split it into a list
            command = command.split()
        # Ensure that the command is a list
        command = [str(c) for c in command]
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


async def transfer_files(source_files, destination, host_vars, use_scp=False, password=None, scp_options=None, sshpass_available=False, retries=3):
    """
    Transfer files to a remote host using either scp or rsync.

    This function will first create the remote directory if it does not exist.
    If sshpass is available, it will use it to forward the password to the
    scp or rsync command. Otherwise, it will use paramiko to transfer the
    files.

    Args:
        source_files (list): The list of files to transfer.
        destination (str): The destination in the format of "host:remote_path".
        host_vars (dict): The host variables, including the username and port.
        use_scp (bool, optional): Whether to use scp or rsync. Defaults to False.
        password (str, optional): The password for the host. Defaults to None.
        scp_options (list, optional): The options to pass to the scp command. Defaults to None.
        sshpass_available (bool, optional): Whether sshpass is available. Defaults to False.
        retries (int, optional): Number of retries for file transfer in case of failure. Defaults to 3.

    Returns:
        str: The stdout of the command.
    """
    user = host_vars.get('_user', host_vars.get('ansible_user', 'root'))
    port = host_vars.get('ansible_port', '22')
    host, remote_path = destination.split(':', 1)

    for attempt in range(retries):
        try:

            if not await ensure_remote_directory(host, host_vars, remote_path, password, sshpass_available):
                raise Exception(
                    f"Failed to create remote directory {remote_path} on {user}@{host}:{port}")

            for source_file in source_files:
                if not os.path.exists(source_file):
                    logger.error(
                        f"Source file not found: {source_file} on {user}@{host}:{port}")
                    raise FileNotFoundError(
                        f"Source file not found: {source_file}")

            if sshpass_available:
                # use scp to transfer files
                if use_scp or not shutil.which('rsync'):
                    base_command = ['scp', '-r', '-v', '-P', port]
                    if scp_options:
                        base_command.extend(scp_options)
                    if password:
                        base_command = ['sshpass', '-p',
                                        password] + base_command
                    command = base_command + source_files + \
                        [f"{user}@{host}:{remote_path}"]
                else:
                    command = ['rsync', '-avz', '--progress', f'-e ssh -p {port}'] + \
                        source_files + [f"{user}@{host}:{remote_path}"]
                    if password:
                        command = ['sshpass', '-p', password] + command

                stdout, stderr, returncode = await run_command(command, check=False)
                if returncode != 0:
                    logger.error(f"File transfer failed. Stdout: {stdout}")
                    logger.error(f"File transfer failed. Stderr: {stderr}")
                    raise subprocess.CalledProcessError(
                        returncode, command, stderr)
            else:
                # use paramiko to transfer files
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                try:
                    ssh.connect(host, port=int(port),
                                username=user, password=password)
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
        except Exception as e:
            logger.error(f"Attempt {attempt + 1}/{retries} failed: {str(e)}")
            if attempt + 1 < retries:
                raise e

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


async def ensure_remote_directory(host, host_vars, remote_path, password=None, sshpass_available=False, cache=set()):
    """
    Ensure that the remote directory exists and is writable. The function will cache the created directories to avoid
    repeating the check unnecessarily.

    Args:
        host (str): The remote host.
        host_vars (dict): The host variables, including the username and port.
        remote_path (str): The remote directory path to ensure.
        password (str, optional): The password for the host. Defaults to None.
        sshpass_available (bool, optional): Whether sshpass is available. Defaults to False.
        cache (set, optional): Cache to track directories that have been ensured. Defaults to an empty set.

    Returns:
        bool: True if the directory exists or was created successfully, False otherwise.
    """
    if (host, remote_path) in cache:
        return True

    user = host_vars.get('_user', host_vars.get('ansible_user', 'root'))
    port = host_vars.get('ansible_port', '22')

    if host in ('localhost', '127.0.0.1'):
        logger.info(f"Skipping directory clearing for localhost")
        return True

    if sshpass_available:
        logger.info(
            f"Check if remote directory {remote_path} exists and is writable using sshpass...")
        check_dir_command = f"ssh {' '.join(SSH_OPTIONS)} -p {port} {user}@{host} '[ -d {remote_path} ] && [ -w {remote_path} ] && echo exists_writable || echo not_exists_or_not_writable'"
        if password:
            check_dir_command = f"sshpass -p {password} " + check_dir_command

        stdout, stderr, returncode = await run_command(check_dir_command, shell=True, check=False)

        if returncode != 0:
            logger.error(f"Failed to check remote directory. Error: {stderr}")
            return False

        if "exists_writable" in stdout:
            logger.info(
                f"Clearing contents of existing directory {remote_path} on {user}@{host}...")
            clear_dir_command = f"ssh {' '.join(SSH_OPTIONS)} -p {port} {user}@{host} 'rm -rf {remote_path}/*'"
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
            create_dir_command = f"ssh {' '.join(SSH_OPTIONS)} -p {port} {user}@{host} 'mkdir -p {remote_path} && chmod 755 {remote_path}'"
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
            ssh.connect(host, port=int(port), username=user, password=password)

            # Check if remote directory exists and is writable
            stdin, stdout, stderr = ssh.exec_command(
                f'[ -d {remote_path} ] && [ -w {remote_path} ] && echo exists_writable || echo not_exists_or_not_writable')
            output = stdout.read().decode().strip()

            if output == 'exists_writable':
                logger.info(
                    f"Clearing contents of existing directory {remote_path} on {user}@{host}...")
                ssh.exec_command(f'rm -rf {remote_path}/*')
            else:
                logger.info(
                    f"Creating remote directory {remote_path} on {user}@{host}...")
                ssh.exec_command(
                    f'mkdir -p {remote_path} && chmod 755 {remote_path}')

            # Verify the directory was created or cleared successfully
            stdin, stdout, stderr = ssh.exec_command(
                f'[ -d {remote_path} ] && [ -w {remote_path} ] && echo success || echo failure')
            if stdout.read().decode().strip() != 'success':
                logger.error(
                    f"Failed to create or clear remote directory {remote_path} on {user}@{host}")
                return False

        except Exception as e:
            logger.error(
                f"Failed to ensure remote directory via paramiko: {e}")
            return False
        finally:
            ssh.close()

    cache.add((host, remote_path))

    return True


async def check_network(host, host_vars, config):
    """
    Checks if a host is reachable via SSH by attempting to establish a connection.

    Args:
        host (str): The hostname or IP address of the host to check.
        port (str): The SSH port to use for the connection.
        config (dict): The configuration dictionary.

    Returns:
        bool: True if the host is reachable, False otherwise.
    """
    try:
        user = host_vars.get('_user', host_vars.get('ansible_user', 'root'))
        port = host_vars.get('ansible_port', '22')
        logger.debug(
            f"Attempting to connect to {host}:{port} with username {user}...")
        command = f"ssh -o BatchMode=yes -p {port} {user}@{host} exit"
        stdout, stderr, returncode = await run_command(command, shell=True, check=False)
        if returncode == 0:
            logger.info(f"Successfully connected to {host}:{port}")
            return True
        else:
            logger.warning(
                f"SSH connection failed for {host}:{port}: {stderr}")
    except Exception as e:
        logger.warning(f"Network check failed for {host}:{port}: {str(e)}")
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
    try:
        await check_ssh_key()
    except Exception as e:
        logger.error(f"Failed to setup SSH key: {e}")
        return

    for host, host_vars in tqdm(hosts, desc="Setting up SSH keyless auth"):
        user = host_vars.get('_user', host_vars.get('ansible_user', 'root'))
        port = host_vars.get('ansible_port', '22')
        max_retries = 3
        for attempt in range(max_retries):
            try:
                logger.info(
                    f"Copying SSH key to {user}@{host}:{port}...(Attempt {attempt + 1}/{max_retries})...")
                # Remove the host from the known hosts file
                remove_known_host = f"ssh-keygen -R [{host}]:{port} || true"
                await run_command(remove_known_host, shell=True)

                if sshpass_available:
                    # Use sshpass to copy ssh key
                    copy_id = f"sshpass -p {password} ssh-copy-id -o StrictHostKeyChecking=no -p {port} {user}@{host}"
                    _, stderr, returncode = await run_command(copy_id, shell=True, check=False)
                else:
                    # Use paramiko to copy ssh key
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    try:
                        ssh.connect(host, port=int(port),
                                    username=user, password=password)
                        stdin, stdout, stderr = ssh.exec_command(
                            'mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys', get_pty=True)
                        stdin.write(open(os.path.expanduser(
                            '~/.ssh/id_rsa.pub')).read())
                        stdin.channel.shutdown_write()
                        returncode = stdout.channel.recv_exit_status()
                    finally:
                        ssh.close()

                if returncode == 0:
                    logger.info(
                        f"SSH key successfully copied to {user}@{host}:{port}")
                    break
                else:
                    logger.warning(
                        f"Failed to copy SSH key to {user}@{host}:{port} (Attempt {attempt + 1}/{max_retries})")
                    logger.warning(f"Command output: {stdout.read().decode()}")
                    logger.warning(f"Command error: {stderr.read().decode()}")

                    if attempt == max_retries - 1:
                        logger.error(
                            f"All attempts to copy SSH key to {user}@{host}:{port} failed.")
                    else:
                        logger.info(f"Retrying in 5 seconds...")
                        await asyncio.sleep(5)

            except Exception as e:
                logger.error(
                    f"Error during SSH key copy for {user}@{host}:{port}: {str(e)}")
                if attempt == max_retries - 1:
                    logger.error(
                        f"All attempts to copy SSH key to {user}@{host}:{port} failed.")
                else:
                    logger.info(f"Retrying in 5 seconds...")
                    await asyncio.sleep(5)


def clear_stale_lock(lock_file, max_age=30):  # 1 hour
    if os.path.exists(lock_file):
        if time.time() - os.path.getctime(lock_file) > max_age:
            try:
                os.remove(lock_file)
                logger.warning(f"Removed stale lock file: {lock_file}")
            except OSError:
                logger.error(f"Failed to remove stale lock file: {lock_file}")


async def download_miniconda(config, graid_path):
    """
    Downloads the Miniconda installer from the internet and saves it to the
    specified path on the target host.

    Args:
        config (dict): The configuration dictionary.
        graid_path (Path): The path to save the installer.
        host (str): The hostname for which we're downloading.

    Returns:
        Path: The path to the Miniconda installer on the target host.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == 'windows':
        if machine == 'amd64' or machine == 'x86_64':
            miniconda_installer = f"Miniconda3-{config['miniconda_version']}-Windows-x86_64.exe"
        else:
            miniconda_installer = f"Miniconda3-{config['miniconda_version']}-Windows-x86.exe"
    elif system == 'darwin':  # macOS
        if machine == 'arm64':
            miniconda_installer = f"Miniconda3-{config['miniconda_version']}-MacOSX-arm64.sh"
        else:
            miniconda_installer = f"Miniconda3-{config['miniconda_version']}-MacOSX-x86_64.sh"
    elif system == 'linux':
        if machine == 'x86_64':
            miniconda_installer = f"Miniconda3-{config['miniconda_version']}-Linux-x86_64.sh"
        elif machine == 'aarch64':
            miniconda_installer = f"Miniconda3-{config['miniconda_version']}-Linux-aarch64.sh"
    else:
        raise ValueError(f"Unsupported system: {system}")

    installer_path = graid_path / miniconda_installer
    graid_path.mkdir(parents=True, exist_ok=True)
    lock_file = graid_path / ".miniconda_download.lock"

    logger.info(
        f"Attempting to acquire global lock for downloading Miniconda installer: {lock_file}")
    max_retries = 5

    for attempt in range(max_retries):
        try:
            clear_stale_lock(lock_file)
            logger.info(
                f"Attempting to acquire lock (Attempt {attempt + 1}/{max_retries})")

            with acquire_lock(lock_file):
                logger.info(
                    "Lock acquired for downloading Miniconda installer")

                if not installer_path.exists():
                    logger.info(
                        f"Downloading Miniconda installer to {installer_path}... on control node")
                    url = f"https://repo.anaconda.com/miniconda/{miniconda_installer}"
                    logger.info(f"Download URL: {url}")

                    if not os.access(graid_path, os.W_OK):
                        logger.error(
                            f"No write permission for directory: {graid_path}")
                        raise PermissionError(f"Cannot write to {graid_path}")

                    async with aiohttp.ClientSession() as session:
                        logger.info("Starting download...")
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=600)) as response:
                            logger.info(
                                f"Received response with status: {response.status}")
                            if response.status == 200:
                                content = await response.read()
                                logger.info(
                                    f"Content length: {len(content)} bytes")
                                async with aiofiles.open(installer_path, 'wb') as f:
                                    await f.write(content)
                                logger.info(
                                    f"Miniconda installer downloaded successfully to {installer_path}")
                            else:
                                raise Exception(
                                    f"Failed to download Miniconda installer. Status code: {response.status}")
                else:
                    logger.info(
                        f"Miniconda installer already exists at {installer_path}")

                # Check file size after download
                file_size = installer_path.stat().st_size
                logger.info(f"Downloaded file size: {file_size} bytes")
                if file_size == 0:
                    raise Exception("Downloaded file is empty")

                return installer_path

        except IOError:
            logger.warning(
                "Failed to acquire lock. Another process may be downloading.")
            if attempt < max_retries - 1:
                wait_time = random.uniform(1, 5)
                logger.info(f"Retrying in {wait_time:.2f} seconds...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(
                    f"Failed to acquire lock after {max_retries} attempts.")
                raise
        except Exception as e:
            logger.error(f"Error during download: {str(e)}")
            if attempt < max_retries - 1:
                wait_time = random.uniform(1, 5)
                logger.info(f"Retrying in {wait_time:.2f} seconds...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(
                    f"Failed to download Miniconda after {max_retries} attempts.")
                raise

    raise Exception(
        f"Failed to download Miniconda after {max_retries} attempts")


def release_lock(lock_file, lockf):
    fcntl.flock(lockf, fcntl.LOCK_UN)
    lockf.close()
    try:
        os.remove(lock_file)
    except OSError:
        pass


async def create_remote_setup_script(config, graid_path_remote, user):
    """
    Creates a remote setup script on the target host.

    The script will:
    1. Create a directory for the Graid environment
    2. Install Miniconda if not already present
    3. Create a conda environment for Graid if not already present
    4. Create a script to run commands in the Graid environment

    Args:
        config (dict): The configuration dictionary.
        graid_path_remote (Path): The path to the Graid environment on the remote host.
        user (str): The username for the current host.

    Returns:
        Path: The path to the remote setup script.
    """
    
    logger.info(f"Creating remote setup script for user {user} at {graid_path_remote}")
    
    remote_setup_script = textwrap.dedent(f"""\
    #!/usr/bin/env bash
    set -e
    set -x
    trap 'echo "Error occurred at line $LINENO"; exit 1' ERR
    
    # Create the Graid directory if it doesn't exist
    echo "Starting remote setup script for user {user}"
    echo "Current directory: $(pwd)"
    echo "Graid path: {graid_path_remote}"
    mkdir -p {graid_path_remote}
    echo "Graid directory created/verified"

    # Install Miniconda if it's not already installed
    if ! command -v conda &> /dev/null; then
        echo "Conda not found, installing Miniconda"
        bash {graid_path_remote}/Miniconda3-{config['miniconda_version']}-Linux-x86_64.sh -b -u -p {graid_path_remote}/miniconda
        # Initialize conda for bash and zsh
        {graid_path_remote}/miniconda/bin/conda init bash
        {graid_path_remote}/miniconda/bin/conda init zsh
        # Add the conda bin directory to the PATH
        export PATH="{graid_path_remote}/miniconda/bin:$PATH"
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
    cat << EOT > {graid_path_remote}/run_in_graid_env.sh
    #!/bin/bash
    # Activate the conda environment
    source {graid_path_remote}/miniconda/bin/activate {config['conda_env_name']}
    # Verify that the environment is activated
    python --version
    EOT
    chmod +x {graid_path_remote}/run_in_graid_env.sh

    echo "Remote setup completed successfully for user {user}"
    echo "{graid_path_remote}/run_in_graid_env.sh"
    exit 0
    echo "Final exit status: $?"
    """)
    local_graid_path = Path(SCRIPT_DIR) / 'temp_scripts'
    local_graid_path.mkdir(parents=True, exist_ok=True)

    remote_setup_path = local_graid_path / f'remote_setup_{user}.sh'
    async with aiofiles.open(remote_setup_path, 'w') as f:
        await f.write(remote_setup_script)
    return remote_setup_path

async def setup_remote_host(host, host_vars, config, installer_path, remote_setup_path, password=None, use_keyless=False, sshpass_available=False):
    """
    Sets up a remote host with the required environment.

    Args:
        host (str): The hostname of the host to set up.
        host_vars (dict): The host variables for the host.
        config (dict): The configuration dictionary.
        installer_path (Path): The path to the Miniconda installer.
        remote_setup_path (Path): The path to the remote setup script.
        password (str, optional): The password for the host. Defaults to None.
        use_keyless (bool, optional): Whether to use keyless SSH. Defaults to False.
        sshpass_available (bool, optional): Whether sshpass is available. Defaults to False.

    Returns:
        bool: True if the setup was successful, False otherwise.
    """
    user = host_vars.get('_user', host_vars.get('ansible_user', 'root'))
    port = host_vars.get('ansible_port', '22')
    if user == 'root':
        graid_path = Path(config['graid_path'].replace(
            "{{ ansible_env.HOME }}", "/root"))
    else:
        graid_path = Path(config['graid_path'].replace(
            "{{ ansible_env.HOME }}", f"/home/{user}"))
    logger.info(f"Setting up {user}@{host}:{port}...")
    logger.info(f"Using graid_path: {graid_path}")
    max_retries = 3
    retry_delay = 10

    # Check if the host is reachable
    if not await check_network(host, host_vars, config):
        logger.warning(f"Skipping setup for {host} due to network issues")
        return False

    try:
        if not await ensure_remote_directory(host, host_vars, graid_path, password, sshpass_available):
            raise Exception(
                f"Failed to create remote directory {graid_path} on {user}@{host}:{port}")

        if host in ('localhost', '127.0.0.1'):
            remote_setup_script = graid_path / f'remote_setup_{user}.sh'
            if not remote_setup_script.exists():
                logger.error(
                    f"Remote setup script not found at {remote_setup_script}")
                return False

            os.chmod(remote_setup_script, 0o755)
            stdout, stderr, returncode = await run_command(['bash', str(remote_setup_script)], shell=False)
            if returncode != 0:
                logger.error(f"Local setup script execution failed: {stdout}")
                logger.error(f"Exit code: {returncode}")
                logger.error(f"STDOUT: {stdout}")
                logger.error(f"STDERR: {stderr}")
                return False

            remote_python_path = graid_path / 'miniconda' / \
                "envs" / config['conda_env_name'] / 'bin' / 'python'
            logger.info(f"Remote Python path on {host}: {remote_python_path}")
            inventory_path = Path(SCRIPT_DIR).parent / config['inventory_file']
            await update_ansible_inventory(host, host_vars, remote_python_path, inventory_path)

            logger.info(f"Setup completed for {user}@{host}")
            return True
        else:
            await retry_with_scp(
                transfer_files,
                [str(installer_path), str(remote_setup_path)],
                f"{host}:{graid_path}/",
                host_vars,
                use_scp=True,
                password=password,
                scp_options=SSH_OPTIONS,
                sshpass_available=sshpass_available
            )

            try:
                await transfer_files([str(installer_path), str(remote_setup_path)], f"{host}:{graid_path}/", host_vars, use_scp=True, password=password, sshpass_available=sshpass_available)
            except Exception as e:
                logger.error(
                    f"File transfer verification failed for {user}@{host}:{port}: {str(e)}")
                return False
            
            for attempt in range(max_retries):
                try:
                    
                    ssh_command = f"bash -x {graid_path}/remote_setup_{user}.sh"
                    if sshpass_available and password and not use_keyless:
                        ssh_command = ['sshpass', '-p', password, 'ssh', '-p', port] + \
                            SSH_OPTIONS + [f"{user}@{host}", ssh_command]
                    else:
                        ssh_command = ['ssh', '-p', port] + SSH_OPTIONS + \
                            [f"{user}@{host}", ssh_command]
                    if isinstance(ssh_command, list):
                        ssh_command = ' '.join(ssh_command)

                    if sshpass_available or use_keyless:
                        stdout, stderr, returncode = await run_command(ssh_command, shell=True)
                    else:
                        stdout, stderr, returncode = await ssh_connect(host, user, password, ssh_command)

                    if returncode != 0:
                        logger.error(
                            f"Remote setup script execution failed on {user}@{host}:{port}")
                        logger.error(f"Exit code: {returncode}")
                        logger.error(f"STDOUT: {stdout}")
                        logger.error(f"STDERR: {stderr}")
                        return False
                    else:
                        logger.info(
                            f"Remote setup script execution successful on {user}@{host}:{port}")
                except Exception as e:
                    logger.warning(
                        f" Attempt {attempt + 1}/{max_retries} failed for {user}@{host}:{port}: {str(e)}"
                    )
                    if attempt < max_retries - 1:
                        logger.info(
                            f"{retry_delay} seconds before next attempt for {user}@{host}:{port}")
                        await asyncio.sleep(retry_delay)

                    else:
                        logger.error(
                            f"Failed to connect to {user}@{host}:{port} after {max_retries} attempts")
                        raise

            remote_python_path = graid_path / "miniconda" / \
                "envs" / config['conda_env_name'] / "bin" / "python"

            logger.info(
                f"Python interpreter path on {host}: {remote_python_path}")

            inventory_path = Path(SCRIPT_DIR).parent / config['inventory_file']
            await update_ansible_inventory(host, host_vars, remote_python_path, inventory_path)

            logger.info(f"Setup completed for {user}@{host}")
            return True

    except FileNotFoundError as e:
        logger.error(
            f"File not found error during setup for {user}@{host}:{port}: {str(e)}")
        return False
    except Exception as e:
        logger.error(
            f"Unexpected error during setup for {user}@{host}:{port}: {str(e)}")
        return False
    # try:

    #     # Create the remote setup script
    #     remote_setup_path = await create_remote_setup_script(config, graid_path)
    #     if host not in ('localhost', '127.0.0.1'):
    #         # Transfer the Miniconda installer and the remote setup script to the host
    #         await retry_with_scp(transfer_files,
    #                              [str(installer_path),
    #                               str(remote_setup_path)],
    #                              f"{host}:{graid_path}/",
    #                              host_vars,
    #                              use_scp=True,
    #                              password=password,
    #                              scp_options=SSH_OPTIONS,
    #                              sshpass_available=sshpass_available)

    #     else:
    #         logger.info(f"Skipping transfer file to localhost")

    #     if host in ('localhost', '127.0.0.1'):
    #         logger.info(f"Setup the environment on localhost")
    #         remote_setup_path = graid_path / 'remote_setup.sh'
    #         if not remote_setup_path.exists():
    #             logger.error(
    #                 f"Remote setup script not found on localhost: {remote_setup_path}")
    #             return False

    #         os.chmod(remote_setup_path, 0o755)
    #         stdout, stderr, returncode = await run_command(
    #             [str(remote_setup_path)], shell=False)
    #         if returncode != 0:
    #             logger.error(f"Local setup script execution failed")
    #             logger.error(f"Exit code: {returncode}")
    #             logger.error(f"STDOUT: {stdout}")
    #             logger.error(f"STDERR: {stderr}")
    #             return False
    #         logger.info(f"Update Ansible inventory on localhost")
    #         remote_python_path = graid_path / 'miniconda' / 'bin' / 'python'
    #         logger.info(f"Python interpreter path: {remote_python_path}")
    #         inventory_path = Path(SCRIPT_DIR).parent / config['inventory_file']
    #         await update_ansible_inventory(
    #             host, host_vars, remote_python_path, inventory_path)
    #         logger.info(
    #             f"Local setup completed successfully for {user}@{host}")
    #         return True
    #     else:
    #         # Run the remote setup script
    #         ssh_command = f"bash -x {graid_path}/remote_setup.sh"
    #         if sshpass_available and password and not use_keyless:
    #             ssh_command = ['sshpass', '-p', password, 'ssh'] + \
    #                 SSH_OPTIONS + [f"{user}@{host}", ssh_command]
    #         else:
    #             ssh_command = ['ssh'] + SSH_OPTIONS + \
    #                 [f"{user}@{host}", ssh_command]
    #         if isinstance(ssh_command, list):
    #             ssh_command = ' '.join(ssh_command)

    #         if sshpass_available or use_keyless:
    #             stdout, stderr, returncode = await run_command(ssh_command, shell=True)
    #         else:
    #             stdout, stderr, returncode = await ssh_connect(host, user, password, ssh_command)

    #         if returncode != 0:
    #             logger.error(
    #                 f"Remote setup script execution failed on {user}@{host}")
    #             logger.error(f"Exit code: {returncode}")
    #             logger.error(f"STDOUT: {stdout}")
    #             logger.error(f"STDERR: {stderr}")
    #             return False

    #         # Update the Ansible inventory file with the new remote Python interpreter path
    #         remote_python_path = graid_path / "miniconda" / \
    #             "envs" / config['conda_env_name'] / "bin" / "python"
    #         logger.info(
    #             f"Python interpreter path on {host}: {remote_python_path}")

    #         inventory_path = Path(SCRIPT_DIR).parent / config['inventory_file']
    #         await update_ansible_inventory(host, host_vars, remote_python_path, inventory_path)

    #         logger.info(f"Setup completed for {user}@{host}")
    #         return True
    # except FileNotFoundError as e:
    #     logger.error(
    #         f"File not found error during setup for {user}@{host}: {str(e)}")
    #     return False
    # except Exception as e:
    #     logger.error(
    #         f"Unexpected error during setup for {user}@{host}: {str(e)}")
    #     return False


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
    # logger.info("Checking and installing sshpass...")

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


async def ssh_connect(host, username, password, command, port=22):
    """
    Connects to a host using SSH and executes a command.

    Args:
        host (str): The hostname or IP address of the host to connect to.
        username (str): The username to use for the SSH connection.
        password (str): The password to use for the SSH connection.
        command (str): The command to execute on the remote host.
        port (int, optional): The SSH port to use. Defaults to 22.

    Returns:
        tuple: A tuple containing the stdout, stderr, and exit status of the command.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        # Connect to the host using SSH
        client.connect(host, port=port, username=username, password=password)
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
    pub_key_path = key_path + '.pub'
    if not os.path.exists(key_path) or not os.path.exists(pub_key_path):
        # If the key doesn't exist, generate a new one
        logger.info("SSH key not found. Generating a new one...")
        await run_command(['ssh-keygen', '-t', 'rsa', '-N', '', '-f', key_path])

        for _ in range(30):  # Wait up to 30 seconds for the key to be generated
            if os.path.exists(key_path) and os.path.exists(pub_key_path):
                if os.path.getsize(key_path) > 0 and os.path.getsize(pub_key_path) > 0:
                    logger.info("SSH key generated successfully.")
                    return
            time.sleep(1)

        raise Exception("Failed to generate SSH key or key file is empty.")

        # logger.info("SSH key generated successfully.")
    else:
        # If the key exists, just log a message
        logger.info("Existing SSH key found.")

def cleanup_directories(base_dir, dir_names):
    """
    cleanup_directories

    Args:
        base_dir (Path): base_dir。
        dir_names (list[str]): target directory。

    Returns:
        dict: result dictionary
    """
    results = {}
    base_dir = Path(base_dir)
    for dir_name in dir_names:
        directory = base_dir / dir_name
        if not directory.exists():
            logging.info(f"Directory {directory} does not exist, skipping.")
            results[dir_name] = None  # use None to indicate that the directory does not exist
            continue
        
        if not directory.is_dir():
            logging.error(f"{directory} is not a directory, skipping.")
            results[dir_name] = False
            continue

        try:
            file_count = 0
            for item in directory.iterdir():
                if item.is_file():
                    item.unlink()
                    file_count += 1
                elif item.is_dir():
                    for sub_item in item.rglob("*"):
                        if sub_item.is_file():
                            sub_item.unlink()
                            file_count += 1
                    item.rmdir()  # delete the empty directory

            logging.info(f"Successfully removed {file_count} files/subdirectories in {directory}")
            results[dir_name] = True
        except Exception as e:
            logging.error(f"Error occurred while cleaning {directory}: {e}")
            results[dir_name] = False

    return results


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

    graid_path = Path(config['graid_path'].replace(
        "{{ ansible_env.HOME }}", os.environ.get('HOME', '')))

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
            return await setup_remote_host(
                host,
                host_vars,
                config,
                installer_path,
                remote_setup_scripts[host],
                password=password,
                use_keyless=setup_keyless,
                sshpass_available=sshpass_available
            )

    # Download Miniconda installer once
    installer_path = await download_miniconda(config, graid_path)

    # Create remote_setup.sh and get the path
    remote_setup_scripts = {}
    for host, host_vars in hosts:
        user = host_vars.get('_user', host_vars.get('ansible_user', 'root'))
        
        if user == 'root':
            graid_path = Path(config['graid_path'].replace(
                "{{ ansible_env.HOME }}", "/root"))
        else:
            graid_path = Path(config['graid_path'].replace(
                "{{ ansible_env.HOME }}", f"/home/{user}"))
        remote_setup_scripts[host] = await create_remote_setup_script(config, graid_path, user)

    logger.info(f"Starting setup for {len(hosts)} hosts...")
    tasks = [setup_host_with_semaphore(host, host_vars)
             for host, host_vars in hosts]
    results = await asyncio.gather(*tasks)

    success_count = sum(results)
    logger.info(
        f"Setup completed. Successful: {success_count}, Failed: {len(hosts) - success_count}")

    graid_path = Path(config['graid_path'].replace(
        "{{ ansible_env.HOME }}", os.environ.get('HOME', '')))

    directories_to_clean = ['temp_scripts', 'graid_packages']
    cleanup_results = cleanup_directories(SCRIPT_DIR, directories_to_clean)
    for dir_name, result in cleanup_results.items():
        if result is None:
            logger.warning(f"{dir_name} does not exist")
        elif result:
            logger.info(f"{dir_name} was cleaned successfully")
        else:
            logger(f"Failed to clean {dir_name}")

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
