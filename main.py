#!/usr/bin/env python3
"""
Clangd Call Stack Tree Visualizer - CLI Entry Point

Main entry point for generating function call stack trees from C/C++ projects
using Clangd's JSON-RPC protocol.
"""

import sys
from src.cli import main as cli_main

if __name__ == "__main__":
    sys.exit(cli_main())
