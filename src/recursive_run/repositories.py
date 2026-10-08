"""Pinned SWE-smith repositories admitted as task sources and what a repair may touch.

Pygments was the original pilot source. sqlglot was added because Pygments alone cannot
supply the 88 independent families of the default protocol (docs/family-supply.md).
mistune is profiled for supply analysis and its grader pipeline, not yet admitted.
"""

import re

from pydantic import Field

from recursive_run.contracts import Record


class RepositoryProfile(Record):
    repository: str
    reference: str = Field(pattern=r"^[0-9a-f]{40}$")
    mirror: str
    source_root: str  # path prefix stripped to form dotted module names
    import_root: str  # snapshot directory the worker puts on sys.path
    scope: str  # regex over repository paths a repair may edit
    excluded: tuple[str, ...] = ()  # registry/CLI machinery rather than repairable behavior
    excluded_reason: str = "edits excluded module"
    snapshot_prefixes: tuple[str, ...]  # package code supplied to workers; no tests or history
    license_files: tuple[str, ...]
    pipelines: tuple[str, ...]  # grader entry points (oracle.PIPELINES) or "lexer"
    admitted: bool

    def module(self, path: str) -> str:
        dotted = path.removeprefix(self.source_root).removesuffix(".py").replace("/", ".")
        return dotted.removesuffix(".__init__")

    def in_scope(self, path: str) -> bool:
        return re.fullmatch(self.scope, path) is not None

    def editable(self, path: str) -> bool:
        return self.in_scope(path) and path not in self.excluded

    def snapshot(self, path: str) -> bool:
        return path.startswith(self.snapshot_prefixes) or path in self.license_files


PROFILES = {
    "pygments": RepositoryProfile(
        repository="swesmith/pygments__pygments.27649ebb",
        reference="d53d06ab2c467be43d32f68881422698ee33adf6",
        mirror="https://github.com/swesmith/pygments__pygments.27649ebb.git",
        source_root="",
        import_root="",
        scope=r"pygments/lexers/[A-Za-z0-9_]+\.py",
        excluded=("pygments/lexers/__init__.py", "pygments/lexers/_mapping.py"),
        excluded_reason="edits lexer registry",
        snapshot_prefixes=("pygments/",),
        license_files=("LICENSE", "AUTHORS"),
        pipelines=("lexer",),
        admitted=True,
    ),
    "sqlglot": RepositoryProfile(
        repository="swesmith/tobymao__sqlglot.036601ba",
        reference="11710cc8e44a685969087a8b1f24a5a06fde0161",
        mirror="https://github.com/swesmith/tobymao__sqlglot.036601ba.git",
        source_root="",
        import_root="",
        scope=r"sqlglot/(?:[A-Za-z0-9_]+/)?[A-Za-z0-9_]+\.py",
        excluded=("sqlglot/__main__.py",),
        excluded_reason="edits command-line entry point",
        snapshot_prefixes=("sqlglot/",),
        license_files=("LICENSE",),
        pipelines=("sqlglot.transpile", "sqlglot.optimize"),
        admitted=True,
    ),
    "mistune": RepositoryProfile(
        repository="swesmith/lepture__mistune.bf54ef67",
        reference="62750c95db25a8fb93dda09242f64e07508d9231",
        mirror="https://github.com/swesmith/lepture__mistune.bf54ef67.git",
        source_root="src/",
        import_root="src",
        scope=r"src/mistune/(?:[A-Za-z0-9_]+/)?[A-Za-z0-9_]+\.py",
        excluded=("src/mistune/__main__.py",),
        excluded_reason="edits command-line entry point",
        snapshot_prefixes=("src/mistune/",),
        license_files=("LICENSE",),
        pipelines=("mistune.html",),
        admitted=False,
    ),
}
