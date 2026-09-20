"""Tests for Comment-Checker Guard."""

import pytest

from alpha.safety.comment_guard import (
    LazyCommentDetectedError,
    check_for_lazy_comments,
)


def test_clean_code_passes():
    code = """def add(a, b):
    # Perform arithmetic sum
    return a + b
"""
    violations = check_for_lazy_comments(code, strict=True)
    assert len(violations) == 0


def test_lazy_rest_of_code_detected():
    bad_code = """class UserService:
    def login(self, username, password):
        return True
    
    # ... rest of methods unchanged ...
"""
    with pytest.raises(LazyCommentDetectedError, match="lazy comment omission"):
        check_for_lazy_comments(bad_code, strict=True)


def test_lazy_todo_implement_later_detected():
    bad_js = """function processPayment() {
    // TODO: implement this later
    return false;
}"""
    with pytest.raises(LazyCommentDetectedError, match="lazy comment omission"):
        check_for_lazy_comments(bad_js, strict=True)


def test_non_strict_returns_violations():
    bad_python = """
    # ... existing implementation unchanged ...
    # TODO: implement later
    """
    violations = check_for_lazy_comments(bad_python, strict=False)
    assert len(violations) >= 2
