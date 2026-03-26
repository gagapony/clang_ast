# Clangd Call Stack Tree Visualizer - Optimization Plan

**Version:** 2.0
**Date:** 2026-03-25
**Status:** Reworked - Final Version

---

## Executive Summary

This optimization plan addresses performance bottlenecks identified through comparison with a fast script analysis. The current implementation suffers from:
- Latent file loading (on-demand `didOpen` calls)
- Inefficient function range extraction via repeated LSP `documentSymbol` requests
- Tree-based data structure requiring recursive traversals
- Redundant cache layer (Clangd has built-in indexing)
- Missing backward call tracking

The proposed optimizations target **20-500x performance improvement** for typical projects (100-500 source files).

---

## Phase A: High Priority Optimizations

### A1. Preload Directory Function (Fixed - Apply Filters BEFORE Opening)

#### Problem
Current implementation opens documents on-demand as calls are resolved. Each `textDocument/didOpen` triggers Clangd to parse and index the file, causing significant latency when tracking cross-file calls.

**Impact:** 100-500ms per file opening time, multiplied by number of unique files in call tree.

**Previous Issue:** Preload opened files BEFORE checking filter rules, wasting resources on excluded files.

#### Solution Design

**Approach:** Batch-load all source files in scope BEFORE starting call tree construction. Apply filters BEFORE calling `open_document()`.

**Implementation Details:**

```python
class CallTreeBuilder:
    def __init__(self, ...):
        # Existing initialization
        self.opened_documents: Set[str] = set()  # Track opened files
        self.preload_delay: float = 0.01  # 10ms between opens

    def preload_source_directory(self) -> int:
        """
        Preload all source files in scope into Clangd.

        Returns:
            Number of files preloaded
        """
        import glob
        import time

        # Source file extensions to include
        extensions = ['*.cpp', '*.h', '*.c', '*.hpp', '*.cc', '*.cxx']

        file_count = 0

        for ext in extensions:
            # Recursively find all files with this extension
            pattern = os.path.join(self.scope_root, '**', ext)
            for file_path in glob.glob(pattern, recursive=True):
                # FILTER BEFORE OPENING - CRITICAL FIX
                if not self._should_include(file_path):
                    continue

                # Skip if already opened
                if file_path in self.opened_documents:
                    continue

                try:
                    # Open document in Clangd
                    self.client.open_document(file_path)
                    self.opened_documents.add(file_path)
                    file_count += 1

                    # Small delay to avoid overwhelming Clangd
                    if self.preload_delay > 0:
                        time.sleep(self.preload_delay)

                except Exception as e:
                    # Log warning but continue
                    self._log(f"Warning: Failed to open {file_path}: {e}")

        return file_count
```

**Integration Point:** Call `preload_source_directory()` after Clangd initialization and before `build()`.

**Benefits:**
- All files indexed upfront, reducing per-file latency
- **FIXED:** Only opens files that match scope (no wasted I/O)
- Clangd can use its index for faster resolution
- Predictable initialization time

**Trade-offs:**
- Initial startup time increased (but amortized over all resolutions)

---

### A2. Document Symbol Caching + Batching (REPLACES Brace Matching)

#### Problem
Current implementation makes repeated LSP `textDocument/documentSymbol` requests:
- One request per function to get range information
- Each request causes JSON-RPC round-trip (~50-100ms)
- Multiple calls to same file request symbols multiple times

**Impact:** 50-100ms per function range query, repeated for each unique function in call tree.

**Previous Failed Approach:** Brace matching was fundamentally broken for real-world C++ code (lambdas, templates, macros, member initializer lists).

#### Solution Design

**Approach:** Query document symbols ONCE per file, cache entire symbol tree, and reuse for all function range lookups.

**Algorithm:**

```
function _get_function_info(file_path, function_name):
    """
    Get function info from document symbol cache.

    Args:
        file_path: Path to source file
        function_name: Function name to look up

    Returns:
        SymbolInfo or None if not found
    """
    # CHECK MEMORY CACHE FIRST
    cache_key = f"{file_path}:{function_name}"
    if cache_key in self.document_symbol_cache:
        return self.document_symbol_cache[cache_key]

    # QUERY ALL SYMBOLS FOR FILE (once)
    if file_path not in self.file_symbols_cache:
        uri = self.client._path_to_uri(file_path)
        symbols = self.client.textDocument_documentSymbol(uri)

        # Cache ALL symbols for this file
        self.file_symbols_cache[file_path] = symbols

    # FIND FUNCTION IN CACHED SYMBOLS
    symbols = self.file_symbols_cache[file_path]
    for symbol in symbols:
        if symbol['name'] == function_name and symbol['kind'] in FUNCTION_KINDS:
            start_line = symbol['range']['start']['line'] + 1  # 1-based
            end_line = symbol['range']['end']['line'] + 1

            symbol_info = SymbolInfo(
                name=function_name,
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                kind=symbol['kind']
            )

            # CACHE IN MEMORY
            self.document_symbol_cache[cache_key] = symbol_info
            return symbol_info

    return None
```

