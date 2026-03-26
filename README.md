# Clangd Call Graph Generator

Generate function call graphs from C/C++ projects using Clangd language server.

## Features

- **Unified Entry Point**: Supports both position-based and function-name-based modes
- **Bidirectional Traversal**: Find both callers (upstream) and callees (downstream)
- **Multiple Output Formats**: Text (indented tree) and JSON (adjacency list)
- **On-Demand Loading**: No preloading - files opened as needed
- **Scope Control**: Limit analysis to specific directories
- **Filter Support**: Exclude/include files based on patterns

## Installation

```bash
# Install Clangd
sudo apt-get install clangd

# Or via LLVM
# https://clangd.llvm.org/installation
```

## Usage

### Position Mode (file:line:character)

```bash
python main.py -p /path/to/project -e "src/main.cpp:191:5"
```

### Function Name Mode

```bash
python main.py -p /path/to/project -e "setup"
```

### Common Options

```bash
# Specify scope directory
python main.py -p /path/to/project -e "setup" -s src/

# Set depth limit
python main.py -p /path/to/project -e "setup" -d 3

# JSON output
python main.py -p /path/to/project -e "setup" -f json -o call_graph.json

# Verbose output
python main.py -p /path/to/project -e "setup" -v
```

### All Options

```
-p, --path PATH        Project directory (must contain compile_commands.json)
-e, --entry ENTRY      Entry point: "file.cpp:line:char" or "function_name"
-s, --scope SCOPE      Scope root directory (default: project root)
-c, --config CONFIG    Filter config file (default: filter.cfg)
-d, --max-depth N     Maximum recursion depth (default: 10)
-m, --max-nodes N     Maximum nodes to process (default: 10000)
-f, --format FORMAT    Output format: text|json (default: text)
-o, --output FILE     Output file (default: stdout)
-v, --verbose         Enable verbose logging
```

## Examples

### ESP32 Project (Arduino)

```bash
# Analyze setup function
python main.py -p ~/projects/smart-drying-module -e "setup" \
  -s ~/projects/smart-drying-module/src -d 2

# Analyze control system
python main.py -p ~/projects/smart-drying-module \
  -e "src/ControlSystem.cpp:77:10" -d 3
```

### C++ Project

```bash
# Find all callers of a function
python main.py -p ~/projects/myapp -e "main" -d 2

# Generate JSON for visualization
python main.py -p ~/projects/myapp -e "process_data" \
  -f json -o call_graph.json
```

## Output Formats

### Text Format (Indented Tree)

```
setup (main.cpp:192)
    begin (HardwareSerial.cpp:262) [EXTERNAL]
    delay (esp32-hal-misc.c:176) [EXTERNAL]
    init (ControlSystem.cpp:50)
        reset (PIDController.cpp:68)
```

### JSON Format (Adjacency List)

```json
[
  {
    "index": 0,
    "self": {
      "path": "/path/to/src/main.cpp",
      "line": [192, 265],
      "type": "function",
      "name": "setup",
      "qualified_name": "setup",
      "brief": ""
    },
    "parents": [16],
    "children": [1, 2, 3, 4, 5]
  }
]
```

## Architecture

```
CallGraphBuilder (Core Logic)
├─ ClangdClient (LSP Communication)
│   └─ Synchronous blocking I/O
├─ FilterConfig (File Filtering)
├─ NodeRegistry (Graph Storage)
│   ├─ Nodes (functions)
│   └─ Edges (call relationships)
└─ OutputFormatter (Export)
    ├─ Text (indented tree)
    └─ JSON (adjacency list)
```

## Design Decisions

### Unified Entry Point

Single `-e/--entry` parameter handles both modes:
- `file.cpp:line:char` → Position mode
- `function_name` → Function name mode

Function name mode internally resolves to position using:
1. `workspace/symbol` (if available)
2. Fallback: `textDocument/documentSymbol` on common source files

### Synchronous I/O

Uses synchronous blocking I/O (not multi-threaded) for:
- Simpler code
- More reliable response matching
- Better compatibility with reference implementation

### On-Demand Loading

Files opened only when needed:
- Faster startup
- Lower memory usage
- Dynamic delay: `max(0.5, opened_count * 0.05)`

## Troubleshooting

### "compile_commands.json not found"

Ensure your project has a compile commands database:
- **CMake**: `set(CMAKE_EXPORT_COMPILE_COMMANDS ON)`
- **Meson**: `meson compile_commands.json`
- **Bear**: `bear -- make`
- **Clang**: `clang -MJ compile_commands.json`

### "Function not found"

- Try position mode: `-e "file.cpp:line:char"`
- Ensure file is in scope: `-s src/`
- Check compile_commands.json includes the file

### "Clangd not found"

Install Clangd:
```bash
# Ubuntu/Debian
sudo apt-get install clangd

# macOS
brew install clangd

# NixOS (in configuration.nix)
environment.systemPackages = [ pkgs.clang-tools ]
```

### Performance issues

- Reduce depth: `-d 2` or `-d 3`
- Limit scope: `-s src/` (not entire project)
- Use simplified compile_commands.json

## Contributing

Project structure:
```
src/
├─ clangd_client.py      # LSP communication
├─ call_graph_builder.py # Core graph logic
├─ config.py              # Filter configuration
├─ validator.py           # Input validation
└─ cli.py                # Command-line interface
```

## License

MIT
