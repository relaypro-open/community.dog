# -*- coding: utf-8 -*-
# Copyright (c) 2023, Drew Gulino <dgulino at relaypro.com>
# Copyright (c) 2020, Felix Fontein <felix@fontein.de>
# For the parts taken from the docker inventory script:
# Copyright (c) 2016, Paul Durivage <paul.durivage@gmail.com>
# Copyright (c) 2016, Chris Houseknecht <house@redhat.com>
# Copyright (c) 2016, James Tanner <jtanner@redhat.com>
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
import re
import traceback
import sys

from ansible.errors import AnsibleError
from ansible.module_utils.common.text.converters import to_native
from ansible.plugins.inventory import BaseInventoryPlugin, Constructable

import jinja2
import ansible

import configparser

import os

from deepmerge import always_merger
from apiclient.exceptions import ClientError

HAVE_DOG = False
try:
    import dog.api as dc

    HAVE_DOG = True
except ImportError:
    pass


__metaclass__ = type


DOCUMENTATION = """
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
    dog_env:
        description:
            - dog environment, used to lookup credentials
        type: str
        required: true
    dog_fact:
        description:
            - name of dog fact entry
        type: str
        required: false
    group_suffix:
        version_added: "1.0.5"
        description:
            - environment specific suffix at the end of group names. example group name = 'test_qa', group_suffix = '_qa' 
        type: str
        required: false
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
    request_timeout:
        version_added: "1.0.4"
        description:
            - Request timeout in seconds to dog API
        ini:
            - {key: request_timeout, section: dog_connection}
        type: float
        default: 300.0
"""

EXAMPLES = """
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
"""


MIN_DOG_API = "V2"


