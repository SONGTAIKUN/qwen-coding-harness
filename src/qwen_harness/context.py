from __future__ import annotations

import copy
import json
from pathlib import Path

from jinja2.sandbox import ImmutableSandboxedEnvironment
from tokenizers import Tokenizer


class ContextCounter:
    """Count the installed model's actual chat template, including tool schemas."""

    def __init__(self, directory: Path):
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
        env.filters["tojson"] = lambda value: json.dumps(value, ensure_ascii=False)
        env.globals["raise_exception"] = self._raise
        self.template = env.from_string((directory / "chat_template.jinja").read_text())

    @staticmethod
    def _raise(message):
        raise ValueError(message)

    def count(self, body: dict) -> int:
        messages = copy.deepcopy(body["messages"])
        for message in messages:
            for tool in message.get("tool_calls") or []:
                arguments = tool.get("function", {}).get("arguments")
                if isinstance(arguments, str):
                    tool["function"]["arguments"] = json.loads(arguments or "{}")
            if isinstance(message.get("content"), list):
                if any(part.get("type") != "text" for part in message["content"]):
                    raise ValueError("Coding gateway currently accepts text and tools only")
        rendered = self.template.render(
            messages=messages, tools=body.get("tools"), add_generation_prompt=True,
            **body.get("chat_template_kwargs", {}),
        )
        return len(self.tokenizer.encode(rendered, add_special_tokens=False).ids)


def compact(messages: list[dict], evidence: list[str]) -> list[dict]:
    # Keep the task and an explicit recovery ledger; files remain authoritative.
    ledger = "\n".join(evidence[-60:])[-18000:]
    return [messages[0], messages[1], {
        "role": "user",
        "content": "Earlier tool history was compacted. Completed actions and observations:\n"
        + ledger + "\nRead files again when details are needed. Continue the original task; do not repeat completed edits.",
    }]
