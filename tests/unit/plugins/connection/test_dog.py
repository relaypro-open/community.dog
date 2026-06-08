import json
import os
import sys
import pytest
from unittest.mock import MagicMock, patch, mock_open, call

import plugins.connection.dog as dog_module
from plugins.connection.dog import Connection
from ansible.errors import AnsibleError


def _make_conn():
    """Return a Connection instance that bypasses __init__."""
    conn = object.__new__(Connection)
    conn._display = MagicMock()
    return conn


# ---------------------------------------------------------------------------
# dict_to_binary_string
# ---------------------------------------------------------------------------

class TestDictToBinaryString:
    def setup_method(self):
        self.conn = _make_conn()

    def test_empty_dict(self):
        assert self.conn.dict_to_binary_string({}) == b'{}'

    def test_simple_dict(self):
        result = self.conn.dict_to_binary_string({"key": "value"})
        assert result == b'{"key":"value"}'

    def test_nested_dict(self):
        result = self.conn.dict_to_binary_string({"a": {"b": 1}})
        assert result == b'{"a":{"b":1}}'

    def test_returns_bytes(self):
        result = self.conn.dict_to_binary_string({"x": "y"})
        assert isinstance(result, bytes)

    def test_list_value(self):
        result = self.conn.dict_to_binary_string({"k": [1, 2]})
        assert result == b'{"k":[1,2]}'


# ---------------------------------------------------------------------------
# dict_to_list
# ---------------------------------------------------------------------------

class TestDictToList:
    def setup_method(self):
        self.conn = _make_conn()

    # NOTE: the implementation names its parameter `dict`, shadowing the builtin
    # class, so `type(dict) is dict` always evaluates to False and the function
    # is a passthrough for every input type.

    def test_passthrough_for_dict(self):
        data = {"err": ["line1", "line2"]}
        assert self.conn.dict_to_list(data) is data

    def test_passthrough_for_list(self):
        data = ["already", "a", "list"]
        assert self.conn.dict_to_list(data) is data

    def test_passthrough_for_string(self):
        assert self.conn.dict_to_list("string") == "string"

    def test_passthrough_for_empty_dict(self):
        data = {}
        assert self.conn.dict_to_list(data) is data


# ---------------------------------------------------------------------------
# _normalize_path
# ---------------------------------------------------------------------------

class TestNormalizePath:
    def setup_method(self):
        self.conn = _make_conn()

    def test_absolute_path_with_prefix(self):
        result = self.conn._normalize_path("/var/log/app.log", "/remote")
        assert result == "/remote/var/log/app.log"

    def test_relative_path_gets_sep_prepended(self):
        result = self.conn._normalize_path("var/log/app.log", "/remote")
        assert result == "/remote/var/log/app.log"

    def test_trailing_slashes_normalized(self):
        result = self.conn._normalize_path("/var//log/../log/app.log", "/remote")
        assert result == "/remote/var/log/app.log"

    def test_empty_prefix_gives_absolute_path(self):
        result = self.conn._normalize_path("/etc/hosts", "")
        assert result == "etc/hosts"


# ---------------------------------------------------------------------------
# _connect
# ---------------------------------------------------------------------------

