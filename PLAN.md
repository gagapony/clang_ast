# Clangd Call Stack Tree Visualizer - Architecture and Implementation Plan

## Overview

**Project:** clangd-call-tree
**Phase:** Architecture Design
**Target:** v1.0

This document defines the architecture, components, and implementation approach for building a Clangd-based function call stack tree visualizer.

---

## System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          User (CLI)                                  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    CLI Layer (main.py)                                │
│  - Parse command-line arguments                                      │
│  - Validate inputs (project path, compile_commands.json, .cache/)     │
│  - Load filter.cfg configuration                                      │
│  - Coordinate between components                                      │
└──────────────────────────┬──────────────────────────────────────────┘
                               │
                ┌──────────────┴──────────────┐
                │                             │
                ▼                             ▼
┌───────────────────────────┐   ┌─────────────────────────────┐
│   Configuration Manager   │   │     Clangd Client           │
│   - Load filter.cfg       │   │   - JSON-RPC connection     │
│   - Parse include/exclude │   │   - Send LSP requests       │
│   - Resolve paths        │   │   - Handle responses        │
└───────────────────────────┘   └─────────────────────────────┘
                                        │
                                        ▼
                               ┌──────────────────────┐
                               │   Call Tree Builder   │
                               │   - Locate entry point│
                               │   - Parse AST symbols │
                               │   - Track calls       │
                               │   - Check scope       │
                               │   - Recurse depth-wise│
                               └──────────────────────┘
                                        │
                                        ▼
                               ┌──────────────────────┐
                               │   Output Formatter   │
                               │   - Generate indented│
                               │   - Add metadata     │
                               │   - Mark external    │
                               └──────────────────────┘
                                        │
                                        ▼
                               ┌──────────────────────┐
                               │   Output (STDOUT/File)│
                               └──────────────────────┘
```

### Component Interactions

```
1. CLI Parser
   └─> Config Manager: Load filter.cfg
   └─> Validator: Check project path, compile_commands.json, .cache/
   └─> Clangd Client: Initialize connection

2. Clangd Client
   └─> Call Tree Builder: Entry point lookup
   └─> Call Tree Builder: Symbol resolution
   └─> Call Tree Builder: AST queries

3. Call Tree Builder
   └─> Scope Checker: Verify call in scope
   └─> Cycle Detector: Check for recursion
   └─> Output Formatter: Format each node

4. Output Formatter
   └─> CLI: Write to stdout or file
```

---

## Directory Structure

```
clangd-call-tree/
├── README.md                 # Project overview and usage
├── REQUIREMENTS.md           # Detailed requirements (created)
├── PLAN.md                   # This file
├── pyproject.toml            # Python project configuration
├── requirements.txt          # Python dependencies
├── filter.cfg.example       # Example filter configuration
├── main.py                  # CLI entry point
├── src/
│   ├── __init__.py
│   ├── cli.py               # Command-line argument parsing
│   ├── config.py            # Filter configuration loading
│   ├── validator.py         # Input validation
│   ├── clangd_client.py     # JSON-RPC client for Clangd
│   ├── call_tree_builder.py # Core call tree construction logic
│   ├── scope_checker.py     # Scope verification for calls
│   ├── cycle_detector.py    # Cycle detection in call graph
│   ├── output_formatter.py  # Hierarchical output formatting
│   └── cache.py             # Symbol information caching
├── tests/
│   ├── __init__.py
│   ├── test_config.py       # Test filter configuration parsing
│   ├── test_validator.py    # Test input validation
│   ├── test_clangd_client.py # Test JSON-RPC client (mocked)
│   ├── test_call_tree_builder.py # Test call tree construction
│   ├── test_scope_checker.py     # Test scope verification
│   └── test_output_formatter.py  # Test output formatting
├── examples/
│   ├── simple_project/
│   │   ├── compile_commands.json
│   │   ├── filter.cfg
│   │   └── src/
│   │       ├── main.cpp
│   │       ├── utils.cpp
│   │       └── utils.h
│   └── simple_output.txt    # Expected output for simple project
└── docs/
    ├── ARCHITECTURE.md      # Extended architecture documentation
    ├── API.md               # API documentation
    └── TROUBLESHOOTING.md   # Common issues and solutions
```

---

## Component Specifications

### 1. CLI Parser (`src/cli.py`)

**Responsibility:** Parse command-line arguments and coordinate components.

**Key Functions:**
```python
def parse_args() -> argparse.Namespace:
    """Parse and validate command-line arguments."""

