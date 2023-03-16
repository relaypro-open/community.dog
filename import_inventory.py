#!/bin/env python

import json
import os
import sys
import argparse

def main(argv, stdout, environ):
    parser = argparse.ArgumentParser(
        description='Transform an ansible inventory export into a dog_inventory')
    parser.add_argument('-n', '--name', type=str, required=True,
                        help='user name')
    args = parser.parse_args()
    inventory_name = args.name

    # ansible-inventory -i hosts --list --export > inventory.export.json
    # edit head of inventory.export.json to remove non-json text
    with open('inventory.export.json') as file:
        file_string = file.read()
        groups = json.loads(file_string)
        meta = groups.get("_meta")
        hostvars = meta['hostvars']
        del groups['_meta']

        for group_name, group in groups.items():
            hosts_dict = {}
            if group.get('hosts'):
                hosts_list = group.get('hosts')
                for host in hosts_list:
                    host_dict = hostvars.get(host)
                    hosts_dict[host] = host_dict
                groups[group_name]["hosts"] = hosts_dict
            else:
                groups[group_name]["hosts"] = {}

        dog_inventory = {"name": inventory_name,
                         "groups": groups}
        with open('inventory.import.json', 'w') as output_file:
            output_file.write(json.dumps(dog_inventory))
    # http POST https://$DOG:8443/api/V2/inventory @inventory.import.json -A bearer -a $TOKEN


if __name__ == "__main__":
    main(sys.argv, sys.stdout, os.environ)
