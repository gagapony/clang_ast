"""
Tests for input validator.
"""

import pytest
import os
import tempfile
import json
from src.validator import (
    validate_project_path,
    validate_compile_commands,
    validate_cache_directory,
    validate_filter_config,
    validate_function_name,
    validate_scope_root,
    validate_project_structure,
    ValidationResult,
    Location
)


class TestValidationResult:
    """Test ValidationResult class."""

    def test_success(self):
        """Test creating successful result."""
        result = ValidationResult.success()
        assert result.is_valid is True
        assert result.message == ""

    def test_failure(self):
        """Test creating failed result."""
        result = ValidationResult.failure("Test error")
        assert result.is_valid is False
        assert result.message == "Test error"


class TestLocation:
    """Test Location dataclass."""

    def test_location_creation(self):
        """Test creating location."""
        loc = Location("/path/to/file.cpp", 42)
        assert loc.file_path == "/path/to/file.cpp"
        assert loc.line_number == 42


class TestValidateProjectPath:
    """Test validate_project_path function."""

    def test_valid_directory(self, tmp_path):
        """Test validation with valid directory."""
        result = validate_project_path(str(tmp_path))
        assert result.is_valid is True

    def test_nonexistent_path(self):
        """Test validation with nonexistent path."""
        result = validate_project_path("/nonexistent/path")
        assert result.is_valid is False
        assert "does not exist" in result.message

    def test_empty_path(self):
        """Test validation with empty path."""
        result = validate_project_path("")
        assert result.is_valid is False
        assert "cannot be empty" in result.message

    def test_file_instead_of_directory(self, tmp_path):
        """Test validation with file instead of directory."""
        file_path = tmp_path / "test.txt"
        file_path.write_text("test")

        result = validate_project_path(str(file_path))
        assert result.is_valid is False
        assert "not a directory" in result.message


class TestValidateCompileCommands:
    """Test validate_compile_commands function."""

    def test_valid_compile_commands(self, tmp_path):
        """Test validation with valid compile_commands.json."""
        compile_db = tmp_path / "compile_commands.json"
        compile_db.write_text(json.dumps([{
            "directory": "/path/to/project",
            "command": "gcc -c main.cpp",
            "file": "main.cpp"
        }]))

        result = validate_compile_commands(str(tmp_path))
        assert result.is_valid is True

    def test_missing_compile_commands(self, tmp_path):
        """Test validation when compile_commands.json is missing."""
        result = validate_compile_commands(str(tmp_path))
        assert result.is_valid is False
        assert "not found" in result.message

    def test_invalid_json(self, tmp_path):
        """Test validation with invalid JSON."""
        compile_db = tmp_path / "compile_commands.json"
        compile_db.write_text("invalid json")

        result = validate_compile_commands(str(tmp_path))
        assert result.is_valid is False
        assert "Invalid JSON" in result.message


class TestValidateCacheDirectory:
    """Test validate_cache_directory function."""

    def test_valid_cache_directory(self, tmp_path):
        """Test validation with valid .cache directory."""
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir()

        result = validate_cache_directory(str(tmp_path))
        assert result.is_valid is True

    def test_missing_cache_directory(self, tmp_path):
        """Test validation when .cache directory is missing."""
        result = validate_cache_directory(str(tmp_path))
        assert result.is_valid is False
        assert "not found" in result.message

    def test_cache_is_file(self, tmp_path):
        """Test validation when .cache is a file instead of directory."""
        cache_file = tmp_path / ".cache"
        cache_file.write_text("test")

        result = validate_cache_directory(str(tmp_path))
        assert result.is_valid is False
        assert "not a directory" in result.message


