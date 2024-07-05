#!/usr/bin/python

import csv
import os

class FilterModule(object):
    def filters(self):
        return {'get_license_key': self.get_license_key}

    def get_license_key(self, serial_number, csv_path=None):
        if csv_path is None:
            csv_path = os.path.join(os.path.dirname(__file__), '..', 'files', 'license_mapping.csv')
        
        with open(csv_path, mode='r') as file:
            reader = csv.DictReader(file)
            for row in reader:
                if row['serial'] == serial_number:
                    return row['license_key']
        
        return f"No license key found for serial number: {serial_number}"