class InventoryModule(BaseInventoryPlugin, Constructable):
    """Host inventory parser for ansible using dog as source."""

    NAME = "dog_inventory"

    def _slugify(self, value):
        return "dog_%s" % (re.sub(r"[^\w-]", "_", value).lower().lstrip("_"))

    def _populate(self, client):
        self.strict = self.get_option("strict")

        self.add_ec2_groups = self.get_option("add_ec2_groups")
        only_include_active = self.get_option("only_include_active")
        self.unique_id_key = self.get_option("unique_id_key")
        self.filters = self.get_option("filters")

        try:
            if only_include_active:
                hosts = client.get_all_active_hosts()
            else:
                hosts = client.get_all_hosts()
        except Exception as exc:
            raise AnsibleError("Error listing containers: %s" % to_native(exc))

        try:
            dog_groups = {}
            dog_groups_list = client.get_all_groups()
            for group in dog_groups_list:
                group_name = self.fix_group(group.get("name"))
                dog_groups[group_name] = group
        except Exception as exc:
            raise AnsibleError("Error listing groups: %s" % to_native(exc))

        try:
            fact = client.get_fact_by_name(self.dog_fact)
            fact_groups_dict = fact.get("groups")
            fact_groups = {}
            for group_name, group in fact_groups_dict.items():
                group_name = self.fix_group(group_name)
                group_hosts = group.get("hosts")
                #only add fact hosts that are active hosts
                new_group_hosts = {}
                for group_host, group_host_values in group_hosts.items():  
                    if group_host in hosts:
                        new_group_hosts[group_host] = group_host_values
                group["hosts"] = new_group_hosts
                fact_groups[group_name] = group
            self.groups = always_merger.merge(fact_groups, dog_groups)
        except ClientError:
            print(f'WARNING: dog_fact "{self.dog_fact}" not found')
            self.groups = dog_groups

        for group_name, group in self.groups.items():
            #create another generic group and add environment specific group as child
            if re.fullmatch(r'.*' + self.group_suffix + '$', group_name):
                group_group_name = re.sub(r'' + self.group_suffix + '$', '', group_name)
                group_group = {
                            "name": group_group_name,
                            "children":[group_name]
                        }
                self.parse_group(group_group_name, group_group)
            self.parse_group(group_name, group)

        for host in hosts:
            break_flag = False
            for filter in self.filters:
                key = filter.get("key")
                expected_value = filter.get("value")
                try:
                    value = self._compose(key, host)
                    if value != expected_value:
                        break_flag = True
                        break
                except jinja2.exceptions.UndefinedError:
                    break
                except ansible.errors.AnsibleUndefinedVariable:
                    break

            if break_flag is True:
                continue

            self.parse_host(host)

    def parse_host(self, host):
        name = host.get(self.unique_id_key)
        group = host.get("group")
        group = self.fix_group(group)
        dog_id = host.get("id")
        dog_name = host.get("name")
        hostkey = host.get("hostkey")
        dog_version = host.get("version")
        os_distribution = host.get("os_distribution")
        os_version = host.get("os_version")

        ec2_instance_id = host.get("ec2_instance_id")
        ec2_region = host.get("ec2_region")
        ec2_vpc_id = host.get("ec2_vpc_id")
        ec2_subnet_id = host.get("ec2_subnet_id")
        ec2_availability_zone = host.get("ec2_availability_zone")

        self.inventory.add_host(name)
        facts = dict(
            dog_name=dog_name,
        )
        full_facts = dict()

        full_facts.update(facts)
        # print(f'host: {host}')
        host_vars = host.get("vars")
        try:
            host.pop("vars")
        except KeyError:
            pass
        # print(f'host: {host}')

        if os_version is not None:
            self.inventory.add_group(
                "os_" + os_distribution + "_" + self.fix_group(os_version)
            )
            self.inventory.add_host(
                name, group="os_" + os_distribution + "_" + self.fix_group(os_version)
            )
        if group is not None and group != "":
            self.parse_group(group, self.groups.get(group))

        self.inventory.add_host(name, group=group)
        if host_vars is not None:
            for key, value in host_vars.items():
                self.inventory.set_variable(name, key, value)
                self.inventory.add_group(key + "_" + self.fix_group(value))
        if dog_name is not None:
            self.inventory.add_group("name_" + self.fix_group(dog_name))
            self.inventory.add_host(name, group="name_" + self.fix_group(dog_name))
        if hostkey is not None:
            self.inventory.add_group("hostkey_" + self.fix_group(hostkey))
            self.inventory.add_host(name, group="hostkey_" + self.fix_group(hostkey))
        if dog_id is not None:
            self.inventory.add_group("id_" + self.fix_group(dog_id))
            self.inventory.add_host(name, group="id_" + self.fix_group(dog_id))
        if dog_version is not None:
            self.inventory.add_group("version_" + self.fix_group(dog_version))
            self.inventory.add_host(
                name, group="version_" + self.fix_group(dog_version)
            )

        if self.add_ec2_groups:
            if ec2_instance_id is not None:
                self.inventory.add_group(
                    "ec2_instance_" + self.fix_group(ec2_instance_id)
                )
                self.inventory.add_host(
                    name, group="ec2_instance_" + self.fix_group(ec2_instance_id)
                )
            if ec2_region is not None:
                self.inventory.add_group("ec2_region_" + self.fix_group(ec2_region))
                self.inventory.add_host(
                    name, group="ec2_region_" + self.fix_group(ec2_region)
                )
            if ec2_vpc_id is not None:
                self.inventory.add_group("ec2_" + self.fix_group(ec2_vpc_id))
                self.inventory.add_host(name, group="ec2_" + self.fix_group(ec2_vpc_id))
            if ec2_subnet_id is not None:
                self.inventory.add_group("ec2_" + self.fix_group(ec2_subnet_id))
                self.inventory.add_host(
                    name, group="ec2_" + self.fix_group(ec2_subnet_id)
                )
            if ec2_availability_zone is not None:
                self.inventory.add_group(
                    "ec2_availability_zone_" + self.fix_group(ec2_availability_zone)
                )
                self.inventory.add_host(
                    name,
                    group="ec2_availability_zone_"
                    + self.fix_group(ec2_availability_zone),
                )

        for key, value in host.items():
            if value is not None:
                fact_key = self._slugify(key)
                full_facts[fact_key] = value

        for key, value in full_facts.items():
            if value is not None:
                self.inventory.set_variable(name, key, value)

        # Use constructed if applicable
        # Composed variables
        self._set_composite_vars(
            self.get_option("compose"), full_facts, name, strict=self.strict
        )
        # Complex groups based on jinja2 conditionals, hosts that meet the conditional are added to group
        self._add_host_to_composed_groups(
            self.get_option("groups"), full_facts, name, strict=self.strict
        )
        # Create groups based on variable values and add the corresponding hosts to it
        self._add_host_to_keyed_groups(
            self.get_option("keyed_groups"), full_facts, name, strict=self.strict
        )

    def parse_group(self, group, data):
        self.inventory.add_group(group)

        if "hosts" in data:
            if not isinstance(data["hosts"], dict):
                raise AnsibleError(
                    "You defined a group '%s' with bad data for the host list:\n %s"
                    % (group, data)
                )

            for hostname, values in data["hosts"].items():
                self.inventory.add_host(hostname, group)

        if "vars" in data:
            if not isinstance(data["vars"], dict):
                raise AnsibleError(
                    "You defined a group '%s' with bad data for variables:\n %s"
                    % (group, data)
                )

            for k, v in data["vars"].items():
                self.inventory.set_variable(group, k, v)

        if group != "_meta" and isinstance(data, dict) and "children" in data:
            for child_name in data["children"]:
                self.inventory.add_group(child_name)
                self.inventory.add_child(group, child_name)

    def fix_group(self, name):
        return str(name).replace("-", "_").replace("+", "_").replace(".", "_")

    def verify_file(self, path):
        """Return the possibly of a file being consumable by this plugin."""
        return super(InventoryModule, self).verify_file(path) and path.endswith((
            "dog.yaml",
            "dog.yml",
        ))

    def _create_client(self):
        self.dog_env = self.get_option("dog_env")
        self.dog_fact = self.get_option("dog_fact")
        self.group_suffix = self.get_option("group_suffix")
        if self.dog_env is None:
            print("ERROR: dog_env opion not set in dog.yml")
            exit
        if self.dog_fact is None:
            print("WARNING: dog_fact option not set in dog.yml")
        config_token = None
        creds_path = os.path.expanduser("~/.dog/credentials")
        if os.path.exists(creds_path):
            config = configparser.ConfigParser()
            config.read(creds_path)
            creds = config[self.dog_env]
            config_token = creds["token"]
        if config_token is not None:
            self.apitoken = config_token
        else:
            self.apitoken = os.getenv("DOG_API_TOKEN")

        if self.apitoken is None:
            print("ERROR: Neither credential setting or DOG_API_TOKEN is set")
            exit

        self.dog_url = self.get_option("dog_url")
        if self.dog_url is None:
            self.base_url = os.getenv("DOG_API_ENDPOINT")
            if self.base_url is None:
                print("ERROR: DOG_API_ENDPOINT not set")
                exit
        else:
            self.base_url = self.dog_url
        self.request_timeout = self.get_option("request_timeout")
        self.client = dc.DogClient(base_url=self.base_url, apitoken=self.apitoken, request_timeout=self.request_timeout)
        return self.client

    def parse(self, inventory, loader, path, cache=True):
        super(InventoryModule, self).parse(inventory, loader, path, cache)
        self._read_config_data(path)
        client = self._create_client()
        try:
            self._populate(client)
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            raise AnsibleError("An unexpected dog error occurred: {0}".format(e))
