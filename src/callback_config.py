"""
Callback API Configuration

Loads callback API definitions from configuration file.
Provides class-based config management with proper caching.
"""

import os
import sys
from typing import Dict, List, Tuple, Optional


class CallbackConfig:
    """
    Manages callback API configuration with caching.

    Callback config maps API function names to the parameter index
    that contains the callback function pointer.
    Format: api_name:param_index (one per line)
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize callback config.

        Args:
            config_path: Path to config file, or None for default
        """
        self._config_path = config_path
        self._loaded_config: Optional[Dict[str, int]] = None

    def _resolve_path(self) -> str:
        """Resolve config file path."""
        if self._config_path is not None:
            return self._config_path
        return os.path.join(os.path.dirname(__file__), "..", "callback.cfg")

    def load(self) -> Dict[str, int]:
        """
        Load callback API configuration from file.

        Returns:
            Dictionary mapping API names to parameter indices
        """
        if self._loaded_config is not None:
            return self._loaded_config

        config_path = self._resolve_path()

        if not os.path.exists(config_path):
            self._loaded_config = {}
            return self._loaded_config

        callback_apis: Dict[str, int] = {}

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()

                    # Skip comments and empty lines
                    if not line or line.startswith('#'):
                        continue

                    # Parse: api_name:param_index
                    if ':' in line:
                        parts = line.split(':', 1)
                        api_name = parts[0].strip()
                        try:
                            param_index = int(parts[1].strip())
                            callback_apis[api_name] = param_index
                        except ValueError:
                            continue

        except IOError as e:
            print(f"Warning: Failed to load callback config: {e}", file=sys.stderr)

        self._loaded_config = callback_apis
        return self._loaded_config

    def is_callback(self, function_name: str) -> Tuple[bool, int]:
        """
        Check if function is a known callback API.

        Args:
            function_name: Function name to check (may be qualified)

        Returns:
            (is_callback, param_index) tuple
        """
        callback_apis = self.load()

        # Strip namespace for matching
        base_name = function_name.split("::")[-1]

        # Exact match
        if base_name in callback_apis:
            return True, callback_apis[base_name]

        # Check for namespaced version
        for api_name, param_idx in callback_apis.items():
            if function_name.endswith("::" + api_name):
                return True, param_idx

        return False, -1

    def get_all(self) -> List[str]:
        """Get list of all known callback API names."""
        return list(self.load().keys())


# Module-level cache: maps config_path -> CallbackConfig instance
_config_instances: Dict[Optional[str], CallbackConfig] = {}


def _get_config(config_path: Optional[str] = None) -> CallbackConfig:
    """Get or create cached CallbackConfig instance for the given path."""
    if config_path not in _config_instances:
        _config_instances[config_path] = CallbackConfig(config_path)
    return _config_instances[config_path]


def is_callback_api(function_name: str, config_path: Optional[str] = None) -> Tuple[bool, int]:
    """
    Check if function is a known callback API.

    Args:
        function_name: Function name to check
        config_path: Path to config file, or None for default

    Returns:
        (is_callback, param_index) tuple
    """
    return _get_config(config_path).is_callback(function_name)


def get_callback_apis(config_path: Optional[str] = None) -> Dict[str, int]:
    """
    Get callback API configuration.

    Args:
        config_path: Path to config file, or None for default

    Returns:
        Dictionary mapping API names to parameter indices
    """
    return _get_config(config_path).load()


def get_all_callback_apis(config_path: Optional[str] = None) -> List[str]:
    """
    Get list of all known callback API names.

    Args:
        config_path: Path to config file, or None for default

    Returns:
        List of API names
    """
    return _get_config(config_path).get_all()