def main() -> int:
    """Main entry point for the CLI."""
```

**CLI Arguments:**
```
-p, --path PATH       Project directory (must contain compile_commands.json)
-f, --function FUNC    Entry point function name (required)
-s, --scope SCOPE      Root directory for scope control (default: project root)
-c, --config CONFIG    Filter configuration file (default: filter.cfg)
-o, --output OUTPUT    Output file (default: stdout)
-d, --max-depth DEPTH  Maximum recursion depth (default: 100)
-m, --max-nodes NODES  Maximum nodes to process (default: 10000)
-v, --verbose          Enable verbose logging
-h, --help             Show help message
```

**Workflow:**
1. Parse arguments
2. Validate project path exists
3. Call `validator.validate_project_structure()`
4. Load filter configuration via `config.load_filter_config()`
5. Initialize `clangd_client.ClangdClient()`
6. Create `call_tree_builder.CallTreeBuilder()`
7. Build call tree starting from entry point
8. Format output via `output_formatter.format_tree()`
9. Write to stdout or file
10. Return exit code (0 for success, non-zero for errors)

---

### 2. Configuration Manager (`src/config.py`)

**Responsibility:** Load and parse filter.cfg configuration.

**Key Classes:**
```python
class FilterConfig:
    """Represents filter configuration."""

    def __init__(self, rules: List[FilterRule]):
        self.rules = rules

    def matches(self, path: str) -> bool:
        """Check if path matches any rule."""

    @staticmethod
    def from_file(file_path: str) -> 'FilterConfig':
        """Load filter configuration from file."""


class FilterRule:
    """Represents a single filter rule."""

    def __init__(self, action: str, pattern: str):
        self.action = action  # '+' or '-'
        self.pattern = pattern  # Path pattern

    def matches(self, path: str) -> bool:
        """Check if path matches this rule."""
```

**File Format (filter.cfg):**
```
# Include rules start with +
+src/
+lib/common/

# Exclude rules start with -
-test/
-test_*.cpp
-build/

# Comments start with #
```

**Parsing Logic:**
1. Read file line by line
2. Skip empty lines and comments (starting with `#`)
3. Parse rule: first character is `+` or `-`, rest is pattern
4. Validate pattern format
5. Store rules in order (first match wins)

---

### 3. Input Validator (`src/validator.py`)

**Responsibility:** Validate project structure and inputs.

**Key Functions:**
```python
def validate_project_structure(project_path: str) -> ValidationResult:
    """
    Validate project contains required files.

    Returns:
        ValidationResult with is_valid flag and error message
    """

def validate_entry_point(clangd_client, function_name: str) -> Location:
    """
    Validate that entry point function exists.

    Returns:
        Location (file_path, line_number) of function definition
    """


class ValidationResult:
    """Encapsulates validation result."""

    def __init__(self, is_valid: bool, message: str = ""):
        self.is_valid = is_valid
        self.message = message
```

**Validation Checks:**
1. Project path exists and is a directory
2. `compile_commands.json` exists in project root
3. `.cache/` directory exists (Clangd cache)
4. `filter.cfg` exists (or use default rules)
5. Function name is provided and non-empty
6. Entry point function exists (query Clangd)

---

### 4. Clangd Client (`src/clangd_client.py`)

**Responsibility:** Communicate with Clangd via JSON-RPC protocol.

**Key Classes:**
```python
class ClangdClient:
    """JSON-RPC client for Clangd language server."""

    def __init__(self, project_path: str):
        self.project_path = project_path
        self.request_id = 0
        self.process = None
        self.reader = None
        self.writer = None

    def start(self) -> None:
        """Start Clangd process and initialize connection."""

    def stop(self) -> None:
        """Stop Clangd process."""

    def send_request(self, method: str, params: dict) -> dict:
        """
        Send JSON-RPC request and return response.

        Args:
            method: JSON-RPC method name
            params: Request parameters

        Returns:
            Response result or raises error
        """

    def textDocument_definition(self, uri: str, line: int, character: int) -> dict:
        """Get definition of symbol at position."""

    def textDocument_documentSymbol(self, uri: str) -> List[dict]:
        """Get document symbol tree."""

    def workspace_symbol(self, query: str) -> List[dict]:
        """Search for symbols in workspace."""

    def initialize(self) -> None:
        """Send 'initialize' request to Clangd."""

    def shutdown(self) -> None:
        """Send 'shutdown' request to Clangd."""

    def exit(self) -> None:
        """Send 'exit' notification to Clangd."""
```