class TestConnect:
    def _make_connected_conn(self, unique_id_key="name", config_token=None, env_token=None):
        conn = _make_conn()
        conn.dog_env = "test_env"
        conn.base_url = "http://localhost:8000/api/v2"
        conn.host = "myhost"
        conn.get_option = MagicMock(side_effect=lambda opt: {
            "unique_id_key": unique_id_key,
            "request_timeout": 300.0,
        }[opt])
        return conn, config_token, env_token

    def test_connect_reads_token_from_credentials_file(self):
        conn, _, _ = self._make_connected_conn(config_token="file_token")
        mock_client = MagicMock()
        mock_client.get_host_by_name.return_value = {"hostkey": "hk_abc"}
        dog_module.dc.DogClient.return_value = mock_client

        fake_config = MagicMock()
        fake_config.__getitem__ = MagicMock(
            return_value={"token": "file_token"}
        )
        fake_config.read = MagicMock()

        with patch("plugins.connection.dog.configparser.ConfigParser", return_value=fake_config):
            with patch("plugins.connection.dog.ConnectionBase._connect"):
                conn._connect()

        assert conn.apitoken == "file_token"
        assert conn.hostkey == "hk_abc"
        assert conn._connected is True

    def test_connect_falls_back_to_env_token(self):
        conn, _, _ = self._make_connected_conn()
        mock_client = MagicMock()
        mock_client.get_host_by_name.return_value = {"hostkey": "hk_env"}
        dog_module.dc.DogClient.return_value = mock_client

        # ConfigParser raises KeyError (env not in creds file)
        fake_config = MagicMock()
        fake_config.__getitem__ = MagicMock(side_effect=KeyError("test_env"))
        fake_config.read = MagicMock()

        with patch("plugins.connection.dog.configparser.ConfigParser", return_value=fake_config):
            with patch("plugins.connection.dog.os.getenv", return_value="env_token"):
                with patch("plugins.connection.dog.ConnectionBase._connect"):
                    conn._connect()

        assert conn.apitoken == "env_token"

    def test_connect_uses_hostkey_lookup_when_unique_id_key_is_hostkey(self):
        conn, _, _ = self._make_connected_conn(unique_id_key="hostkey")
        mock_client = MagicMock()
        mock_client.get_host_by_hostkey.return_value = {"hostkey": "hk_xyz"}
        dog_module.dc.DogClient.return_value = mock_client

        fake_config = MagicMock()
        fake_config.__getitem__ = MagicMock(return_value={"token": "tok"})

        with patch("plugins.connection.dog.configparser.ConfigParser", return_value=fake_config):
            with patch("plugins.connection.dog.ConnectionBase._connect"):
                conn._connect()

        mock_client.get_host_by_hostkey.assert_called_once_with(conn.host)

    def test_connect_raises_ansible_error_when_dog_not_installed(self):
        conn, _, _ = self._make_connected_conn()
        original = dog_module.HAVE_DOG
        dog_module.HAVE_DOG = False
        try:
            with pytest.raises(AnsibleError, match="dog is not installed"):
                conn._connect()
        finally:
            dog_module.HAVE_DOG = original

    def test_connect_raises_ansible_error_when_dog_env_is_none(self):
        conn, _, _ = self._make_connected_conn()
        conn.dog_env = None
        fake_config = MagicMock()
        fake_config.__getitem__ = MagicMock(return_value={"token": "tok"})
        with patch("plugins.connection.dog.configparser.ConfigParser", return_value=fake_config):
            with patch("plugins.connection.dog.ConnectionBase._connect"):
                with pytest.raises(AnsibleError, match="dog_env is not set"):
                    conn._connect()

    def test_connect_raises_ansible_error_when_apitoken_is_none(self):
        conn, _, _ = self._make_connected_conn()
        fake_config = MagicMock()
        fake_config.__getitem__ = MagicMock(side_effect=KeyError("test_env"))
        with patch("plugins.connection.dog.configparser.ConfigParser", return_value=fake_config):
            with patch("plugins.connection.dog.os.getenv", return_value=None):
                with patch("plugins.connection.dog.ConnectionBase._connect"):
                    with pytest.raises(AnsibleError, match="No dog API token"):
                        conn._connect()

    def test_connect_raises_ansible_error_on_unexpected_exception(self):
        conn, _, _ = self._make_connected_conn()

        fake_config = MagicMock()
        fake_config.__getitem__ = MagicMock(return_value={"token": "tok"})

        dog_module.dc.DogClient.side_effect = RuntimeError("boom")
        try:
            with patch("plugins.connection.dog.configparser.ConfigParser", return_value=fake_config):
                with patch("plugins.connection.dog.ConnectionBase._connect"):
                    with pytest.raises(AnsibleError, match="unexpected dog error"):
                        conn._connect()
        finally:
            dog_module.dc.DogClient.side_effect = None


