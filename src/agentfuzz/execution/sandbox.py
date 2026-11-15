"""The Episode sandbox: where the test Agent's tools actually run.

Everything with a side effect is contained here:

* the workspace is a per-Episode directory; ``PATH`` arguments are rewritten to
  resolve inside it and escapes are refused;
* ``run_shell`` / ``run_saved_script`` / ``get_system_date`` never reach a real
  subprocess -- a minimal interpreter serves workspace files and records every
  command, which is what TASK-10's effect probes will extend;
* ``fetch_document``'s network access is redirected to a controlled fetcher that
  serves pre-registered endpoints and refuses (records) everything else -- no
  egress;
* the metrics database is a sandbox-seeded SQLite file, not the host.

The toolset module is the analysis target and is imported unmodified; the
sandbox injects collaborators through the module seams the toolset already
declares (``_BACKEND``, ``_METRICS_DB``) and patches ``urllib.request.urlopen``
for the duration of the Episode (restored on exit).
"""

from __future__ import annotations

import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentfuzz import toolset
from agentfuzz.contracts import ToolContract

#: Tools the sandbox intercepts instead of delegating to the toolset module:
#: their real implementations reach for a real subprocess, which the sandbox
#: replaces with a recorded, workspace-contained interpreter.
INTERCEPTED_TOOLS = frozenset({"run_shell", "run_saved_script", "get_system_date"})

EPISODE_TIMESTAMP = "2026-11-15T09:00:00+0000"


class SandboxError(ValueError):
    """A tool argument or operation the sandbox refuses."""


@dataclass(frozen=True)
class SandboxFetch:
    """One ``fetch_document`` attempt, recorded whether served or refused."""

    url: str
    served: bool
    body: str

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "served": self.served, "body_chars": len(self.body)}


class _ControlledResponse:
    """Minimal urlopen stand-in serving a pre-registered body."""

    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_ControlledResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class EpisodeSandbox:
    """Per-Episode workspace, stores, controlled endpoints and the shell shim."""

    def __init__(
        self,
        root: Path,
        *,
        documents: dict[str, str] | None = None,
        metrics_rows: list[dict[str, Any]] | None = None,
        endpoints: dict[str, str] | None = None,
        contracts: dict[str, ToolContract] | None = None,
        episode_id: str = "episode",
    ) -> None:
        self.root = Path(root)
        self.documents = dict(documents or {})
        self.metrics_rows = list(metrics_rows or [])
        self.endpoints = dict(endpoints or {})
        self.contracts = dict(contracts or {})
        self.episode_id = episode_id
        self.fetches: list[SandboxFetch] = []
        self.shell_log: list[dict[str, Any]] = []
        self.published: list[dict[str, Any]] = []
        self._urlopen_original: Any = None
        self._metrics_db_path = self.root / "metrics.db"

    # -- lifecycle ---------------------------------------------------------------------------

    def __enter__(self) -> "EpisodeSandbox":
        self.root.mkdir(parents=True, exist_ok=True)
        self._seed_metrics()
        self._install_backend()
        self._install_fetch_shim()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._urlopen_original is not None:
            urllib.request.urlopen = self._urlopen_original
            self._urlopen_original = None

    def _seed_metrics(self) -> None:
        # The toolset reads its database path from this module global at call
        # time; pointing it at the seeded file is the whole injection.
        toolset._METRICS_DB = str(self._metrics_db_path)
        connection = sqlite3.connect(self._metrics_db_path)
        try:
            connection.execute("CREATE TABLE metrics (name TEXT, value REAL)")
            for row in self.metrics_rows:
                connection.execute(
                    "INSERT INTO metrics (name, value) VALUES (?, ?)",
                    (str(row.get("name")), float(row.get("value", 0))),
                )
            connection.commit()
        finally:
            connection.close()

    def _install_backend(self) -> None:
        backend = toolset._Backend()
        backend.search = lambda query: [
            {"url": url, "title": url, "snippet": body[:80]}
            for url, body in self.documents.items()
            if query.casefold() in body.casefold()
        ][:5]
        backend.summarize = lambda text: " ".join(text.split())[:200] or "(empty)"
        backend.publish = self._publish
        toolset._BACKEND = backend

    def _publish(self, title: str, body: str) -> str:
        self.published.append({"title": title, "body": body})
        return f"report-{len(self.published):03d}"

    def _install_fetch_shim(self) -> None:
        self._urlopen_original = urllib.request.urlopen

        def _controlled_fetch(url: str, timeout: int = 10) -> Any:
            body = self.endpoints.get(url)
            served = body is not None
            self.fetches.append(SandboxFetch(url=url, served=served, body=body or ""))
            if not served:
                raise urllib.error.URLError(
                    f"controlled fetch refused (endpoint not registered): {url}"
                )
            return _ControlledResponse(body)

        urllib.request.urlopen = _controlled_fetch

    # -- path containment ----------------------------------------------------------------------

    def resolve(self, raw: str) -> Path:
        candidate = Path(raw)
        if candidate.is_absolute():
            raise SandboxError(f"absolute paths are refused by the sandbox: {raw}")
        resolved = (self.root / candidate).resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise SandboxError(f"path escapes the workspace: {raw}")
        return resolved

    def _rewrite_paths(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        contract = self.contracts.get(name)
        if contract is None:
            return arguments
        rewritten = dict(arguments)
        for param in contract.params:
            if param.tag.value == "path" and isinstance(rewritten.get(param.name), str):
                rewritten[param.name] = str(self.resolve(rewritten[param.name]))
        return rewritten

    # -- shell shim ------------------------------------------------------------------------------

    def _read_ws(self, raw: str) -> str:
        return self.resolve(raw).read_text(encoding="utf-8")

    def _shell(self, command: str) -> str:
        self.shell_log.append({"command": command, "interpreter": "sandbox-shim"})
        parts = command.split()
        if not parts:
            return ""
        verb, rest = parts[0], parts[1:]
        if verb == "cat" and len(rest) == 1:
            return self._read_ws(rest[0])
        if verb == "ls" and len(rest) <= 1:
            target = self.resolve(rest[0]) if rest else self.root
            return "\n".join(sorted(item.name for item in target.iterdir()))
        if verb == "echo":
            return command[len("echo") :].strip()
        # Anything else is recorded and answered with nothing: the command text
        # itself is the observable effect TASK-10's probes will extend.
        return ""

    def _run_saved(self, raw: str) -> str:
        script = self._read_ws(raw)
        return "\n".join(self._shell(line.strip()) for line in script.splitlines() if line.strip())

    # -- dispatch ---------------------------------------------------------------------------------

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Execute one tool call inside the sandbox and return its raw result."""

        function = getattr(toolset, name, None)
        if function is None or not callable(function):
            raise SandboxError(f"unknown tool: {name}")
        arguments = self._rewrite_paths(name, dict(arguments))
        if name == "run_shell":
            return self._shell(str(arguments.get("command", "")))
        if name == "run_saved_script":
            return self._run_saved(str(arguments.get("path", "")))
        if name == "get_system_date":
            self.shell_log.append({"command": "get_system_date", "interpreter": "sandbox-shim"})
            return EPISODE_TIMESTAMP + "\n"
        return function(**arguments)

    # -- read-outs ---------------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "fetches": [item.to_dict() for item in self.fetches],
            "shell_log": list(self.shell_log),
            "published": list(self.published),
        }
