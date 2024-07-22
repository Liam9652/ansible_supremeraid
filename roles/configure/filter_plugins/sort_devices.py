#!/usr/bin/env python3

from ansible.errors import AnsibleFilterError
from ansible.module_utils.common.text.converters import to_native
import json

class FilterModule(object):
    def filters(self):
        return {
            'sort_devices': self.sort_devices
        }

    def sort_devices(self, devices, sorting_method, create_by_numa=False, led_config=None, physical_slots=None):
        if not devices:
            return []

        try:
            led_config = json.loads(led_config) if isinstance(led_config, str) else (led_config or {})
            physical_slots = json.loads(physical_slots) if isinstance(physical_slots, str) else (physical_slots or {})

            led_bdf_list = led_config.get('Result', {}).get('LedBdf', [])

            def sort_key(device):
                dev_path = device.get('DevPath', '')
                address = device.get('Address', '')
                numa = device.get('Numa', '')
                nqn = device.get('NQN', '')
                wwid = device.get('WWID', '')

                if sorting_method == 'Address':
                    if led_bdf_list:
                        led_order = address.replace(':', '').replace('.', '0')
                        try:
                            return led_bdf_list.index(f"0x{led_order}")
                        except ValueError:
                            pass
                    return physical_slots.get(address, f"fallback_{address}")
                elif sorting_method == 'DevPath':
                    return (not dev_path.startswith('/dev/nvme'), dev_path)
                elif sorting_method == 'NQN':
                    return wwid if nqn else wwid  # Use WWID as fallback for SCSI devices
                elif sorting_method == 'Numa' or create_by_numa:
                    return (int(numa) if numa.isdigit() else float('inf'), dev_path)
                else:
                    return device.get(sorting_method, '')

            return sorted(devices, key=sort_key)

        except Exception as e:
            raise AnsibleFilterError(f"Error in sort_devices filter: {to_native(e)}")

if __name__ == "__main__":
    # This block is for testing purposes and won't be executed when used as an Ansible filter
    import sys
    
    # Mock data for testing
    test_devices = [
        {'DevPath': '/dev/nvme0n1', 'Address': '0000:03:00.0', 'Numa': '0', 'NQN': 'nqn.2019-08.org.qemu:nvme1'},
        {'DevPath': '/dev/nvme1n1', 'Address': '0000:04:00.0', 'Numa': '1', 'NQN': 'nqn.2019-08.org.qemu:nvme2'},
        {'DevPath': '/dev/sda', 'Address': '0000:05:00.0', 'WWID': 't10.ATA_QEMU_HARDDISK_QM00001'},
    ]
    
    filter_module = FilterModule()
    try:
        for method in ['Address', 'DevPath', 'NQN', 'Numa']:
            print(f"\nSorting by {method}:")
            sorted_devices = filter_module.sort_devices(test_devices, method)
            print(json.dumps(sorted_devices, indent=2))
        
        print("\nSorting with create_by_numa=True:")
        sorted_devices = filter_module.sort_devices(test_devices, 'Numa', create_by_numa=True)
        print(json.dumps(sorted_devices, indent=2))
    except AnsibleFilterError as e:
        print(f"Error: {str(e)}", file=sys.stderr)
        sys.exit(1)
    pass