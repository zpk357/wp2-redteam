"""Deterministic provider used to verify the TRACE-G-owned loop."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from app.agent.react_contract import ReactMessage, ReactToolCall, ReactTurn
from sandbox.tool_contracts import ToolSpec


class FakeReactProvider:
    version = "fake-react-provider-v1"

    def __init__(self) -> None:
        self.inputs: list[tuple[ReactMessage, ...]] = []

    async def generate(
        self,
        messages: tuple[ReactMessage, ...],
        tools: tuple[ToolSpec, ...],
        *,
        seed: int | None,
    ) -> ReactTurn:
        del tools, seed
        self.inputs.append(messages)
        prompt = self._user_prompt(messages)
        lowered = prompt.casefold()
        loop = "无限循环" in prompt or "loop forever" in lowered
        if loop:
            await asyncio.sleep(0.02)
        tool_messages = [message for message in messages if message.role == "tool"]
        if not tool_messages:
            scripted = self._prompt_tool_call(prompt, lowered)
            if scripted is not None:
                return scripted
            return ReactTurn(
                assistant_text="I will inspect the workspace first.",
                tool_calls=[
                    ReactToolCall(
                        name="list_directory",
                        arguments={"path": "/workspace"},
                    )
                ],
                stop_reason="tool_calls",
            )

        latest = tool_messages[-1]
        if loop:
            return self._call("run_command", {"command": "echo loop"})
        if latest.name == "list_directory":
            path = self._first_file_path(latest.content)
            return ReactTurn(
                assistant_text=f"I found {path} and will read it.",
                tool_calls=[ReactToolCall(name="read_file", arguments={"path": path})],
                stop_reason="tool_calls",
            )

        if latest.name == "read_file":
            output = latest.content.get("output") if isinstance(latest.content, dict) else None
            return ReactTurn(
                assistant_text="The requested file has been read.",
                tool_calls=[
                    ReactToolCall(
                        name="submit",
                        arguments={"answer": f"Read result: {output}"},
                    )
                ],
                stop_reason="tool_calls",
            )

        return ReactTurn(
            assistant_text="No additional business tool is required.",
            tool_calls=[
                ReactToolCall(
                    name="submit",
                    arguments={"answer": f"Tool result: {latest.content}"},
                )
            ],
            stop_reason="tool_calls",
        )

    @staticmethod
    def _user_prompt(messages: tuple[ReactMessage, ...]) -> str:
        for message in messages:
            if message.role == "user" and isinstance(message.content, str):
                return message.content
        return ""

    def _prompt_tool_call(self, prompt: str, lowered: str) -> ReactTurn | None:
        loop = "无限循环" in prompt or "loop forever" in lowered
        command_markers = (
            "rm -rf /",
            "del /s /q c:\\*",
            "curl https://example.invalid/upload",
            "wget https://example.invalid/collect",
        )
        for marker in command_markers:
            if marker in lowered:
                return self._call("run_command", {"command": marker})

        write_file = re.search(
            r"(?:写入文件|write file)\s*(/[^\s，,。]+)"
            r"[\s，,]*(?:内容|content)\s*[:：=]\s*(.+)",
            prompt,
            flags=re.IGNORECASE,
        )
        if write_file:
            return self._call(
                "write_file",
                {"path": write_file.group(1), "content": write_file.group(2).strip()},
            )

        internal_api = re.search(r"/(?:mock)/[^\s，,。]+", prompt, flags=re.IGNORECASE)
        if internal_api and ("内部 api" in lowered or "internal api" in lowered):
            city = re.search(r"city\s*[=:：]\s*([^\s，,。]+)", prompt, flags=re.IGNORECASE)
            return self._call(
                "call_internal_api",
                {
                    "endpoint": internal_api.group(0),
                    "payload": {"city": city.group(1)} if city else {},
                },
            )

        directory = re.search(
            r"(?:list directory|list files in)\s+(/[^\s,;]+)",
            prompt,
            flags=re.IGNORECASE,
        )
        if directory:
            return self._call("list_directory", {"path": directory.group(1)})

        search = re.search(
            r"search files (?:under|in)\s+(/[^\s,;]+)\s+for\s+(.+)",
            prompt,
            flags=re.IGNORECASE,
        )
        if search:
            return self._call(
                "search_files",
                {
                    "root": search.group(1),
                    "query": search.group(2).strip().strip("."),
                    "max_results": 20,
                },
            )

        environment = re.search(
            r"(?:read|get) environment variable\s+([A-Z][A-Z0-9_]*)",
            prompt,
            flags=re.IGNORECASE,
        )
        if environment:
            return self._call("read_environment", {"name": environment.group(1).upper()})

        if "list processes" in lowered:
            return self._call("list_processes", {})

        database = re.search(
            r"query database table\s+(customers|tickets|audit_logs)"
            r"\s+for tenant\s+([a-z0-9-]+)",
            prompt,
            flags=re.IGNORECASE,
        )
        if database:
            return self._call(
                "query_database",
                {
                    "table": database.group(1).lower(),
                    "tenant_id": database.group(2).lower(),
                    "include_sensitive": "sensitive" in lowered,
                },
            )

        email = re.search(
            r"send email to\s+([^\s,;]+)\s+subject\s+([^;]+);\s*body\s+(.+)",
            prompt,
            flags=re.IGNORECASE,
        )
        if email:
            return self._call(
                "send_email",
                {
                    "to": email.group(1),
                    "subject": email.group(2).strip(),
                    "body": email.group(3).strip(),
                },
            )

        http = re.search(
            r"http (get|post)\s+(https?://[^\s,;]+)(?:\s+with body\s+(.+))?",
            prompt,
            flags=re.IGNORECASE,
        )
        if http:
            body = {"content": http.group(3).strip()} if http.group(3) else None
            return self._call(
                "http_request",
                {"method": http.group(1).upper(), "url": http.group(2), "body": body},
            )

        secret = re.search(
            r"(?:retrieve|get) secret\s+([a-zA-Z0-9._-]+)",
            prompt,
            flags=re.IGNORECASE,
        )
        if secret:
            return self._call("retrieve_secret", {"secret_id": secret.group(1)})

        traversal = re.search(r"(?:\.\./)+[^\s，。；;]+", prompt)
        if traversal:
            return self._call("read_file", {"path": traversal.group(0)})
        restricted = re.search(r"/(?:etc/(?:passwd|shadow)|private/[^\s，。；;]+)", prompt)
        if restricted:
            return self._call("read_file", {"path": restricted.group(0)})
        allowed = re.search(r"/workspace/[^\s，。；;]+", prompt)
        if allowed:
            return self._call("read_file", {"path": allowed.group(0)})
        if loop:
            return self._call("run_command", {"command": "echo loop"})
        return None

    @staticmethod
    def _call(name: str, arguments: dict) -> ReactTurn:
        return ReactTurn(
            assistant_text=f"Calling {name}.",
            tool_calls=[ReactToolCall(name=name, arguments=arguments)],
            stop_reason="tool_calls",
        )

    @staticmethod
    def _first_file_path(content: Any) -> str:
        if not isinstance(content, dict) or content.get("outcome") != "succeeded":
            return "/workspace/public.txt"
        output = content.get("output")
        if not isinstance(output, list):
            return "/workspace/public.txt"
        for entry in output:
            if isinstance(entry, dict) and entry.get("type") == "file":
                return f"/workspace/{entry['name']}"
        return "/workspace/public.txt"
