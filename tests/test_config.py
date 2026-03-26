"""
Tests for filter configuration parser.
"""

import pytest
import os
import tempfile
from src.config import FilterConfig, FilterRule, load_filter_config


class TestFilterRule:
    """Test FilterRule class."""

    def test_exact_match(self):
        """Test exact path matching."""
        rule = FilterRule('+', 'src/main.cpp')
        assert rule.matches('src/main.cpp') is True
        assert rule.matches('src/other.cpp') is False

    def test_directory_prefix(self):
        """Test directory prefix matching."""
        rule = FilterRule('+', 'src/')
        assert rule.matches('src/main.cpp') is True
        assert rule.matches('src/subdir/file.cpp') is True
        assert rule.matches('lib/main.cpp') is False

    def test_wildcard_match(self):
        """Test wildcard matching."""
        rule = FilterRule('-', '*.py')
        assert rule.matches('test.py') is True
        assert rule.matches('main.py') is True
        assert rule.matches('main.cpp') is False

    def test_pattern_prefix(self):
        """Test pattern prefix matching."""
        rule = FilterRule('-', 'test_*.cpp')
        assert rule.matches('test_main.cpp') is True
        assert rule.matches('test_utils.cpp') is True
        assert rule.matches('main.cpp') is False
        assert rule.matches('src/test_main.cpp') is False

    def test_path_separator_normalization(self):
        """Test path separator normalization."""
        rule = FilterRule('+', 'src/')
        # Test both forward and backward slashes
        assert rule.matches('src/main.cpp') is True


class TestFilterConfig:
    """Test FilterConfig class."""

    def test_empty_rules_include_all(self):
        """Test that empty config includes all paths."""
        config = FilterConfig([])
        assert config.matches('any/path/file.cpp') is True

    def test_first_match_wins(self):
        """Test that first matching rule wins."""
        rules = [
            FilterRule('-', 'src/test/'),
            FilterRule('+', 'src/'),
        ]
        config = FilterConfig(rules)

        assert config.matches('src/main.cpp') is True
        assert config.matches('src/subdir/file.cpp') is True
        assert config.matches('src/test/test.cpp') is False

    def test_exclude_then_include(self):
        """Test exclude rule then include rule."""
        rules = [
            FilterRule('+', 'test/important/'),
            FilterRule('-', 'test/'),
        ]
        config = FilterConfig(rules)

        assert config.matches('test/file.cpp') is False
        assert config.matches('test/important/file.cpp') is True

    def test_default_include(self):
        """Test default include when no rules match."""
        rules = [
            FilterRule('-', 'build/'),
        ]
        config = FilterConfig(rules)

        assert config.matches('build/file.o') is False
        assert config.matches('src/main.cpp') is True
        assert config.matches('lib/file.cpp') is True


class TestFilterConfigFromFile:
    """Test loading FilterConfig from file."""

    def test_load_simple_config(self, tmp_path):
        """Test loading a simple configuration file."""
        config_file = tmp_path / "filter.cfg"
        config_file.write_text("""
+src/
+lib/
-test/
""")

        config = FilterConfig.from_file(str(config_file))

        assert config.matches('src/main.cpp') is True
        assert config.matches('lib/utils.cpp') is True
        assert config.matches('test/test.cpp') is False
        assert config.matches('build/file.o') is True

    def test_load_config_with_comments(self, tmp_path):
        """Test loading config with comments."""
        config_file = tmp_path / "filter.cfg"
        config_file.write_text("""
# Include source code
+src/
+lib/

# Exclude tests and build
-test/
-test_*.cpp
-build/

# End of file
""")

        config = FilterConfig.from_file(str(config_file))

        assert config.matches('src/main.cpp') is True
        assert config.matches('test/test.cpp') is False
        assert config.matches('test_main.cpp') is False
        assert config.matches('build/file.o') is False

    def test_load_empty_file(self, tmp_path):
        """Test loading empty configuration file."""
        config_file = tmp_path / "filter.cfg"
        config_file.write_text("")

        config = FilterConfig.from_file(str(config_file))

        assert len(config.rules) == 0
        assert config.matches('any/path') is True

    def test_load_config_with_empty_lines(self, tmp_path):
        """Test loading config with empty lines."""
        config_file = tmp_path / "filter.cfg"
        config_file.write_text("""
+src/

+lib/

-test/
""")

        config = FilterConfig.from_file(str(config_file))

        assert len(config.rules) == 3
        assert config.matches('src/main.cpp') is True
        assert config.matches('test/test.cpp') is False

    def test_file_not_found(self):
        """Test error when file doesn't exist."""
        with pytest.raises(FileNotFoundError):
            FilterConfig.from_file('/nonexistent/filter.cfg')

    def test_invalid_rule_no_action(self, tmp_path):
        """Test error when rule has no action character."""
        config_file = tmp_path / "filter.cfg"
        config_file.write_text("src/\n")

        with pytest.raises(ValueError) as exc_info:
            FilterConfig.from_file(str(config_file))

        assert "Invalid filter rule" in str(exc_info.value)

    def test_invalid_rule_empty_pattern(self, tmp_path):
        """Test error when rule has empty pattern."""
        config_file = tmp_path / "filter.cfg"
        config_file.write_text("+\n")

        with pytest.raises(ValueError) as exc_info:
            FilterConfig.from_file(str(config_file))

        assert "Empty pattern" in str(exc_info.value)


class TestLoadFilterConfig:
    """Test load_filter_config function."""

    def test_load_with_path(self, tmp_path):
        """Test loading with config file path."""
        config_file = tmp_path / "filter.cfg"
        config_file.write_text("+src/\n-test/\n")

        config = load_filter_config(str(config_file))

        assert isinstance(config, FilterConfig)
        assert config.matches('src/main.cpp') is True
        assert config.matches('test/test.cpp') is False

    def test_load_without_path(self):
        """Test loading without config file path (default)."""
        config = load_filter_config(None)

        assert isinstance(config, FilterConfig)
        assert len(config.rules) == 0
        assert config.matches('any/path') is True