**Implementation Details:**

```python
class CallTreeBuilder:
    def __init__(self, ...):
        # Existing initialization
        self.file_symbols_cache: Dict[str, List[Dict]] = {}  # file_path -> all symbols
        self.document_symbol_cache: Dict[str, SymbolInfo] = {}  # "file:name" -> symbol

    def _get_function_info(
        self,
        file_path: str,
        function_name: str
    ) -> Optional[SymbolInfo]:
        """
        Get function info from document symbol cache.

        This replaces the broken brace matching approach.

        Args:
            file_path: Path to source file
            function_name: Function name to look up

        Returns:
            SymbolInfo or None if not found
        """
        # Check memory cache first
        cache_key = f"{file_path}:{function_name}"
        if cache_key in self.document_symbol_cache:
            return self.document_symbol_cache[cache_key]

        # Query all symbols for this file (once)
        if file_path not in self.file_symbols_cache:
            uri = self.client._path_to_uri(file_path)
            try:
                symbols = self.client.textDocument_documentSymbol(uri)
                self.file_symbols_cache[file_path] = symbols
            except ClangdClientError as e:
                self._log(f"Error getting symbols for {file_path}: {e}")
                return None

        # Find function in cached symbols
        symbols = self.file_symbols_cache.get(file_path, [])
        for symbol in symbols:
            if symbol.get('name') == function_name and symbol.get('kind') in (
                self.LSK_FUNCTION, self.LSK_METHOD,
                self.LSK_CONSTRUCTOR, self.LSK_DESTRUCTOR
            ):
                range_info = symbol.get('range', {})
                start_line = range_info.get('start', {}).get('line', 0) + 1
                end_line = range_info.get('end', {}).get('line', 0) + 1
                kind = symbol.get('kind', 0)

                symbol_info = SymbolInfo(
                    name=function_name,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    kind=kind,
                    children=[]
                )

                # Cache in memory
                self.document_symbol_cache[cache_key] = symbol_info
                return symbol_info

        return None
```

**Integration Point:** Replace all calls to `_get_function_info()` with cached version.

**Benefits:**
- **10-100x speedup** for subsequent function lookups in same file
- Eliminates repeated LSP requests (one per file instead of one per function)
- **Reliable:** Uses LSP's built-in AST parsing (no brace matching issues)
- Works with all C++ constructs (templates, lambdas, macros, etc.)

**Trade-offs:**
- Higher memory usage (caches all symbols for visited files)
- Initial cost for first function in each file (amortized by subsequent lookups)

---

### A3. Adjacency List Data Structure

#### Problem
Current implementation uses tree-based `CallNode` structure with nested `children` lists. This causes:
- Deep recursive traversals for queries
- Difficulty sharing subtrees
- No efficient parent lookups
- O(N) complexity for finding nodes by location

**Impact:** Slow tree construction, difficult to optimize, no parent tracking.

#### Solution Design

**Approach:** Replace tree with flat adjacency list using index-based nodes.

**Data Structure:**

```python
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

@dataclass
class CallGraphNode:
    """Node in adjacency list representation."""
    index: int  # Unique index (position in nodes array)
    function_name: str
    file_path: str
    start_line: int
    end_line: int

    # Metadata
    qualified_name: Optional[str] = None  # e.g., "Namespace::Class::method"
    brief: Optional[str] = None  # Brief comment from code

    # Flags
    is_external: bool = False
    is_virtual: bool = False
    is_pointer: bool = False

    # Adjacency lists (store indices)
    parents: List[int] = field(default_factory=list)  # Caller indices
    children: List[int] = field(default_factory=list)  # Callee indices

@dataclass
class CallGraph:
    """Complete call graph using adjacency list."""
    nodes: List[CallGraphNode]  # Index-based node list
    node_map: Dict[str, int]  # Key: "filepath:line" -> node index
    root_index: Optional[int] = None  # Index of root entry point

    def get_node(self, index: int) -> Optional[CallGraphNode]:
        """Get node by index."""
        if 0 <= index < len(self.nodes):
            return self.nodes[index]
        return None

    def find_node(self, file_path: str, line: int) -> Optional[int]:
        """Find node index by location."""
        key = f"{file_path}:{line}"
        return self.node_map.get(key)

    def find_node_by_name(self, function_name: str, file_path: Optional[str] = None) -> List[int]:
        """
        Find all nodes with matching function name.

        Args:
            function_name: Name to search for
            file_path: Optional file filter

        Returns:
            List of node indices
        """
        indices = []
        for node in self.nodes:
            if node.function_name == function_name:
                if file_path is None or node.file_path == file_path:
                    indices.append(node.index)
        return indices
```

