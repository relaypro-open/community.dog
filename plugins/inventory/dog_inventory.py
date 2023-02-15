# -*- coding: utf-8 -*-
# Copyright (c) 2023, Drew Gulino <dgulino at relaypro.com>
# Copyright (c) 2020, Felix Fontein <felix@fontein.de>
# For the parts taken from the docker inventory script:
# Copyright (c) 2016, Paul Durivage <paul.durivage@gmail.com>
# Copyright (c) 2016, Chris Houseknecht <house@redhat.com>
# Copyright (c) 2016, James Tanner <jtanner@redhat.com>
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import (absolute_import, division, print_function)

__metaclass__ = type


DOCUMENTATION = '''
name: dog_inventory
short_description: Ansible dynamic inventory plugin for dog agents
version_added: 2.10.8
author:
    - drew gulino <dgulino at relaypro.com>
extends_documentation_fragment:
    - ansible.builtin.constructed
description:
    - Reads inventories from the dog API.
    - Uses a YAML configuration file that ends with C(dog.[yml|yaml]).
options:
    plugin:
        description:
            - The name of this plugin, it should always be set to C(community.dog.dog_inventory)
              for this plugin to recognize it as it's own.
        type: str
        required: true
        choices: [ community.dog.dog_inventory, dog_inventory ]

    add_ec2_groups:
        description:
            - Adds groups based on ec2 instance metadata
        type: bool
        default: false
    
    only_include_active:
        description:
            - Only includes hosts with active=active
        type: bool
        default: true
    
    dog_url:
        description:
            - URL of dog_trainer
        type: str
        default: http://dog:8000/api/V2

    unique_id_key:
        description:
            - The key to be used as the server's name
        type: str
        default: name
        choices: [ name, hostkey ]
    compose:
        description:
            - Create vars from jinja2 expressions.
        required: false
        type: dict
        default: {}
    keyed_groups:
        description:
            - Add hosts to group based on the values of a variable.
        required: false
        type: list
        default: []
    filters:
        description:
            - list of key/values to filter hosts
        required: false
        type: list
        default: []
'''

EXAMPLES = '''
# Minimal example using remote dog_trainer
plugin: community.dog.dog_inventory
dog_host: http://my-dog-host:8000/api/V2

# Example using remote dog_trainer with unverified TLS
plugin: community.dog.dog_inventory
dog_host: https://my-dog-host:8000/api/V2

# Example using constructed features to create groups
plugin: community.dog.dog_inventory
add_ec2_groups: true
only_include_active: true
dog_url: https://my-dog-host:8443/api/V2
unique_id_key: hostkey
compose:
  dog_group_alias: dog_group+"_"+dog_ec2_instance_tags.alias
keyed_groups:
  - prefix: alias
    key: 'dog_group_alias'
filters:
  - key: ec2_instance_tags.environment
    value: qa
  - key: ec2_instance_tags.cluster
    value: beta
'''

import re
import traceback
import sys

from ansible.errors import AnsibleError
from ansible.module_utils.common.text.converters import to_native
from ansible.plugins.inventory import BaseInventoryPlugin, Constructable
from ansible.inventory.group import Group

from apiclient import APIClient, endpoint, retry_request
from apiclient import HeaderAuthentication,JsonResponseHandler,JsonRequestFormatter

import jinja2
import ansible

HAVE_DOG = False
try:
    import dog.api as dc
    HAVE_DOG = True
except ImportError:
    pass

import os
from ansible import errors
from ansible.plugins.connection import ConnectionBase

MIN_DOG_API = 'V2'


