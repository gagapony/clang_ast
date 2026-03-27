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
        project_path: str,
        max_depth: int = 100,
        max_nodes: int = 10000,
        verbose: bool = False,
        callback_config: str = None
    ):
        """
        Initialize call graph builder.

        Args:
            clangd_client: Clangd JSON-RPC client
            filter_config: Filter configuration (defines scope + file inclusion)
            project_path: Project root directory (for fallback search)
            max_depth: Maximum recursion depth
            max_nodes: Maximum nodes to process
            verbose: Enable verbose logging
            callback_config: Callback API configuration file path
        """
        self.client = clangd_client
        self.filter_config = filter_config
        self.project_path = os.path.abspath(project_path)
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.verbose = verbose
        self.callback_config = callback_config

        # Graph storage
        self.nodes: List[CallGraphNode] = []
        self.node_map: Dict[str, int] = {}  # "filepath:line" -> index
        self.next_id = 0

        # Track processed edges to avoid duplicates
        self.processed_outgoing = set()  # (caller_id, callee_id)
        self.processed_incoming = set()  # (caller_id, callee_id)

        # Track opened files
        self.opened_files: Set[str] = set()

        # Pre-resolved definition cache for indirect targets (ioctl handlers, func_map targets)
        # Maps target_name -> (file_path, line_0)
        self._target_def_cache: Dict[str, Tuple[str, int]] = {}

        self._log(f"CallGraphBuilder initialized, filter_rules={len(self.filter_config.rules)}")

    def _log(self, message: str) -> None:
        """Log message if verbose mode is enabled."""
        if self.verbose:
            print(f"[CallGraphBuilder] {message}")

    def _pre_resolve_indirect_targets(self) -> None:
        """
        Pre-resolve all indirect call targets (ioctl handlers + func_map targets)
        to avoid repeated _fallback_search_definition calls during traversal.
        Uses search_dir from func_map to scope the search (non-recursive).
        """
        from .callback_config import _get_config

        cfg = _get_config(self.callback_config)
        data = cfg.load()

        # Collect all unique targets with their search directories
        # target_name -> search_dir (empty string = full project fallback)
        target_dirs: Dict[str, str] = {}

        # ioctl handlers (use path hint from config, non-recursive)
        ioctl_data = data.get("ioctl_map", {})
        ioctl_path = ioctl_data.get("path", "")
        ioctl_commands = ioctl_data.get("commands", {})
        for handler in ioctl_commands.values():
            if handler not in target_dirs:
                target_dirs[handler] = ioctl_path

        # func_map targets (use search_dir from config)
        func_map = data.get("func_map", {})
        for expr, info in func_map.items():
            if isinstance(info, dict):
                search_dir = info.get("search_dir", "")
                targets = info.get("targets", [])
                for t in targets:
                    if t not in target_dirs:
                        target_dirs[t] = search_dir

        if not target_dirs:
            return

        self._log(f"Pre-resolving {len(target_dirs)} indirect targets...")

        resolved = 0
        for target_name, search_dir in target_dirs.items():
            if search_dir:
                def_line = self._search_in_dir(search_dir, target_name)
            else:
                def_line = self._fallback_search_definition(target_name)

            if def_line is not None:
                self._target_def_cache[target_name] = def_line
                resolved += 1
                self._log(f"  Cached: {target_name} → {def_line[0]}:{def_line[1]}")
            else:
                self._log(f"  Not found: {target_name}")

        self._log(f"Pre-resolved {resolved}/{len(target_dirs)} targets")

    def _search_in_dir(self, rel_dir: str, function_name: str) -> Optional[Tuple[str, int]]:
        """
        Search for function definition in a specific directory (non-recursive).

        Args:
            rel_dir: Relative directory path (from project root)
            function_name: Function name to search for

        Returns:
            (file_path, line_0) or None
        """
        import glob
        abs_dir = os.path.join(self.project_path, rel_dir)
        if not os.path.isdir(abs_dir):
            self._log(f"  Search dir not found: {abs_dir}")
            return None

        search_patterns = ["*.c", "*.cpp", "*.cc", "*.cxx"]
        for pattern in search_patterns:
            for file_path in glob.glob(os.path.join(abs_dir, pattern)):
                abs_path = os.path.abspath(file_path)
                if not os.path.isfile(abs_path):
                    continue
                line_0 = self._search_for_definition_in_file(abs_path, function_name)
                if line_0 is not None:
                    return (abs_path, line_0)
        return None

    def _make_node_id(self, file_path: str, line: int) -> str:
        """Create unique node ID from file path and line number."""
        return f"{file_path}:{line}"

    def _search_for_definition_in_file(self, file_path: str, function_name: str) -> Optional[int]:
        """
        Search for function definition within a file.

        Args:
            file_path: Path to source file
            function_name: Function name to search for (bare or qualified like "ClassName::funcName")

        Returns:
            Line number of definition (0-based), or None if not found
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                lines = content.splitlines(True)

            # Build search patterns based on whether function_name is qualified
            patterns = []

            if '::' in function_name:
                # Qualified name like "PWMDriver::setPTCPWM"
                escaped = re.escape(function_name)
                # Single-line: ... PWMDriver::setPTCPWM(...) {
                patterns.append(rf'\b{escaped}\s*\([^)]*\)\s*\{{')
                # Multi-line: ... PWMDriver::setPTCPWM(...)\n{
                patterns.append(rf'\b{escaped}\s*\([^)]*\)\s*\n\s*\{{')
                # Definition with return type on same line
                patterns.append(rf'\b{escaped}\s*\(')
            else:
                # Bare name like "computePTCPWM"
                escaped = re.escape(function_name)
                # Bare function definition: funcName(...) {
                patterns.append(rf'\b{escaped}\s*\([^)]*\)\s*\{{')
                # Member function definition: ClassName::funcName(...) {
                patterns.append(rf'\b\w+::{escaped}\s*\([^)]*\)\s*\{{')
                # Multi-line variants: opening brace on next line
                patterns.append(rf'\b{escaped}\s*\([^)]*\)\s*\n\s*\{{')
                patterns.append(rf'\b\w+::{escaped}\s*\([^)]*\)\s*\n\s*\{{')

            # Try each pattern against full content (handles multi-line)
            for pattern in patterns:
                m = re.search(pattern, content, re.MULTILINE)
                if m:
                    # Find which line this match is on
                    line_num = content[:m.start()].count('\n')
                    return line_num

            # Fallback: search line by line for partial matches
            for i, line in enumerate(lines):
                clean_line = re.sub(r'//.*|/\*.*\*/', '', line)
                for pattern in patterns:
                    if re.search(pattern, clean_line):
                        return i

            return None
        except Exception:
            return None

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

                # Look for brief comment above function definition
                brief = self._extract_brief_comment(lines, idx)

                # Reject brief that's just the function name (redundant)
                if brief and brief.lower() == default_name.lower():
                    brief = ""
        except Exception:
            pass

        return qualified_name, brief

    def _fallback_search_definition(self, function_name: str) -> Optional[Tuple[str, int]]:
        """
        Search project files for a function definition by name.

        Returns:
            (file_path, line_0) or None
        """
        import glob
        search_patterns = ["**/*.cpp", "**/*.c", "**/*.cc", "**/*.cxx"]
        for pattern in search_patterns:
            for file_path in glob.glob(os.path.join(self.project_path, pattern), recursive=True):
                abs_path = os.path.abspath(file_path)
                if not os.path.isfile(abs_path):
                    continue
                if not self._should_include(abs_path):
                    continue
                line_0 = self._search_for_definition_in_file(abs_path, function_name)
                if line_0 is not None:
                    return (abs_path, line_0)
        return None

    def _extract_brief_comment(self, lines: List[str], def_line_idx: int) -> str:
        """
        Extract brief from /** @brief ... */ block above function definition.

        Rules:
        - ONLY extracts from /** ... */ blocks containing @brief
        - Everything between @brief and next @tag (or end of block) is the brief
        - Multi-line joined with spaces, * prefixes stripped
        - All other comments (//, /* */, license, etc.) are IGNORED
        """
        # Find closing */ above function definition (up to 8 lines)
        closing_idx = None
        for i in range(def_line_idx - 1, max(-1, def_line_idx - 8), -1):
            if i < 0 or i >= len(lines):
                break
            if lines[i].strip().endswith('*/'):
                closing_idx = i
                break

        if closing_idx is None:
            return ""

        # Collect lines from closing */ upward to opening /**/
        block_lines = []
        found_opening = False
        for i in range(closing_idx, max(-1, closing_idx - 20), -1):
            line = lines[i].strip()
            block_lines.insert(0, line)
            if line.startswith('/**'):
                found_opening = True
                break

        if not found_opening:
            return ""

        # Join all lines, strip * prefixes, preserve blank lines as separators
        parts = []
        for line in block_lines:
            content = re.sub(r'^/\*{0,2}\s*', '', line)
            content = re.sub(r'\*/$', '', content)
            content = re.sub(r'^\s*\*+\s?', '', content)
            content = content.strip()
            # Keep empty lines as separator (don't filter out)
            parts.append(content)

        if not parts:
            return ""

        # Join with \n so blank lines become separators in the text
        full_text = '\n'.join(parts)

        # Extract @brief content: stop at @tag, or blank line (paragraph break)
        match = re.search(r'@brief\s+(.+?)(?=\s+@\w+|\n\s*\n|$)', full_text, re.DOTALL)
        if not match:
            return ""

        brief = match.group(1).strip()
        # Collapse whitespace and newlines into single space
        brief = re.sub(r'\s+', ' ', brief)

        # Truncate: first sentence or 120 chars
        sentence_end = re.search(r'[.!?。]\s', brief)
        if sentence_end and sentence_end.end() < 120:
            brief = brief[:sentence_end.end()].strip()
        elif len(brief) > 120:
            brief = brief[:117].strip() + '...'

        return brief

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
        """Check if file is within scope (defined by filter.cfg)."""
        return self._should_include(file_path)

    def _should_include(self, file_path: str) -> bool:
        """Check if file should be included based on filter config (relative to project root)."""
        abs_path = os.path.abspath(file_path)
        try:
            rel_path = os.path.relpath(abs_path, self.project_path)
        except ValueError:
            rel_path = abs_path
        return self.filter_config.should_include(rel_path)

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
    ) -> List[Tuple[str, int, int, bool]]:
        """
        Find function calls within a function body.

        Returns list of (function_name, line, character, is_callback_api) tuples.
        is_callback_api indicates if this is a callback API call.

        Catches:
        - Direct calls: func(...)
        - Member calls: obj.method(...) -> reports method name
        - Qualified calls: Class::method(...) -> reports full qualified name
        """
        calls = []
        keywords = {'if', 'while', 'for', 'switch', 'sizeof', 'return',
                    'catch', '__attribute__', 'new', 'delete'}

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            # Load config once before the loop (not every line)
            from .callback_config import is_callback_api, _get_config
            cfg = _get_config(self.callback_config)
            ioctl_re = cfg.parse_ioctl_format()
            ioctl_commands = cfg.load().get("ioctl_map", {}).get("commands", {}) if ioctl_re else {}

            for i in range(max(0, start_line_0), min(end_line_0, len(lines))):
                line = re.sub(r'//.*', '', lines[i])

                # Pass 1: Find qualified calls Class::method(...)
                # Must check BEFORE bare name pass to avoid duplicating method names
                for match in re.finditer(r'\b([a-zA-Z_]\w*::[a-zA-Z_]\w*)\s*\(', line):
                    func_name = match.group(1)
                    base_name = func_name.split('::')[-1]
                    if base_name not in keywords:
                        is_callback, _ = is_callback_api(func_name, self.callback_config)
                        calls.append((func_name, i, match.start(1), is_callback))

                # Pass 2: Find direct function calls: func() and member calls obj.method()
                # Track positions already covered by qualified calls
                covered_ranges = set()
                for match in re.finditer(r'\b([a-zA-Z_]\w*::[a-zA-Z_]\w*)\s*\(', line):
                    for pos in range(match.start(), match.end()):
                        covered_ranges.add(pos)

                for match in re.finditer(r'\b([a-zA-Z_]\w*)\s*\(', line):
                    func_name = match.group(1)
                    if func_name not in keywords and match.start() not in covered_ranges:
                        is_callback, _ = is_callback_api(func_name, self.callback_config)
                        calls.append((func_name, i, match.start(1), is_callback))

                # Pass 3: Find member calls obj.method(...), extract method name
                for match in re.finditer(r'(?<!::)\b([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)\s*\(', line):
                    obj_name = match.group(1)
                    method_name = match.group(2)
                    if method_name not in keywords and obj_name not in keywords:
                        found, targets = cfg.is_func_ptr_call(obj_name, method_name)
                        if found and targets:
                            calls.append((method_name, i, match.start(2), "func_map", targets))
                        else:
                            is_callback, _ = is_callback_api(method_name, self.callback_config)
                            calls.append((method_name, i, match.start(2), is_callback))

                # Pass 4: Find pointer member calls obj->method(...)
                for match in re.finditer(r'\b([a-zA-Z_]\w*)->([a-zA-Z_]\w*)\s*\(', line):
                    obj_name = match.group(1)
                    method_name = match.group(2)
                    if method_name not in keywords and obj_name not in keywords:
                        found, targets = cfg.is_func_ptr_call(obj_name, method_name)
                        if found and targets:
                            calls.append((method_name, i, match.start(2), "func_map", targets))
                        else:
                            is_callback, _ = is_callback_api(method_name, self.callback_config)
                            calls.append((method_name, i, match.start(2), is_callback))

                # Pass 5: Find function pointer args (obj.method NOT followed by ()
                # e.g., LDC_CHECK_FEATURE(g_stWrapIntf.stWrapLdc.SetLDCAttr, ...)
                # Direct calls (obj.method(...)) are already caught by Pass 3.
                # Here we match func_map keys appearing without trailing '('.
                func_map_entries = cfg.get_all_func_ptr_entries()
                for expr, info in func_map_entries.items():
                    idx = line.find(expr)
                    while idx != -1:
                        after_idx = idx + len(expr)
                        # Skip if followed by ( (direct call, handled by Pass 3/4)
                        rest = line[after_idx:].lstrip()
                        if not rest.startswith('('):
                            targets = info.get("targets", [])
                            if targets:
                                calls.append((expr, i, idx, "func_map", targets))
                        idx = line.find(expr, after_idx)

                # Pass 6: Detect ioctl-like calls matching configured format pattern
                if ioctl_re:
                    for match in ioctl_re.finditer(line):
                        for group in match.groups():
                            cmd = group.strip()
                            if cmd in ioctl_commands:
                                handler = ioctl_commands[cmd]
                                calls.append((handler, i, match.start(), "ioctl"))
                                break


        except Exception as e:
            self._log(f"Error finding calls: {e}")

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

        self._log(f"  Analyzing {caller_name} @ lines {start_0}-{end_0}")

        # Find calls in function body
        calls = self._find_calls_in_function(caller_path, start_0, end_0)

        self._log(f"  Found {len(calls)} calls in {caller_name}")

        for call_info in calls:
            # Unpack: standard is (name, line, char, is_callback), func_map adds targets
            callee_name = call_info[0]
            line_idx = call_info[1]
            char_idx = call_info[2]
            call_type = call_info[3]

            # ── func_map: direct resolution to mapped targets ──
            if call_type == "func_map":
                targets = call_info[4]  # list of function names
                self._log(f"  func_map match: {callee_name} → {targets}")
                for target_name in targets:
                    # Use pre-resolved cache, fallback to search
                    actual_def_line = self._target_def_cache.get(target_name)
                    if actual_def_line is None:
                        actual_def_line = self._fallback_search_definition(target_name)
                        if actual_def_line is not None:
                            self._target_def_cache[target_name] = actual_def_line
                    if actual_def_line is not None:
                        t_path, t_line = actual_def_line
                        callee_id = self._register_node(t_path, t_line + 1, target_name)
                        edge_key = (caller_id, callee_id)
                        if edge_key not in self.processed_outgoing:
                            self.processed_outgoing.add(edge_key)
                            self._add_edge(caller_id, callee_id)
                            if self._is_in_scope(t_path):
                                t_uri = self.client._path_to_uri(t_path)
                                self._ensure_file_opened(t_path)
                                self._build_outgoing(t_uri, target_name, t_line, callee_id, current_depth + 1)
                            else:
                                self._log(f"  External func_map target: {target_name} @ {t_path}")
                continue

            # ── ioctl: handler resolved during call detection ──
            if call_type == "ioctl":
                handler_name = callee_name  # already the resolved handler
                self._log(f"  ioctl match: handler={handler_name}")
                # Use pre-resolved cache, fallback to search
                actual_def_line = self._target_def_cache.get(handler_name)
                if actual_def_line is None:
                    actual_def_line = self._fallback_search_definition(handler_name)
                    if actual_def_line is not None:
                        self._target_def_cache[handler_name] = actual_def_line
                if actual_def_line is not None:
                    h_path, h_line = actual_def_line
                    callee_id = self._register_node(h_path, h_line + 1, handler_name)
                    edge_key = (caller_id, callee_id)
                    if edge_key not in self.processed_outgoing:
                        self.processed_outgoing.add(edge_key)
                        self._add_edge(caller_id, callee_id)
                        if self._is_in_scope(h_path):
                            h_uri = self.client._path_to_uri(h_path)
                            self._ensure_file_opened(h_path)
                            self._build_outgoing(h_uri, handler_name, h_line, callee_id, current_depth + 1)
                continue

            is_callback_api = call_type  # boolean for normal calls

            # Special handling for callback APIs
            if is_callback_api:
                # Try to resolve callback function (indirect call)
                # Skip the callback API itself, only add the callback function
                callback_results = self._resolve_callback_parameter(
                    caller_path, line_idx, callee_name
                )

                if callback_results:
                    for cb_name, cb_line, cb_char in callback_results:
                        # Resolve callback function definition
                        try:
                            result = self.client.textDocument_definition(
                                caller_uri,
                                line=cb_line,
                                character=cb_char
                            )
                        except Exception as e:
                            self._log(f"Error resolving callback {cb_name}: {e}")
                            continue

                        if result:
                            target_uri = result.get("uri") or result.get("targetUri")
                            if target_uri:
                                range_info = result.get("range") or result.get("targetRange", {})
                                start_line_0 = range_info.get("start", {}).get("line", 0)

                                cb_path = self.client._uri_to_path(target_uri)

                                cb_id = self._register_node(cb_path, start_line_0 + 1, cb_name)

                                edge_key = (caller_id, cb_id)
                                if edge_key not in self.processed_outgoing:
                                    self.processed_outgoing.add(edge_key)
                                    self._add_edge(caller_id, cb_id)

                                    if self._is_in_scope(cb_path):
                                        self._ensure_file_opened(cb_path)
                                        self._build_outgoing(target_uri, cb_name, start_line_0, cb_id, current_depth + 1)
                                    else:
                                        self._log(f"  External callback: {cb_name} @ {cb_path}")

                continue  # Skip normal processing for callback APIs

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

            # Only recurse if in scope (defined by filter.cfg)
            if self._is_in_scope(callee_path):
                # In-scope: resolve actual definition, then register + recurse

                # Check if callee is within caller's function body
                # This handles cases where Clangd returns declaration instead of definition
                in_body = (start_0 <= start_line_0 <= end_0)
                self._log(f"  Definition check: caller_lines={start_0}-{end_0}, callee_line={start_line_0}, in_body={in_body}")

                if not in_body:
                    # Try to search for actual definition in the same file
                    self._log(f"  Warning: Definition at line {start_line_0} is outside caller body")
                    actual_def_line = self._search_for_definition_in_file(
                        callee_path, callee_name
                    )
                    if actual_def_line is None and '::' in callee_name:
                        base_name = callee_name.split('::')[-1]
                        self._log(f"  Retrying with base name: {base_name}")
                        actual_def_line = self._search_for_definition_in_file(
                            callee_path, base_name
                        )
                    if actual_def_line is None and callee_path.endswith(('.h', '.hpp')):
                        cpp_candidates = []
                        base = callee_path.rsplit('.', 1)[0]
                        for ext in ('.cpp', '.cc', '.cxx', '.c'):
                            cpp_candidates.append(base + ext)
                        caller_dir = os.path.dirname(caller_path)
                        basename = os.path.basename(base)
                        for ext in ('.cpp', '.cc', '.cxx', '.c'):
                            cpp_candidates.append(os.path.join(caller_dir, basename + ext))
                        for cpp_path in cpp_candidates:
                            if os.path.isfile(cpp_path):
                                self._log(f"  Trying .cpp counterpart: {cpp_path}")
                                actual_def_line = self._search_for_definition_in_file(
                                    cpp_path, callee_name
                                )
                                if actual_def_line is None and '::' in callee_name:
                                    actual_def_line = self._search_for_definition_in_file(
                                        cpp_path, callee_name.split('::')[-1]
                                    )
                                if actual_def_line is not None:
                                    callee_path = cpp_path
                                    target_uri = self.client._path_to_uri(cpp_path)
                                    break
                    if actual_def_line is not None:
                        start_line_0 = actual_def_line
                        self._log(f"  Using actual definition at line {actual_def_line}")
                    else:
                        self._log(f"  Skipping: couldn't find definition for {callee_name}")
                        continue

                # Register node and recurse
                callee_id = self._register_node(callee_path, start_line_0 + 1, callee_name)
                edge_key = (caller_id, callee_id)
                if edge_key not in self.processed_outgoing:
                    self.processed_outgoing.add(edge_key)
                    self._add_edge(caller_id, callee_id)

                self._ensure_file_opened(callee_path)
                self._build_outgoing(target_uri, callee_name, start_line_0, callee_id, current_depth + 1)
            else:
                # Out of scope (EXTERNAL): register as leaf, no recursion
                callee_id = self._register_node(callee_path, start_line_0 + 1, callee_name)
                edge_key = (caller_id, callee_id)
                if edge_key not in self.processed_outgoing:
                    self.processed_outgoing.add(edge_key)
                    self._add_edge(caller_id, callee_id)
                self._log(f"  External: {callee_name} @ {callee_path}")

    def _resolve_callback_parameter(
        self,
        file_path: str,
        line_0: int,
        api_name: str
    ) -> List[Tuple[str, int, int]]:
        """
        Resolve callback function parameters in callback API calls.

        Args:
            file_path: File containing the API call
            line_0: Line number (0-based)
            api_name: Name of the callback API

        Returns:
            List of (callback_name, line, char) tuples
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            if line_0 >= len(lines):
                return []

            # Get callback parameter position for this API (from config)
            from .callback_config import is_callback_api
            is_callback, param_idx = is_callback_api(api_name, self.callback_config)

            if not is_callback:
                return []

            self._log(f"  Resolving callback parameter {param_idx} for API '{api_name}'")

            # For multi-line calls, accumulate lines until closing parenthesis
            lines_accum = []
            paren_count = 0
            start_line = line_0

            for i in range(start_line, len(lines)):
                line = lines[i]
                lines_accum.append(line)

                paren_count += line.count('(')
                paren_count -= line.count(')')

                if paren_count <= 0:
                    break

            full_text = ''.join(lines_accum)

            # Extract parameters (simplified parsing)
            open_paren = full_text.find('(')
            if open_paren == -1:
                return []

            params_str = full_text[open_paren + 1:]

            # Find matching closing parenthesis
            paren_count = 1
            close_paren = -1
            for i, c in enumerate(params_str):
                if c == '(':
                    paren_count += 1
                elif c == ')':
                    paren_count -= 1
                    if paren_count == 0:
                        close_paren = i
                        break

            if close_paren == -1:
                return []

            params_str = params_str[:close_paren]

            # Split by comma and extract the callback parameter
            params = [p.strip() for p in params_str.split(',')]

            if param_idx >= len(params):
                return []

            callback_param = params[param_idx]

            # Extract callback name (function identifier)
            callback_match = re.search(r'\b([a-zA-Z_]\w*)\b', callback_param)
            if not callback_match:
                return []

            callback_name = callback_match.group(1)

            # Find character position in original line
            callback_char = lines[start_line].find(callback_name)

            return [(callback_name, line_0, callback_char)]

        except Exception as e:
            self._log(f"Error resolving callback: {e}")
            return []

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

            # Register caller node
            caller_id = self._register_node(caller_path, caller_sig_line + 1, caller_name)

            # Add edge
            edge_key = (caller_id, target_id)
            if edge_key in self.processed_incoming:
                continue
            self.processed_incoming.add(edge_key)
            self._add_edge(caller_id, target_id)

            # Only recurse if in scope
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
            else:
                self._log(f"  Not recursing into external caller: {caller_name} @ {caller_path}")

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

        # Pre-resolve all indirect targets (ioctl handlers + func_map targets)
        if not self._target_def_cache:
            self._pre_resolve_indirect_targets()

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

        Only searches files matching filter.cfg rules.

        Args:
            function_name: Function name to find

        Returns:
            Root node ID, or None if not found
        """
        import glob

        # Search recursively from current directory, filter by config
        search_patterns = ["**/*.cpp", "**/*.c", "**/*.cc", "**/*.cxx"]

        for pattern in search_patterns:
            for file_path in glob.glob(os.path.join(self.project_path, pattern), recursive=True):
                abs_path = os.path.abspath(file_path)
                if not os.path.isfile(abs_path):
                    continue
                # Only search files allowed by filter.cfg
                if not self._should_include(abs_path):
                    continue

                # Open document first
                self._ensure_file_opened(abs_path)

                uri = self.client._path_to_uri(abs_path)
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
                                    abs_path, function_name, line_0
                                )

                                character_0 = self._find_function_name_position(
                                    abs_path, function_name, actual_line_0
                                )

                                self._log(
                                    f"Found '{function_name}' in {abs_path}:{actual_line_0}:{character_0}"
                                )

                                # Delegate to position-based build
                                return self.build(abs_path, actual_line_0, character_0)
                except Exception as e:
                    self._log(f"Error searching {abs_path}: {e}")

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
                "tag": "EXTERNAL" if node.is_external else "INTERNAL",
                "self": {
                    "path": node.file_path,
                    "line": [node.start_line, node.end_line],
                    "type": "function",
                    "name": node.function_name,
                    "qualified_name": node.qualified_name,
                    "brief": node.brief if node.brief else None
                },
                "parents": node.parents,
                "children": node.children
            })

        return json.dumps(graph_data, indent=2, ensure_ascii=False)

    def _format_node_text(self, node: CallGraphNode) -> str:
        """
        Format a single node for text output.

        Format: funcName [file:filename:line, brief:xxx, tag: INTERNAL/EXTERNAL]

        Args:
            node: CallGraphNode to format

        Returns:
            Formatted string
        """
        filename = os.path.basename(node.file_path)
        parts = [f"file:{filename}:{node.start_line}"]

        if node.brief:
            parts.append(f"brief: {node.brief}")
        else:
            parts.append("brief: null")

        tag = "EXTERNAL" if node.is_external else "INTERNAL"
        parts.append(f"tag: {tag}")

        return f"{node.function_name} [{', '.join(parts)}]"

    def to_tree_text(self, root_id: int, indent: int = 4, show_callers: bool = True, max_display_depth: int = 3) -> str:
        """
        Export graph as indented tree text.

        Shows both callers (who calls this function) and callees (who this function calls).

        Args:
            root_id: Root node ID
            indent: Indentation size (default: 4)
            show_callers: Whether to show caller section (default: True)
            max_display_depth: Maximum depth to display in tree (default: 3)
        """
        lines = []
        root_node = self.nodes[root_id]

        # Print root function
        lines.append(self._format_node_text(root_node))

        # Print callers (if any and enabled)
        if show_callers and root_node.parents:
            lines.append("")
            lines.append("  [Called by]")
            for parent_id in root_node.parents:
                parent = self.nodes[parent_id]
                lines.append(f"    {self._format_node_text(parent)}")

        # Print callees (if any)
        if root_node.children:
            if show_callers and root_node.parents:
                lines.append("")

            lines.append("  [Calls]")

            def print_children(node_id: int, depth: int, visited: set):
                # Prevent cycles with visited set
                if node_id in visited:
                    return

                if depth >= max_display_depth:
                    return

                visited.add(node_id)

                node = self.nodes[node_id]
                prefix = " " * (2 + depth * indent)

                lines.append(f"{prefix}{self._format_node_text(node)}")

                # Print children recursively
                for child_id in node.children:
                    print_children(child_id, depth + 1, visited.copy())

            # Print children from root
            for child_id in root_node.children:
                print_children(child_id, 1, set())

        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        """Get build statistics."""
        return {
            "total_nodes": len(self.nodes),
            "total_edges": sum(len(n.children) for n in self.nodes),
            "opened_files": len(self.opened_files),
            "external_nodes": sum(1 for n in self.nodes if n.is_external)
        }
