"""Terminal input for the interactive REPL."""

from __future__ import annotations

import os
import select
import sys

try:  # Line editing; also makes backspace remove whole wide characters.
    import readline  # noqa: F401
except ImportError:  # pragma: no cover
    pass

BLOCK_DELIMITER = '"""'


def clean_text(text: str) -> str:
    """Replace invalid UTF-8 kept by Python as surrogates (e.g. a half-deleted
    multi-byte character) with U+FFFD."""
    return text.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")


def read_user_input(prompt: str = "You: ") -> str:
    """Read one user message.

    A multi-line paste arrives as one message instead of one message per line.
    A line containing only ``\"\"\"`` starts an explicit block that ends at the
    next such line.
    """
    return clean_text(_read(prompt))


def _read(prompt: str) -> str:
    first = input(prompt)

    if first.strip() == BLOCK_DELIMITER:
        lines = []
        while True:
            line = input()
            if line.strip() == BLOCK_DELIMITER:
                return "\n".join(lines)
            lines.append(line)

    if not sys.stdin.isatty():
        return first

    # The remaining lines of a paste are already waiting on the terminal.
    fd = sys.stdin.fileno()
    chunks = []
    while select.select([fd], [], [], 0.05)[0]:
        data = os.read(fd, 65536)
        if not data:
            break
        chunks.append(data)
    if not chunks:
        return first
    return first + "\n" + b"".join(chunks).decode("utf-8", errors="replace").rstrip("\n")
