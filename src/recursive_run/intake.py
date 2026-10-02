"""Inspect bounded inert patch bytes; reconstruction belongs in an isolated worker."""

import re
from pathlib import PurePosixPath


class IntakeError(ValueError):
    pass


def regular_path(name: str) -> str:
    if not name or "\\" in name or "\x00" in name or any(c.isspace() for c in name):
        raise IntakeError("ambiguous path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in {"", ".", ".."} for p in name.split("/")):
        raise IntakeError("absolute or non-normal path")
    return str(path)


def validate_patch(data: bytes, allowed_paths: set[str], maximum_bytes: int = 262144) -> set[str]:
    if not data or len(data) > maximum_bytes:
        raise IntakeError("empty or oversized patch")
    try:
        lines = data.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as error:
        raise IntakeError("non-UTF8 patch") from error
    paths: set[str] = set()
    index = 0
    while index < len(lines):
        match = re.fullmatch(r"diff --git a/(\S+) b/(\S+)", lines[index])
        if not match:
            raise IntakeError("expected ordinary git diff")
        old, new = map(regular_path, match.groups())
        if old != new or new not in allowed_paths or new in paths:
            raise IntakeError("rename, duplicate or forbidden path")
        paths.add(new)
        index += 1
        if index < len(lines) and lines[index].startswith("index "):
            if not re.fullmatch(r"index [0-9a-f]+\.\.[0-9a-f]+(?: 100644)?", lines[index]):
                raise IntakeError("unsupported index or file mode")
            index += 1
        if lines[index : index + 2] != [f"--- a/{old}", f"+++ b/{new}"]:
            raise IntakeError("new/deleted/binary/link file unsupported")
        index += 2
        hunks = 0
        while index < len(lines) and not lines[index].startswith("diff --git "):
            hunk = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*", lines[index])
            if not hunk:
                raise IntakeError("invalid hunk header")
            before = int(hunk[2] or 1)
            after = int(hunk[4] or 1)
            index += 1
            seen_before = seen_after = 0
            while index < len(lines) and not lines[index].startswith(("@@ ", "diff --git ")):
                line = lines[index]
                if line == "\\ No newline at end of file":
                    index += 1
                    continue
                if not line or line[0] not in " +-":
                    raise IntakeError("invalid hunk body")
                seen_before += line[0] in " -"
                seen_after += line[0] in " +"
                index += 1
            if (seen_before, seen_after) != (before, after):
                raise IntakeError("hunk counts differ")
            hunks += 1
        if not hunks:
            raise IntakeError("no content change")
    return paths