**Algorithm for Tree Construction:**

```
function build_adjacency_list(entry_function):
    """
    Build adjacency list from entry point.

    Returns:
        CallGraph object
    """
    graph = CallGraph(nodes=[], node_map={}, root_index=None)

    # Create root node
    root_info = _get_function_info(entry_function)
    root_index = _create_node(graph, root_info)
    graph.root_index = root_index

    # BFS/DFS traversal
    queue = [root_index]
    visited = set()

    while queue:
        current_index = queue.pop(0)
        current_node = graph.get_node(current_index)

        if current_index in visited:
            continue
        visited.add(current_index)

        # Find function calls in body
        calls = _find_function_calls(current_node.file_path,
                                     current_node.start_line,
                                     current_node.end_line)

        for call_name, call_line in calls:
            # Resolve call target
            target_info = _resolve_call(call_name, call_line, current_node.file_path)

            if target_info:
                # Check if node already exists
                target_key = f"{target_info['file_path']}:{target_info['start_line']}"
                target_index = graph.node_map.get(target_key)

                if target_index is None:
                    # Create new node
                    target_index = _create_node(graph, target_info)

                # Add adjacency relationships
                current_node.children.append(target_index)
                graph.nodes[target_index].parents.append(current_index)

                # Queue for traversal (if in scope)
                if not target_info['is_external']:
                    queue.append(target_index)

    return graph

function _create_node(graph, info):
    """
    Create a new node in the graph.

    Returns:
        Node index
    """
    index = len(graph.nodes)

    node = CallGraphNode(
        index=index,
        function_name=info['name'],
        file_path=info['file_path'],
        start_line=info['start_line'],
        end_line=info['end_line'],
        qualified_name=info.get('qualified_name'),
        brief=info.get('brief'),
        is_external=info.get('is_external', False),
        is_virtual=info.get('is_virtual', False),
        is_pointer=info.get('is_pointer', False)
    )

    graph.nodes.append(node)
    graph.node_map[f"{info['file_path']}:{info['start_line']}"] = index

    return index
```

**JSON Output Format:**

```json
[
  {
    "index": 0,
    "self": {
      "function_name": "main",
      "file_path": "/path/to/src/main.cpp",
      "start_line": 42,
      "end_line": 89,
      "qualified_name": "main",
      "brief": "Entry point of the application"
    },
    "parents": [],
    "children": [1, 2]
  },
  {
    "index": 1,
    "self": {
      "function_name": "initialize",
      "file_path": "/path/to/src/init.cpp",
      "start_line": 12,
      "end_line": 35,
      "qualified_name": "initialize",
      "brief": "Initialize application state"
    },
    "parents": [0],
    "children": [3]
  }
]
```

**Integration Points:**
- Replace `CallNode` tree construction in `CallTreeBuilder.build()`
- Update `OutputFormatter` to traverse adjacency list
- Update CLI to output JSON format (add `--json` flag)

**Benefits:**
- O(1) node lookup by index
- Bidirectional traversal (parent + children)
- Easy to add metadata fields
- Naturally supports multiple parents (multiple callers)
- JSON-friendly output

**Trade-offs:**
- Less intuitive for simple hierarchical output
- Requires formatter adaptation
- More memory for parent arrays

---

### A4. Add `textDocument/references` (Backward Tracking)

#### Problem
Current implementation only tracks forward calls (callees). To find all callers of a function, users must manually search codebase. This limits tool's usefulness for impact analysis.

**Impact:** Cannot answer "what functions call X?" without manual code review.

#### Solution Design

**Approach:** Implement `build_incoming_tree_via_references()` using LSP `textDocument/references` request.

**Algorithm:**

