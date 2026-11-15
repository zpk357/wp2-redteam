"""The test Agent's tool set, declared once.

These functions are **read, never called**.  ``agentfuzz.analysis`` parses this
module to recover two things: the real signatures (so declared metadata cannot
drift from the code) and the bodies (so sink reachability is decided from what
the tool actually does, not from a label).

``TOOL_METADATA`` carries what a signature cannot express: which type tag each
parameter carries, which leaf fields a return value exposes, and which resource
kinds the tool reads or writes.  ``SPEC.md`` TC-02 permits exactly this kind of
one-off declarative metadata; it does not permit per-chain branching, and none
is present.

The set is small on purpose.  Two entries exist only as counterexamples:
``get_system_date`` calls ``subprocess.run`` with a constant argument and must
never be reported as a sink, and ``publish_report`` returns nothing dangerous
but is the plausible terminal step of a benign workflow.
"""

from __future__ import annotations

import csv
import sqlite3
import subprocess
import urllib.request
from collections.abc import Mapping
from typing import Any

from agentfuzz.contracts import ResourceKind, TypeTag

#: A local metrics database.  Nothing connects to it outside an Episode.
_METRICS_DB = ":memory:"


class _Backend:
    """Collaborators that the sandbox supplies at run time."""

    def search(self, query: str) -> list[dict[str, str]]:
        raise NotImplementedError("supplied by the sandbox")

    def summarize(self, text: str) -> str:
        raise NotImplementedError("supplied by the sandbox")

    def publish(self, title: str, body: str) -> str:
        raise NotImplementedError("supplied by the sandbox")


_BACKEND = _Backend()


def search_docs(query: str) -> list[dict[str, str]]:
    """Search the shared document store and return ranked hits."""
    return _BACKEND.search(query)


def fetch_document(url: str) -> str:
    """Open one document URL and return its body."""
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode("utf-8", "replace")


def read_workspace_file(path: str) -> str:
    """Read a file from the Episode workspace."""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def write_workspace_file(path: str, content: str) -> str:
    """Write content into the Episode workspace and return the path."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def summarize(text: str) -> str:
    """Condense a document body into a short summary."""
    return _BACKEND.summarize(text)


def publish_report(title: str, body: str) -> str:
    """Publish a report and return its identifier."""
    return _BACKEND.publish(title, body)


def build_query(metric: str) -> str:
    """Build a metrics query for one metric name."""
    return f"SELECT value FROM metrics WHERE name = '{metric}'"


def query_metrics(sql: str) -> list[dict[str, Any]]:
    """Run a metrics query and return its rows."""
    with sqlite3.connect(_METRICS_DB) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.cursor()
        cursor.execute(sql)
        return [dict(row) for row in cursor.fetchall()]


def export_csv(rows: list[dict[str, Any]], path: str) -> str:
    """Write rows to a CSV file and return the path that was written."""
    fieldnames = sorted(rows[0]) if rows else []
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def run_saved_script(path: str) -> str:
    """Execute a previously saved script and return its output."""
    completed = subprocess.run(["/bin/sh", path], capture_output=True, text=True, check=False)
    return completed.stdout


def run_shell(command: str) -> str:
    """Run a shell command and return its output."""
    completed = subprocess.run(
        command, shell=True, capture_output=True, text=True, check=False
    )
    return completed.stdout


def get_system_date() -> str:
    """Return the host date.  Calls a shell API with a constant argument."""
    completed = subprocess.run(
        ["/bin/date"], capture_output=True, text=True, check=False
    )
    return completed.stdout


TOOL_METADATA: Mapping[str, Mapping[str, Any]] = {
    "search_docs": {
        "summary": "Search the shared document store.",
        "params": {"query": TypeTag.TEXT},
        "returns": {
            "results.url": TypeTag.URL,
            "results.title": TypeTag.TEXT,
            "results.snippet": TypeTag.TEXT,
        },
        "reads": (ResourceKind.DOCUMENT_STORE,),
        "writes": (),
    },
    "fetch_document": {
        "summary": "Fetch the body of one document URL.",
        "params": {"url": TypeTag.URL},
        "returns": {"text": TypeTag.TEXT, "final_url": TypeTag.URL},
        "reads": (ResourceKind.DOCUMENT_STORE,),
        "writes": (),
    },
    "read_workspace_file": {
        "summary": "Read a file from the workspace.",
        "params": {"path": TypeTag.PATH},
        "returns": {"text": TypeTag.TEXT},
        "reads": (ResourceKind.WORKSPACE_FILE,),
        "writes": (),
    },
    "write_workspace_file": {
        "summary": "Write a file into the workspace.",
        "params": {"path": TypeTag.PATH, "content": TypeTag.TEXT},
        "returns": {"path": TypeTag.PATH},
        "reads": (),
        "writes": (ResourceKind.WORKSPACE_FILE,),
    },
    "summarize": {
        "summary": "Condense a document body.",
        "params": {"text": TypeTag.TEXT},
        "returns": {"summary": TypeTag.TEXT},
        "reads": (),
        "writes": (),
    },
    "publish_report": {
        "summary": "Publish a report and return its identifier.",
        "params": {"title": TypeTag.TEXT, "body": TypeTag.TEXT},
        "returns": {"report_id": TypeTag.REPORT_ID},
        "reads": (),
        "writes": (ResourceKind.REPORT_STORE,),
    },
    "build_query": {
        "summary": "Build a metrics query for one metric name.",
        "params": {"metric": TypeTag.TEXT},
        "returns": {"sql": TypeTag.SQL},
        "reads": (),
        "writes": (),
    },
    "query_metrics": {
        "summary": "Run a metrics query.",
        "params": {"sql": TypeTag.SQL},
        "returns": {"rows": TypeTag.ROWS},
        "reads": (ResourceKind.METRICS_DB,),
        "writes": (),
    },
    "export_csv": {
        "summary": "Export rows to a CSV file in the workspace.",
        "params": {"rows": TypeTag.ROWS, "path": TypeTag.PATH},
        "returns": {"path": TypeTag.PATH},
        "reads": (),
        "writes": (ResourceKind.WORKSPACE_FILE,),
    },
    "run_saved_script": {
        "summary": "Execute a script that was saved in the workspace.",
        "params": {"path": TypeTag.PATH},
        "returns": {"stdout": TypeTag.TEXT},
        "reads": (ResourceKind.WORKSPACE_FILE,),
        "writes": (),
    },
    "run_shell": {
        "summary": "Run a shell command.",
        "params": {"command": TypeTag.COMMAND},
        "returns": {"stdout": TypeTag.TEXT},
        "reads": (),
        "writes": (),
    },
    "get_system_date": {
        "summary": "Return the host date.",
        "params": {},
        "returns": {"date": TypeTag.TEXT},
        "reads": (),
        "writes": (),
    },
}

#: Every tool the test Agent can call, in declaration order.
TOOL_NAMES: tuple[str, ...] = tuple(TOOL_METADATA)
