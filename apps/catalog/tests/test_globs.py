from apps.catalog.globs import compile_globs, matches_any


def _matches(pattern: str, path: str) -> bool:
    return matches_any(path, compile_globs([pattern]))


def test_double_star_matches_nested_and_root_migrations():
    assert _matches("**/migrations/**", "app/migrations/0001.py")
    assert _matches("**/migrations/**", "migrations/0001.py")


def test_basename_pattern_matches_at_any_depth_but_not_wrong_suffix():
    assert _matches("*.lock", "sub/dir/uv.lock")
    assert not _matches("*.lock", "lockfile.py")


def test_rooted_pattern_matches_only_from_the_root():
    assert _matches("tests/**", "tests/test_foo.py")
    assert not _matches("tests/**", "sub/tests/test_foo.py")


def test_basename_pattern_with_prefix_matches_at_any_depth():
    assert _matches("test_*.py", "apps/foo/tests/test_bar.py")
    assert _matches("test_*.py", "test_bar.py")


def test_star_does_not_cross_slash():
    assert _matches("a/*", "a/b")
    assert not _matches("a/*", "a/b/c")


def test_invalid_pattern_is_skipped_not_raised():
    compiled = compile_globs(["*.lock", None, 42])  # type: ignore[list-item]
    assert matches_any("uv.lock", compiled)


def test_empty_pattern_list_matches_nothing():
    assert compile_globs([]) == []
    assert not matches_any("anything.py", compile_globs([]))


def test_double_star_matches_zero_or_more_middle_segments():
    assert _matches("**/x/**", "x/y")
