# Clangd Call Stack Tree Visualizer - Requirements

## Project Overview

**Name:** clangd-call-tree
**Language:** Python
**Target:** C/C++ code analysis
**Core Technology:** Clangd JSON-RPC protocol
**Input:** Project path with `compile_commands.json` and `.cache/` directory
**Output:** Hierarchical indented call tree with function metadata

**Goal:** Generate a function call stack tree starting from a specified entry point, tracking calls across .cpp/.h files with precise scope control.

---

## Functional Requirements

### R1: Input Validation and Initialization
- **R1.1:** Validate that project path contains `compile_commands.json`
- **R1.2:** Validate that project path contains `.cache/` directory (Clangd cache)
- **R1.3:** Validate that `filter.cfg` file exists and is readable
- **R1.4:** Parse `filter.cfg` to extract folder filter rules
- **R1.5:** Resolve `-s` parameter (root directory) to absolute path if provided
- **R1.6:** If `-s` not provided, default to project root directory
- **R1.7:** Validate that function name parameter is provided and non-empty
- **R1.8:** Clear error messages for all validation failures

### R2: Clangd JSON-RPC Connection
- **R2.1:** Initialize JSON-RPC connection to Clangd language server
- **R2.2:** Configure Clangd with project's compilation database
- **R2.3:** Set workspace URI to project root directory
- **R2.3:** Verify Clangd is ready and responsive before proceeding
- **R2.4:** Handle connection failures with clear error messages
- **R2.5:** Support Clangd communication via stdin/stdout or TCP socket
- **R2.6:** Implement proper JSON-RPC request/response matching
- **R2.7:** Handle JSON-RPC errors and notifications gracefully

### R3: Entry Point Function Location
- **R3.1:** Search for function definition matching the provided function name
- **R3.2:** Support qualified names (e.g., `Namespace::ClassName::method`)
- **R3.3:** Use Clangd `textDocument/definition` or `workspace/symbol` request
- **R3.4:** Return absolute file path and line number of entry point
- **R3.5:** Handle multiple matches (ambiguous function names) with user prompt or error
- **R3.6:** Handle function not found with clear error message including searched name
- **R3.7:** Support overloaded functions by signature matching if needed

### R4: Cross-File Deep Tracking
- **R4.1:** Starting from entry point, parse function body for call expressions
- **R4.2:** For each call expression, identify the target function name
- **R4.3:** Resolve target function definition using Clangd semantic queries
- **R4.4:** Recursively track calls across .cpp/.h files
- **R4.5:** Maintain call depth counter to prevent infinite recursion
- **R4.6:** Detect cycles in call graph and handle gracefully (report cycle depth)
- **R4.7:** Track which file each call originates from
- **R4.8:** Support both direct calls (`foo()`) and member calls (`obj->foo()`)
- **R4.9:** Handle template function calls
- **R4.10:** Handle overloaded function resolution

### R5: Precise Scope Control
- **R5.1:** Support `-s` parameter to specify root directory (e.g., `src/`)
- **R5.2:** Root directory is recursively traversed including subdirectories
- **R5.3:** When a call targets a function outside the root directory scope:
  - **R5.3.1:** Mark the call with special notation (e.g., `[EXTERNAL]`)
  - **R5.3.2:** Record the external file path for reference
  - **R5.3.3:** Stop recursive tracking for that branch
  - **R5.3.4:** Continue tracking other branches in the call tree
- **R5.4:** Apply filter.cfg rules to determine which paths are in scope
- **R5.5:** filter.cfg supports:
  - Include patterns (e.g., `+src/`, `+lib/common/`)
  - Exclude patterns (e.g., `-test/`, `-build/`)
  - Wildcard patterns (e.g., `+*.cpp`, `-*_test.cpp`)
- **R5.6:** External system libraries (e.g., `/usr/include/`, `/nix/store/`) are always marked as external
- **R5.7:** Third-party library directories are marked as external based on filter.cfg
- **R5.8:** Log when branches exit scope (file path and reason)

### R6: Complete Function Implementation Range
- **R6.1:** Do not return only the line number where function name appears in a call
- **R6.2:** Request AST symbol nodes from Clangd to extract function definition
- **R6.3:** Extract start line: line number of function declaration/opening brace
- **R6.4:** Extract end line: line number of function closing brace `}`
- **R6.5:** Use 1-based line numbering (standard for editors and tools)
- **R6.6:** Return both start and end line as a range: `[start_line, end_line]`
- **R6.7:** Handle functions without explicit bodies (forward declarations) gracefully
- **R6.8:** Handle inline functions and lambda functions
- **R6.9:** Validate that end_line >= start_line

