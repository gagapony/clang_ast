"""Callback configuration loader (TOML-based)."""

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
        base = os.path.join(os.path.dirname(__file__), "..")
        toml_path = os.path.join(base, "callback.toml")
        if os.path.exists(toml_path):
            return toml_path
        return None

    def load(self) -> dict:
        if self._data is not None:
            return self._data

        path = self._resolve_path()
        if path is None or not os.path.exists(path):
            self._data = {"param_in": {}, "func_map": {}, "ioctl_map": {"format": ""}}
            return self._data

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
        func_map_nested = self._normalize_func_map(func_map)

        ioctl_raw = raw.get("ioctl_map", {})
        ioctl_format = ioctl_raw.get("format", "")
        ioctl_path = ioctl_raw.get("path", "")
        ioctl_commands = {k: v for k, v in ioctl_raw.items() if k not in ("format", "path")}

        return {
            "param_in": {str(k): list(v) if isinstance(v, list) else [int(v)] for k, v in param_in.items()},
            "func_map": func_map_nested,
            "ioctl_map": {"format": ioctl_format, "path": ioctl_path, "commands": ioctl_commands},
        }

    @staticmethod
    def _normalize_func_map(func_map: dict) -> dict:
        """
        Normalize func_map to flat structure: {expression: {"targets": [...], "search_dir": "..."}}
        TOML structure:
            [func_map.genbin]
            path = "sdk/interface/src/ldc/wrapper/src"
            "pC->Init" = ["LDC_EPTZ_Init", ...]
            g_stWrapIntf.stWrapLdc.SetLDCAttr = ["LDC_WRAPPER_LDC_SetChnLDCAttr"]
        Parsed by tomllib as:
            {"genbin": {"path": "...", "pC->Init": [...],
                        "g_stWrapIntf": {"stWrapLdc": {"SetLDCAttr": [...]}}}}
        We flatten dotted keys and extract search_dir + expression entries.
        """
        result = {}

        def _flatten_entries(entries: dict, prefix: str = "") -> dict:
            """Flatten nested TOML keys (dotted keys become flat with dot separator)."""
            flat = {}
            for key, value in entries.items():
                full_key = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict):
                    flat.update(_flatten_entries(value, full_key))
                else:
                    flat[full_key] = value
            return flat

        def _process_group(group: dict) -> None:
            """Extract search_dir and expression entries from a func_map group."""
            search_dir = ""
            raw_entries = {}

            for k, v in group.items():
                if k == "path" and isinstance(v, str):
                    search_dir = v
                else:
                    raw_entries[k] = v

            # Flatten dotted keys (e.g., g_stWrapIntf.stWrapLdc.SetLDCAttr)
            flat_entries = _flatten_entries(raw_entries)

            for expr, targets in flat_entries.items():
                if isinstance(targets, list):
                    result[expr] = {
                        "targets": targets,
                        "search_dir": search_dir
                    }

        for key, value in func_map.items():
            if isinstance(value, dict):
                if "path" in value:
                    # This is a func_map group
                    _process_group(value)
                else:
                    # Nested TOML key, check deeper
                    for k2, v2 in value.items():
                        if isinstance(v2, dict) and "path" in v2:
                            _process_group(v2)

        return result


    # ── param_in API ──
    def is_callback_api(self, function_name: str) -> Tuple[bool, List[int]]:
        param_in = self.load()["param_in"]
        base_name = function_name.split("::")[-1]
        if base_name in param_in:
            return True, param_in[base_name]
        for api, indices in param_in.items():
            if function_name.endswith("::" + api):
                return True, indices
        return False, []

    # ── func_map API ──
    def resolve_func_ptr(self, expression: str) -> List[str]:
        """
        Resolve a function pointer call expression to concrete targets.
        """
        func_map = self.load()["func_map"]
        if expression in func_map:
            return func_map[expression].get("targets", [])
        return []

    def get_func_ptr_search_dir(self, expression: str) -> str:
        """
        Get the search directory hint for a function pointer expression.
        """
        func_map = self.load()["func_map"]
        if expression in func_map:
            return func_map[expression].get("search_dir", "")
        return ""

    def get_all_func_ptr_entries(self) -> dict:
        """
        Get all func_map entries as {expression: {"targets": [...], "search_dir": "..."}}.
        """
        return self.load()["func_map"]

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
def is_callback_api(function_name: str, config_path: Optional[str] = None) -> Tuple[bool, List[int]]:
    return _get_config(config_path).is_callback_api(function_name)

def get_callback_apis(config_path: Optional[str] = None) -> Dict[str, List[int]]:
    return _get_config(config_path).load()["param_in"]

def get_all_callback_apis(config_path: Optional[str] = None) -> List[str]:
    return list(_get_config(config_path).load()["param_in"].keys())

def resolve_func_ptr(expression: str, config_path: Optional[str] = None) -> List[str]:
    return _get_config(config_path).resolve_func_ptr(expression)

def resolve_ioctl(command: str, config_path: Optional[str] = None) -> Optional[str]:
    return _get_config(config_path).resolve_ioctl(command)

def get_ioctl_format(config_path: Optional[str] = None) -> str:
    return _get_config(config_path).get_ioctl_format()
