# SupremeRAID Ansible Playbook

This Ansible playbook automates the installation and configuration of SupremeRAID on supported systems. It handles system preparation, software installation, and RAID configuration.

**Note: Windows functionality and offline setup are not yet complete and are planned for future versions.**

## Prerequisites

- Ansible 2.9 or higher
- Target systems running supported operating systems (Red Hat or Debian)
- SSH access to target systems
- Necessary permissions to install software and configure RAID
- Internet connectivity on target systems (offline setup is not yet supported)
- Python 3.8 or higher

If your system does have an older version of python you can use the python virtual env.

```
python3 -m venv graid_venv
source graid_venv/bin/activate
pip install virtualenv
virtualenv demo22 --python=python3.11
deactivate
source demo22/bin/activate
python --version
   Python 3.11.9
...

At this point your system has a virtual environment with a different version of python. Otherwise you can use your OS base python if it's already running version higher than 3.8. If you want to install a higher version of ansible you can run pip install ansible. You may have to de-active and reactivate your virtual environment to use the new version.

## Usage

To run the entire playbook:

```

ansible-playbook playbook.yaml

```

**Important:** The current version only supports online installation.

...

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



```

## Setup

1. Clone this repository:

   ```
   git clone https://github.com/Liam9652/ansible_supremeraid
   cd supremeraid-ansible
   ```

2. Configure the required files:

   a. Edit `vars/download_urls.yaml`:

   - Add the necessary download links for SupremeRAID packages.

   b. Modify RAID configuration in `/roles/configure/vars/main.yml`:

   - Adjust RAID parameters according to your requirements.

   c. Prepare the license mapping file:

   - Place your `license_mapping.csv` file in `/roles/configure/files/`.
   - Ensure the file contains the correct mapping between serial numbers and license keys.

   d. Configure / update the `inventory/hosts` file
   [supremraid_servers]
   server1 ansible_host=192.168.1.101 #if needed set the user ansible_user=root
   server2 ansible_host=192.168.1.102

Note: The `inventory/hosts` file uses localhost in the repo. You can use the ansible_user=root if your local user is not the same and you do not have set the right user permission.

5. Review and modify other variables in `vars/supremeraid_vars.yaml` as needed.

## Usage

To run the entire playbook:

```
ansible-playbook playbook.yaml
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
ansible-playbook playbook.yaml --tags raid
```

## Roles

1. `prepare`: Prepares the system for installation
2. `install`: Handles package download and installation
3. `configure`: Activates the service and configures RAID

## Common Use Cases

1. Full installation and configuration:

   ```
   ansible-playbook playbook.yaml
   ```

2. Update RAID configuration on existing installations:

   ```
   ansible-playbook  playbook.yaml --tags raid
   ```

3. Activate service on systems with SupremeRAID already installed:
   ```
   ansible-playbook playbook.yaml --tags activate
   ```

**Important:** The current version only supports online installation. Offline setup functionality is planned for a future release.

...

## Troubleshooting

- If you encounter variable undefined errors, ensure that all required variables are set in the appropriate vars files or that the tasks setting these variables are not skipped due to tag usage.
- For issues with specific tasks, use the `--start-at-task` option to begin playbook execution at a specific task.
- Use `-vvv` for verbose output to help diagnose issues:
  ```
  ansible-playbook playbook.yaml -vvv
  ```
