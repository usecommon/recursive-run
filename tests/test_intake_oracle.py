import json
import pytest
from recursive_run.intake import validate_patch, IntakeError
from recursive_run.oracle import LexerContract, compare_outputs, parse_tokens

PATCH = b"diff --git a/pygments/lexers/a.py b/pygments/lexers/a.py\nindex abc..def 100644\n--- a/pygments/lexers/a.py\n+++ b/pygments/lexers/a.py\n@@ -1 +1 @@\n-broken\n+fixed\n"
ALLOWED = {"pygments/lexers/a.py"}


def test_regular_patch():
    assert validate_patch(PATCH, ALLOWED) == ALLOWED


@pytest.mark.parametrize(
    "patch",
    [
        PATCH.replace(b"pygments/lexers/a.py", b"../outside"),
        PATCH.replace(b"pygments/lexers/a.py", b"/absolute"),
        PATCH.replace(b"pygments/lexers/a.py", b"pygments/../a.py"),
        PATCH.replace(b"100644", b"120000"),
        PATCH.replace(b"index abc..def 100644", b"new file mode 100644"),
        PATCH.replace(b"+++ b/pygments/lexers/a.py", b"+++ b/pygments/lexers/b.py"),
        PATCH.replace(b"@@ -1 +1 @@", b"@@ -2,3 +1 @@"),
        b"GIT binary patch\n",
        b"PASS",
        b"\xff",
    ],
)
def test_reject_unsafe_patch(patch):
    with pytest.raises(IntakeError):
        validate_patch(patch, ALLOWED)


def test_patch_size_and_scope():
    with pytest.raises(IntakeError):
        validate_patch(PATCH, ALLOWED, maximum_bytes=10)
    with pytest.raises(IntakeError):
        validate_patch(PATCH, {"other.py"})


@pytest.fixture
def contract():
    return LexerContract(module="pygments.lexers.python", class_name="PythonLexer")


def payload(text):
    return json.dumps([[0, "Token.Text", text]], ensure_ascii=False).encode()


def test_exact_output_and_complete_counts(contract):
    reference = [payload("λ\r\n"), payload("hello")]
    assert compare_outputs(reference, reference, contract).success
    assert not compare_outputs(reference, [reference[0]], contract).success
    assert not compare_outputs(reference, [payload("λ\n"), reference[1]], contract).success
    assert not compare_outputs(reference, reference, contract, forbidden_edit=True).success
    assert not compare_outputs(reference, [b"PASS", reference[1]], contract).success
    assert not compare_outputs(reference, [None, reference[1]], contract).success


@pytest.mark.parametrize(
    "data",
    [
        b"NaN",
        b"{}",
        b'[[true,"Token.Text","x"]]',
        b'[[2,"Token.Text","a"],[1,"Token.Text","b"]]',
        b'[[0,"PASS","x"]]',
        b'[[0,"Token.Text",{}]]',
    ],
)
def test_inert_serialization_rejects_malformed(data, contract):
    with pytest.raises(ValueError):
        parse_tokens(data, contract)