**JSON-RPC Implementation:**
- Use Python `subprocess.Popen` to start Clangd
- Use `stdin` and `stdout` for JSON-RPC communication
- Implement Content-Length header parsing
- Handle JSON-RPC errors and notifications
- Implement request/response ID matching

**Key Clangd Requests:**
1. `initialize` - Initialize language server
2. `textDocument/definition` - Find definition of symbol
3. `textDocument/documentSymbol` - Get document symbol tree
4. `workspace/symbol` - Search for symbols
5. `shutdown` - Shutdown language server
6. `exit` - Exit language server

---

### 5. Call Tree Builder (`src/call_tree_builder.py`)

**Responsibility:** Build the call tree from entry point recursively.

**Key Classes:**
```python
class CallTreeNode:
    """Represents a node in the call tree."""

    def __init__(self,
                 function_name: str,
                 file_path: str,
                 line_range: Tuple[int, int],
                 depth: int,
                 is_external: bool = False):
        self.function_name = function_name
        self.file_path = file_path
        self.line_range = line_range
        self.depth = depth
        self.is_external = is_external
        self.children: List['CallTreeNode'] = []


class CallTreeBuilder:
    """Builds call tree using Clangd semantic queries."""

    def __init__(self,
                 clangd_client: ClangdClient,
                 scope_checker: ScopeChecker,
                 cycle_detector: CycleDetector,
                 cache: SymbolCache,
                 max_depth: int = 100,
                 max_nodes: int = 10000):
        self.clangd_client = clangd_client
        self.scope_checker = scope_checker
        self.cycle_detector = cycle_detector
        self.cache = cache
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.node_count = 0

    def build_tree(self,
                   entry_point: str,
                   scope_root: str) -> CallTreeNode:
        """
        Build call tree starting from entry point.

        Returns:
            Root node of the call tree
        """

    def _expand_function(self,
                         function_name: str,
                         file_path: str,
                         depth: int) -> CallTreeNode:
        """
        Recursively expand function calls.

        Returns:
            CallTreeNode for this function
        """

    def _get_function_calls(self, file_path: str, line_range: Tuple[int, int]) -> List[Call]:
        """
        Get list of function calls within function body.

        Returns:
            List of Call objects
        """

    def _get_function_range(self, function_name: str, file_path: str) -> Tuple[int, int]:
        """
        Get complete function range (start_line, end_line) using AST symbols.

        Returns:
            Tuple of (start_line, end_line), 1-based
        """
```

**Key Algorithm:**
```
build_tree(entry_point, scope_root):
    1. Locate entry point definition using workspace/symbol
    2. Create root node with entry point info
    3. Call _expand_function(root_node, depth=0)
    4. Return root node

_expand_function(function_name, file_path, depth):
    1. Check depth limit (max_depth)
    2. Check node count limit (max_nodes)
    3. Get function range using documentSymbol
    4. Get list of function calls within range
    5. For each call:
        a. Resolve call target using textDocument/definition
        b. Check if target is in scope (scope_checker)
        c. Check for cycles (cycle_detector)
        d. Create child node
        e. If not external and in scope and no cycle:
           i. Recursively expand child
        f. Add child to current node's children
    6. Return current node
```

---

### 6. Scope Checker (`src/scope_checker.py`)

**Responsibility:** Verify if a call is within the analysis scope.

**Key Classes:**
```python
class ScopeChecker:
    """Checks if paths are within analysis scope."""

    def __init__(self,
                 scope_root: str,
                 filter_config: FilterConfig):
        self.scope_root = os.path.abspath(scope_root)
        self.filter_config = filter_config
        self.system_paths = self._detect_system_paths()

    def is_in_scope(self, file_path: str) -> bool:
        """
        Check if file path is within analysis scope.

        Returns:
            True if in scope, False if external
        """

    def is_system_library(self, file_path: str) -> bool:
        """
        Check if file is a system library.

        Returns:
            True if system library, False otherwise
        """

    def _detect_system_paths(self) -> List[str]:
        """
        Detect system library paths.

        Returns:
            List of system paths (e.g., /usr/include/, /nix/store/)
        """

    def _apply_filter_rules(self, file_path: str) -> bool:
        """
        Apply filter.cfg rules to file path.

        Returns:
            True if allowed by filter, False if excluded
        """
```

**Scope Logic:**
```
is_in_scope(file_path):
    1. If is_system_library(file_path): return False
    2. If not under scope_root: return False
    3. If not _apply_filter_rules(file_path): return False
    4. Otherwise: return True
```