```
function build_incoming_tree_via_references(target_function, max_depth=3):
    """
    Build reverse call graph showing all callers.

    Args:
        target_function: Function name to analyze
        max_depth: Maximum caller depth to track

    Returns:
        CallGraph (inverted direction)
    """
    graph = CallGraph(nodes=[], node_map={}, root_index=None)

    # Find target function location
    target_symbols = workspace_symbol(target_function)
    if not target_symbols:
        return None

    # Create root node
    target_info = extract_symbol_info(target_symbols[0])
    root_index = _create_node(graph, target_info)
    graph.root_index = root_index

    # BFS traversal backward
    queue = [(root_index, 0)]  # (node_index, depth)
    visited = set()

    while queue:
        current_index, depth = queue.pop(0)

        if current_index in visited or depth >= max_depth:
            continue
        visited.add(current_index)

        current_node = graph.get_node(current_index)

        # Find all references (callers) using LSP
        references = textDocument_references(
            uri=current_node.file_path,
            line=current_node.start_line - 1,  # 0-based
            character=0,
            context={"includeDeclaration": False}
        )

        for ref in references:
            # Extract caller location
            caller_file = ref['uri']
            caller_line = ref['range']['start']['line'] + 1

            # Resolve caller function
            caller_info = resolve_enclosing_function(caller_file, caller_line)

            if caller_info:
                # Check if caller node already exists
                caller_key = f"{caller_info['file_path']}:{caller_info['start_line']}"
                caller_index = graph.node_map.get(caller_key)

                if caller_index is None:
                    caller_index = _create_node(graph, caller_info)

                # Add reverse adjacency
                current_node.parents.append(caller_index)
                graph.nodes[caller_index].children.append(current_index)

                # Queue for backward traversal
                queue.append((caller_index, depth + 1))

    return graph
```

**LSP `textDocument/references` Request:**

```python
def textDocument_references(
    self,
    uri: str,
    line: int,
    character: int,
    context: Optional[Dict] = None
) -> List[Dict]:
    """
    Find all references to symbol at position.

    Args:
        uri: Document URI
        line: 0-based line number
        character: 0-based character position
        context: Optional reference context

    Returns:
        List of reference locations
    """
    params = {
        "textDocument": {"uri": uri},
        "position": {"line": line, "character": character},
        "context": context or {"includeDeclaration": False}
    }

    result = self.send_request("textDocument/references", params=params)

    if not result:
        return []

    return result
```

**Helper Function - Resolve Enclosing Function:**

```python
def resolve_enclosing_function(self, file_path: str, line: int) -> Optional[Dict]:
    """
    Find which function contains a given line.

    Args:
        file_path: File path
        line: Line number (1-based)

    Returns:
        Function info dict, or None if not found
    """
    uri = self.client._path_to_uri(file_path)

    # Get document symbols
    symbols = self.client.textDocument_documentSymbol(uri)

    # Find function containing this line
    for symbol in symbols:
        if symbol.get('kind') in (self.LSK_FUNCTION, self.LSK_METHOD,
                                  self.LSK_CONSTRUCTOR, self.LSK_DESTRUCTOR):
            range_info = symbol.get('range', {})
            start_line = range_info.get('start', {}).get('line', 0) + 1
            end_line = range_info.get('end', {}).get('line', 0) + 1

            if start_line <= line <= end_line:
                return {
                    'name': symbol['name'],
                    'file_path': file_path,
                    'start_line': start_line,
                    'end_line': end_line,
                    'is_external': not self._is_in_scope(file_path)
                }

    return None
```

**CLI Integration:**

```bash
# Forward tracking (existing)
python main.py -p /path/to/project -f main

# Backward tracking (new)
python main.py -p /path/to/project -f processData --direction=backward --max-depth=3
```

**Benefits:**
- Enables impact analysis ("what breaks if I change this function?")
- Identifies all call sites
- Useful for refactoring and code review

**Trade-offs:**
- Slower than forward tracking (may require more LSP requests)
- May find many references in large codebases
- Requires scope boundary handling (stop at project root)

---

### A5. Remove Cache System

#### Problem
Current implementation includes a custom `SymbolCache` class with disk and memory caching. However:
- Clangd maintains its own index for fast symbol lookup
- Cache adds complexity and potential staleness issues
- Cache invalidation is difficult (file changes, edits)
- Disk I/O overhead may outweigh benefits

**Impact:** Unnecessary complexity, potential for stale data, disk I/O overhead.

#### Solution Design

**Approach:** Remove entire cache system, rely on Clangd's built-in index and in-memory document symbol cache (A2).

**Files to Delete:**
- `src/cache.py` (entire file)

**Files to Modify:**

1. **`src/call_tree_builder.py`:**
   - Remove `SymbolCache` import
   - Remove `symbol_cache` parameter from `__init__()`
   - Add document symbol caching (see A2)
   - Remove all old cache-related logic

2. **`src/cli.py`:**
   - Remove cache directory initialization
   - Remove cache-related CLI options

**Changes to `CallTreeBuilder.__init__()`:**