class TestValidateFilterConfig:
    """Test validate_filter_config function."""

    def test_valid_filter_config(self, tmp_path):
        """Test validation with valid filter config."""
        config_file = tmp_path / "filter.cfg"
        config_file.write_text("+src/\n-test/\n")

        result = validate_filter_config(str(config_file))
        assert result.is_valid is True

    def test_missing_filter_config(self, tmp_path):
        """Test validation when filter config is missing."""
        result = validate_filter_config(str(tmp_path / "nonexistent.cfg"))
        assert result.is_valid is False
        assert "not found" in result.message

    def test_filter_config_is_directory(self, tmp_path):
        """Test validation when filter config path is a directory."""
        config_dir = tmp_path / "filter.cfg"
        config_dir.mkdir()

        result = validate_filter_config(str(config_dir))
        assert result.is_valid is False
        assert "not a file" in result.message

    def test_none_filter_config(self):
        """Test validation with None (optional config)."""
        result = validate_filter_config(None)
        assert result.is_valid is True


class TestValidateFunctionName:
    """Test validate_function_name function."""

    def test_valid_function_name(self):
        """Test validation with valid function name."""
        result = validate_function_name("main")
        assert result.is_valid is True

    def test_qualified_function_name(self):
        """Test validation with qualified function name."""
        result = validate_function_name("Namespace::Class::method")
        assert result.is_valid is True

    def test_empty_function_name(self):
        """Test validation with empty function name."""
        result = validate_function_name("")
        assert result.is_valid is False
        assert "cannot be empty" in result.message

    def test_whitespace_function_name(self):
        """Test validation with whitespace function name."""
        result = validate_function_name("   ")
        assert result.is_valid is False
        assert "whitespace" in result.message


class TestValidateScopeRoot:
    """Test validate_scope_root function."""

    def test_none_scope_root(self, tmp_path):
        """Test validation with None (default to project root)."""
        scope_root = validate_scope_root(str(tmp_path), None)
        assert scope_root == str(tmp_path)

    def test_relative_scope_root(self, tmp_path):
        """Test validation with relative scope root."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        scope_root = validate_scope_root(str(tmp_path), "src")
        assert scope_root == str(src_dir)

    def test_absolute_scope_root(self, tmp_path):
        """Test validation with absolute scope root."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        scope_root = validate_scope_root(str(tmp_path), str(src_dir))
        assert scope_root == str(src_dir)

    def test_nonexistent_scope_root(self, tmp_path):
        """Test validation with nonexistent scope root."""
        with pytest.raises(ValueError) as exc_info:
            validate_scope_root(str(tmp_path), "nonexistent")

        assert "does not exist" in str(exc_info.value)

    def test_file_instead_of_directory(self, tmp_path):
        """Test validation with file instead of directory."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test")

        with pytest.raises(ValueError) as exc_info:
            validate_scope_root(str(tmp_path), "test.txt")

        assert "not a directory" in str(exc_info.value)

    def test_scope_root_outside_project(self, tmp_path):
        """Test validation when scope root is outside project."""
        with pytest.raises(ValueError) as exc_info:
            validate_scope_root(str(tmp_path), "/")

        assert "must be within project" in str(exc_info.value)


class TestValidateProjectStructure:
    """Test validate_project_structure function."""

    def test_valid_project_structure(self, tmp_path):
        """Test validation with valid project structure."""
        # Create compile_commands.json
        compile_db = tmp_path / "compile_commands.json"
        compile_db.write_text(json.dumps([{
            "directory": str(tmp_path),
            "command": "gcc -c main.cpp",
            "file": "main.cpp"
        }]))

        # Create .cache directory
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir()

        result = validate_project_structure(str(tmp_path))
        assert result.is_valid is True

    def test_invalid_project_path(self):
        """Test validation with invalid project path."""
        result = validate_project_structure("/nonexistent/project")
        assert result.is_valid is False

    def test_missing_compile_commands(self, tmp_path):
        """Test validation with missing compile_commands.json."""
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir()

        result = validate_project_structure(str(tmp_path))
        assert result.is_valid is False

    def test_missing_cache_directory(self, tmp_path):
        """Test validation with missing .cache directory."""
        compile_db = tmp_path / "compile_commands.json"
        compile_db.write_text(json.dumps([{
            "directory": str(tmp_path),
            "command": "gcc -c main.cpp",
            "file": "main.cpp"
        }]))

        result = validate_project_structure(str(tmp_path))
        assert result.is_valid is False
