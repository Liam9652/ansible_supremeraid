import json
import argparse
import logging
import yaml
from pathlib import Path


logging.basicConfig(level=logging.INFO,
                    format='[%(asctime)s] [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def generate_urls_from_pattern(url_pattern, variants):
    """Generate full URLs from a pattern and a list of variants."""
    return [url_pattern.replace('*', variant) for variant in variants]


def update_yaml(metadata_path, yaml_path):
    try:
        # Read metadata file
        with open(metadata_path, 'r') as f:
            metadata = json.load(f)

        # Read YAML file
        with open(yaml_path, 'r') as f:
            yaml_data = yaml.safe_load(f)

        # Update Linux URLs
        if 'linux' in metadata and 'linux' in yaml_data:
            if 'nvidia_driver' in metadata['linux']:
                yaml_data['linux']['nv_url'] = metadata['linux']['nvidia_driver'].get(
                    'url', '')
            if 'pre_installer' in metadata['linux']:
                yaml_data['linux']['pre_installer_url'] = metadata['linux']['pre_installer'].get(
                    'url', '')
            if 'installer' in metadata['linux']:
                yaml_data['linux']['installer_urls'] = generate_urls_from_pattern(
                    metadata['linux']['installer'].get('url_pattern', ''),
                    metadata['linux']['installer'].get('variants', [])
                )

        # Update Windows URLs
        if 'windows' in metadata and 'windows' in yaml_data:
            if 'nvidia_driver' in metadata['windows']:
                yaml_data['windows']['nv_url'] = metadata['windows']['nvidia_driver'].get(
                    'url', '')
            if 'installer' in metadata['windows']:
                yaml_data['windows']['installer_win_urls'] = generate_urls_from_pattern(
                    metadata['windows']['installer'].get('url_pattern', ''),
                    metadata['windows']['installer'].get('variants', [])
                )

        # Write back to YAML file
        with open(yaml_path, 'w') as f:
            yaml.dump(yaml_data, f, default_flow_style=False)

        logger.info(f"Successfully updated all URLs in {yaml_path}")

    except Exception as e:
        logger.error(f"Error updating links: {str(e)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Update driver download links from metadata file")
    parser.add_argument("--metadata", required=True,
                        help="Path to metadata JSON file")
    parser.add_argument("--yaml", required=True,
                        help="Path to download_urls.yml file")
    args = parser.parse_args()

    # Update YAML file
    update_yaml(args.metadata, args.yaml)