```python
# BEFORE
def __init__(
    self,
    clangd_client: ClangdClient,
    filter_config: FilterConfig,
    scope_root: str,
    symbol_cache: Optional[SymbolCache] = None,  # REMOVE
    max_depth: int = 100,
    max_nodes: int = 10000,
    verbose: bool = False
):
    # ...
    self.cache = symbol_cache  # REMOVE

# AFTER
def __init__(
    self,
    clangd_client: ClangdClient,
    filter_config: FilterConfig,
    scope_root: str,
    max_depth: int = 100,
    max_nodes: int = 10000,
    verbose: bool = False
):
    # ...
    # Document symbol cache (NEW)
    self.file_symbols_cache: Dict[str, List[Dict]] = {}
    self.document_symbol_cache: Dict[str, SymbolInfo] = {}
```

**Benefits:**
- Simpler codebase
- No cache invalidation issues
- Leverages Clangd's optimized index
- Reduces disk I/O
- Faster startup (no disk cache loading)

**Trade-offs:**
- Higher memory usage (in-memory symbol cache instead of disk cache)
- No persistent cache across runs

---

## Phase B: Medium Priority Optimizations

### B1. Batch LSP Requests

#### Problem
Each function resolution requires individual LSP requests:
- `textDocument/definition` for each call
- `textDocument/documentSymbol` for each file
- Sequential requests cause cumulative latency

**Impact:** Network/IPC overhead multiplied by number of requests.

#### Solution Design

**Approach:** Parallelize independent LSP requests using ThreadPoolExecutor.

**Implementation:**

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

class ClangdClient:
    def __init__(self, ..., max_concurrent: int = 10):
        self.max_concurrent = max_concurrent
        self.request_semaphore = threading.Semaphore(max_concurrent)

    def batch_definition_requests(
        self,
        requests: List[Dict[str, Any]]
    ) -> List[Optional[Dict]]:
        """
        Send multiple definition requests in parallel.

        Args:
            requests: List of {"uri": str, "line": int, "character": int}

        Returns:
            List of results (same order as requests)
        """
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            futures = {
                executor.submit(
                    self.textDocument_definition,
                    req['uri'],
                    req['line'],
                    req['character']
                ): idx
                for idx, req in enumerate(requests)
            }

            results = [None] * len(requests)

            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    self._log(f"Request {idx} failed: {e}")

            return results
```

**Integration Point:** Use batch requests when resolving multiple calls in the same function.

**Benefits:**
- Parallelizes network/IPC latency
- Better utilization of Clangd's indexing
- **2-5x speedup** for batch operations

**Trade-offs:**
- Requires thread-safe ClangdClient
- May overwhelm Clangd if too many concurrent requests
- Increased memory usage

---

### B2. Parallel Processing

#### Problem
Call tree construction is inherently sequential (must resolve caller before callees). However, independent branches can be processed in parallel.

**Impact:** Underutilization of multi-core systems for large call trees.

#### Solution Design

**Approach:** Parallelize expansion of independent sibling branches.

**Algorithm:**

```python
from concurrent.futures import ThreadPoolExecutor

class CallTreeBuilder:
    def __init__(self, ..., thread_pool_size: int = 4):
        self.thread_pool_size = thread_pool_size

    def _expand_children_parallel(
        self,
        node: CallGraphNode,
        file_path: str,
        symbol_info: SymbolInfo
    ) -> None:
        """
        Expand children of a node using parallel processing.
        """
        if self.processed_nodes >= self.max_nodes:
            return

        if node.depth >= self.max_depth:
            return

        if node.is_external:
            return

        # Find function calls
        calls = self._find_function_calls(file_path, symbol_info)

        # Group calls into batches for parallel processing
        batch_size = self.thread_pool_size

        for i in range(0, len(calls), batch_size):
            batch = calls[i:i + batch_size]

            with ThreadPoolExecutor(max_workers=self.thread_pool_size) as executor:
                futures = {
                    executor.submit(
                        self._process_call,
                        call_name,
                        call_line,
                        file_path,
                        node.depth + 1
                    ): (call_name, call_line)
                    for call_name, call_line in batch
                }

                for future in as_completed(futures):
                    call_name, call_line = futures[future]
                    try:
                        result = future.result()
                        if result:
                            # Add child to adjacency list
                            child_index = result
                            node.children.append(child_index)

                            # Recursively expand (could also be parallel)
                            child_node = self.graph.get_node(child_index)
                            self._expand_children_parallel(
                                child_node,
                                child_node.file_path,
                                child_node.start_line,
                                child_node.end_line
                            )

                    except Exception as e:
                        self._log(f"Error processing {call_name}: {e}")

    def _process_call(
        self,
        call_name: str,
        call_line: int,
        caller_file: str,
        new_depth: int
    ) -> Optional[int]:
        """
        Process a single function call (thread-safe).

        Returns:
            Node index if created, None otherwise
        """
        # Check if already processed
        cache_key = f"{call_name}"
        if cache_key in self.visited_functions:
            return None

        self.visited_functions.add(cache_key)
        self.processed_nodes += 1

        # Resolve call target
        call_target = self._resolve_call(call_name, call_line, caller_file)

        if not call_target:
            return None

        # Check scope filter
        if not self._should_include(call_target["file_path"]):
            return None

        # Create node
        node_index = self._create_node(call_target)

        return node_index
