from recursive_run.artifacts import digest
from recursive_run.intake import validate_patch
from recursive_run.repositories import PROFILES
from recursive_run.smoke import export_patch

PROFILE = PROFILES["sqlglot"]
BROKEN = {
    "sqlglot/dialects/sqlite.py": b"a = 1\nb = 2\n",
    "sqlglot/parser.py": b"p = 1\n",
    "LICENSE": b"MIT\n",
}


class FakeRunner:
    def __init__(self, tree):
        self.tree = tree
        self.downloads = []

    def hashes(self, lease):
        return {k: "NONREGULAR" if v is None else digest(v) for k, v in self.tree.items()}

    def download(self, lease, path, maximum):
        self.downloads.append(path)
        return self.tree[path]


def test_export_patch_keeps_only_editable_modifications():
    tree = {
        **BROKEN,
        "sqlglot/dialects/sqlite.py": b"a = 1\nb = 3\n",
        "LICENSE": b"changed\n",  # existing file outside the repair scope
        "reproduce.py": b"print(1)\n",  # scratch file at the root is ignored
        "sqlglot/new_module.py": b"x = 1\n",  # new package file is forbidden
    }
    runner = FakeRunner(tree)
    patch, edited, forbidden, scratch = export_patch(runner, "lease", BROKEN, PROFILE)
    assert edited == ["sqlglot/dialects/sqlite.py"]
    assert sorted(forbidden) == ["LICENSE", "sqlglot/new_module.py"]
    assert scratch == ["reproduce.py"]
    assert runner.downloads == ["sqlglot/dialects/sqlite.py"]
    assert validate_patch(patch, set(edited)) == set(edited)


def test_deleted_or_linked_package_file_is_forbidden():
    tree = {**BROKEN, "sqlglot/parser.py": None}
    del tree["sqlglot/dialects/sqlite.py"]
    patch, edited, forbidden, _ = export_patch(FakeRunner(tree), "lease", BROKEN, PROFILE)
    assert patch == b"" and edited == []
    assert sorted(forbidden) == ["sqlglot/dialects/sqlite.py", "sqlglot/parser.py"]
