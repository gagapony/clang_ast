"""
Filter Configuration Manager

Loads and parses filter.cfg configuration for scope control.
"""

import os
from dataclasses import dataclass
from typing import List, Optional
import fnmatch


@dataclass
class FilterRule:
    """Represents a single filter rule."""
    action: str  # '+' or '-'
    pattern: str  # Path pattern

    def matches(self, path: str) -> bool:
        """
        Check if path matches this rule.

        Supports:
        - Directory prefixes: src/ or src (both match src/foo.c)
        - Exact paths: src/main.cpp
        - Wildcards: *.cpp, test_*.c
        """
        norm_path = path.replace(os.sep, '/')
        norm_pattern = self.pattern.replace(os.sep, '/')

        # Directory prefix match: pattern ends with / OR path starts with pattern/
        if norm_pattern.endswith('/'):
            return norm_path.startswith(norm_pattern) or norm_path == norm_pattern.rstrip('/')

        # If no wildcards, try prefix match (treat as directory)
        if '*' not in norm_pattern and '?' not in norm_pattern:
            if norm_path.startswith(norm_pattern + '/') or norm_path == norm_pattern:
                return True

        # Exact or wildcard match
        return fnmatch.fnmatch(norm_path, norm_pattern) or norm_path == norm_pattern


class FilterConfig:
    """Represents filter configuration."""

    def __init__(self, rules: List[FilterRule]):
        self.rules = rules

    def should_include(self, path: str) -> bool:
        """
        Check if path should be included based on filter rules.

        Returns True if included, False if excluded.
        First match wins (rules are processed in order).

        If include rules (+) exist and no rule matches: exclude (False).
        If only exclude rules (-) exist and no rule matches: include (True).
        If no rules: include all (True).
        """
        for rule in self.rules:
            if rule.matches(path):
                return rule.action == '+'

        # No rule matched: check if include rules exist
        has_include_rules = any(r.action == '+' for r in self.rules)
        return not has_include_rules

    @staticmethod
    def from_file(file_path: str) -> 'FilterConfig':
        """
        Load filter configuration from file.

        File format:
        # Comment
        +pattern  # Include pattern
        -pattern  # Exclude pattern

        Args:
            file_path: Path to filter configuration file

        Returns:
            FilterConfig instance

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file format is invalid
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Filter configuration not found: {file_path}")

        rules = []

        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()

                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue

                # Parse rule
                if line[0] not in ('+', '-'):
                    raise ValueError(
                        f"Invalid filter rule at line {line_num}: {line}\n"
                        f"Rules must start with '+' or '-'"
                    )

                action = line[0]
                pattern = line[1:].strip()

                if not pattern:
                    raise ValueError(
                        f"Empty pattern at line {line_num}: {line}\n"
                        f"Pattern cannot be empty after action character"
                    )

                rules.append(FilterRule(action=action, pattern=pattern))

        return FilterConfig(rules)

    @staticmethod
    def default() -> 'FilterConfig':
        """
        Create default filter configuration (include all paths).

        Returns:
            FilterConfig with no rules (includes all paths)
        """
        return FilterConfig(rules=[])


def load_filter_config(config_path: Optional[str] = None) -> FilterConfig:
    """
    Load filter configuration from file or use default.

    Args:
        config_path: Path to filter configuration file, or None for default

    Returns:
        FilterConfig instance
    """
    if config_path is None:
        return FilterConfig.default()

    return FilterConfig.from_file(config_path)
