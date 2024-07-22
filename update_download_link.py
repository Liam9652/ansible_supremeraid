import requests
from bs4 import BeautifulSoup
from ruamel.yaml import YAML
import os

def get_latest_driver_links():
    url = "https://docs.graidtech.com/#supremeraidtm-driver-release-notes"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
    }
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")

    def find_latest_driver_link(section_id, latest_stable_text="Latest Stable"):
        section = soup.find("h3", {"id": section_id})
        if section:
            for ul in section.find_all_next("ul"):
                for li in ul.find_all("li", recursive=True):
                    if latest_stable_text in li.text:
                        link = li.find("a")
                        if link:
                            return "https://docs.graidtech.com" + link["href"]
        return None

    latest_linux_driver = find_latest_driver_link("linux-driver")
    latest_windows_driver = find_latest_driver_link("windows-driver")
    return latest_linux_driver, latest_windows_driver

def extract_detailed_info(url, is_windows=False):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
    }
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")

    def extract_links_from_table(table):
        links = {}
        for row in table.find_all('tr'):
            cols = row.find_all('td')
            if len(cols) == 2:
                name = cols[0].text.strip()
                link = cols[1].find('a')
                if link:
                    links[name] = link['href']
        return links

    if is_windows:
        dependencies_section = soup.find('h3', {'id': 'dependencies-and-utilities'})
    else:
        dependencies_section = soup.find('h2', {'id': 'dependencies-and-utilities'})
    
    if dependencies_section:
        dependencies_table = dependencies_section.find_next('table')
        dependencies_links = extract_links_from_table(dependencies_table)
    else:
        dependencies_links = {}

    if is_windows:
        driver_package_section = soup.find('h3', {'id': 'driver-packages'})
    else:
        driver_package_section = soup.find('h2', {'id': 'driver-package'})
    
    if driver_package_section:
        driver_package_table = driver_package_section.find_next('table')
        driver_package_links = {}
        for row in driver_package_table.find_all('tr')[1:]:  # Skip header row
            cols = row.find_all('td')
            if len(cols) >= 3:
                product_model = cols[0].text.strip()
                gpu = cols[1].text.strip()
                link = cols[-1].find('a')
                if link:
                    driver_package_links[f"{product_model} ({gpu})"] = link['href']
    else:
        driver_package_links = {}

    return {
        'dependencies': dependencies_links,
        'driver_package': driver_package_links
    }

def update_yaml_file(linux_info, windows_info):
    yaml_file_path = 'group_vars/all/download_urls.yml'
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096  # Avoid automatic line breaks
    
    # Check if the file exists
    if not os.path.exists(yaml_file_path):
        print(f"Warning: YAML file '{yaml_file_path}' does not exist. Creating a new file.")
        data = {'linux': {}, 'windows': {}}
    else:
        try:
            with open(yaml_file_path, 'r') as file:
                data = yaml.load(file)
        except Exception as e:
            print(f"Error: Failed to read the file: {e}")
            return

    # Ensure data is a dictionary and contains 'linux' and 'windows' keys
    if not isinstance(data, dict):
        data = {}
    if 'linux' not in data:
        data['linux'] = {}
    if 'windows' not in data:
        data['windows'] = {}

    # Update Linux section
    if linux_info:
        # Correctly update pre_installer_url
        for name, link in linux_info['dependencies'].items():
            if "Pre-installer" in name:
                data['linux']['pre_installer_url'] = link
                break
        data['linux']['installer_urls'] = list(linux_info['driver_package'].values())

    # Update Windows section
    if windows_info:
        data['windows']['nv_url'] = windows_info['dependencies'].get('NVIDIA Driver', '')
        data['windows']['installer_win_urls'] = list(windows_info['driver_package'].values())

    # Write back to YAML file
    try:
        with open(yaml_file_path, 'w') as file:
            yaml.dump(data, file)
        print(f"YAML file successfully updated: {yaml_file_path}")
    except Exception as e:
        print(f"Error: Unable to write to YAML file: {e}")

if __name__ == "__main__":
    latest_linux_driver_link, latest_windows_driver_link = get_latest_driver_links()
    
    linux_info = None
    windows_info = None

    if latest_linux_driver_link:
        print(f"Latest Linux Driver Link: {latest_linux_driver_link}")
        linux_info = extract_detailed_info(latest_linux_driver_link)
        
        print("\nLinux Dependencies and Utilities:")
        for name, link in linux_info['dependencies'].items():
            print(f"{name}: {link}")
        
        print("\nLinux Driver Package:")
        for name, link in linux_info['driver_package'].items():
            print(f"{name}: {link}")
    else:
        print("Unable to find the latest Linux driver download link.")
    
    if latest_windows_driver_link:
        print(f"\nLatest Windows Driver Link: {latest_windows_driver_link}")
        windows_info = extract_detailed_info(latest_windows_driver_link, is_windows=True)
        
        print("\nWindows Dependencies and Utilities:")
        for name, link in windows_info['dependencies'].items():
            print(f"{name}: {link}")
        
        print("\nWindows Driver Package:")
        for name, link in windows_info['driver_package'].items():
            print(f"{name}: {link}")
    else:
        print("Unable to find the latest Windows driver download link.")

    # Update YAML file
    update_yaml_file(linux_info, windows_info)