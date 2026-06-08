import os
import sys
import pytest
import jinja2
from unittest.mock import MagicMock, patch, call

from apiclient.exceptions import ClientError
from ansible.errors import AnsibleError
from plugins.inventory.dog_inventory import InventoryModule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_module(**overrides):
    """Return an InventoryModule with minimal attrs for unit testing."""
    mod = object.__new__(InventoryModule)
    mod.inventory = MagicMock()
    mod.display = MagicMock()
    mod.unique_id_key = "name"
    mod.add_ec2_groups = False
    mod.strict = False
    mod.groups = {}
    mod._set_composite_vars = MagicMock()
    mod._add_host_to_composed_groups = MagicMock()
    mod._add_host_to_keyed_groups = MagicMock()
    mod.get_option = MagicMock(return_value=[])
    for k, v in overrides.items():
        setattr(mod, k, v)
    return mod


def _default_get_option(opts):
    """Build a get_option side_effect from a dict of option values."""
    def _get(name):
        return opts.get(name)
    return _get


# ---------------------------------------------------------------------------
# fix_group
# ---------------------------------------------------------------------------

class TestFixGroup:
    def setup_method(self):
        self.mod = _make_module()

    def test_replaces_dashes(self):
        assert self.mod.fix_group("my-group") == "my_group"

    def test_replaces_dots(self):
        assert self.mod.fix_group("ubuntu.22.04") == "ubuntu_22_04"

    def test_replaces_plus(self):
        assert self.mod.fix_group("group+extra") == "group_extra"

    def test_handles_mixed_chars(self):
        assert self.mod.fix_group("a-b.c+d") == "a_b_c_d"

    def test_passthrough_for_clean_names(self):
        assert self.mod.fix_group("already_clean") == "already_clean"

    def test_numeric_input_becomes_string(self):
        assert self.mod.fix_group(42) == "42"


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------

class TestSlugify:
    def setup_method(self):
        self.mod = _make_module()

    def test_prepends_dog_prefix(self):
        assert self.mod._slugify("name") == "dog_name"

    def test_dashes_are_preserved(self):
        # regex is [^\w-] so dashes are NOT replaced, only non-word non-dash chars are
        assert self.mod._slugify("ec2-instance-id") == "dog_ec2-instance-id"

    def test_strips_leading_underscores_after_prefix(self):
        # re.sub + lstrip("_") in the implementation
        assert self.mod._slugify("_private") == "dog_private"

    def test_lowercases_result(self):
        assert self.mod._slugify("CamelCase") == "dog_camelcase"

    def test_dots_become_underscores(self):
        assert self.mod._slugify("os.version") == "dog_os_version"


# ---------------------------------------------------------------------------
# verify_file
# ---------------------------------------------------------------------------

class TestVerifyFile:
    def setup_method(self):
        self.mod = _make_module()

    def test_accepts_dog_yml(self):
        with patch(
            "ansible.plugins.inventory.BaseInventoryPlugin.verify_file",
            return_value=True,
        ):
            assert self.mod.verify_file("/inventory/dog.yml") is True

    def test_accepts_dog_yaml(self):
        with patch(
            "ansible.plugins.inventory.BaseInventoryPlugin.verify_file",
            return_value=True,
        ):
            assert self.mod.verify_file("/inventory/dog.yaml") is True

    def test_rejects_other_extensions(self):
        with patch(
            "ansible.plugins.inventory.BaseInventoryPlugin.verify_file",
            return_value=True,
        ):
            assert self.mod.verify_file("/inventory/hosts.yml") is False

    def test_returns_false_when_super_returns_false(self):
        with patch(
            "ansible.plugins.inventory.BaseInventoryPlugin.verify_file",
            return_value=False,
        ):
            assert self.mod.verify_file("/inventory/dog.yml") is False


# ---------------------------------------------------------------------------
# parse_group
# ---------------------------------------------------------------------------

