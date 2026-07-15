"""Unit tests for the saved medical-term list store (web/terms.py)."""

from web import terms

E = "u@corti.ai"


def test_save_read_and_count():
    assert terms.save_terms(E, "My List", "alpha\nbeta\n\ngamma\n") == "My-List"
    content = terms.read_terms(E, "My List")
    assert content is not None
    assert terms.term_count(content) == 3  # blank line ignored


def test_safe_name():
    assert terms.safe_name("Cardiology v2!!") == "Cardiology-v2"
    assert terms.safe_name("   ") == ""
    assert terms.safe_name("../../etc/passwd") == "etc-passwd"  # no traversal


def test_normalize_terms():
    assert terms.normalize_terms("  a \n\n b \n") == "a\nb\n"
    assert terms.normalize_terms("   ") == ""


def test_empty_not_saved():
    assert terms.save_terms(E, "empty", "   \n  ") is None
    assert not terms.exists(E, "empty")


def test_exists_and_delete():
    terms.save_terms(E, "tmp", "x")
    assert terms.exists(E, "tmp")
    assert terms.delete_terms(E, "tmp") is True
    assert not terms.exists(E, "tmp")
    assert terms.delete_terms(E, "tmp") is False  # already gone


def test_duplicate_makes_unique_copies():
    terms.save_terms(E, "src", "a\nb")
    assert terms.duplicate_terms(E, "src") == "src-copy"
    assert terms.duplicate_terms(E, "src") == "src-copy-2"
    assert terms.read_terms(E, "src-copy").strip() == "a\nb"


def test_list_terms_reports_counts():
    terms.save_terms(E, "one", "a")
    terms.save_terms(E, "two", "a\nb")
    by_name = {t["name"]: t["count"] for t in terms.list_terms(E)}
    assert by_name["one"] == 1 and by_name["two"] == 2
