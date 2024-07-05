# SupremeRAID Ansible Playbook

This Ansible playbook automates the installation and configuration of SupremeRAID on supported systems. It handles system preparation, software installation, and RAID configuration.

**Note: Windows functionality and offline setup are not yet complete and are planned for future versions.**

## Prerequisites

- Ansible 2.9 or higher
- Python 3.8 or later on both the control node and target systems
- Target systems running supported operating systems (RedHat or Debian)
- SSH access to target systems
- Necessary permissions to install software and configure RAID
- Internet connectivity on target systems (offline setup is not yet supported)

### Checking Python Version

To check your Python version, run:

```
python3 --version
```

If your Python version is older than 3.8, you may need to update it. The process for updating Python depends on your operating system:

- For Ubuntu/Debian:
  ```
  sudo apt update
  sudo apt install python3.8
  ```

- For RedHat/CentOS:
  ```
  sudo yum install python38
  ```

Note: You may need to use a third-party repository like EPEL for RedHat/CentOS if Python 3.8 is not available in the default repositories.

Ensure that the correct Python version is used by Ansible. You can specify this in your Ansible configuration file or by setting the `ansible_python_interpreter` variable in your inventory:

```
[supremraid_servers]
server1 ansible_host=192.168.1.101 ansible_python_interpreter=/usr/bin/python3.8
```

## Setup

1. Clone this repository:
   ```
   git clone https://github.com/Liam9652/ansible_supremeraid
   cd supremeraid-ansible
   ```

2. Configure SSH access to your target systems. You have two options for SSH access: keyless (recommended) or password-based authentication.

#### Option 1: Keyless SSH (Recommended)

