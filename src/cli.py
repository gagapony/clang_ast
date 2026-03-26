"""
CLI Parser - Unified Version

Command-line interface for Clangd Call Graph Visualizer.
Unified entry point: supports both position-based and function-name-based modes.
"""

import argparse
import sys
import os
import re
from typing import Optional

from .config import load_filter_config
from .validator import validate_project_structure


def parse_entry_point(entry_str: str) -> tuple:
    """
    Parse entry point string.

    Supports:
    - Position mode: "file.cpp:line:character" (e.g., "src/main.cpp:191:5")
    - Function mode: "function_name" (e.g., "setup")

    Returns:
        (mode, data) where mode is 'position' or 'function'
        - For 'position': data is (file_path, line, character)
        - For 'function': data is function_name
    """
    # Check if it's a position format (file:line:character)
    if ':' in entry_str:
        try:
            # Split from right: last = character, second-to-last = line, rest = file
            parts = entry_str.rsplit(':', 2)
            if len(parts) == 3:
                file_path = parts[0].strip()
                line = int(parts[1].strip())
                character = int(parts[2].strip())
                if line >= 0 and character >= 0:
                    return 'position', (file_path, line, character)
        except (ValueError, IndexError):
            pass

    # Default: function name mode
    return 'function', entry_str.strip()


