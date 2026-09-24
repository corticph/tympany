"""Tests for the adjusted-WER reconstruction logic in tympany.bewer_eval.

These tests exercise `reconstruct_example()` and `build_rows()` directly
with synthetic token streams and edit lists — no bewer dependency needed.
"""

from tympany.bewer_eval import build_rows, reconstruct_example


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(text: str) -> dict:
    return {"cls": "ok", "text": text, "left_compound": False, "right_compound": False}


def _sub(text: str) -> dict:
    return {"cls": "sub", "text": text, "left_compound": False, "right_compound": False}


def _del(text: str) -> dict:
    return {"cls": "del", "text": text, "left_compound": False, "right_compound": False}


def _ins(text: str) -> dict:
    return {"cls": "ins", "text": text, "left_compound": False, "right_compound": False}


# ---------------------------------------------------------------------------
# Single substitution
# ---------------------------------------------------------------------------

def test_single_sub_excluded():
    """Excluded sub: generated replaced with reference text."""
    ref_tokens = [_ok("hello"), _sub("world")]
    pred_tokens = [_ok("hello"), _sub("word")]
    edits = [{"ref": "world", "gen": "word", "excluded": True, "example": 1}]
    ref, gen = reconstruct_example(ref_tokens, pred_tokens, edits)
    assert ref == "hello world"
    assert gen == "hello world"


def test_single_sub_kept():
    """Kept sub: generated text passes through."""
    ref_tokens = [_ok("hello"), _sub("world")]
    pred_tokens = [_ok("hello"), _sub("word")]
    edits = [{"ref": "world", "gen": "word", "excluded": False, "example": 1}]
    ref, gen = reconstruct_example(ref_tokens, pred_tokens, edits)
    assert ref == "hello world"
    assert gen == "hello word"


# ---------------------------------------------------------------------------
# Sub + delete group
# ---------------------------------------------------------------------------

def test_sub_del_mixed_exclusion():
    """Sub excluded (use ref), del kept (gen has nothing — deletion stays)."""
    ref_tokens = [_ok("the"), _ok("patient"), _ok("has"), _sub("hypertension"), _del("today")]
    pred_tokens = [_ok("the"), _ok("patient"), _ok("has"), _sub("hypotension")]
    edits = [
        {"ref": "hypertension", "gen": "hypotension", "excluded": True, "example": 1},
        {"ref": "today", "gen": "", "excluded": False, "example": 1},
    ]
    ref, gen = reconstruct_example(ref_tokens, pred_tokens, edits)
    assert ref == "the patient has hypertension today"
    assert gen == "the patient has hypertension"


def test_sub_del_both_excluded():
    """Both excluded: all reference text used."""
    ref_tokens = [_ok("the"), _ok("patient"), _ok("has"), _sub("hypertension"), _del("today")]
    pred_tokens = [_ok("the"), _ok("patient"), _ok("has"), _sub("hypotension")]
    edits = [
        {"ref": "hypertension", "gen": "hypotension", "excluded": True, "example": 1},
        {"ref": "today", "gen": "", "excluded": True, "example": 1},
    ]
    ref, gen = reconstruct_example(ref_tokens, pred_tokens, edits)
    assert ref == "the patient has hypertension today"
    assert gen == "the patient has hypertension today"


# ---------------------------------------------------------------------------
# Equal-length 2:2 substitution (split by categorize_group)
# ---------------------------------------------------------------------------

def test_split_2_2_first_excluded():
    """First edit excluded (use ref), second kept (use gen)."""
    ref_tokens = [_sub("big"), _sub("red"), _ok("car")]
    pred_tokens = [_sub("large"), _sub("blue"), _ok("car")]
    edits = [
        {"ref": "big", "gen": "large", "excluded": True, "example": 1},
        {"ref": "red", "gen": "blue", "excluded": False, "example": 1},
    ]
    ref, gen = reconstruct_example(ref_tokens, pred_tokens, edits)
    assert ref == "big red car"
    assert gen == "big blue car"


def test_split_2_2_both_kept():
    """Both edits kept: all generated text used."""
    ref_tokens = [_sub("big"), _sub("red"), _ok("car")]
    pred_tokens = [_sub("large"), _sub("blue"), _ok("car")]
    edits = [
        {"ref": "big", "gen": "large", "excluded": False, "example": 1},
        {"ref": "red", "gen": "blue", "excluded": False, "example": 1},
    ]
    ref, gen = reconstruct_example(ref_tokens, pred_tokens, edits)
    assert ref == "big red car"
    assert gen == "large blue car"


# ---------------------------------------------------------------------------
# Pure insertion and deletion
# ---------------------------------------------------------------------------

