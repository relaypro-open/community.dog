import sys
import os
from unittest.mock import MagicMock

# Ensure project root is on path so `plugins.*` imports resolve
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# --- Mock dog.api (git dep, not guaranteed in CI) ---
_mock_dog_module = MagicMock()
_mock_dog_api = MagicMock()
sys.modules['dog'] = _mock_dog_module
sys.modules['dog.api'] = _mock_dog_api

# --- Provide a real exception class for apiclient.exceptions.ClientError ---
# Using setdefault so the real package is used when installed.
class ClientError(Exception):
    pass

if 'apiclient' not in sys.modules:
    _mock_apiclient = MagicMock()
    _mock_apiclient_exceptions = MagicMock()
    _mock_apiclient_exceptions.ClientError = ClientError
    sys.modules['apiclient'] = _mock_apiclient
    sys.modules['apiclient.exceptions'] = _mock_apiclient_exceptions

# --- Provide deepmerge.always_merger when not installed ---
if 'deepmerge' not in sys.modules:
    class _AlwaysMerger:
        @staticmethod
        def merge(base, override):
            result = dict(base)
            result.update(override)
            return result

    _mock_deepmerge = MagicMock()
    _mock_deepmerge.always_merger = _AlwaysMerger()
    sys.modules['deepmerge'] = _mock_deepmerge