def parse_args() -> argparse.Namespace:
    """
    Parse and validate command-line arguments.

    Returns:
        Parsed arguments namespace
    """
    parser = argparse.ArgumentParser(
        prog='clangd-call-tree',
        description='Generate function call graph from C/C++ projects using Clangd',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Position mode (file:line:character)
  python main.py -p /path/to/project -e "src/main.cpp:191:5"

  # Function name mode
  python main.py -p /path/to/project -e "setup"

  # With custom filter and depth
  python main.py -p /path/to/project -e "setup" -c filter.cfg -d 3

  # JSON output
  python main.py -p /path/to/project -e "setup" -f json -o call_graph.json
        """
    )

    parser.add_argument(
        '-p', '--path',
        type=str,
        default='.',
        help='Project directory (must contain compile_commands.json) (default: current directory)'
    )

    parser.add_argument(
        '-e', '--entry',
        type=str,
        required=True,
        help='Entry point - either "file.cpp:line:char" or "function_name"'
    )

    parser.add_argument(
        '-c', '--config',
        type=str,
        default='filter.cfg',
        help='Filter configuration file - defines scope (default: filter.cfg)'
    )

    parser.add_argument(
        '--callback-config',
        type=str,
        default=None,
        help='Callback API configuration file (default: callback.cfg in tool directory)'
    )

    parser.add_argument(
        '-d', '--max-depth',
        type=int,
        default=10,
        help='Maximum recursion depth (default: 10)'
    )

    parser.add_argument(
        '-m', '--max-nodes',
        type=int,
        default=10000,
        help='Maximum nodes to process (default: 10000)'
    )

    parser.add_argument(
        '-f', '--format',
        type=str,
        choices=['text', 'json', 'all'],
        default='text',
        help='Output format: text, json, or all (both, requires -o) (default: text)'
    )

    parser.add_argument(
        '-o', '--output',
        type=str,
        default=None,
        help='Output file path (default: stdout)'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    return parser.parse_args()


def validate_arguments(args: argparse.Namespace) -> int:
    """
    Validate command-line arguments.

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    # Parse entry point
    args.entry_mode, args.entry_data = parse_entry_point(args.entry)

    # Validate based on mode
    if args.entry_mode == 'position':
        file_path, line, character = args.entry_data
        project_path = os.path.abspath(args.path)

        # Resolve file path: try relative to project path first, then CWD, then absolute
        if os.path.isabs(file_path):
            resolved = file_path
        else:
            resolved = os.path.join(project_path, file_path)

        if not os.path.isfile(resolved):
            resolved = os.path.abspath(file_path)

        if not os.path.isfile(resolved):
            print(f"Error: File not found: {file_path}", file=sys.stderr)
            print(f"  Tried: {os.path.join(project_path, file_path)}", file=sys.stderr)
            return 1

        args.entry_data = (os.path.abspath(resolved), line, character)

        if line < 0:
            print(f"Error: Line number must be non-negative: {line}", file=sys.stderr)
            return 1

        if character < 0:
            print(f"Error: Character position must be non-negative: {character}", file=sys.stderr)
            return 1
    else:
        # Function mode - validate function name format
        function_name = args.entry_data
        if not re.match(r'^[a-zA-Z_]\w*$', function_name):
            print(f"Warning: Function name '{function_name}' may not be valid C/C++ identifier", file=sys.stderr)

    # Validate project structure
    result = validate_project_structure(args.path, args.config)
    if not result.is_valid:
        print(f"Error: {result.message}", file=sys.stderr)
        return 1

    # Validate max depth
    if args.max_depth <= 0:
        print("Error: max-depth must be positive", file=sys.stderr)
        return 1

    # Validate max nodes
    if args.max_nodes <= 0:
        print("Error: max-nodes must be positive", file=sys.stderr)
        return 1

    return 0


def main() -> int:
    """
    Main entry point for CLI.

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    args = parse_args()

    # Validate arguments
    exit_code = validate_arguments(args)
    if exit_code != 0:
        return exit_code

    # Print configuration
    if args.verbose:
        print(f"Project: {os.path.abspath(args.path)}")
        print(f"Entry mode: {args.entry_mode}")
        if args.entry_mode == 'position':
            file_path, line, character = args.entry_data
            print(f"Entry point: {file_path}:{line}:{character}")
        else:
            print(f"Entry function: {args.entry_data}")
        print(f"Config: {args.config}")
        print(f"Output: {args.output or 'stdout'}")
        print(f"Format: {args.format}")
        print(f"Max depth: {args.max_depth}")
        print(f"Max nodes: {args.max_nodes}")
        print(f"Verbose: {args.verbose}")
        print()

    # Import modules
    try:
        from .clangd_client import ClangdClient
        from .call_graph_builder import CallGraphBuilder
    except ImportError as e:
        print(f"Error: Failed to import required modules: {e}", file=sys.stderr)
        return 1

    # Load filter configuration
    try:
        filter_config = load_filter_config(args.config)
    except Exception as e:
        print(f"Error loading filter config: {e}", file=sys.stderr)
        return 1

    # Initialize Clangd client
    project_path = os.path.abspath(args.path)
    try:
        client = ClangdClient(
            project_path=project_path,
            clangd_path="clangd",
            timeout=30.0,
            verbose=args.verbose
        )
        client.start()
        client.initialize()
    except Exception as e:
        print(f"Error initializing Clangd: {e}", file=sys.stderr)
        print("\nPlease ensure Clangd is installed and compile_commands.json exists.", file=sys.stderr)
        return 1

    try:
        # Initialize builder
        builder = CallGraphBuilder(
            clangd_client=client,
            filter_config=filter_config,
            project_path=project_path,
            max_depth=args.max_depth,
            max_nodes=args.max_nodes,
            verbose=args.verbose,
            callback_config=args.callback_config
        )

        # Resolve entry point
        if args.entry_mode == 'position':
            # Direct position
            file_path, line, character = args.entry_data
            root_id = builder.build(file_path, line, character)
        else:
            # Function name - resolve to position
            function_name = args.entry_data
            root_id = builder.build_from_function_name(function_name)

        if root_id is None:
            print(f"Error: Failed to build call graph from entry point", file=sys.stderr)
            return 1

        # Generate output
        text_output = builder.to_tree_text(root_id, max_display_depth=args.max_depth)
        json_output = builder.to_json()

        # Print stats
        stats = builder.get_stats()
        if args.verbose:
            print(f"\nGraph statistics:")
            print(f"  Total nodes: {stats['total_nodes']}")
            print(f"  Total edges: {stats['total_edges']}")
            print(f"  External nodes: {stats['external_nodes']}")
            print(f"  Opened files: {stats['opened_files']}")
            print()

        # Write output
        if args.format == 'all':
            if not args.output:
                print("Error: -f all requires -o to specify output base name", file=sys.stderr)
                return 1
            for ext, content in [('.txt', text_output), ('.json', json_output)]:
                out_path = args.output + ext
                try:
                    with open(out_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    if args.verbose:
                        print(f"Output written to: {out_path}")
                except IOError as e:
                    print(f"Error writing {out_path}: {e}", file=sys.stderr)
                    return 1
        elif args.output:
            output = json_output if args.format == 'json' else text_output
            try:
                with open(args.output, 'w', encoding='utf-8') as f:
                    f.write(output)
                if args.verbose:
                    print(f"Output written to: {args.output}")
            except IOError as e:
                print(f"Error writing output file: {e}", file=sys.stderr)
                return 1
        else:
            print(json_output if args.format == 'json' else text_output)

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

    finally:
        # Clean up Clangd
        try:
            client.stop()
        except Exception:
            pass


if __name__ == '__main__':
    sys.exit(main())