1. Generate an SSH key pair on your Ansible control node (if you haven't already):
   ```
   ssh-keygen -t rsa -b 4096
   ```

2. Copy your public key to each target system:
   ```
   ssh-copy-id user@target_host
   ```

3. In your Ansible inventory file, specify the SSH user:
   ```
   [supremraid_servers]
   server1 ansible_host=192.168.1.101 ansible_user=your_ssh_user
   server2 ansible_host=192.168.1.102 ansible_user=your_ssh_user
   ```

#### Option 2: Password-based SSH (Root Login)

To use password-based authentication with the root account:

1. Ensure that root login via SSH is enabled on your target systems (Note: this is generally not recommended for security reasons).

2. In your Ansible inventory file, specify the SSH user as root and indicate that you'll be using a password:
   ```
   [supremraid_servers]
   server1 ansible_host=192.168.1.101 ansible_user=root ansible_ssh_pass=YOUR_ROOT_PASSWORD
   server2 ansible_host=192.168.1.102 ansible_user=root ansible_ssh_pass=YOUR_ROOT_PASSWORD
   ```

3. To avoid storing passwords in plain text, use Ansible Vault to encrypt your inventory file:
   ```
   ansible-vault encrypt /path/to/your/inventory
   ```

4. When running your playbook, use the `--ask-vault-pass` option:
   ```
   ansible-playbook -i /path/to/your/inventory playbook.yaml --ask-vault-pass
   ```

Note: If you're not using the root account but need to run commands with sudo, add `ansible_become=yes` and `ansible_become_method=sudo` to your inventory entries, and use `ansible_become_pass` instead of `ansible_ssh_pass` for the sudo password.

For enhanced security, we strongly recommend using keyless SSH authentication whenever possible.
3. Update the inventory file (`/etc/ansible/hosts` or create a custom one) with your target systems:

   For keyless SSH:
   ```
   [supremraid_servers]
   server1 ansible_host=192.168.1.101 ansible_user=your_ssh_user
   server2 ansible_host=192.168.1.102 ansible_user=your_ssh_user
   ```

   For password-based SSH (root login):
   ```
   [supremraid_servers]
   server1 ansible_host=192.168.1.101 ansible_user=root ansible_ssh_pass=YOUR_ROOT_PASSWORD
   server2 ansible_host=192.168.1.102 ansible_user=root ansible_ssh_pass=YOUR_ROOT_PASSWORD
   ```

   Note: To avoid storing passwords in plain text, use Ansible Vault to encrypt your inventory file:
   ```
   ansible-vault encrypt /path/to/your/inventory
   ```

4. Configure the required files:

   a. Edit `vars/download_urls.yaml`:
      - Add the necessary download links for SupremeRAID packages.

   b. Modify RAID configuration in `/roles/configure/vars/main.yml`:
      - Adjust RAID parameters according to your requirements.

   c. Prepare the license mapping file:
      - Place your `license_mapping.csv` file in `/roles/configure/files/`.
      - Ensure the file contains the correct mapping between serial numbers and license keys.

5. Review and modify other variables in `vars/supremeraid_vars.yaml` as needed.

Note: For enhanced security, we strongly recommend using keyless SSH authentication whenever possible. If you're not using the root account but need to run commands with sudo, add `ansible_become=yes` and `ansible_become_method=sudo` to your inventory entries, and use `ansible_become_pass` instead of `ansible_ssh_pass` for the sudo password.

## Usage

To run the entire playbook:

```
ansible-playbook -i /etc/ansible/hosts playbook.yaml
```

### Tags

Use tags to run specific parts of the playbook:

- `prepare`: Run preparation tasks
  - `identify`: Identify system
  - `query`: Query serial number
- `install`: Run installation tasks
  - `download`: Download packages
  - `copy`: Copy files (offline mode)
  - `setup`: Set up environment
- `configure`: Run configuration tasks
  - `activate`: Activate service
  - `raid`: Configure RAID

Example: To only configure RAID:

```
ansible-playbook -i /etc/ansible/hosts playbook.yaml --tags raid
```

## Roles

1. `prepare`: Prepares the system for installation
2. `install`: Handles package download and installation
3. `configure`: Activates the service and configures RAID

## Common Use Cases

1. Full installation and configuration:
   ```
   ansible-playbook -i /etc/ansible/hosts playbook.yaml
   ```

2. Update RAID configuration on existing installations:
   ```
   ansible-playbook -i /etc/ansible/hosts playbook.yaml --tags raid
   ```

3. Activate service on systems with SupremeRAID already installed:
   ```
   ansible-playbook -i /etc/ansible/hosts playbook.yaml --tags activate
   ```


**Important:** The current version only supports online installation. Offline setup functionality is planned for a future release.

...

## Troubleshooting

- If you encounter variable undefined errors, ensure that all required variables are set in the appropriate vars files or that the tasks setting these variables are not skipped due to tag usage.
- For issues with specific tasks, use the `--start-at-task` option to begin playbook execution at a specific task.
- Use `-vvv` for verbose output to help diagnose issues:
  ```
  ansible-playbook -i /etc/ansible/hosts playbook.yaml -vvv
  ```

## Future Work

We are continuously working to improve this Ansible playbook. Here are some features and enhancements planned for future releases:

1. Windows Support: Full functionality for Windows systems.

2. Offline Setup: Implement complete offline installation and setup capabilities.

3. Enhanced RAID Configuration: More flexible and advanced RAID configuration options.

4. Automated Updates: Implement a mechanism for easy updates of SupremeRAID software.

5. Performance Optimization: Fine-tune the playbook for faster execution and reduced downtime during installation.

6. Extended Hardware Support: Expand compatibility with a wider range of hardware configurations.

7. Improved Error Handling: Enhance error messages and recovery procedures for a smoother experience.

8. Backup and Restore: Implement automated backup and restore functionality for SupremeRAID configurations.

9. Monitoring Integration: Add options to integrate with popular monitoring solutions.

We welcome feedback and suggestions for additional features or improvements.

## License

This project is licensed under the [Your License] - see the LICENSE.md file for details.