class TestParseGroup:
    def setup_method(self):
        self.mod = _make_module()

    def test_adds_group_to_inventory(self):
        self.mod.parse_group("webservers", {})
        self.mod.inventory.add_group.assert_called_with("webservers")

    def test_adds_hosts_to_group(self):
        self.mod.parse_group("webservers", {"hosts": {"host1": {}, "host2": {}}})
        calls = self.mod.inventory.add_host.call_args_list
        added = {c.args for c in calls}
        assert ("host1", "webservers") in added
        assert ("host2", "webservers") in added

    def test_sets_group_vars(self):
        self.mod.parse_group("webservers", {"vars": {"env": "prod", "port": 443}})
        self.mod.inventory.set_variable.assert_any_call("webservers", "env", "prod")
        self.mod.inventory.set_variable.assert_any_call("webservers", "port", 443)

    def test_adds_children(self):
        self.mod.parse_group("parent", {"children": ["child_qa", "child-prod"]})
        self.mod.inventory.add_group.assert_any_call("child_prod")
        self.mod.inventory.add_child.assert_any_call("parent", "child_prod")

    def test_raises_on_bad_hosts_format(self):
        with pytest.raises(AnsibleError, match="bad data for the host list"):
            self.mod.parse_group("badgroup", {"hosts": ["not", "a", "dict"]})

    def test_raises_on_bad_vars_format(self):
        with pytest.raises(AnsibleError, match="bad data for variables"):
            self.mod.parse_group("badgroup", {"vars": ["not", "a", "dict"]})

    def test_skips_children_for_meta_group(self):
        self.mod.parse_group("_meta", {"children": ["child"]})
        self.mod.inventory.add_child.assert_not_called()


# ---------------------------------------------------------------------------
# parse_host
# ---------------------------------------------------------------------------

class TestParseHost:
    def _make_host(self, **overrides):
        host = {
            "name": "host1",
            "id": "id_abc",
            "group": "app",
            "hostkey": "hk_123",
            "version": "1.0",
            "os_distribution": "ubuntu",
            "os_version": "22.04",
            "ec2_instance_id": None,
            "ec2_region": None,
            "ec2_vpc_id": None,
            "ec2_subnet_id": None,
            "ec2_availability_zone": None,
        }
        host.update(overrides)
        return host

    def test_adds_host_to_inventory(self):
        mod = _make_module(groups={"app": {}})
        mod.parse_group = MagicMock()
        mod.parse_host(self._make_host())
        mod.inventory.add_host.assert_any_call("host1")

    def test_adds_host_to_group(self):
        mod = _make_module(groups={"app": {"hosts": {}, "vars": {}}})
        mod.parse_group = MagicMock()
        mod.parse_host(self._make_host())
        mod.inventory.add_host.assert_any_call("host1", group="app")

    def test_adds_os_group(self):
        mod = _make_module(groups={"app": {}})
        mod.parse_group = MagicMock()
        mod.parse_host(self._make_host(os_distribution="ubuntu", os_version="22.04"))
        mod.inventory.add_group.assert_any_call("os_ubuntu_22_04")
        mod.inventory.add_host.assert_any_call("host1", group="os_ubuntu_22_04")

    def test_adds_name_group(self):
        mod = _make_module(groups={"app": {}})
        mod.parse_group = MagicMock()
        mod.parse_host(self._make_host(name="host1"))
        mod.inventory.add_group.assert_any_call("name_host1")

    def test_adds_hostkey_group(self):
        mod = _make_module(groups={"app": {}})
        mod.parse_group = MagicMock()
        mod.parse_host(self._make_host(hostkey="hk_123"))
        mod.inventory.add_group.assert_any_call("hostkey_hk_123")

    def test_ec2_groups_added_when_enabled(self):
        mod = _make_module(add_ec2_groups=True, groups={"app": {}})
        mod.parse_group = MagicMock()
        mod.parse_host(self._make_host(
            ec2_instance_id="i-abc123",
            ec2_region="us-east-1",
        ))
        mod.inventory.add_group.assert_any_call("ec2_instance_i_abc123")
        mod.inventory.add_group.assert_any_call("ec2_region_us_east_1")

    def test_ec2_groups_skipped_when_disabled(self):
        mod = _make_module(add_ec2_groups=False, groups={"app": {}})
        mod.parse_group = MagicMock()
        mod.parse_host(self._make_host(ec2_instance_id="i-abc123"))
        group_calls = [str(c) for c in mod.inventory.add_group.call_args_list]
        assert not any("ec2_instance" in c for c in group_calls)

    def test_host_vars_are_set(self):
        mod = _make_module(groups={"app": {}})
        mod.parse_group = MagicMock()
        mod.parse_host(self._make_host(vars={"role": "web", "tier": "1"}))
        mod.inventory.set_variable.assert_any_call("host1", "role", "web")
        mod.inventory.set_variable.assert_any_call("host1", "tier", "1")

    def test_uses_hostkey_as_name_when_unique_id_key_is_hostkey(self):
        mod = _make_module(unique_id_key="hostkey", groups={"app": {}})
        mod.parse_group = MagicMock()
        host = self._make_host(name="long-hostname", hostkey="hk_short")
        mod.parse_host(host)
        mod.inventory.add_host.assert_any_call("hk_short")

    def test_host_group_not_in_groups_dict_does_not_crash(self):
        """parse_host must not crash when a host's group is absent from self.groups."""
        mod = _make_module(groups={})  # empty — host's group "app" not present
        mod.parse_host(self._make_host())
        mod.inventory.add_host.assert_any_call("host1")