### R7: Hierarchical Output Format
- **R7.1:** Generate indented call tree showing call hierarchy
- **R7.2:** Indentation level increases with call depth (e.g., 2 spaces per level)
- **R7.3:** Use specific JSON-style English format for each node:
  ```
  --> [function_name] {"file":"absolute_path", line[start_line, end_line]}
  ```
- **R7.4:** For external calls, append marker:
  ```
  --> [function_name] {"file":"absolute_path", line[start_line, end_line]} [EXTERNAL]
  ```
- **R7.5:** Output to stdout or specified file path
- **R7.6:** Maintain order of calls as they appear in source code
- **R7.7:** Show file path using absolute paths (for clarity)
- **R7.8:** Include call depth markers (optional, for debugging)

### R8: AST Symbol Node Extraction
- **R8.1:** Use Clangd `textDocument/documentSymbol` request to get symbol tree
- **R8.2:** Parse symbol nodes to identify function definitions
- **R8.3:** Extract symbol range information (`range.start.line`, `range.end.line`)
- **R8.4:** Convert 0-based Clangd line numbers to 1-based for output
- **R8.5:** Handle nested function definitions (e.g., lambdas, local functions)
- **R8.6:** Cache symbol information for functions to avoid repeated queries
- **R8.7:** Handle cases where symbol information is not available

### R9: Call Expression Parsing
- **R9.1:** Identify call expressions within function bodies
- **R9.2:** Extract function name from call expression
- **R9.3:** Resolve qualified names (e.g., `std::vector::push_back`)
- **R9.4:** Handle member function calls (`object->method()`, `object.method()`)
- **R9.5:** Handle constructor calls (`ClassName()`)
- **R9.6:** Handle destructor calls (`~ClassName()`)
- **R9.7:** Handle operator overloads (`operator+`, `operator[]`)
- **R9.8:** Ignore calls to built-in operators (e.g., `+`, `+=`)

### R10: Error Handling and Resilience
- **R10.1:** Gracefully handle files that fail to parse (continue with other files)
- **R10.2:** Skip functions without symbol information with warning
- **R10.3:** Handle Clangd errors (timeouts, crashes, invalid responses)
- **R10.4:** Implement retry logic for transient failures
- **R10.5:** Provide clear error messages with file and line information
- **R10.6:** Log warnings for skipped or incomplete data
- **R10.7:** Support verbose mode with detailed logging
- **R10.8:** Handle keyboard interrupt (Ctrl+C) gracefully

### R11: Performance Considerations
- **R11.1:** Cache Clangd responses to avoid redundant queries
- **R11.2:** Use parallel requests where possible (independent branches)
- **R11.3:** Implement maximum depth limit (configurable via `-d` parameter)
- **R11.4:** Implement timeout for each Clangd request
- **R11.5:** Limit number of total nodes (configurable via `-m` parameter)
- **R11.6:** Report progress (e.g., "Processed 50 functions, depth=4")
- **R11.7:** Optimize for typical projects (hundreds of files, not thousands)

### R12: Filter Configuration Format
- **R12.1:** `filter.cfg` is a plain text file with one rule per line
- **R12.2:** Lines starting with `+` are include rules
- **R12.3:** Lines starting with `-` are exclude rules
- **R12.4:** Empty lines and lines starting with `#` are ignored
- **R12.5:** Rules are processed in order: first match wins
- **R12.6:** Path patterns support:
  - Exact paths: `src/main.cpp`
  - Directory prefixes: `src/`
  - Wildcards: `*.cpp`, `test_*.c`
  - Recursion: `src/` matches `src/` and all subdirectories
- **R12.7:** Default rule (if no matches): include all paths

### R13: Command-Line Interface
- **R13.1:** Accept project path as positional argument or `-p/--path` option
- **R13.2:** Accept function name as `-f/--function` option (required)
- **R13.3:** Accept root directory as `-s/--scope` option (optional, default: project root)
- **R13.4:** Accept filter config file as `-c/--config` option (optional, default: `filter.cfg`)
- **R13.5:** Accept output file as `-o/--output` option (optional, default: stdout)
- **R13.6:** Accept max depth as `-d/--max-depth` option (optional, default: 100)
- **R13.7:** Accept max nodes as `-m/--max-nodes` option (optional, default: 10000)
- **R13.8:** Accept verbose flag as `-v/--verbose` option (optional, default: False)
- **R13.9:** Accept help flag as `-h/--help` option
- **R13.10:** Display clear usage message on invalid arguments

### R14: External Call Notation
- **R14.1:** Mark calls to system libraries as `[EXTERNAL]`
- **R14.2:** Mark calls to third-party libraries as `[EXTERNAL]`
- **R14.3:** Mark calls outside the root scope as `[EXTERNAL]`
- **R14.4:** Include file path for external calls for reference
- **R14.5:** Do not recurse into external calls
- **R14.6:** Optional: categorize external calls (e.g., `[EXTERNAL: SYSTEM]`, `[EXTERNAL: LIB]`)

