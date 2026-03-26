"""
Input Validator

Validates project structure and entry point function.
"""

import os
from dataclasses import dataclass
from typing import Optional
import json


@dataclass
class ValidationResult:
    """Encapsulates validation result."""
    is_valid: bool
    message: str

    @staticmethod
    def success() -> 'ValidationResult':
        """Create a successful validation result."""
        return ValidationResult(is_valid=True, message="")

    @staticmethod
    def failure(message: str) -> 'ValidationResult':
        """Create a failed validation result."""
        return ValidationResult(is_valid=False, message=message)


@dataclass
class Location:
    """Represents a source code location."""
    file_path: str
    line_number: int


def validate_project_path(project_path: str) -> ValidationResult:
    """
    Validate that project path exists and is a directory.

    Args:
        project_path: Path to project directory

    Returns:
        ValidationResult with validation status
    """
    if not project_path:
        return ValidationResult.failure("Project path cannot be empty")

    abs_path = os.path.abspath(project_path)

    if not os.path.exists(abs_path):
        return ValidationResult.failure(
            f"Project path does not exist: {abs_path}"
        )

    if not os.path.isdir(abs_path):
        return ValidationResult.failure(
            f"Project path is not a directory: {abs_path}"
        )

    return ValidationResult.success()


def validate_compile_commands(project_path: str) -> ValidationResult:
    """
    Validate that compile_commands.json exists in project root.

    Args:
        project_path: Path to project directory

    Returns:
        ValidationResult with validation status
    """
    compile_db_path = os.path.join(project_path, "compile_commands.json")

    if not os.path.exists(compile_db_path):
        return ValidationResult.failure(
            f"compile_commands.json not found in project root: {project_path}\n"
            f"Expected location: {compile_db_path}"
        )

    # Try to parse JSON to ensure it's valid
    try:
        with open(compile_db_path, 'r', encoding='utf-8') as f:
            json.load(f)
    except json.JSONDecodeError as e:
        return ValidationResult.failure(
            f"Invalid JSON in compile_commands.json: {e}"
        )

    return ValidationResult.success()


def validate_cache_directory(project_path: str) -> ValidationResult:
    """
    Validate that .cache/ directory exists (Clangd cache).

    Args:
        project_path: Path to project directory

    Returns:
        ValidationResult with validation status
    """
    cache_path = os.path.join(project_path, ".cache")

    if not os.path.exists(cache_path):
        return ValidationResult.failure(
            f".cache directory not found in project root: {project_path}\n"
            f"Expected location: {cache_path}\n"
            f"Hint: Run Clangd on the project first to generate cache"
        )

    if not os.path.isdir(cache_path):
        return ValidationResult.failure(
            f".cache is not a directory: {cache_path}"
        )

    return ValidationResult.success()


def validate_filter_config(config_path: Optional[str] = None) -> ValidationResult:
    """
    Validate that filter configuration file exists and is readable.

    Args:
        config_path: Path to filter configuration file, or None for default

    Returns:
        ValidationResult with validation status
    """
    if config_path is None:
        # Default config is optional
        return ValidationResult.success()

    if not os.path.exists(config_path):
        return ValidationResult.failure(
            f"Filter configuration not found: {config_path}"
        )

    if not os.path.isfile(config_path):
        return ValidationResult.failure(
            f"Filter configuration is not a file: {config_path}"
        )

    # Try to read the file
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            pass
    except IOError as e:
        return ValidationResult.failure(
            f"Cannot read filter configuration: {config_path}\n"
            f"Error: {e}"
        )

    return ValidationResult.success()


def validate_function_name(function_name: str) -> ValidationResult:
    """
    Validate that function name is provided and non-empty.

    Args:
        function_name: Function name to validate

    Returns:
        ValidationResult with validation status
    """
    if not function_name:
        return ValidationResult.failure(
            "Function name cannot be empty. Use -f/--function to specify entry point."
        )

    if not function_name.strip():
        return ValidationResult.failure(
            "Function name cannot be whitespace only."
        )

    return ValidationResult.success()


def validate_project_structure(
    project_path: str,
    config_path: Optional[str] = None
) -> ValidationResult:
    """
    Validate complete project structure.

    Args:
        project_path: Path to project directory
        config_path: Path to filter configuration file, or None

    Returns:
        ValidationResult with validation status
    """
    # Validate project path
    result = validate_project_path(project_path)
    if not result.is_valid:
        return result

    abs_project_path = os.path.abspath(project_path)

    # Validate compile_commands.json
    result = validate_compile_commands(abs_project_path)
    if not result.is_valid:
        return result

    # Validate .cache directory
    result = validate_cache_directory(abs_project_path)
    if not result.is_valid:
        return result

    # Validate filter config (optional)
    result = validate_filter_config(config_path)
    if not result.is_valid:
        return result

    return ValidationResult.success()