def test_pure_insertion_excluded():
    """Inserted word excluded: removed from generated text."""
    ref_tokens = [_ok("patient"), _ok("has"), _ok("diabetes")]
    pred_tokens = [_ok("patient"), _ok("has"), _ins("also"), _ok("diabetes")]
    edits = [{"ref": "", "gen": "also", "excluded": True, "example": 1}]
    ref, gen = reconstruct_example(ref_tokens, pred_tokens, edits)
    assert ref == "patient has diabetes"
    assert gen == "patient has diabetes"


def test_pure_insertion_kept():
    """Inserted word kept: stays in generated text."""
    ref_tokens = [_ok("patient"), _ok("has"), _ok("diabetes")]
    pred_tokens = [_ok("patient"), _ok("has"), _ins("also"), _ok("diabetes")]
    edits = [{"ref": "", "gen": "also", "excluded": False, "example": 1}]
    ref, gen = reconstruct_example(ref_tokens, pred_tokens, edits)
    assert ref == "patient has diabetes"
    assert gen == "patient has also diabetes"


def test_pure_deletion_excluded():
    """Deleted word excluded: restored in generated text."""
    ref_tokens = [_ok("patient"), _ok("has"), _del("severe"), _ok("diabetes")]
    pred_tokens = [_ok("patient"), _ok("has"), _ok("diabetes")]
    edits = [{"ref": "severe", "gen": "", "excluded": True, "example": 1}]
    ref, gen = reconstruct_example(ref_tokens, pred_tokens, edits)
    assert ref == "patient has severe diabetes"
    assert gen == "patient has severe diabetes"


def test_pure_deletion_kept():
    """Deleted word kept: stays absent from generated text."""
    ref_tokens = [_ok("patient"), _ok("has"), _del("severe"), _ok("diabetes")]
    pred_tokens = [_ok("patient"), _ok("has"), _ok("diabetes")]
    edits = [{"ref": "severe", "gen": "", "excluded": False, "example": 1}]
    ref, gen = reconstruct_example(ref_tokens, pred_tokens, edits)
    assert ref == "patient has severe diabetes"
    assert gen == "patient has diabetes"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_no_edits():
    """No edits: generated text passes through unchanged."""
    ref_tokens = [_ok("hello"), _ok("world")]
    pred_tokens = [_ok("hello"), _ok("world")]
    ref, gen = reconstruct_example(ref_tokens, pred_tokens, [])
    assert ref == "hello world"
    assert gen == "hello world"


def test_all_ok_tokens():
    """All tokens matched: no diff groups, no edits."""
    ref_tokens = [_ok("a"), _ok("b"), _ok("c")]
    pred_tokens = [_ok("a"), _ok("b"), _ok("c")]
    ref, gen = reconstruct_example(ref_tokens, pred_tokens, [])
    assert ref == "a b c"
    assert gen == "a b c"


def test_empty_tokens():
    """Empty token streams."""
    ref, gen = reconstruct_example([], [], [])
    assert ref == ""
    assert gen == ""


def test_build_rows_multiple_examples():
    """build_rows reconstructs across multiple examples."""
    samples = [
        {
            "example": 1,
            "ref_tokens": [_ok("hello"), _sub("world")],
            "pred_tokens": [_ok("hello"), _sub("word")],
        },
        {
            "example": 2,
            "ref_tokens": [_ok("foo"), _ok("bar")],
            "pred_tokens": [_ok("foo"), _ok("bar")],
        },
    ]
    edits = [
        {"ref": "world", "gen": "word", "excluded": True, "example": 1},
        {"ref": "", "gen": "", "excluded": False, "example": 2},
    ]
    rows = build_rows(samples, edits)
    assert len(rows) == 2
    assert rows[0] == ("hello world", "hello world")
    assert rows[1] == ("foo bar", "foo bar")


def test_build_rows_edits_grouped_by_example():
    """build_rows correctly routes edits to their examples."""
    samples = [
        {
            "example": 1,
            "ref_tokens": [_sub("a"), _ok("x")],
            "pred_tokens": [_sub("b"), _ok("x")],
        },
        {
            "example": 2,
            "ref_tokens": [_sub("c"), _ok("y")],
            "pred_tokens": [_sub("d"), _ok("y")],
        },
    ]
    edits = [
        {"ref": "a", "gen": "b", "excluded": True, "example": 1},
        {"ref": "c", "gen": "d", "excluded": True, "example": 2},
    ]
    rows = build_rows(samples, edits)
    assert rows[0] == ("a x", "a x")
    assert rows[1] == ("c y", "c y")
