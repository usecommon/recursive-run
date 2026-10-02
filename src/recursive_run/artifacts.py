"""Flush artifact bytes and directory before publishing any database reference."""

import hashlib
import os
import tempfile
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ArtifactStore:
    def __init__(self, root: Path, maximum_bytes: int = 16_777_216):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.maximum_bytes = maximum_bytes

    def put(self, data: bytes) -> str:
        if len(data) > self.maximum_bytes:
            raise ValueError("artifact exceeds byte limit")
        key = digest(data)
        path = self.root / key
        if path.exists():
            self.get(key)
            return key
        fd, temporary = tempfile.mkstemp(dir=self.root)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return key

    def get(self, key: str) -> bytes:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("invalid artifact key")
        path = self.root / key
        if path.is_symlink() or path.stat().st_size > self.maximum_bytes:
            raise ValueError("unsafe artifact")
        data = path.read_bytes()
        if digest(data) != key:
            raise ValueError("corrupt artifact")
        return data