### R15: Cross-Reference Support
- **R15.1:** Show the calling line number for each function call
- **R15.2:** Format: line [N] shows where the call originates
- **R15.3:** Include this information in the output format (optional)
- **R15.4:** Support jumping to definition (output format compatible with tools)

---

## Non-Functional Requirements

### N16: Python Implementation
- **N16.1:** All code must be Python 3.8+ compatible
- **N16.2:** Use type hints for all function signatures
- **N16.3:** No C++ compilation required
- **N16.4:** Follow PEP 8 style guidelines
- **N16.5:** Use docstrings for all public APIs

### N17: Dependencies
- **N17.1:** Python standard library for core functionality
- **N17.2:** No external dependencies for JSON-RPC (implement minimal client)
- **N17.3:** Optional: Use `pydantic` for JSON schema validation
- **N17.4:** Optional: Use `rich` for colored terminal output
- **N17.5:** Clangd must be installed and available in PATH
- **N17.6:** Clangd version must support JSON-RPC protocol

### N18: Correctness
- **N18.1:** Must use Clangd for all semantic analysis (no regex or grep)
- **N18.2:** Respect compilation database (compile_commands.json)
- **N18.3:** Handle edge cases correctly:
  - Template functions
  - Overloaded functions
  - Lambda expressions
  - Preprocessor macros
  - Conditional compilation (#ifdef)
- **N18.4:** Ensure line numbers are 1-based and accurate

### N19: Usability
- **N19.1:** CLI interface with clear help text and examples
- **N19.2:** Provide meaningful error messages with context
- **N19.3:** Support verbose mode for debugging
- **N19.4:** Reasonable defaults for all optional parameters
- **N19.5:** Progress indicators for long-running operations

### N20: Performance
- **N20.1:** Handle typical projects (100-500 source files) in < 10 seconds
- **N20.2:** Memory usage should be reasonable (< 500MB)
- **N20.3:** Cache frequently accessed data
- **N20.4:** Avoid unnecessary Clangd requests
- **N20.5:** Implement early termination limits (depth, nodes)

### N21: Reliability
- **N21.1:** Handle Clangd crashes gracefully
- **N21.2:** Implement timeouts for all Clangd requests
- **N21.3:** Handle invalid or corrupted compilation database
- **N21.4:** Handle missing .cache directory with clear error message
- **N21.5:** Continue processing despite partial failures

### N22: Platform Independence
- **N22.1:** Support Linux (primary platform)
- **N22.2:** Support macOS (secondary platform)
- **N22.3:** Handle path separators correctly (`/` vs `\`)
- **N22.4:** Handle NixOS nix-store paths correctly

---

## Constraints

### C1: Clangd JSON-RPC Only
- Must use Clangd's JSON-RPC protocol for all semantic queries
- No libclang, no regex, no grep for code analysis
- Clangd must be installed and available

### C2: Single-Pass Tree Generation
- Generate call tree in one pass from entry point
- No multi-pass analysis or global indexing required
- Use Clangd's on-demand queries

### C3: Minimal External Dependencies
- Prefer standard library over external packages
- Keep dependency footprint small
- No heavy frameworks (Django, Flask, etc.)

### C4: Simple Architecture
- Avoid over-engineering
- Prefer straightforward scripts over complex class hierarchies
- Clear separation of concerns

### C5: Output Format Constraint
- Must use the specified JSON-style English format
- No alternative output formats in initial version
- Output must be human-readable and machine-parsable

---

## Input/Output Specification

### Input: filter.cfg
```
# Include source code
+src/
+lib/

# Exclude tests
-test/
-test_*.cpp

# Exclude build artifacts
-build/
-dist/
```

### Input: Command Line
```bash
python main.py \
  -p /path/to/project \
  -f main \
  -s src/ \
  -c filter.cfg \
  -o call_tree.txt \
  -d 10
```

### Output: Call Tree
```
--> main {"file":"/path/to/project/src/main.cpp", line[42, 89]}
  --> initialize {"file":"/path/to/project/src/init.cpp", line[12, 35]}
    --> loadConfig {"file":"/path/to/project/src/config.cpp", line[5, 20]}
  --> processData {"file":"/path/to/project/src/process.cpp", line[45, 120]}
    --> validateInput {"file":"/path/to/project/src/validate.cpp", line[8, 25]}
    --> transform {"file":"/path/to/project/src/transform.cpp", line[30, 60]}
      --> std::vector::push_back {"file":"/usr/include/c++/vector", line[1150, 1180]} [EXTERNAL]
    --> saveOutput {"file":"/path/to/project/src/output.cpp", line[70, 95]}
      --> std::ofstream::open {"file":"/usr/include/c++/fstream", line[890, 920]} [EXTERNAL]
  --> cleanup {"file":"/path/to/project/src/cleanup.cpp", line[50, 75]}
```

### Output: Error Messages
```
Error: compile_commands.json not found in /path/to/project
Error: .cache directory not found in /path/to/project
Error: function 'undefined_function' not found
Warning: Could not extract symbol information for 'foo' at src/bar.cpp:25
Info: Reached max depth (10), stopping recursion
Info: External call detected: std::vector::push_back (/usr/include/c++/vector)
```

---

## Acceptance Criteria

The tool is accepted if:

1. **AC1:** Validates input parameters correctly (project path, compile_commands.json, .cache/)
2. **AC2:** Connects to Clangd via JSON-RPC successfully
3. **AC3:** Locates entry point function and returns correct file path and line number
4. **AC4:** Tracks function calls across .cpp/.h files recursively
5. **AC5:** Extracts complete function ranges (start line to end line) using AST symbols
6. **AC6:** Uses 1-based line numbering consistently
7. **AC7:** Respects `-s` parameter for scope control
8. **AC8:** Marks external calls (system libs, outside scope) with `[EXTERNAL]`
9. **AC9:** Stops tracking when branch exits scope
10. **AC10:** Generates hierarchical indented output in specified format
11. **AC11:** Handles filter.cfg rules correctly (include/exclude patterns)
12. **AC12:** Handles cycles in call graph gracefully
13. **AC13:** Provides clear error messages for all failure modes
14. **AC14:** Works with real-world C++ projects (e.g., smart-drying-module)
15. **AC15:** Performance is acceptable (< 10 seconds for typical projects)
16. **AC16:** No C++ code is present in implementation
17. **AC17:** Uses Clangd JSON-RPC for all semantic queries
18. **AC18:** CLI provides clear help and usage information
19. **AC19:** Output is both human-readable and machine-parsable
20. **AC20:** Handle edge cases (templates, overloads, lambdas) correctly

---

## Edge Cases and Special Considerations

### Edge Case 1: Multiple Function Definitions
- **Scenario:** Function name appears in multiple files (overloaded or different namespaces)
- **Behavior:** Report ambiguity and exit with error (or prompt user to choose)

### Edge Case 2: Forward Declarations
- **Scenario:** Function is declared but not defined (header-only interface)
- **Behavior:** Report missing definition and skip that branch

### Edge Case 3: Recursive Functions
- **Scenario:** Function calls itself directly or indirectly
- **Behavior:** Detect cycle, report depth, stop recursion for that branch

### Edge Case 4: Virtual Functions
- **Scenario:** Call to virtual function where actual implementation unknown statically
- **Behavior:** Report as [VIRTUAL] and note that exact target unknown

### Edge Case 5: Function Pointers
- **Scenario:** Call through function pointer where target unknown
- **Behavior:** Report as [POINTER] and skip that branch

### Edge Case 6: Macros as Functions
- **Scenario:** Macro invocation looks like function call
- **Behavior:** Ignore macros (Clangd handles macro expansion internally)

### Edge Case 7: Missing Compilation Flags
- **Scenario:** Function requires specific macros to be defined
- **Behavior:** Use compilation database flags for correct parsing

### Edge Case 8: Large Projects
- **Scenario:** Project with thousands of files
- **Behavior:** Respect max-depth and max-nodes limits, report progress

### Edge Case 9: Clangd Not Responding
- **Scenario:** Clangd crashes or hangs
- **Behavior:** Timeout, clear error message, graceful exit

### Edge Case 10: Corrupted .cache Directory
- **Scenario:** .cache directory contains invalid or incomplete data
- **Behavior:** Warning, attempt to continue, may require manual cache clear

---

## Future Enhancements (Out of Scope for v1.0)

1. **HTML Visualization:** Interactive web-based call tree viewer
2. **JSON Output:** Machine-readable JSON format for integration
3. **Call Graph Statistics:** Metrics like cyclomatic complexity, depth distribution
4. **Multiple Entry Points:** Support analyzing multiple functions in one run
5. **Reverse Traversal:** Show callers of a function instead of callees
6. **Diff Mode:** Compare call trees between two versions of code
7. **Annotation Support:** Add custom notes or documentation to call tree nodes
8. **Filter by Type:** Show/hide specific function types (methods, free functions, lambdas)
9. **Clangd-Free Mode:** Optional fallback using static analysis (less accurate)
10. **Language Server Support:** Work as a language server extension

---

## References

- **Clangd Documentation:** https://clangd.llvm.org/
- **JSON-RPC Specification:** https://www.jsonrpc.org/specification
- **LSP (Language Server Protocol):** https://microsoft.github.io/language-server-protocol/
- **compile_commands.json Format:** https://clang.llvm.org/docs/JSONCompilationDatabase.html