---

### 7. Cycle Detector (`src/cycle_detector.py`)

**Responsibility:** Detect cycles in the call graph to prevent infinite recursion.

**Key Classes:**
```python
class CycleDetector:
    """Detects cycles in call graph during tree expansion."""

    def __init__(self):
        self.call_stack: List[Tuple[str, str]] = []  # (function_name, file_path)

    def enter_function(self, function_name: str, file_path: str) -> Optional[int]:
        """
        Enter a function in the call stack.

        Returns:
            Depth of cycle if detected, None otherwise
        """

    def exit_function(self) -> None:
        """Exit the current function."""

    def _detect_cycle(self, function_name: str, file_path: str) -> Optional[int]:
        """
        Check if calling this function creates a cycle.

        Returns:
            Depth of cycle (number of stack frames), or None
        """

    def reset(self) -> None:
        """Reset the call stack (for new tree)."""
```

**Cycle Detection Logic:**
```
enter_function(function_name, file_path):
    1. Check if (function_name, file_path) already in call_stack
    2. If yes: return depth of first occurrence
    3. If no: add to call_stack, return None

exit_function():
    1. Pop last entry from call_stack
```

---

### 8. Output Formatter (`src/output_formatter.py`)

**Responsibility:** Format call tree as hierarchical indented text.

**Key Functions:**
```python
def format_tree(root: CallTreeNode, include_calling_line: bool = False) -> str:
    """
    Format call tree as hierarchical indented text.

    Args:
        root: Root node of the call tree
        include_calling_line: Include line number where call originates

    Returns:
        Formatted output string
    """

def _format_node(node: CallTreeNode) -> str:
    """
    Format a single node.

    Returns:
        Formatted node string
    """

def _create_indent(depth: int) -> str:
    """
    Create indentation string for depth level.

    Returns:
        Indentation string (e.g., "  " for depth=1)
    """
```

**Output Format:**
```
--> [function_name] {"file":"absolute_path", line[start_line, end_line]} [EXTERNAL]

Where:
- Indentation: 2 spaces per depth level
- function_name: Function name (qualified if needed)
- absolute_path: Absolute path to source file
- start_line, end_line: 1-based line numbers of function range
- [EXTERNAL]: Only added for external calls
```

---

### 9. Symbol Cache (`src/cache.py`)

**Responsibility:** Cache symbol information to avoid redundant Clangd queries.

**Key Classes:**
```python
class SymbolCache:
    """Caches symbol information from Clangd."""

    def __init__(self):
        self.symbol_cache: Dict[str, SymbolInfo] = {}
        self.document_cache: Dict[str, List[dict]] = {}

    def get_symbol(self, function_name: str, file_path: str) -> Optional[SymbolInfo]:
        """
        Get symbol from cache or query Clangd.

        Returns:
            SymbolInfo if found, None otherwise
        """

    def get_document_symbols(self, uri: str) -> Optional[List[dict]]:
        """
        Get document symbols from cache or query Clangd.

        Returns:
            List of symbol nodes or None
        """

    def invalidate_document(self, uri: str) -> None:
        """Invalidate cached document symbols."""

    def clear(self) -> None:
        """Clear all cached data."""


class SymbolInfo:
    """Information about a function symbol."""

    def __init__(self,
                 name: str,
                 qualified_name: str,
                 file_path: str,
                 line_range: Tuple[int, int],
                 kind: str):
        self.name = name
        self.qualified_name = qualified_name
        self.file_path = file_path
        self.line_range = line_range  # (start_line, end_line), 1-based
        self.kind = kind  # 'function', 'method', 'constructor', etc.
```

**Caching Strategy:**
1. Cache function definitions by (function_name, file_path)
2. Cache document symbols by file URI
3. Lazy loading: query Clangd on first access
4. Never invalidate during single run (Clangd maintains state)

---

## Dependencies

### Python Dependencies (requirements.txt)
```
# Core dependencies (minimal)
# No external dependencies required for JSON-RPC (implement in-house)

# Optional dependencies (for enhanced features)
pydantic>=2.0.0    # For JSON schema validation (optional)
rich>=13.0.0       # For colored terminal output (optional)
```

### System Dependencies
- **Python 3.8+**: Required runtime
- **Clangd 14+**: Language server for C/C++ semantic analysis
- **git**: For version control (optional)

