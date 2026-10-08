"""Build ordinary git-format patches from bounded inert file contents (no execution).

Output matches intake.validate_patch and the worker's patch applier: one
`diff --git` block per modified file, `--- a/` / `+++ b/` headers, unified hunks, and
`\\ No newline at end of file` markers. Lines split on "\\n" only.
"""

import difflib


def _lines(data: bytes) -> list[str]:
    text = data.decode("utf-8")
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])  # final line without newline
    return lines


def make_patch(before: dict[str, bytes], after: dict[str, bytes], context: int = 3) -> bytes:
    """Patch from `before` to `after` for every path present in both whose bytes differ."""
    blocks = []
    for path in sorted(set(before) & set(after)):
        if before[path] == after[path]:
            continue
        old, new = _lines(before[path]), _lines(after[path])
        block = [f"diff --git a/{path} b/{path}\n"]
        for line in difflib.unified_diff(old, new, f"a/{path}", f"b/{path}", n=context):
            if line.startswith(("---", "+++")):
                block.append(line.rstrip("\n") + "\n")
            elif line.endswith("\n"):
                block.append(line)
            else:
                block.append(line + "\n\\ No newline at end of file\n")
        blocks.append("".join(block))
    return "".join(blocks).encode("utf-8")
