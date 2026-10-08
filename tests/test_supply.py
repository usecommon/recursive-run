import pytest
from recursive_run.supply import (
    EffortReceipt,
    checkpoint,
    lexer_imports,
    provisional_families,
    supply_report,
)

FILES = {
    "pygments/lexers/__init__.py": b"from pygments.lexers._mapping import LEXERS\n",
    "pygments/lexers/_mapping.py": b"LEXERS = {}\n",
    "pygments/lexers/html.py": b"from pygments.lexer import RegexLexer\n",
    "pygments/lexers/css.py": b"X = 1\n",
    "pygments/lexers/ruby.py": b"X = 1\n",
    "pygments/lexers/templates.py": (
        b"from pygments.lexers.html import HtmlLexer\nfrom pygments.lexers import ruby\n"
    ),
    "pygments/lexers/lisp.py": b"raise SystemExit('never executed')\n",
}


def patch(*paths):
    return "".join(f"diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n" for p in paths)


def test_imports_are_parsed_not_executed():
    edges = lexer_imports(FILES)
    assert edges["pygments/lexers/templates.py"] == {
        "pygments/lexers/html.py",
        "pygments/lexers/ruby.py",
    }
    assert edges["pygments/lexers/lisp.py"] == set()


def test_shared_importer_does_not_chain_unrelated_lexers():
    rows = [
        {"instance_id": "a", "patch": patch("pygments/lexers/html.py")},
        {"instance_id": "b", "patch": patch("pygments/lexers/ruby.py")},
        {"instance_id": "c", "patch": patch("pygments/lexers/templates.py")},
        {"instance_id": "d", "patch": patch("pygments/lexers/css.py", "pygments/lexers/lisp.py")},
        {"instance_id": "e", "patch": patch("pygments/lexers/__init__.py")},
        {"instance_id": "f", "patch": patch("tests/test_x.py")},
    ]
    report = supply_report(rows, FILES, "rev")
    assert report["structural_candidates"] == 4
    assert report["rejections"] == {"edits lexer registry": 1, "edits outside lexer modules": 1}
    # css+lisp merge through the combined mutation; templates conflicts with html and ruby.
    assert report["provisional_families"] == 4
    assert report["independent_supply"] == 3
    assert "pygments/lexers/templates.py" not in report["independent_families"]
    assert report["structural_upper_bound_met"] is False


def test_combined_mutation_inherits_both_files():
    families = provisional_families({"x": ("a.py", "b.py"), "y": ("b.py",), "z": ("c.py",)})
    assert families["x"] == families["y"] != families["z"]


def receipts(admitted_families, rejected=0, hours=2.0):
    made = [
        EffortReceipt(
            family_id=f"f{i}",
            task_id=f"t{i}",
            preparation_hours=hours,
            review_hours=1.0,
            admitted=True,
            reviewer_id="r",
        )
        for i in range(admitted_families)
    ]
    made += [
        EffortReceipt(
            family_id=f"x{i}",
            task_id=f"u{i}",
            preparation_hours=hours,
            review_hours=1.0,
            admitted=False,
            rejection_reason="oracle cannot observe change",
            reviewer_id="r",
        )
        for i in range(rejected)
    ]
    return made


def test_checkpoint_blocks_without_eight_admitted_families():
    assert checkpoint(receipts(7), 200, 500).decision == "BLOCKED"
    assert checkpoint([], 200, 500).decision == "BLOCKED"
    with pytest.raises(ValueError):
        checkpoint(receipts(8), 200, 0)


def test_checkpoint_proceeds_or_requests_revision():
    good = checkpoint(receipts(8), 200, 500)
    assert good.decision == "PROCEED"
    assert good.hours_per_admitted_family == 3.0 and good.projected_hours == 240.0
    over_effort = checkpoint(receipts(8), 200, 100)
    assert over_effort.decision == "REVISE_PROTOCOL"
    # 8 of 16 accepted: 80 more families need ~160 reviews, but only 82 - 16 remain.
    short = checkpoint(receipts(8, rejected=8), 82, 10_000)
    assert short.decision == "REVISE_PROTOCOL"
    assert short.acceptance_rate == 0.5 and short.projected_candidates_to_review == 160
