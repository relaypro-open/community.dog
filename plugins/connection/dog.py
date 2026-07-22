# Based on saltstack.py (c) 2014, Michael Scherer <misc@zarb.org>
# Based on local.py (c) 2012, Michael DeHaan <michael.dehaan@gmail.com>
# Based on chroot.py (c) 2013, Maykel Moya <mmoya@speedyrails.com>
# Based on func.py
# (c) 2022, Drew Gulino 
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import sys
import os
import traceback

from ansible import errors
from ansible.errors import AnsibleError
from ansible.plugins.connection import ConnectionBase
import configparser
import argparse
import yaml
import json

HAVE_DOG = False
try:
    import dog.api as dc
    HAVE_DOG = True
except ImportError:
    pass


DOCUMENTATION = """
    author: Drew Gulino
    name: dog
    short_description: Run tasks over dog
    description:
        - Run commands or put/fetch on a target via dog
        - This plugin allows extra arguments to be passed that are supported by the protocol but not explicitly defined here.
          They should take the form of variables declared with the following pattern C(ansible_winrm_<option>).
    version_added: "2.0"
    requirements:
        - dog (distributed firewall manager)
    options:
      # figure out more elegant 'delegation'
      base_url:
        default: http://localhost:8000/api/v2
        description:
            - Address of the dog_trainer
        env: [{name: ANSIBLE_DOG_BASE_URL}]
        ini:
        - {key: base_url, section: dog_connection}
        type: str
      apitoken:
        description:
            - apitoken to access dog_trainer
        env: [{name: ANSIBLE_DOG_BASE_URL}]
        ini:
        - {key: apitoken, section: dog_connection}
        type: str
      unique_id_key:
        description:
            - The key to be used as the server's name
        ini:
        - {key: unique_id_key, section: dog_connection}
        type: str
        default: name
        choices: [ name, hostkey ]
      request_timeout:
        version_added: "1.0.4"
        description:
            - Request timeout in seconds to dog API
        ini:
        - {key: request_timeout, section: dog_connection}
        type: float
        default: 300.0
"""