### Clangd Installation
```bash
# Ubuntu/Debian
sudo apt-get install clangd

# macOS
brew install llvm

# NixOS
nix-shell -p clang-tools

# From source
git clone https://github.com/llvm/llvm-project.git
cd llvm-project
cmake -G Ninja -DCMAKE_BUILD_TYPE=Release -DLLVM_ENABLE_PROJECTS="clang-tools-extra" llvm
ninja clangd
```

---

## Implementation Phases

### Phase 1: Foundation (Week 1)
**Goal:** Basic infrastructure and Clangd connection

**Tasks:**
1. Set up project structure and dependencies
2. Implement CLI parser (`src/cli.py`)
3. Implement input validator (`src/validator.py`)
4. Implement Clangd client (`src/clangd_client.py`)
   - Process management
   - JSON-RPC protocol handling
   - Basic requests (initialize, shutdown)
5. Write basic tests for validator and Clangd client (mocked)

**Deliverables:**
- Working CLI with argument parsing
- Clangd client that can start/stop and send requests
- Validation for project structure

---

### Phase 2: Configuration and Scope (Week 2)
**Goal:** Filter configuration and scope checking

**Tasks:**
1. Implement filter configuration parser (`src/config.py`)
2. Implement scope checker (`src/scope_checker.py`)
3. Implement system path detection
4. Write tests for config and scope checker

**Deliverables:**
- Working filter.cfg parsing
- Scope verification logic
- External call detection

---

### Phase 3: Call Tree Construction (Week 3)
**Goal:** Core call tree building logic

**Tasks:**
1. Implement cycle detector (`src/cycle_detector.py`)
2. Implement symbol cache (`src/cache.py`)
3. Implement call tree builder (`src/call_tree_builder.py`)
   - Entry point location
   - Symbol extraction
   - Recursive expansion
   - External call handling
4. Write tests for call tree builder

**Deliverables:**
- Complete call tree construction
- AST symbol extraction
- Cross-file tracking

---

### Phase 4: Output Formatting (Week 4)
**Goal:** Hierarchical output generation

**Tasks:**
1. Implement output formatter (`src/output_formatter.py`)
2. Integrate all components in CLI
3. Add error handling and logging
4. Write integration tests
5. Create example project and expected output

**Deliverables:**
- Working end-to-end tool
- Hierarchical indented output
- Example project with expected output

---

### Phase 5: Testing and Refinement (Week 5)
**Goal:** Robustness and edge cases

**Tasks:**
1. Test with real-world projects
2. Fix bugs found during testing
3. Add more comprehensive tests
4. Improve error messages
5. Performance optimization
6. Documentation completion

**Deliverables:**
- Production-ready tool
- Comprehensive test suite
- Complete documentation

---

## Testing Strategy

### Unit Tests
- **Coverage Goal:** > 80%
- **Framework:** pytest
- **Focus:** Test each component in isolation

**Key Test Files:**
1. `test_config.py` - Filter configuration parsing
2. `test_validator.py` - Input validation logic
3. `test_clangd_client.py` - JSON-RPC client (mocked)
4. `test_scope_checker.py` - Scope verification
5. `test_cycle_detector.py` - Cycle detection
6. `test_call_tree_builder.py` - Tree construction (mocked Clangd)
7. `test_output_formatter.py` - Output formatting

### Integration Tests
- Test full workflow with example projects
- Test with smart-drying-module project
- Verify output format matches specification

### Manual Testing
- Test with various C++ projects
- Test edge cases (templates, overloads, recursion)
- Test error handling

### Test Command
```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html

# Run specific test
pytest tests/test_config.py -v
```

---

## Error Handling Strategy

### Error Categories

**1. Input Errors**
- Missing or invalid project path
- Missing compile_commands.json
- Missing .cache directory
- Invalid filter.cfg
- Function not found

**2. Clangd Errors**
- Clangd not installed
- Clangd fails to start
- Clangd crashes or hangs
- Invalid JSON-RPC response
- Timeout on request

**3. Semantic Errors**
- Cannot resolve function definition
- Missing symbol information
- Cycle in call graph
- Maximum depth exceeded
- Maximum nodes exceeded

### Error Handling Approach

**Validation Errors (Fatal):**
```
Error: compile_commands.json not found in /path/to/project
Usage: python main.py -p /path/to/project -f function_name
```

**Clangd Errors (Fatal):**
```
Error: Failed to start Clangd. Ensure Clangd is installed.
Command: clangd --version
```

**Semantic Errors (Warning):**
```
Warning: Could not resolve definition for 'foo' at src/bar.cpp:25
Info: Skipping branch, continuing with other calls
```