class InventoryModule(BaseInventoryPlugin, Constructable):
    ''' Host inventory parser for ansible using dog as source. '''

    NAME = 'dog_inventory'

    def _slugify(self, value):
        return 'dog_%s' % (re.sub(r'[^\w-]', '_', value).lower().lstrip('_'))

    def _populate(self, client):
        strict = self.get_option('strict')

        add_ec2_groups = self.get_option('add_ec2_groups')
        only_include_active = self.get_option('only_include_active')
        self.unique_id_key = self.get_option('unique_id_key')
        self.filters = self.get_option('filters')

        try:
            hosts = client.get_all_hosts()
        except Exception as exc:
            raise AnsibleError("Error listing containers: %s" % to_native(exc))

        try:
            groups_list = client.get_all_groups()
            groups = {item['name']:item for item in groups_list}
        except Exception as exc:
            raise AnsibleError("Error listing groups: %s" % to_native(exc))

        extra_facts = {}

        for host in hosts:
            dog_id = host.get('id')
            dog_name = host.get('name')
            name = host.get(self.unique_id_key)
            hostkey = host.get('hostkey')
            group = host.get('group')
            group = group.replace("-","_")
            active = host.get('active')
            dog_version = host.get('version')
            os_distribution = host.get('os_distribution')
            os_version = host.get('os_version')

            ec2_instance_id = host.get('ec2_instance_id')
            ec2_region = host.get('ec2_region')
            ec2_vpc_id = host.get('ec2_vpc_id')
            ec2_subnet_id = host.get('ec2_subnet_id')
            ec2_availability_zone = host.get('ec2_availability_zone')

            break_flag = False
            for filter in self.filters:
                key = filter.get('key')
                expected_value = filter.get('value')
                try:
                    value = self._compose(key, host)
                    if value != expected_value:
                        break_flag = True
                        break
                except jinja2.exceptions.UndefinedError as ue:
                    break
                except ansible.errors.AnsibleUndefinedVariable as aue:
                    break

            if break_flag == True:
                continue

            if only_include_active == True:
                if active != "active":
                    continue

            self.inventory.add_host(name)
            facts = dict(
                dog_name=dog_name,
            )
            full_facts = dict()

            full_facts.update(facts)
            
            #print(f'host: {host}')
            host_vars = host.get('vars')
            try:
                host.pop('vars')
            except KeyError:
                pass
            #print(f'host: {host}')

            for key, value in host.items():
                if value != None:
                    fact_key = self._slugify(key)
                    full_facts[fact_key] = value

            for key, value in full_facts.items():
                if value != None:
                    self.inventory.set_variable(name, key, value)

            ## Use constructed if applicable
            ## Composed variables
            self._set_composite_vars(self.get_option('compose'), full_facts, name, strict=strict)
            ## Complex groups based on jinja2 conditionals, hosts that meet the conditional are added to group
            self._add_host_to_composed_groups(self.get_option('groups'), full_facts, name, strict=strict)
            ## Create groups based on variable values and add the corresponding hosts to it
            self._add_host_to_keyed_groups(self.get_option('keyed_groups'), full_facts, name, strict=strict)

            #facts.update(full_facts)

            if os_version != None:
                self.inventory.add_group('os_' + os_distribution + "_" + self.fix_group(os_version))
                self.inventory.add_host(name, group='os_' + os_distribution + "_" + self.fix_group(os_version))
            #self.inventory.add_group(active)
            #self.inventory.add_host(name, group=active)
            if group != None:
                self.inventory.add_group(group)
                if groups.get(group):
                    group_vars = groups.get(group).get("vars")
                    if group_vars:
                        for key,value in group_vars.items():
                            self.inventory.set_variable(group,key,value)

            self.inventory.add_host(name, group=group)
            if host_vars != None:
                for key,value in host_vars.items():
                    self.inventory.set_variable(name,key,value)
                
            if dog_name != None:
                self.inventory.add_group('name_' + self.fix_group(dog_name))
                self.inventory.add_host(name, group='name_' + self.fix_group(dog_name))
            if hostkey != None:
                self.inventory.add_group('hostkey_' + self.fix_group(hostkey))
                self.inventory.add_host(name, group='hostkey_' + self.fix_group(hostkey))
            if dog_id != None:
                self.inventory.add_group('id_' + self.fix_group(dog_id))
                self.inventory.add_host(name, group='id_' + self.fix_group(dog_id))
            if dog_version != None:
                self.inventory.add_group('version_' + self.fix_group(dog_version))
                self.inventory.add_host(name, group='version_' + self.fix_group(dog_version))

            if add_ec2_groups:
                if ec2_instance_id != None:
                    self.inventory.add_group("ec2_instance_" + ec2_instance_id)
                    self.inventory.add_host(name, group="ec2_instance_" + ec2_instance_id)
                if ec2_region != None:
                    self.inventory.add_group("ec2_region_" + ec2_region)
                    self.inventory.add_host(name, group="ec2_region_" + ec2_region)
                if ec2_vpc_id != None:
                    self.inventory.add_group("ec2_" + ec2_vpc_id)
                    self.inventory.add_host(name, group="ec2_" + ec2_vpc_id)
                if ec2_subnet_id != None:
                    self.inventory.add_group("ec2_" + ec2_subnet_id)
                    self.inventory.add_host(name, group="ec2_" + ec2_subnet_id)
                if ec2_availability_zone != None:
                    self.inventory.add_group("ec2_availability_zone_" + ec2_availability_zone)
                    self.inventory.add_host(name, group="ec2_availability_zone_" + ec2_availability_zone)

    def fix_group(self, name):
        return name.replace("-","_").replace("+","_").replace(".","_")

    def verify_file(self, path):
        """Return the possibly of a file being consumable by this plugin."""
        return (
            super(InventoryModule, self).verify_file(path) and
            path.endswith(('dog.yaml', 'dog.yml')))

    def _create_client(self):
        self.dog_url = self.get_option('dog_url')
        if self.dog_url == None:
            self.base_url = os.getenv("DOG_API_ENDPOINT")
            if self.base_url == None:
                print("ERROR: DOG_API_ENDPOINT not set")
        else:
            self.base_url = self.dog_url
        self.apitoken = os.getenv("DOG_API_TOKEN")
        if self.apitoken == None:
            print("ERROR: DOG_API_TOKEN not set")
        self.client = dc.DogClient(base_url = self.base_url, apitoken = self.apitoken)
        return self.client

    def parse(self, inventory, loader, path, cache=True):
        super(InventoryModule, self).parse(inventory, loader, path, cache)
        self._read_config_data(path)
        client = self._create_client()
        try:
            self._populate(client)
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            raise AnsibleError(
                'An unexpected dog error occurred: {0}'.format(e)
            )
