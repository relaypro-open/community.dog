<!--
Copyright (c) Ansible Project
GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
SPDX-License-Identifier: GPL-3.0-or-later
-->

# dog Community Collection

This repo contains the `community.dog` Ansible Collection. The collection includes many modules and plugins to work with the dog server firewall management system.

Please note that this collection does **not** support Windows targets. The connection plugins included in this collection support Windows targets on a best-effort basis, but we are not testing this in CI.

## Tested with Ansible

Check Pipfile, Pipfile.lock for python dependencies, for use with [pipenv](https://pipenv.pypa.io/). 

```
pip3 install -e git+https://github.com/relaypro-open/dog_api_python.git#egg=dog_api_python
pip3 install api-client
```

Also included are .envrc and .tools-version to be used with [asdf](https://asdf-vm.com/)

## External requirements

Requires a working [dog](https://relaypro-open.github.io/dog/)

## Collection Documentation


### Inventory plugin

#### Configuration

Create a yaml config file for each dog_trainer instance you have.  
Filters restrict inventory by metadata and are ANDed
together.  The compose and keyed_groups options are described
[here](https://docs.ansible.com/ansible/latest/collections/ansible/builtin/constructed_inventory.html)

environments/qa/dog.yml:
```yaml
---
plugin: community.dog.dog_inventory
add_ec2_groups: false
only_include_active: true
dog_url: https://dog-qa.mynet.com:8443/api/V2
dog_env: qa
```

environments/pro/dog.yml:
```yaml
---
plugin: community.dog.dog_inventory
add_ec2_groups: true
only_include_active: true
dog_url: https://dog-pro.mynet.com:8443/api/V2
dog_env: pro
dog_fact: pro
unique_id_key: name
compose:
  dog_group_alias: dog_group+"_"+dog_ec2_instance_tags.alias
keyed_groups:
  - prefix: alias
    key: 'dog_group_alias'
filters:
  - key: ec2_instance_tags.environment
    value: qa
  - key: ec2_instance_tags.cluster
    value: mob
```

Create a $HOME/.dog/credentials file with a section for each dog_env:
```ini
[qa]
token = $JWT

[pro]
token = $JWT
```

#### ENV Variables

Can use ENV variables instead of credentials file for inventory:

* DOG_API_TOKEN: key configured in dog api gateway
* DOG_API_ENDPOINT: URL for dog api gateway (example: https://dog.mynet.com:8443/api/V2).  
                    Only use DOG_API_ENDPOINT if the URL is not configured in plugin configuration file.

#### Usage

```
ansible-inventory -i dog.yml --graph
```

### Connection plugin

#### Configuration

set these in your ansible.cfg
```
[defaults]
transport = community.dog.dog

[dog_connection]
unique_id_key = name
```

#### Usage

```
Run ansible normally.
```

## Install Ansible collections

Create requirements.yml file in your playbook repository (or add to the existing file):

```
collections:
  - name: https://github.com/relaypro-open/community.dog.git
    type: git
    version: main
```

If you want to install collections in the project space, you have to run:

```
mkdir collections
ansible-galaxy collection install -f -r requirements.yml -p ./collections
```

If you want to install collections in the global space (~/.ansible/collections), you have to run:

```
ansible-galaxy collection install -f -r requirements.yml
```

See [Ansible Using collections](https://docs.ansible.com/ansible/latest/user_guide/collections_using.html) for more details.

## Contributing to this collection

## Release notes

## Licensing

This collection is primarily licensed and distributed as a whole under the GNU General Public License v3.0 or later.

See [LICENSES/GPL-3.0-or-later.txt](https://github.com/ansible-collections/community.dog/blob/main/COPYING) for the full text.

All files have a machine readable `SDPX-License-Identifier:` comment denoting its respective license(s) or an equivalent entry in an accompanying `.license` file. Only changelog fragments (which will not be part of a release) are covered by a blanket statement in `.reuse/dep5`. This conforms to the [REUSE specification](https://reuse.software/spec/).