**Scope Exit (Info):**
```
Info: External call detected: std::vector::push_back (/usr/include/c++/vector)
Info: Branch exits scope, stopping recursion
```

**Cycle Detected (Warning):**
```
Warning: Cycle detected in call graph: foo() -> bar() -> foo()
Info: Stopping recursion at depth 3
```

---

## Performance Considerations

### Performance Goals
- **Small projects** (< 100 files): < 2 seconds
- **Medium projects** (100-500 files): < 10 seconds
- **Large projects** (500+ files): < 30 seconds or respect limits

### Optimization Strategies

**1. Caching**
- Cache symbol information to avoid redundant queries
- Cache document symbols for each file
- Never cache across runs (invalidate on restart)

**2. Limiting**
- Respect max-depth parameter (default: 100)
- Respect max-nodes parameter (default: 10000)
- Stop when limits reached

**3. Parallel Requests**
- Potentially parallelize independent branches
- Requires careful synchronization

**4. Early Exit**
- Exit scope as soon as external call detected
- Stop recursion on cycle detection
- Return early when node limit reached

---

## Security Considerations

### 1. Path Traversal
- Resolve all paths to absolute paths
- Validate project path is within allowed directory
- Sanitize file paths before opening

### 2. Command Injection
- Never execute shell commands with user input
- Use subprocess with list arguments (not string)
- Validate all command-line arguments

### 3. Resource Limits
- Enforce max-depth and max-nodes to prevent DoS
- Implement timeout for Clangd requests
- Limit memory usage

---

## Documentation Plan

### User Documentation
1. **README.md**: Quick start guide and usage examples
2. **USAGE.md**: Detailed usage documentation
3. **filter.cfg.example**: Example filter configuration

### Developer Documentation
1. **ARCHITECTURE.md**: Extended architecture details
2. **API.md**: API documentation for each module
3. **CONTRIBUTING.md**: Contribution guidelines

### Code Documentation
- Docstrings for all public functions and classes
- Inline comments for complex logic
- Type hints for all function signatures

---

## Success Criteria

The project is considered successful when:

1. **All functional requirements** (R1-R15) are met
2. **All acceptance criteria** (AC1-AC20) are passed
3. **Test coverage** is > 80%
4. **Performance goals** are met (small projects < 2s, medium < 10s)
5. **Works with real-world projects** (e.g., smart-drying-module)
6. **Documentation** is complete and clear
7. **No C++ code** is present in implementation
8. **Uses Clangd JSON-RPC** for all semantic queries
9. **Error handling** is comprehensive and clear
10. **Output format** matches specification exactly

---

## Risks and Mitigations

### Risk 1: Clangd JSON-RPC Complexity
**Description:** JSON-RPC protocol can be complex to implement correctly.

**Mitigation:**
- Start with minimal subset of requests
- Use extensive logging for debugging
- Mock Clangd for testing
- Reference existing LSP client implementations

### Risk 2: Performance with Large Projects
**Description:** Large projects may cause performance issues.

**Mitigation:**
- Implement strict limits (depth, nodes)
- Optimize caching strategy
- Test with progressively larger projects
- Consider parallelization if needed

### Risk 3: Clangd Version Compatibility
**Description:** Different Clangd versions may have different LSP support.

**Mitigation:**
- Specify minimum Clangd version (14+)
- Test with multiple Clangd versions
- Gracefully degrade if features not available

### Risk 4: Edge Cases in C++ Code
**Description:** Complex C++ features may not be handled correctly.

**Mitigation:**
- Test with diverse code samples
- Handle edge cases gracefully
- Provide clear error messages
- Document known limitations

---

## Future Enhancements (Post v1.0)

1. **HTML Visualization:** Interactive web-based call tree viewer
2. **JSON Output:** Machine-readable JSON format
3. **Multiple Entry Points:** Analyze multiple functions
4. **Reverse Traversal:** Show callers instead of callees
5. **Diff Mode:** Compare call trees between versions
6. **Language Server Extension:** Work as LSP extension
7. **Clangd-Free Mode:** Fallback using static analysis
8. **Call Statistics:** Metrics and analytics

---

## References

- **Clangd Documentation:** https://clangd.llvm.org/
- **LSP Specification:** https://microsoft.github.io/language-server-protocol/
- **JSON-RPC Specification:** https://www.jsonrpc.org/specification
- **compile_commands.json:** https://clang.llvm.org/docs/JSONCompilationDatabase.html
- **Python argparse:** https://docs.python.org/3/library/argparse.html
- **pytest:** https://docs.pytest.org/