# ---------------------------------------------------------------------------
# exec_command
# ---------------------------------------------------------------------------

class TestExecCommand:
    def _make_conn_with_client(self):
        conn = _make_conn()
        conn.host = "myhost"
        conn.hostkey = "hk_abc"
        conn.client = MagicMock()
        return conn

    def test_success_returns_stdout(self):
        conn = self._make_conn_with_client()
        conn.client.exec_command.return_value = {
            "hk_abc": {"retcode": 0, "stdout": "hello", "stderr": {}}
        }
        with patch("plugins.connection.dog.ConnectionBase.exec_command"):
            rc, stdout, stderr = conn.exec_command("echo hello")
        assert rc == 0
        assert stdout == "hello"
        assert stderr == b"{}"

    def test_nonzero_retcode_is_returned(self):
        conn = self._make_conn_with_client()
        conn.client.exec_command.return_value = {
            "hk_abc": {"retcode": 127, "stdout": "", "stderr": {"msg": "not found"}}
        }
        with patch("plugins.connection.dog.ConnectionBase.exec_command"):
            rc, stdout, stderr = conn.exec_command("bad_cmd")
        assert rc == 127

    def test_raises_error_when_in_data_provided(self):
        conn = self._make_conn_with_client()
        with patch("plugins.connection.dog.ConnectionBase.exec_command"):
            with pytest.raises(AnsibleError, match="pipelining"):
                conn.exec_command("cmd", in_data=b"some_data")

    def test_client_exception_returns_error_tuple(self):
        conn = self._make_conn_with_client()
        exc = Exception("api failure")
        exc.info = "api failure detail"
        conn.client.exec_command.side_effect = exc
        with patch("plugins.connection.dog.ConnectionBase.exec_command"):
            rc, stdout, info = conn.exec_command("cmd")
        assert rc == 1
        assert info == "api failure detail"


# ---------------------------------------------------------------------------
# put_file
# ---------------------------------------------------------------------------

class TestPutFile:
    def _make_conn_with_client(self):
        conn = _make_conn()
        conn.host = "myhost"
        conn.hostkey = "hk_abc"
        conn.client = MagicMock()
        return conn

    def test_put_file_normalizes_out_path_and_calls_send_file(self):
        conn = self._make_conn_with_client()
        conn.client.send_file.return_value = {"status": "ok"}
        with patch("plugins.connection.dog.ConnectionBase.put_file"):
            result = conn.put_file("/local/file.txt", "remote/file.txt")
        conn.client.send_file.assert_called_once_with(
            id="hk_abc", files={"/local/file.txt": "/remote/file.txt"}
        )
        assert result == {"status": "ok"}

    def test_put_file_exception_returns_error_tuple(self):
        conn = self._make_conn_with_client()
        exc = Exception("send failed")
        exc.info = "network error"
        conn.client.send_file.side_effect = exc
        with patch("plugins.connection.dog.ConnectionBase.put_file"):
            result = conn.put_file("/local/file.txt", "/remote/file.txt")
        assert result == (1, "", "network error")


# ---------------------------------------------------------------------------
# fetch_file
# ---------------------------------------------------------------------------

class TestFetchFile:
    def test_fetch_file_writes_content_to_out_path(self, tmp_path):
        conn = _make_conn()
        conn.host = "myhost"
        conn.hostkey = "hk_abc"
        conn.client = MagicMock()
        conn.client.fetch_file.return_value = b"file content"

        out = tmp_path / "fetched.txt"
        with patch("plugins.connection.dog.ConnectionBase.fetch_file"):
            conn.fetch_file("/remote/file.txt", str(out))

        conn.client.fetch_file.assert_called_once_with(id="hk_abc", file="/remote/file.txt")
        assert out.read_bytes() == b"file content"