# ---------------------------------------------------------------------------
# _populate — filter logic
# ---------------------------------------------------------------------------

class TestPopulateFilters:
    """Tests for the filter-evaluation loop inside _populate."""

    _OPTION_DEFAULTS = {
        "strict": False,
        "add_ec2_groups": False,
        "only_include_active": True,
        "unique_id_key": "name",
        "filters": [],
    }

    def _make_populated_mod(self, filters=None):
        opts = dict(self._OPTION_DEFAULTS, filters=filters or [])
        mod = _make_module()
        mod.dog_fact = None
        mod.group_suffix = None
        mod.get_option = MagicMock(side_effect=_default_get_option(opts))
        mod.parse_host = MagicMock()
        mod.parse_group = MagicMock()
        return mod

    def _make_client(self, hosts, groups=None):
        client = MagicMock()
        client.get_all_active_hosts.return_value = hosts
        client.get_all_groups.return_value = groups or []
        client.get_fact_by_name.side_effect = ClientError("not found")
        return client

    def test_host_passes_when_no_filters(self):
        mod = self._make_populated_mod(filters=[])
        client = self._make_client([{"name": "host1"}])
        mod._populate(client)
        mod.parse_host.assert_called_once()

    def test_host_included_when_filter_value_matches(self):
        mod = self._make_populated_mod(filters=[{"key": "env", "value": "qa"}])
        client = self._make_client([{"name": "host1", "env": "qa"}])
        with patch.object(mod, "_compose", return_value="qa"):
            mod._populate(client)
        mod.parse_host.assert_called_once()

    def test_host_excluded_when_filter_value_mismatches(self):
        mod = self._make_populated_mod(filters=[{"key": "env", "value": "qa"}])
        client = self._make_client([{"name": "host1", "env": "prod"}])
        with patch.object(mod, "_compose", return_value="prod"):
            mod._populate(client)
        mod.parse_host.assert_not_called()

    def test_host_passes_when_key_undefined_and_filter_expects_none(self):
        mod = self._make_populated_mod(filters=[{"key": "missing_key", "value": None}])
        client = self._make_client([{"name": "host1"}])
        with patch.object(mod, "_compose", side_effect=jinja2.exceptions.UndefinedError("undef")):
            mod._populate(client)
        mod.parse_host.assert_called_once()

    def test_host_excluded_when_key_undefined_and_filter_expects_value(self):
        mod = self._make_populated_mod(filters=[{"key": "missing_key", "value": "qa"}])
        client = self._make_client([{"name": "host1"}])
        with patch.object(mod, "_compose", side_effect=jinja2.exceptions.UndefinedError("undef")):
            mod._populate(client)
        mod.parse_host.assert_not_called()

    def test_all_filters_must_match(self):
        """Host is excluded if it passes the first filter but fails the second."""
        filters = [
            {"key": "env", "value": "qa"},
            {"key": "cluster", "value": "alpha"},
        ]
        mod = self._make_populated_mod(filters=filters)
        client = self._make_client([{"name": "host1"}])

        compose_values = iter(["qa", "beta"])  # first filter ok, second fails
        with patch.object(mod, "_compose", side_effect=lambda k, h: next(compose_values)):
            mod._populate(client)
        mod.parse_host.assert_not_called()


# ---------------------------------------------------------------------------
# _populate — host fetching and group handling
# ---------------------------------------------------------------------------