```

**Integration Point:** Replace sequential `_expand_children()` with parallel version.

**Benefits:**
- Utilizes multiple cores
- Faster for wide call trees (many siblings)
- Better scalability for large projects

**Trade-offs:**
- Increased complexity
- Thread safety concerns
- May overwhelm Clangd with concurrent requests

---

### B3. Reduce Logging

#### Problem
Current implementation logs every function call resolution, causing massive output for large projects. This:
- Clutters terminal output
- Slows down execution (I/O overhead)
- Makes debugging difficult

**Impact:** Unnecessary verbosity, performance degradation.

#### Solution Design

**Approach:** Only log errors and warnings. Remove verbose per-function logs.

**Logging Levels:**

```python
import logging

# Configure logging
logging.basicConfig(
    level=logging.WARNING,  # Only warnings and errors
    format='%(levelname)s: %(message)s'
)

class CallTreeBuilder:
    def __init__(self, ..., verbose: bool = False):
        self.logger = logging.getLogger('CallTreeBuilder')
        self.verbose = verbose

    def _log(self, message: str, level: int = logging.INFO) -> None:
        """
        Log message at specified level.

        Args:
            message: Message to log
            level: Logging level (default: INFO)
        """
        if self.verbose or level >= logging.WARNING:
            self.logger.log(level, message)

    # Only log errors and warnings by default
    # Remove all self._log() calls for routine operations
```

**Changes Required:**

1. Remove per-function logs in `_expand_children()`
2. Remove per-resolution logs in `_resolve_call()`
3. Remove per-symbol logs in `_get_function_info()`
4. Keep logs for:
   - Entry point not found
   - Cycle detection
   - Max depth/nodes reached
   - Connection failures
   - File I/O errors

**Benefits:**
- Cleaner output
- Faster execution (less I/O)
- Easier to spot errors

**Trade-offs:**
- Less visibility into progress (use separate progress indicator)

---

### B4. Call Target Resolution Cache (NEW)

#### Problem
The same function call can be resolved multiple times across different branches. Each resolution requires a `textDocument/definition` LSP request (~50-100ms).

**Impact:** Duplicate LSP requests for identical call targets, wasting time.

#### Solution Design

**Approach:** Cache resolved call targets in memory during tree construction.

**Implementation:**

```python
from dataclasses import dataclass, field
from typing import Dict, Optional

@dataclass
class CallTargetCache:
    """Cache resolved call targets to avoid duplicate lookups."""
    _cache: Dict[str, Dict] = field(default_factory=dict)

    def get_or_resolve(
        self,
        caller_file: str,
        call_name: str,
        line: int,
        resolver_func
    ) -> Optional[Dict]:
        """
        Get cached result or resolve call target.

        Args:
            caller_file: File containing the call
            call_name: Name of function being called
            line: Line number of call (1-based)
            resolver_func: Function to call if not cached

        Returns:
            Resolved target info or None
        """
        cache_key = f"{caller_file}:{call_name}:{line}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Resolve and cache
        result = resolver_func(call_name, line, caller_file)
        if result:
            self._cache[cache_key] = result

        return result

    def clear(self) -> None:
        """Clear cache."""
        self._cache.clear()
```

**Integration Point:** Add `CallTargetCache` to `CallTreeBuilder` and use in `_resolve_call()`.

```python
class CallTreeBuilder:
    def __init__(self, ...):
        # ...
        self.call_target_cache = CallTargetCache()

    def _resolve_call(
        self,
        function_name: str,
        line: int,
        caller_file: str
    ) -> Optional[Dict[str, Any]]:
        """Resolve call target with caching."""
        return self.call_target_cache.get_or_resolve(
            caller_file,
            function_name,
            line,
            lambda name, line_num, file: self._resolve_call_impl(name, line_num, file)
        )