class Connection(ConnectionBase):
    ''' Dog-based connections '''

    has_pipelining = False
    transport = 'dog'

    def __init__(self, play_context, new_stdin=None, *args, **kwargs):
        super(Connection, self).__init__(play_context, new_stdin, *args, **kwargs)

        self.host = self._play_context.remote_addr
        self.dog_env = None
        self.base_url = None

        # hack to get dog_env from inventory config
        parser = argparse.ArgumentParser()
        parser.add_argument('-i', '--inventory', dest="inventory_path")
        parsed_args = None
        try:
            parsed_args, unknown = parser.parse_known_args()
        except argparse.ArgumentError as err:
            print(err)
        if parsed_args and parsed_args.inventory_path:
            # hack to allow directory to be specified to get combination inventory sources
            for name in ["", "/dog.yml", "/dog.yaml"]:
                path = parsed_args.inventory_path + name
                try:
                    with open(path, 'r') as f:
                        environment_config = yaml.safe_load(f) or {}
                        self.dog_env = environment_config['dog_env']
                        self.base_url = environment_config['dog_url']
                    break
                except IsADirectoryError:
                    continue
                except FileNotFoundError:
                    continue
                except OSError:
                    continue
                except (KeyError, TypeError):
                    raise AnsibleError(
                        "Inventory config at %s is missing required 'dog_env' or 'dog_url'" % path
                    )

    def _connect(self):
        self.unique_id_key = self.get_option("unique_id_key")
        self.request_timeout = self.get_option("request_timeout")
        try:
            if not HAVE_DOG:
                raise errors.AnsibleError("dog is not installed")
            super(Connection, self)._connect()
            config = configparser.ConfigParser()
            creds_path = os.path.expanduser('~/.dog/credentials')
            config.read(creds_path)
            if self.dog_env is None:
                raise AnsibleError("dog_env is not set — check your dog.yml inventory config")
            config_token = None
            try:
                #cannot use .get on config
                creds = config[self.dog_env] 
                config_token = creds.get("token")
            except KeyError:
                pass
            if config_token is not None:
                self.apitoken = config_token
            else:
                self.apitoken = os.getenv("DOG_API_TOKEN")

            if self.apitoken is None:
                raise AnsibleError(
                    "No dog API token: set DOG_API_TOKEN or add a [%s] token entry to ~/.dog/credentials" % self.dog_env
                )

            #try:
            self.client = dc.DogClient(base_url=self.base_url, apitoken=self.apitoken,
                                       request_timeout=self.request_timeout
                                       )
            #except Exception:
            #    self._connected = False
            #    return self
            self._connected = True
            if self.unique_id_key == "name":
                res = self.client.get_host_by_name(self.host)
            elif self.unique_id_key == "hostkey":
                res = self.client.get_host_by_hostkey(self.host)
            else:
                res = self.client.get_host_by_name(self.host)

            self.hostkey = res.get("hostkey")
            self._display.vvv("hostkey %s" % (self.hostkey))
            return self
        except Exception as e:
            self._connected = False
            traceback.print_exc(file=sys.stdout)
            raise AnsibleError("An unexpected dog error occurred: {0}".format(e))

    def exec_command(self, cmd, sudoable=False, in_data=None):
        ''' run a command on the remote minion '''
        super(Connection, self).exec_command(cmd, in_data=in_data, sudoable=sudoable)

        if in_data:
            raise errors.AnsibleError("Internal Error: this module does not support optimized module pipelining")

        self._display.vvv("EXEC %s" % (cmd), host=self.host)
        cmd = {"command": cmd, "use_shell": "true"}
        res = None
        #res = self.client.exec_command(id=self.hostkey, json=cmd)
        try:
            res = self.client.exec_command(id=self.hostkey, json=cmd)
        except Exception as ex:
            traceback.print_exc(file=sys.stdout)
            return (1, "", getattr(ex, "info", None) or "%s: %s" % (type(ex).__name__, ex))
        #self._display.vvv("exec_commad res %s" % (res))
        p = res[self.hostkey]
        stdout = p['stdout']
        stderr = p['stderr']
        # When the executed command exits non-zero, the dog agent/trainer route
        # its output into stderr.error and leave stdout empty. Ansible parses
        # module results from stdout regardless of exit code (like the ssh
        # connection plugin does), so a normal module failure (fail_json ->
        # exit 1, with valid JSON on stdout) would otherwise surface only as
        # the opaque "Module result deserialization failed: No start of json
        # char found". Recover the carried output into stdout so the real
        # module result (and its `failed`/`msg`) is visible.
        if not stdout and isinstance(stderr, dict) and 'error' in stderr:
            err = stderr['error']
            stdout = ''.join(err) if isinstance(err, list) else err
            stderr = {}
        return (p['retcode'], stdout, self.dict_to_binary_string(stderr))

    def dict_to_list(self, dict):
        if type(dict) is dict:
            res = []
            for key, val in dict.items():
                res.append([key] + val)
            return res
        else:
            return dict
    
    def dict_to_binary_string(self, data):
        """
        Converts a dictionary to a binary string.

        Args:
            data (dict): The dictionary to convert.

        Returns:
            bytes: The binary string representation of the dictionary.
        """
        json_string = json.dumps(data, separators=(',', ':'))
        binary_string = json_string.encode('utf-8')
        return binary_string

    def _normalize_path(self, path, prefix):
        if not path.startswith(os.path.sep):
            path = os.path.join(os.path.sep, path)
        normpath = os.path.normpath(path)
        return os.path.join(prefix, normpath[1:])

    def put_file(self, in_path, out_path):
        ''' transfer a file from local to remote '''

        super(Connection, self).put_file(in_path, out_path)

        out_path = self._normalize_path(out_path, '/')
        self._display.vvv("PUT %s TO %s" % (in_path, out_path), host=self.host)
        files = {in_path: out_path}
        try:
            res = self.client.send_file(id=self.hostkey, files=files)
        except Exception as ex:
            traceback.print_exc(file=sys.stdout)
            return (1, "", getattr(ex, "info", None) or "%s: %s" % (type(ex).__name__, ex))
        return res

    # TODO test it
    def fetch_file(self, in_path, out_path):
        ''' fetch a file from remote to local '''

        super(Connection, self).fetch_file(in_path, out_path)

        in_path = self._normalize_path(in_path, '/')
        self._display.vvv("FETCH %s TO %s" % (in_path, out_path), host=self.host)
        try:
            content = self.client.fetch_file(id=self.hostkey, file=in_path)
            open(out_path, 'wb').write(content)
        except Exception as ex:
            traceback.print_exc(file=sys.stdout)
            return (1, "", getattr(ex, "info", None) or "%s: %s" % (type(ex).__name__, ex))

    def close(self):
        ''' terminate the connection; nothing to do here '''
        self._connected = False