class TestPopulateBehavior:
    _BASE_OPTS = {
        "strict": False,
        "add_ec2_groups": False,
        "only_include_active": True,
        "unique_id_key": "name",
        "filters": [],
    }

    def _setup(self, opts_override=None):
        opts = dict(self._BASE_OPTS, **(opts_override or {}))
        mod = _make_module()
        mod.dog_fact = "myfact"
        mod.group_suffix = None
        mod.get_option = MagicMock(side_effect=_default_get_option(opts))
        mod.parse_host = MagicMock()
        mod.parse_group = MagicMock()
        return mod

    def test_uses_active_hosts_endpoint_when_option_true(self):
        mod = self._setup({"only_include_active": True})
        client = MagicMock()
        client.get_all_active_hosts.return_value = []
        client.get_all_groups.return_value = []
        client.get_fact_by_name.side_effect = ClientError("x")
        mod._populate(client)
        client.get_all_active_hosts.assert_called_once()
        client.get_all_hosts.assert_not_called()

    def test_uses_all_hosts_endpoint_when_option_false(self):
        mod = self._setup({"only_include_active": False})
        client = MagicMock()
        client.get_all_hosts.return_value = []
        client.get_all_groups.return_value = []
        client.get_fact_by_name.side_effect = ClientError("x")
        mod._populate(client)
        client.get_all_hosts.assert_called_once()
        client.get_all_active_hosts.assert_not_called()

    def test_fact_not_found_falls_back_to_dog_groups(self):
        mod = self._setup()
        client = MagicMock()
        client.get_all_active_hosts.return_value = []
        client.get_all_groups.return_value = [{"name": "app", "hosts": {}, "vars": {}}]
        client.get_fact_by_name.side_effect = ClientError("not found")
        mod._populate(client)
        # self.groups should contain the dog group
        assert "app" in mod.groups

    def test_fact_groups_merged_with_dog_groups(self):
        mod = self._setup()
        client = MagicMock()
        client.get_all_active_hosts.return_value = []
        client.get_all_groups.return_value = [{"name": "dog_grp", "hosts": {}, "vars": {}}]
        client.get_fact_by_name.return_value = {
            "groups": {"fact_grp": {"hosts": {}, "vars": {}}}
        }
        mod._populate(client)
        assert "dog_grp" in mod.groups
        assert "fact_grp" in mod.groups

    def test_fact_hosts_filtered_against_active_hosts_list(self):
        """Fact group hosts are filtered via `group_host in hosts`.

        `hosts` is a list of dicts from the API, so `string in list_of_dicts`
        always evaluates to False — every fact host is filtered out.  This test
        documents that current behavior so a future fix is detectable.
        """
        mod = self._setup()
        active_hosts = [{"name": "active_host"}]
        client = MagicMock()
        client.get_all_active_hosts.return_value = active_hosts
        client.get_all_groups.return_value = []
        client.get_fact_by_name.return_value = {
            "groups": {
                "web": {
                    "hosts": {"active_host": {}, "inactive_host": {}},
                    "vars": {},
                }
            }
        }
        mod._populate(client)
        web_group_hosts = mod.groups.get("web", {}).get("hosts", {})
        # current behavior: string-in-list-of-dicts is always False, so both removed
        assert web_group_hosts == {}

    def test_group_suffix_creates_parent_group(self):
        mod = self._setup()
        mod.group_suffix = "_qa"
        client = MagicMock()
        client.get_all_active_hosts.return_value = []
        client.get_all_groups.return_value = [
            {"name": "app_qa", "hosts": {}, "vars": {}, "children": []}
        ]
        client.get_fact_by_name.side_effect = ClientError("not found")
        mod._populate(client)
        parse_group_names = [c.args[0] for c in mod.parse_group.call_args_list]
        assert "app" in parse_group_names

    def test_group_suffix_with_regex_metacharacters_does_not_raise(self):
        """group_suffix with an unclosed bracket must not cause re.error."""
        mod = self._setup()
        # "[qa" is an unterminated character class — without re.escape, re.fullmatch
        # raises re.error; with re.escape it is treated as literal text.
        mod.group_suffix = "[qa"
        client = MagicMock()
        client.get_all_active_hosts.return_value = []
        client.get_all_groups.return_value = [
            {"name": "app[qa", "hosts": {}, "vars": {}, "children": []}
        ]
        client.get_fact_by_name.side_effect = ClientError("not found")
        mod._populate(client)
        parse_group_names = [c.args[0] for c in mod.parse_group.call_args_list]
        assert "app" in parse_group_names

    def test_api_error_on_hosts_raises_ansible_error(self):
        mod = self._setup()
        client = MagicMock()
        client.get_all_active_hosts.side_effect = RuntimeError("API down")
        client.get_all_groups.return_value = []
        client.get_fact_by_name.side_effect = ClientError("x")
        with pytest.raises(AnsibleError, match="Error listing containers"):
            mod._populate(client)

    def test_api_error_on_groups_raises_ansible_error(self):
        mod = self._setup()
        client = MagicMock()
        client.get_all_active_hosts.return_value = []
        client.get_all_groups.side_effect = RuntimeError("groups API down")
        client.get_fact_by_name.side_effect = ClientError("x")
        with pytest.raises(AnsibleError, match="Error listing groups"):
            mod._populate(client)