```

**Benefits:**
- **3-10x speedup** for repeated call targets
- Eliminates duplicate LSP requests
- Simple in-memory cache (no disk I/O)

**Trade-offs:**
- Increased memory usage (stores resolved targets)
- Cache is per-run only (no persistence)

---

### B5. Document Symbol Request Batching (NEW)

#### Problem
Document symbol requests are made once per file, but sequentially. For N files, this is N × 50-100ms.

**Impact:** Sequential symbol loading wastes time on independent requests.

#### Solution Design

**Approach:** Batch document symbol requests in parallel, similar to B1 but for symbols.

**Implementation:**

```python
class ClangdClient:
    def batch_document_symbol_requests(
        self,
        uris: List[str]
    ) -> List[List[Dict]]:
        """
        Request document symbols for multiple files in parallel.

        Args:
            uris: List of document URIs

        Returns:
            List of symbol lists (same order as uris)
        """
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            futures = {
                executor.submit(self.textDocument_documentSymbol, uri): idx
                for idx, uri in enumerate(uris)
            }

            results = [None] * len(uris)

            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    self._log(f"Symbol request {idx} failed: {e}")

            return results
```

**Integration Point:** Use in `CallTreeBuilder.preload_source_directory()` to batch-load symbols.

**Benefits:**
- **2-5x speedup** for initial symbol loading
- Better parallelism for large projects
- Amortizes over all function lookups

**Trade-offs:**
- Increased initial memory usage
- May overwhelm Clangd with concurrent requests

---

## Module Structure Changes

### New Files
- `src/call_graph.py` - Adjacency list data structures
- `src/reference_tracker.py` - Backward tracking implementation
- `src/call_target_cache.py` - Call target resolution cache

### Modified Files
- `src/call_tree_builder.py` - Major refactoring (A1-A5, B4-B5)
- `src/clangd_client.py` - Add `textDocument/references`, batch methods
- `src/output_formatter.py` - Support adjacency list output
- `src/cli.py` - Remove cache options, add backward tracking flag

### Deleted Files
- `src/cache.py` - Entire file deleted (A5)

### New Dependencies
- `concurrent.futures` (standard library, no new install)

---

## Migration Plan

### Phase 1: Foundation (A1, A5) - 2-3 days
1. Implement `preload_source_directory()` with pre-filtering in `CallTreeBuilder`
2. Remove disk cache system (`cache.py`, disk cache-related code)
3. Add in-memory document symbol cache (see A2)
4. Update CLI to remove cache directory option
5. Test: Verify preloading works, cache removal doesn't break functionality

### Phase 2: Document Symbol Caching (A2, B5) - 2-3 weeks
1. Implement document symbol caching (file-level cache)
2. Implement call target resolution cache (B4)
3. Add document symbol request batching (B5)
4. **CRITICAL:** Test on real-world C++ codebases (LLVM, Chromium, Smart Drying Module)
5. **CRITICAL:** Achieve >99% accuracy on symbol extraction
6. Benchmark: Measure speedup vs. previous implementation

**Note:** This phase requires extensive testing with real C++ codebases to ensure correctness. The timeline reflects comprehensive testing requirements (50+ unit tests, real-world validation, performance benchmarks).

### Phase 3: Adjacency List (A3) - 4-5 days
1. Create `CallGraphNode` and `CallGraph` data structures
2. Refactor `CallTreeBuilder.build()` to build adjacency list
3. Update `OutputFormatter` to traverse adjacency list
4. Add JSON output support
5. Test: Verify output format matches specification

### Phase 4: Backward Tracking (A4) - 2-3 days
1. Add `textDocument/references` to `ClangdClient`
2. Implement `resolve_enclosing_function()`
3. Implement `build_incoming_tree_via_references()`
4. Add `--direction` CLI flag
5. Test: Verify backward tracking finds all callers

### Phase 5: Performance (B1-B3) - 3-4 days (optional)
1. Implement batch LSP requests (B1)
2. Implement parallel processing for independent branches (B2)
3. Reduce logging verbosity (B3)
4. Benchmark: Measure performance improvements

---

## Testing Strategy

### Unit Tests (215+ total)

#### Document Symbol Caching Tests (50+)
1. Cache hit/miss scenarios
2. Multiple function lookups in same file
3. Cross-file function lookups
4. Cache invalidation (file changes)
5. Edge cases: duplicate function names, overloads

#### Call Target Cache Tests (50+)
1. Duplicate call targets in different branches
2. Cache key collision handling
3. Resolver function errors
4. Cache clearing
5. Memory usage under load

#### Adjacency List Tests (50+)
1. Single function (no calls)
2. Linear call chain (A → B → C)
3. Binary tree (A → B, C → D, E, F)
4. Multiple parents (A, B → C)
5. Cycles (A → B → A)
6. External calls

#### Backward Tracking Tests (50+)
1. Function with no callers (entry point)
2. Function with one caller
3. Function with multiple callers
4. Cross-file callers
5. Depth-limited traversal

#### Integration Tests (15+)
1. Small project test (10-20 files)
2. Medium project test (100-200 files)
3. Large project test (500+ files)
4. Smart Drying Module test (real project)

### Real-World Validation

**Test Against:**
1. LLVM codebase (modern C++17, heavy templates)
2. Chromium codebase (massive scale)
3. Qt framework (real-world complexity)
4. Smart Drying Module (actual use case)

**Success Criteria:**
- **>99% symbol extraction accuracy**
- All test projects complete successfully
- Performance benchmarks meet targets

### Performance Benchmarks

**Measure:**
1. Time for each optimization phase
2. Compare before/after metrics:
   - Startup time
   - Function resolution time
   - Total tree construction time
   - Memory usage
   - Peak LSP request count

**Target Metrics:**
- **20-500x overall performance improvement**
- <2 seconds for 100-file project
- <10 seconds for 500-file project
- Memory usage <500MB
- **>80% cache hit rate** (document symbol cache, call target cache)

---

## Risk Assessment

### High Risk
- **Document Symbol Caching Accuracy:** May fail on complex code
  - **Mitigation:** Uses LSP's built-in AST (reliable), extensive testing on LLVM/Chromium

### Medium Risk
- **Adjacency List Complexity:** May introduce bugs in refactoring
  - **Mitigation:** Incremental migration, thorough testing
- **Parallel Processing:** May overwhelm Clangd
  - **Mitigation:** Limit thread pool size, monitor errors

### Low Risk
- **Preloading:** May open unnecessary files
  - **Mitigation:** Apply scope filters BEFORE opening (FIXED in A1)
- **Cache Removal:** May slow down repeated lookups
  - **Mitigation:** In-memory caches (document symbols, call targets) mitigate this
- **Reduced Logging:** May reduce debuggability
  - **Mitigation:** Keep verbose flag for debugging

---

## Success Criteria

The optimization is successful if:

1. **Performance:**
   - [ ] 20-500x faster than current implementation
   - [ ] <2 seconds for 100-file project
   - [ ] <10 seconds for 500-file project
   - [ ] >80% cache hit rate

2. **Correctness:**
   - [ ] All unit tests pass (215+ tests)
   - [ ] All integration tests pass
   - [ ] Symbol extraction accuracy >99% on real C++ codebases
   - [ ] Output accuracy matches or exceeds current implementation

3. **Functionality:**
   - [ ] Backward tracking works correctly
   - [ ] JSON output format is valid
   - [ ] CLI interface remains functional

4. **Quality:**
   - [ ] Code follows PEP 8 guidelines
   - [ ] All public APIs documented
   - [ ] Reasonable code complexity

---

## Timeline Estimate

- **Phase 1 (A1, A5):** 2-3 days
- **Phase 2 (A2, B4, B5):** 2-3 weeks (includes comprehensive testing on LLVM/Chromium)
- **Phase 3 (A3):** 4-5 days
- **Phase 4 (A4):** 2-3 days
- **Phase 5 (B1-B3):** 3-4 days (optional)
- **Testing & Bug Fixes:** 3-5 days

**Total Estimated:** 9-12 weeks for full implementation

**Note:** Phase 2 requires extensive testing with real-world C++ codebases. The 2-3 week timeline accounts for:
- 50+ unit tests for symbol caching and call target caching
- Real-world validation (LLVM, Chromium, Smart Drying Module)
- Performance benchmarks
- Debugging edge cases (templates, lambdas, macros)

---

## References

- LSP Specification: https://microsoft.github.io/language-server-protocol/
- Clangd Documentation: https://clangd.llvm.org/
- `textDocument/references`: https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#textDocument_references
- Adjacency List Data Structure: Standard graph representation
- Thread Pool Pattern: Python `concurrent.futures` documentation
- Real-World Codebases for Testing:
  - LLVM: https://github.com/llvm/llvm-project
  - Chromium: https://chromium.googlesource.com/
  - Qt: https://code.qt.io/cgit/qt/qt5/

---

**Document Status:** Final Version - Ready for Linus Review
**Changes from Previous Version:**
- ❌ **REMOVED:** All brace matching code (A2)
- ✅ **ADDED:** Document symbol caching + batching (A2-REVISED)
- ✅ **FIXED:** Preload filter order (apply filters BEFORE opening)
- ✅ **ADDED:** Call target resolution cache (B4)
- ✅ **ADDED:** Document symbol request batching (B5)
- ✅ **FIXED:** Timeline (Phase 2 now 2-3 weeks with comprehensive testing)

**Next Phase:** Developer Implementation (after Linus Reviewer approval)
