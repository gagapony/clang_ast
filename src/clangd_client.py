"""
Clangd Client - Simplified Synchronous Version

JSON-RPC client for Clangd language server.
Uses synchronous blocking I/O like the reference implementation.
"""

import subprocess
import json
import os
import time
import threading
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse


class ClangdClientError(Exception):
    """Base exception for Clangd client errors."""
    pass


class ClangdConnectionError(ClangdClientError):
    """Exception raised when connection to Clangd fails."""
    pass


class ClangdRequestError(ClangdClientError):
    """Exception raised when Clangd request fails."""
    pass


class ClangdTimeoutError(ClangdClientError):
    """Exception raised when Clangd request times out."""
    pass


class ClangdClient:
    """
    Simplified JSON-RPC client for Clangd language server.

    Uses synchronous blocking I/O for reliable communication.
    """

    def __init__(
        self,
        project_path: str,
        clangd_path: str = "clangd",
        timeout: float = 30.0,
        verbose: bool = False
    ):
        """
        Initialize Clangd client.

        Args:
            project_path: Project directory path
            clangd_path: Path to clangd executable
            timeout: Request timeout in seconds
            verbose: Enable verbose logging
        """
        self.project_path = os.path.abspath(project_path)
        self.clangd_path = clangd_path
        self.timeout = timeout
        self.verbose = verbose

        self.msg_id = 1

    def _log(self, message: str) -> None:
        """Log message if verbose mode is enabled."""
        if self.verbose:
            print(f"[ClangdClient] {message}")

    def start(self) -> None:
        """
        Start Clangd process.

        Raises:
            ClangdConnectionError: If Clangd fails to start
        """
        self._log(f"Starting Clangd for project: {self.project_path}")

        # Start Clangd subprocess
        try:
            self.process = subprocess.Popen(
                [self.clangd_path, "--compile-commands-dir=" + self.project_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # Suppress stderr
                cwd=self.project_path
            )
        except FileNotFoundError:
            raise ClangdConnectionError(
                f"Clangd not found: {self.clangd_path}\n"
                f"Please install Clangd (e.g., 'apt-get install clangd' on Ubuntu)"
            )
        except subprocess.SubprocessError as e:
            raise ClangdConnectionError(
                f"Failed to start Clangd: {e}"
            )

        self.reader = self.process.stdout
        self.writer = self.process.stdin

        self._log("Clangd process started")

    def stop(self) -> None:
        """Stop Clangd process."""
        self._log("Stopping Clangd")

        try:
            # Send shutdown request
            self._send_request("shutdown", params={})
        except Exception as e:
            self._log(f"Error during shutdown: {e}")

        try:
            # Send exit notification
            self._send_notification("exit")
        except Exception as e:
            self._log(f"Error during exit: {e}")

        # Wait for process to exit
        if self.process:
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

        self._log("Clangd stopped")

    def initialize(self) -> None:
        """Initialize Clangd LSP session."""
        self._log("Initializing Clangd")

        request_id = self._send_request("initialize", {
            "processId": os.getpid(),
            "rootUri": self._path_to_uri(self.project_path),
            "capabilities": {}
        })

        response = self._read_response(request_id)
        self._log("Clangd initialized")

        # Send initialized notification
        self._send_notification("initialized", {})

    def _send_request(self, method: str, params: Optional[Dict] = None) -> int:
        """
        Send JSON-RPC request and return request ID.

        Args:
            method: JSON-RPC method name
            params: Request parameters

        Returns:
            Request ID
        """
        request = {
            "jsonrpc": "2.0",
            "id": self.msg_id,
            "method": method,
        }

        if params:
            request["params"] = params

        self._send_json(request)
        request_id = self.msg_id
        self.msg_id += 1

        return request_id

    def _send_notification(self, method: str, params: Optional[Dict] = None) -> None:
        """
        Send JSON-RPC notification (no response expected).

        Args:
            method: JSON-RPC method name
            params: Notification parameters
        """
        request = {
            "jsonrpc": "2.0",
            "method": method,
        }

        if params:
            request["params"] = params

        self._send_json(request)

    def _read_response(self, expected_id: int) -> Dict:
        """
        Read response matching expected request ID.

        Blocks until matching response is received or timeout.

        Args:
            expected_id: Expected request ID in response

        Returns:
            Response result

        Raises:
            ClangdTimeoutError: If timeout is reached
            ClangdRequestError: If JSON-RPC error is received
        """
        start_time = time.time()

        while True:
            # Check timeout
            if time.time() - start_time > self.timeout:
                raise ClangdTimeoutError(
                    f"Request timed out after {self.timeout}s (id={expected_id})"
                )

            # Read Content-Length header
            line = self.reader.readline()
            if not line:
                raise ClangdConnectionError("Connection closed by Clangd")

            line = line.decode('utf-8')
            if not line.startswith("Content-Length:"):
                continue

            # Parse length
            try:
                length = int(line.split(":")[1].strip())
            except (ValueError, IndexError):
                self._log(f"Invalid Content-Length header: {line}")
                continue

            # Skip empty line after header
            self.reader.readline()

            # Read content
            content = self.reader.read(length)
            if not content:
                raise ClangdConnectionError("Connection closed while reading content")

            # Parse JSON
            try:
                response = json.loads(content.decode('utf-8'))
                self._log(f"Received (id={response.get('id')}): {json.dumps(response)[:200]}")
            except json.JSONDecodeError as e:
                self._log(f"Failed to parse JSON: {e}")
                continue

            # Check if this is the response we're waiting for
            if response.get("id") == expected_id:
                # Check for JSON-RPC error
                if "error" in response:
                    error = response["error"]
                    raise ClangdRequestError(
                        f"JSON-RPC error (code={error.get('code')}): {error.get('message')}"
                    )

                # Return result
                return response.get("result", {})

            # Not our response, continue waiting
            continue

    def _send_json(self, data: Dict) -> None:
        """
        Send JSON data to Clangd's stdin.

        Args:
            data: Data to send (will be JSON-encoded)
        """
        content = json.dumps(data, separators=(',', ':'))
        message = f"Content-Length: {len(content)}\r\n\r\n{content}"

        self._log(f"Sending: {content[:200]}")

        self.writer.write(message.encode('utf-8'))
        self.writer.flush()

    def textDocument_definition(
        self,
        uri: str,
        line: int,
        character: int
    ) -> Optional[Dict]:
        """
        Get definition of symbol at position.

        Args:
            uri: Document URI
            line: 0-based line number
            character: 0-based character position

        Returns:
            Definition location or None
        """
        request_id = self._send_request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character}
        })

        result = self._read_response(request_id)

        if not result:
            return None

        # Handle single result or array of results
        if isinstance(result, list):
            if len(result) == 0:
                return None
            return result[0]

        return result

    def textDocument_references(
        self,
        uri: str,
        line: int,
        character: int,
        include_declaration: bool = False
    ) -> List[Dict]:
        """
        Find all references to symbol at position.

        Args:
            uri: Document URI
            line: 0-based line number
            character: 0-based character position
            include_declaration: Include declaration in results

        Returns:
            List of reference locations
        """
        request_id = self._send_request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": include_declaration}
        })

        result = self._read_response(request_id)

        if not result:
            return []

        return result

    def textDocument_documentSymbol(self, uri: str) -> List[Dict]:
        """
        Get document symbol tree.

        Args:
            uri: Document URI

        Returns:
            List of symbol nodes
        """
        request_id = self._send_request("textDocument/documentSymbol", {
            "textDocument": {"uri": uri}
        })

        result = self._read_response(request_id)

        if not result:
            return []

        return result

    def workspace_symbol(self, query: str) -> List[Dict]:
        """
        Search for symbols in workspace.

        Args:
            query: Symbol name query

        Returns:
            List of symbol information
        """
        request_id = self._send_request("workspace/symbol", {"query": query})
        result = self._read_response(request_id)

        if not result:
            return []

        return result

    def open_document(self, file_path: str) -> None:
        """
        Open a document in Clangd.

        Args:
            file_path: Absolute file path to open
        """
        uri = self._path_to_uri(file_path)

        # Read file content
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        self._send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": self._get_language_id(file_path),
                "version": 1,
                "text": content
            }
        })

        self._log(f"Opened document: {file_path}")

    @staticmethod
    def _path_to_uri(file_path: str) -> str:
        """
        Convert file path to URI.

        Args:
            file_path: Absolute file path

        Returns:
            File URI (e.g., "file:///path/to/file")
        """
        abs_path = os.path.abspath(file_path)
        return f"file://{abs_path}"

    @staticmethod
    def _uri_to_path(uri: str) -> str:
        """
        Convert URI to file path.

        Args:
            uri: File URI (e.g., "file:///path/to/file")

        Returns:
            Absolute file path
        """
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            raise ValueError(f"Unsupported URI scheme: {parsed.scheme}")

        return parsed.path

    @staticmethod
    def _get_language_id(file_path: str) -> str:
        """
        Get language ID for file.

        Args:
            file_path: File path

        Returns:
            Language ID (e.g., 'cpp', 'c')
        """
        ext = os.path.splitext(file_path)[1].lower()
        language_map = {
            '.cpp': 'cpp',
            '.cxx': 'cpp',
            '.cc': 'cpp',
            '.hpp': 'cpp',
            '.hxx': 'cpp',
            '.h': 'c',
            '.c': 'c',
            '.m': 'objc',
            '.mm': 'objc'
        }
        return language_map.get(ext, 'cpp')


# Import typing for type hints
import typing
