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
dog_host: http://my-dog-host:8000/api/V2
strict: false
keyed_groups:
  # Add containers with primary network foo to a network_foo group
  - prefix: group
    key: 'group'
  # Add Linux hosts to an os_linux group
  - prefix: os
    key: os_distribution
'''

import re
import traceback
import sys

from ansible.errors import AnsibleError
from ansible.module_utils.common.text.converters import to_native
from ansible.plugins.inventory import BaseInventoryPlugin, Constructable

from apiclient import APIClient, endpoint, retry_request
from apiclient import HeaderAuthentication,JsonResponseHandler,JsonRequestFormatter

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

        try:
            hosts = client.get_all_hosts()
        except Exception as exc:
            raise AnsibleError("Error listing containers: %s" % to_native(exc))

        extra_facts = {}

        for host in hosts:
            dog_id = host.get('id')
            name = host.get('name')
            hostkey = host.get('hostkey')
            group = host.get('group')
            active = host.get('active')
            dog_version = host.get('version')
            os_distribution = host.get('os_distribution')
            os_version = host.get('os_version')

            ec2_instance_id = host.get('ec2_instance_id')
            ec2_region = host.get('ec2_region')
            ec2_vpc_id = host.get('ec2_vpc_id')
            ec2_subnet_id = host.get('ec2_subnet_id')
            ec2_availability_zone = host.get('ec2_availability_zone')

            if only_include_active == True:
                if active != "active":
                    continue

            self.inventory.add_host(name)
            facts = dict(
                dog_name=name,
            )
            full_facts = dict()

            full_facts.update(facts)

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
                self.inventory.add_group('os_' + os_distribution + "_" + os_version)
                self.inventory.add_host(name, group='os_' + os_distribution + "_" + os_version)
            #self.inventory.add_group(active)
            #self.inventory.add_host(name, group=active)
            if group != None:
                self.inventory.add_group(group)
                self.inventory.add_host(name, group=group)
            if hostkey != None:
                self.inventory.add_group('hostkey_' + hostkey)
                self.inventory.add_host(name, group='hostkey_' + hostkey)
            if dog_id != None:
                self.inventory.add_group('id_' + dog_id)
                self.inventory.add_host(name, group='id_' + dog_id)
            if dog_version != None:
                self.inventory.add_group('version_' + dog_version)
                self.inventory.add_host(name, group='version_' + dog_version)

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
        self.apikey = os.getenv("DOG_API_KEY")
        if self.apikey == None:
            print("ERROR: DOG_API_KEY not set")
        self.client = dc.DogClient(base_url = self.base_url, apikey = self.apikey)
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
