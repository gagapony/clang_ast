"""Callback configuration loader (TOML-based with legacy .cfg support)."""

import os
import re
import sys
from typing import Dict, List, Tuple, Optional

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # fallback
    except ImportError:
        tomllib = None  # type: ignore


class CallbackConfig:
    """Manages all indirect call resolution configs."""

    def __init__(self, config_path: Optional[str] = None):
        self._config_path = config_path
        self._data: Optional[dict] = None

    def _resolve_path(self) -> Optional[str]:
        if self._config_path is not None:
            return self._config_path
        # Default: prefer callback.toml, fall back to callback.cfg
        base = os.path.join(os.path.dirname(__file__), "..")
        toml_path = os.path.join(base, "callback.toml")
        cfg_path = os.path.join(base, "callback.cfg")
        if os.path.exists(toml_path):
            return toml_path
        if os.path.exists(cfg_path):
            return cfg_path
        return None

    def load(self) -> dict:
        if self._data is not None:
            return self._data

        path = self._resolve_path()
        if path is None or not os.path.exists(path):
            self._data = {"param_in": {}, "func_map": {}, "ioctl_map": {"format": ""}}
            return self._data

        if path.endswith('.cfg'):
            self._data = self._load_legacy_cfg(path)
        else:
            self._data = self._load_toml(path)
        return self._data

    def _load_toml(self, path: str) -> dict:
        if tomllib is None:
            print("Error: No TOML library available. Install 'tomli' for Python < 3.11.",
                  file=sys.stderr)
            return {"param_in": {}, "func_map": {}, "ioctl_map": {"format": ""}}

        try:
            with open(path, "rb") as f:
                raw = tomllib.load(f)
        except Exception as e:
            print(f"Warning: Failed to load callback config: {e}", file=sys.stderr)
            return {"param_in": {}, "func_map": {}, "ioctl_map": {"format": ""}}

        param_in = raw.get("param_in", {})
        func_map = raw.get("func_map", {})
        func_map_flat = self._flatten_dict(func_map)

        ioctl_raw = raw.get("ioctl_map", {})
        ioctl_format = ioctl_raw.get("format", "")
        ioctl_commands = {k: v for k, v in ioctl_raw.items() if k != "format"}

        return {
            "param_in": {str(k): int(v) for k, v in param_in.items()},
            "func_map": func_map_flat,
            "ioctl_map": {"format": ioctl_format, "commands": ioctl_commands},
        }

    def _load_legacy_cfg(self, path: str) -> dict:
        """Load legacy .cfg format (api_name:param_index per line)."""
        param_in: Dict[str, int] = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if ":" in line:
                        parts = line.split(":", 1)
                        api_name = parts[0].strip()
                        try:
                            param_in[api_name] = int(parts[1].strip())
                        except ValueError:
                            continue
        except IOError as e:
            print(f"Warning: Failed to load callback config: {e}", file=sys.stderr)
        return {"param_in": param_in, "func_map": {}, "ioctl_map": {"format": ""}}

    @staticmethod
    def _flatten_dict(d: dict, prefix: str = "") -> dict:
        """Flatten nested dict to dotted-key dict (handles TOML dotted keys)."""
        items = {}
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                items.update(CallbackConfig._flatten_dict(v, key))
            else:
                items[key] = v
        return items

    # ── param_in API ──
    def is_callback_api(self, function_name: str) -> Tuple[bool, int]:
        param_in = self.load()["param_in"]
        base_name = function_name.split("::")[-1]
        if base_name in param_in:
            return True, param_in[base_name]
        for api, idx in param_in.items():
            if function_name.endswith("::" + api):
                return True, idx
        return False, -1

    # ── func_map API ──
    def resolve_func_ptr(self, dotted_path: str) -> List[str]:
        func_map = self.load()["func_map"]
        if dotted_path in func_map:
            targets = func_map[dotted_path]
            return targets if isinstance(targets, list) else [targets]
        return []

    def is_func_ptr_call(self, obj: str, field: str) -> Tuple[bool, List[str]]:
        func_map = self.load()["func_map"]
        for path, targets in func_map.items():
            parts = path.split(".")
            if len(parts) >= 2:
                if parts[-1] == field and ".".join(parts[:-1]).endswith(obj):
                    t = targets if isinstance(targets, list) else [targets]
                    return True, t
                if path == f"{obj}.{field}":
                    t = targets if isinstance(targets, list) else [targets]
                    return True, t
        return False, []

    # ── ioctl_map API ──
    def get_ioctl_format(self) -> str:
        return self.load()["ioctl_map"].get("format", "")

    def resolve_ioctl(self, command: str) -> Optional[str]:
        commands = self.load()["ioctl_map"].get("commands", {})
        return commands.get(command)

    def parse_ioctl_format(self) -> Optional[re.Pattern]:
        """Convert ioctl format string to a compiled regex pattern."""
        fmt = self.get_ioctl_format()
        if not fmt:
            return None
        # Escape regex specials, then replace * with capture group
        escaped = re.escape(fmt)
        # re.escape turns * into \*, so we replace escaped * back to .*
        pattern_str = escaped.replace(r"\*", r"(.+?)")
        return re.compile(pattern_str)


# Module-level singleton
_instance: Optional[CallbackConfig] = None


def _get_config(config_path: Optional[str] = None) -> CallbackConfig:
    global _instance
    if _instance is None or config_path is not None:
        _instance = CallbackConfig(config_path)
    return _instance


# ── Backward-compatible module-level functions ──
def is_callback_api(function_name: str, config_path: Optional[str] = None) -> Tuple[bool, int]:
    return _get_config(config_path).is_callback_api(function_name)

def get_callback_apis(config_path: Optional[str] = None) -> Dict[str, int]:
    return _get_config(config_path).load()["param_in"]

def get_all_callback_apis(config_path: Optional[str] = None) -> List[str]:
    return list(_get_config(config_path).load()["param_in"].keys())

def resolve_func_ptr(dotted_path: str, config_path: Optional[str] = None) -> List[str]:
    return _get_config(config_path).resolve_func_ptr(dotted_path)

def resolve_ioctl(command: str, config_path: Optional[str] = None) -> Optional[str]:
    return _get_config(config_path).resolve_ioctl(command)

def get_ioctl_format(config_path: Optional[str] = None) -> str:
    return _get_config(config_path).get_ioctl_format()
