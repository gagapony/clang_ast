"""
Call Graph Builder - Enhanced Version

Builds bidirectional call graph using Clangd LSP.
Supports both upstream (callers) and downstream (callees) traversal.
"""

import os
import re
import json
from typing import List, Optional, Dict, Any, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

from .clangd_client import ClangdClient
from .config import FilterConfig


@dataclass
class CallGraphNode:
    """Represents a function in the call graph."""
    id: int  # Unique index
    function_name: str
    file_path: str
    start_line: int  # 1-based
    end_line: int  # 1-based
    qualified_name: str = ""
    brief: str = ""
    parents: List[int] = field(default_factory=list)  # Indices of callers
    children: List[int] = field(default_factory=list)  # Indices of callees
    is_external: bool = False


class CallGraphBuilder:
    """Builds bidirectional call graph from Clangd LSP."""

    def __init__(
        self,
        clangd_client: ClangdClient,
        filter_config: FilterConfig,
        scope_root: str,
        max_depth: int = 100,
        max_nodes: int = 10000,
        verbose: bool = False
    ):
        """
        Initialize call graph builder.

        Args:
            clangd_client: Clangd JSON-RPC client
            filter_config: Filter configuration
            scope_root: Root directory for scope control
            max_depth: Maximum recursion depth
            max_nodes: Maximum nodes to process
            verbose: Enable verbose logging
        """
        self.client = clangd_client
        self.filter_config = filter_config
        self.scope_root = os.path.abspath(scope_root)
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.verbose = verbose

        # Graph storage
        self.nodes: List[CallGraphNode] = []
        self.node_map: Dict[str, int] = {}  # "filepath:line" -> index
        self.next_id = 0

        # Track processed edges to avoid duplicates
        self.processed_outgoing = set()  # (caller_id, callee_id)
        self.processed_incoming = set()  # (caller_id, callee_id)

        # Track opened files
        self.opened_files: Set[str] = set()

        self._log(f"CallGraphBuilder initialized, scope_root={self.scope_root}")

    def _log(self, message: str) -> None:
        """Log message if verbose mode is enabled."""
        if self.verbose:
            print(f"[CallGraphBuilder] {message}")

    def _make_node_id(self, file_path: str, line: int) -> str:
        """Create unique node ID from file path and line number."""
        return f"{file_path}:{line}"

    def _ensure_file_opened(self, file_path: str) -> None:
        """Ensure file is opened in Clangd."""
        if file_path not in self.opened_files:
            try:
                self.client.open_document(file_path)
                self.opened_files.add(file_path)
                self._log(f"Opened: {file_path}")
                # Dynamic delay like reference code: increases with more files
                import time
                delay = max(0.5, len(self.opened_files) * 0.05)
                time.sleep(delay)
            except Exception as e:
                self._log(f"Failed to open {file_path}: {e}")

    def _get_word_at_position(
        self,
        file_path: str,
        line: int,
        char: int
    ) -> str:
        """Get word at position in file."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                if line < len(lines):
                    text_line = lines[line]
                    for match in re.finditer(r'[a-zA-Z_]\w*', text_line):
                        if match.start() <= char <= match.end():
                            return match.group(0)
        except Exception:
            pass
        return "UnknownFunction"

    def _get_function_range(
        self,
        file_path: str,
        start_line_0: int
    ) -> Tuple[int, int]:
        """Get function range by brace matching."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            brace_count = 0
            found_first_brace = False
            end_line = start_line_0

            for i in range(start_line_0, len(lines)):
                clean_line = re.sub(r'//.*', '', lines[i])
                brace_count += clean_line.count('{')
                brace_count -= clean_line.count('}')

                if found_first_brace and brace_count <= 0:
                    end_line = i
                    break

                if '{' in clean_line:
                    found_first_brace = True

            return (start_line_0 + 1, end_line + 1)
        except Exception:
            return (start_line_0 + 1, start_line_0 + 1)

    def _get_enclosing_function(
        self,
        file_path: str,
        target_line_0: int
    ) -> Tuple[str, int]:
        """Get enclosing function name and line."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            brace_count = 0
            for i in range(min(target_line_0, len(lines) - 1), -1, -1):
                clean_line = re.sub(r'//.*|/\*.*\*/', '', lines[i])
                brace_count += clean_line.count('}')
                brace_count -= clean_line.count('{')

                if brace_count < 0:
                    for j in range(i, max(-1, i - 6), -1):
                        m = re.search(r'\b([a-zA-Z_]\w*)\s*\(', lines[j])
                        if m:
                            name = m.group(1)
                            if name not in {'if', 'for', 'while', 'switch', 'catch', 'else'}:
                                return name, j
                    brace_count = 0
        except Exception:
            pass
        return "UnknownContext", target_line_0

    def _get_function_metadata(
        self,
        file_path: str,
        line_start_1: int,
        default_name: str
    ) -> Tuple[str, str]:
        """Get qualified name and brief comment."""
        qualified_name = default_name
        brief = ""

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            idx = line_start_1 - 1
            if 0 <= idx < len(lines):
                # Try to find Class::Method pattern
                match = re.search(r'\b([a-zA-Z_]\w*::[a-zA-Z_]\w*)\b', lines[idx])
                if match:
                    qualified_name = match.group(1)

                # Look for comment on previous line
                if idx - 1 >= 0:
                    prev_line = lines[idx - 1].strip()
                    if prev_line.startswith('//'):
                        brief = prev_line.lstrip('/').strip()
                    elif prev_line.endswith('*/'):
                        brief = prev_line.replace('/*', '').replace('*/', '').strip()
        except Exception:
            pass

        return qualified_name, brief

    def _pick_best_definition(self, defs) -> Optional[Dict]:
        """Pick best definition from multiple results."""
        if not defs:
            return None

        # If it's a single result (dict), check if it's in a source file
        if isinstance(defs, dict):
            uri = defs.get('targetUri', defs.get('uri', ''))
            # Only reject header files if there are likely alternatives
            if uri and uri.endswith('.h'):
                return None  # Signal to try alternatives or use current position
            return defs

        # If it's a list, pick best one
        if isinstance(defs, list):
            if len(defs) == 0:
                return None

            # Prefer definitions in .cpp/.c files (skip headers)
            for d in defs:
                uri = d.get('targetUri', d.get('uri', ''))
                if uri and uri.endswith(('.c', '.cpp', '.cc', '.cxx')):
                    return d

            # Fallback to first result
            return defs[0]

        return defs

    def _is_in_scope(self, file_path: str) -> bool:
        """Check if file is within scope root."""
        try:
            abs_path = os.path.abspath(file_path)
            return os.path.commonpath([self.scope_root, abs_path]) == self.scope_root
        except ValueError:
            return False

    def _should_include(self, file_path: str) -> bool:
        """Check if file should be included based on filter config."""
        if not self.filter_config.rules:
            return True
        return self.filter_config.should_include(os.path.abspath(file_path))

    def _register_node(
        self,
        file_path: str,
        line: int,
        function_name: str
    ) -> int:
        """Register a node and return its index."""
        node_id = self._make_node_id(file_path, line)

        if node_id in self.node_map:
            return self.node_map[node_id]

        # Create new node
        start_line, end_line = self._get_function_range(file_path, line - 1)
        qualified_name, brief = self._get_function_metadata(
            file_path, start_line, function_name
        )

        is_external = not self._is_in_scope(file_path)

        node = CallGraphNode(
            id=self.next_id,
            function_name=function_name,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            qualified_name=qualified_name,
            brief=brief,
            is_external=is_external
        )

        self.nodes.append(node)
        self.node_map[node_id] = self.next_id
        self.next_id += 1

        self._log(
            f"Registered node #{node.id}: {function_name} @ {file_path}:{start_line}"
        )

        return node.id

    def _add_edge(self, caller_id: int, callee_id: int) -> None:
        """Add edge between caller and callee."""
        if callee_id not in self.nodes[caller_id].children:
            self.nodes[caller_id].children.append(callee_id)
        if caller_id not in self.nodes[callee_id].parents:
            self.nodes[callee_id].parents.append(caller_id)

    def _find_calls_in_function(
        self,
        file_path: str,
        start_line_0: int,
        end_line_0: int
    ) -> List[Tuple[str, int, int]]:
        """Find function calls within a function body."""
        calls = []
        keywords = {'if', 'while', 'for', 'switch', 'sizeof', 'return',
                    'catch', '__attribute__', 'new', 'delete'}

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            for i in range(max(0, start_line_0), min(end_line_0, len(lines))):
                line = re.sub(r'//.*', '', lines[i])
                for match in re.finditer(r'\b([a-zA-Z_]\w*)\s*\(', line):
                    func_name = match.group(1)
                    if func_name not in keywords:
                        calls.append((func_name, i, match.start(1)))
        except Exception:
            pass

        return calls

    def _build_outgoing(
        self,
        caller_uri: str,
        caller_name: str,
        caller_start_line: int,
        caller_id: int,
        current_depth: int
    ) -> None:
        """Build outgoing calls (callees) using textDocument/definition."""
        if current_depth >= self.max_depth:
            return

        if len(self.nodes) >= self.max_nodes:
            self._log(f"Reached max nodes limit: {self.max_nodes}")
            return

        caller_path = self.client._uri_to_path(caller_uri)
        start_0, end_0 = self._get_function_range(caller_path, caller_start_line)

        # Find calls in function body
        calls = self._find_calls_in_function(caller_path, start_0, end_0)

        for callee_name, line_idx, char_idx in calls:
            # Resolve call target using definition
            try:
                result = self.client.textDocument_definition(
                    caller_uri,
                    line=line_idx,
                    character=char_idx
                )
            except Exception as e:
                self._log(f"Error resolving {callee_name}: {e}")
                continue

            if not result:
                continue

            # Parse target URI and line
            target_uri = result.get("uri") or result.get("targetUri")
            if not target_uri:
                continue

            range_info = result.get("range") or result.get("targetRange", {})
            start_line_0 = range_info.get("start", {}).get("line", 0)

            callee_path = self.client._uri_to_path(target_uri)

            # Check if should include this file
            if not self._should_include(callee_path):
                continue

            # Register callee node
            callee_id = self._register_node(callee_path, start_line_0 + 1, callee_name)

            # Add edge
            edge_key = (caller_id, callee_id)
            if edge_key in self.processed_outgoing:
                continue
            self.processed_outgoing.add(edge_key)
            self._add_edge(caller_id, callee_id)

            # Recurse if in scope
            if self._is_in_scope(callee_path):
                self._ensure_file_opened(callee_path)
                self._build_outgoing(target_uri, callee_name, start_line_0, callee_id, current_depth + 1)

    def _build_incoming(
        self,
        target_uri: str,
        target_name: str,
        target_line: int,
        target_char: int,
        target_id: int,
        current_depth: int
    ) -> None:
        """Build incoming calls (callers) using textDocument/references."""
        if current_depth >= self.max_depth:
            return

        if len(self.nodes) >= self.max_nodes:
            self._log(f"Reached max nodes limit: {self.max_nodes}")
            return

        # Get references to this function
        try:
            result = self.client.textDocument_references(
                target_uri,
                line=target_line,
                character=target_char
            )
        except Exception as e:
            self._log(f"Error getting references: {e}")
            return

        if not result:
            return

        for ref in result:
            caller_uri = ref['uri']
            caller_line_0 = ref['range']['start']['line']
            caller_path = self.client._uri_to_path(caller_uri)

            # Get enclosing function
            caller_name, caller_sig_line = self._get_enclosing_function(
                caller_path, caller_line_0
            )

            if caller_name == target_name or caller_name == "UnknownContext":
                continue

            # Check if should include this file
            if not self._should_include(caller_path):
                continue

            # Register caller node
            caller_id = self._register_node(caller_path, caller_sig_line + 1, caller_name)

            # Add edge
            edge_key = (caller_id, target_id)
            if edge_key in self.processed_incoming:
                continue
            self.processed_incoming.add(edge_key)
            self._add_edge(caller_id, target_id)

            # Recurse if in scope
            if self._is_in_scope(caller_path):
                # Find character position of caller name
                char_idx = 0
                try:
                    with open(caller_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        if caller_sig_line < len(lines):
                            match = re.search(r'\b' + re.escape(caller_name) + r'\b', lines[caller_sig_line])
                            if match:
                                char_idx = match.start()
                except Exception:
                    pass

                self._ensure_file_opened(caller_path)
                self._build_incoming(
                    caller_uri, caller_name, caller_sig_line, char_idx, caller_id, current_depth + 1
                )

    def build(
        self,
        file_path: str,
        line: int,
        character: int
    ) -> Optional[int]:
        """
        Build call graph starting from function at given position.

        Args:
            file_path: Source file path
            line: Line number (0-based)
            character: Character position (0-based)

        Returns:
            Root node ID, or None if not found
        """
        self._log(f"Building graph from {file_path}:{line}:{character}")

        # Ensure file is opened first
        abs_path = os.path.abspath(file_path)
        self._ensure_file_opened(abs_path)

        # Get definition of symbol at position
        uri = self.client._path_to_uri(abs_path)

        target_path = abs_path
        target_line_0 = line
        target_char_0 = character

        try:
            result = self.client.textDocument_definition(
                uri,
                line=line,
                character=character
            )

            # If definition found, use it
            if result:
                # Pick best definition (prefer .cpp/.c over headers)
                target_def = self._pick_best_definition(result)

                if target_def:
                    # Parse target
                    target_uri = target_def.get("uri") or target_def.get("targetUri")
                    if target_uri:
                        range_info = target_def.get("range") or target_def.get("targetRange", {})
                        target_line_0 = range_info.get("start", {}).get("line", 0)
                        target_char_0 = range_info.get("start", {}).get("character", 0)
                        target_path = self.client._uri_to_path(target_uri)
                        uri = target_uri  # Update uri for subsequent calls
                        self._log(f"Definition found, jumping to {target_path}:{target_line_0}")
                else:
                    # _pick_best_definition returned None (e.g., only header definition)
                    self._log(f"Only header definition found, using current position as definition")
            else:
                # No definition returned - assume position is already at definition
                self._log(f"No definition returned, using current position as definition")
        except Exception as e:
            self._log(f"Error getting definition: {e}, using current position")

        target_name = self._get_word_at_position(target_path, target_line_0, target_char_0)

        # Register root node
        root_id = self._register_node(target_path, target_line_0 + 1, target_name)

        # Ensure file is opened
        self._ensure_file_opened(target_path)

        # Build graph in both directions
        self._log(f"Building outgoing calls from {target_name}...")
        self._build_outgoing(uri, target_name, target_line_0, root_id, 0)

        self._log(f"Building incoming calls to {target_name}...")
        self._build_incoming(uri, target_name, target_line_0, target_char_0, root_id, 0)

        self._log(f"Graph built: {len(self.nodes)} nodes, {sum(len(n.children) for n in self.nodes)} edges")
        return root_id

    def build_from_function_name(self, function_name: str) -> Optional[int]:
        """
        Build call graph starting from function name.

        Uses workspace/symbol to find function location, then calls build().
        If workspace/symbol fails, uses fallback search.

        Args:
            function_name: Function name to search for

        Returns:
            Root node ID, or None if not found
        """
        self._log(f"Building graph from function name: {function_name}")

        # Try workspace/symbol first
        try:
            symbols = self.client.workspace_symbol(function_name)

            if symbols and len(symbols) > 0:
                # Filter for function definitions
                function_kinds = [5, 12, 9, 10]  # Method, Function, Constructor, Destructor
                candidates = []

                for symbol in symbols:
                    kind = symbol.get("kind", 0)
                    if kind in function_kinds:
                        candidates.append(symbol)

                if candidates:
                    # Pick best candidate: prefer .cpp/.c files, skip headers
                    best = self._pick_best_symbol(candidates)

                    if best:
                        # Extract location
                        location = best.get("location", {})
                        uri = location.get("uri")
                        range_info = location.get("range", {})

                        if uri and range_info:
                            start_info = range_info.get("start", {})
                            line_0 = start_info.get("line", 0)
                            character_0 = start_info.get("character", 0)

                            file_path = self.client._uri_to_path(uri)

                            self._log(
                                f"Found '{function_name}' at {file_path}:{line_0}:{character_0}"
                            )

                            # Delegate to position-based build
                            return self.build(file_path, line_0, character_0)
        except Exception as e:
            self._log(f"Error searching workspace symbols: {e}")

        # Fallback: manual file search
        self._log("workspace/symbol returned no results, trying fallback search...")
        return self._fallback_find_function(function_name)

    def _fallback_find_function(self, function_name: str) -> Optional[int]:
        """
        Fallback method to find function by searching source files.

        Args:
            function_name: Function name to find

        Returns:
            Root node ID, or None if not found
        """
        import glob

        # Try common source directories
        search_paths = [
            "main.cpp",
            "src/main.cpp",
            "src/*.cpp",
            "lib/*.cpp",
            "*.cpp"
        ]

        for pattern in search_paths:
            full_pattern = os.path.join(self.scope_root, pattern)
            for file_path in glob.glob(full_pattern, recursive=True):
                if os.path.isfile(file_path):
                    # Open document first
                    self._ensure_file_opened(file_path)

                    uri = self.client._path_to_uri(file_path)
                    try:
                        symbols = self.client.textDocument_documentSymbol(uri)
                        for symbol in symbols:
                            if symbol.get("name") == function_name:
                                kind = symbol.get("kind", 0)
                                if kind in (5, 12, 9, 10):  # Method, Function, Constructor, Destructor
                                    # Handle different LSP response formats
                                    location = symbol.get("location", {})
                                    if not location:
                                        location = {
                                            "uri": uri,
                                            "range": symbol.get("range", {})
                                        }

                                    # Convert to workspace/symbol format
                                    range_info = location.get("range", {})
                                    start_info = range_info.get("start", {})
                                    line_0 = start_info.get("line", 0)

                                    # Search backwards to find actual function definition line
                                    # (LSP may return range of function body, not definition line)
                                    actual_line_0 = self._find_function_definition_line(
                                        file_path, function_name, line_0
                                    )

                                    character_0 = self._find_function_name_position(
                                        file_path, function_name, actual_line_0
                                    )

                                    self._log(
                                        f"Found '{function_name}' in {file_path}:{actual_line_0}:{character_0}"
                                    )

                                    # Delegate to position-based build
                                    return self.build(file_path, actual_line_0, character_0)
                    except Exception as e:
                        self._log(f"Error searching {file_path}: {e}")

        self._log(f"Function not found: {function_name}")
        return None

    def _find_function_definition_line(
        self,
        file_path: str,
        function_name: str,
        start_line_0: int
    ) -> int:
        """
        Find actual function definition line by searching backwards.

        LSP may return the range of function body (from { to }),
        so we need to find the line with the actual function name.

        Args:
            file_path: Path to source file
            function_name: Function name to find
            start_line_0: Starting line number (0-based) from LSP

        Returns:
            Actual definition line number (0-based)
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            # Search backwards from start_line_0 (up to 10 lines)
            search_limit = max(0, start_line_0 - 10)
            for line_num in range(start_line_0, search_limit - 1, -1):
                line = lines[line_num] if line_num < len(lines) else ""
                if function_name in line:
                    # Found it
                    return line_num
        except Exception as e:
            self._log(f"Error finding definition line: {e}")

        # Fallback to original line
        return start_line_0

    def _find_function_name_position(
        self,
        file_path: str,
        function_name: str,
        line_0: int
    ) -> int:
        """
        Find character position of function name on the line.

        Args:
            file_path: Path to source file
            function_name: Function name to find
            line_0: Line number (0-based)

        Returns:
            Character position (0-based)
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            if line_0 < len(lines):
                line = lines[line_0]
                match = re.search(r'\b' + re.escape(function_name) + r'\b', line)
                if match:
                    return match.start()
        except Exception as e:
            self._log(f"Error finding function name position: {e}")

        # Fallback
        return 0

    def _pick_best_symbol(self, symbols: List[Dict]) -> Optional[Dict]:
        """
        Pick best symbol from candidates.

        Prefers .cpp/.c files over headers.

        Args:
            symbols: List of symbol candidates

        Returns:
            Best symbol, or None
        """
        if not symbols:
            return None

        # If single symbol, use it
        if len(symbols) == 1:
            return symbols[0]

        # Prefer .cpp/.c files
        for symbol in symbols:
            location = symbol.get("location", {})
            uri = location.get("uri", "")
            if uri and uri.endswith(('.cpp', '.c', '.cc', '.cxx')):
                return symbol

        # Fallback: use first match
        return symbols[0]

    def to_json(self) -> str:
        """Export graph as JSON adjacency list."""
        graph_data = []

        for node in self.nodes:
            graph_data.append({
                "index": node.id,
                "self": {
                    "path": node.file_path,
                    "line": [node.start_line, node.end_line],
                    "type": "function",
                    "name": node.function_name,
                    "qualified_name": node.qualified_name,
                    "brief": node.brief
                },
                "parents": node.parents,
                "children": node.children
            })

        return json.dumps(graph_data, indent=2, ensure_ascii=False)

    def to_tree_text(self, root_id: int, indent: int = 4) -> str:
        """Export graph as indented tree text (downstream only)."""
        lines = []

        def print_node(node_id: int, depth: int):
            node = self.nodes[node_id]
            prefix = " " * (depth * indent)
            loc = f"{os.path.basename(node.file_path)}:{node.start_line}"
            marker = " [EXTERNAL]" if node.is_external else ""

            lines.append(f"{prefix}{node.function_name} ({loc}){marker}")

            # Print children
            for child_id in node.children:
                print_node(child_id, depth + 1)

        print_node(root_id, 0)
        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        """Get build statistics."""
        return {
            "total_nodes": len(self.nodes),
            "total_edges": sum(len(n.children) for n in self.nodes),
            "opened_files": len(self.opened_files),
            "external_nodes": sum(1 for n in self.nodes if n.is_external)
        }
