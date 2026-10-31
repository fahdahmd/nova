"""Filesystem sandbox: traversal, symlinks, blocked names and read limits."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nova.security.sandbox import PathSandbox, SandboxError, build_sandbox


def test_resolves_a_relative_path_inside_the_default_root(
    sandbox: PathSandbox, workspace: Path
) -> None:
    assert sandbox.resolve("readme.md") == (workspace / "readme.md").resolve()


def test_resolves_a_nested_path(sandbox: PathSandbox, workspace: Path) -> None:
    resolved = sandbox.resolve("projects/alpha/main.py")
    assert resolved == (workspace / "projects" / "alpha" / "main.py").resolve()


@pytest.mark.parametrize(
    "candidate",
    [
        "../outside/passwords.txt",
        "../../etc/passwd",
        "notes/../../outside/passwords.txt",
        "projects/../../outside",
        "..",
    ],
)
def test_rejects_parent_directory_traversal(sandbox: PathSandbox, candidate: str) -> None:
    with pytest.raises(SandboxError):
        sandbox.resolve(candidate)


def test_rejects_absolute_path_outside_the_roots(sandbox: PathSandbox, secret_dir: Path) -> None:
    with pytest.raises(SandboxError, match="outside the allowed folders"):
        sandbox.resolve(str(secret_dir / "passwords.txt"))


def test_rejects_empty_and_null_byte_paths(sandbox: PathSandbox) -> None:
    with pytest.raises(SandboxError):
        sandbox.resolve("   ")
    with pytest.raises(SandboxError, match="null byte"):
        sandbox.resolve("readme.md\x00.txt")


def test_symlink_pointing_outside_the_sandbox_is_rejected(
    sandbox: PathSandbox, workspace: Path, secret_dir: Path
) -> None:
    """resolve() follows symlinks, so the real target must still be inside."""
    link = workspace / "escape.txt"
    try:
        os.symlink(secret_dir / "passwords.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("creating symlinks needs privileges on this platform")

    with pytest.raises(SandboxError, match="outside the allowed folders"):
        sandbox.resolve("escape.txt")
    with pytest.raises(SandboxError):
        sandbox.read_text("escape.txt")


def test_symlinked_directory_escape_is_rejected(
    sandbox: PathSandbox, workspace: Path, secret_dir: Path
) -> None:
    link = workspace / "elsewhere"
    try:
        os.symlink(secret_dir, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("creating symlinks needs privileges on this platform")

    with pytest.raises(SandboxError):
        sandbox.resolve("elsewhere/passwords.txt")


def test_symlink_staying_inside_the_sandbox_is_allowed(
    sandbox: PathSandbox, workspace: Path
) -> None:
    link = workspace / "alias.md"
    try:
        os.symlink(workspace / "readme.md", link)
    except (OSError, NotImplementedError):
        pytest.skip("creating symlinks needs privileges on this platform")

    assert sandbox.resolve("alias.md") == (workspace / "readme.md").resolve()


@pytest.mark.parametrize(
    "candidate",
    [".env", "id_rsa", ".hidden/secret.txt", ".env.local"],
)
def test_rejects_sensitive_and_hidden_names(sandbox: PathSandbox, candidate: str) -> None:
    with pytest.raises(SandboxError, match="blocked"):
        sandbox.resolve(candidate)


def test_rejects_key_material_by_pattern(sandbox: PathSandbox, workspace: Path) -> None:
    (workspace / "server.pem").write_text("cert\n", encoding="utf-8")
    with pytest.raises(SandboxError, match="blocked"):
        sandbox.resolve("server.pem")


def test_second_root_is_allowed_but_not_default(
    sandbox: PathSandbox, workspace: Path, second_root: Path
) -> None:
    resolved = sandbox.resolve(str(second_root / "archive.md"))
    assert resolved == (second_root / "archive.md").resolve()
    assert sandbox.is_in_default_root(resolved) is False
    assert sandbox.is_in_default_root(sandbox.resolve("readme.md")) is True
    assert sandbox.default_root == workspace.resolve()


# --------------------------------------------------------------------- reads
def test_reads_a_text_file(sandbox: PathSandbox) -> None:
    path, text, truncated = sandbox.read_text("readme.md")
    assert "Hello" in text
    assert truncated is False
    assert path.name == "readme.md"


def test_refuses_binary_extensions(sandbox: PathSandbox) -> None:
    with pytest.raises(SandboxError, match="not readable as text"):
        sandbox.read_text("photo.jpg")


def test_refuses_a_directory(sandbox: PathSandbox) -> None:
    with pytest.raises(SandboxError, match="not a regular file"):
        sandbox.read_text("notes")


def test_refuses_a_missing_file(sandbox: PathSandbox) -> None:
    with pytest.raises(SandboxError, match="does not exist"):
        sandbox.read_text("nope.txt")


def test_truncates_at_the_byte_limit(workspace: Path) -> None:
    small = PathSandbox(roots=(workspace.resolve(),), max_bytes=64)
    (workspace / "long.txt").write_text("x" * 200, encoding="utf-8")
    _path, text, truncated = small.read_text("long.txt")
    assert truncated is True
    assert len(text) == 64


def test_rejects_a_file_far_above_the_limit(workspace: Path) -> None:
    small = PathSandbox(roots=(workspace.resolve(),), max_bytes=64)
    (workspace / "huge.txt").write_text("y" * 4096, encoding="utf-8")
    with pytest.raises(SandboxError, match="too large"):
        small.read_text("huge.txt")


# ------------------------------------------------------------------ searching
def test_search_finds_files_by_substring(sandbox: PathSandbox) -> None:
    matches = sandbox.search("grocer")
    assert [path.name for path in matches] == ["groceries.txt"]


def test_search_accepts_globs_and_spans_roots(sandbox: PathSandbox) -> None:
    names = {path.name for path in sandbox.search("*.md", limit=10)}
    assert {"readme.md", "archive.md"} <= names


def test_search_never_returns_blocked_files(sandbox: PathSandbox) -> None:
    assert sandbox.search("*") == sandbox.search("*")
    for path in sandbox.search("*", limit=25):
        assert path.name not in {".env", "id_rsa"}
        assert ".hidden" not in path.parts


def test_search_rejects_path_separators(sandbox: PathSandbox) -> None:
    for pattern in ["notes/*.txt", "..\\x", "../x"]:
        with pytest.raises(SandboxError):
            sandbox.search(pattern)


def test_search_rejects_empty_and_overlong_patterns(sandbox: PathSandbox) -> None:
    with pytest.raises(SandboxError):
        sandbox.search("   ")
    with pytest.raises(SandboxError, match="too long"):
        sandbox.search("a" * 200)


# ---------------------------------------------------------------- construction
def test_a_sandbox_needs_at_least_one_root() -> None:
    with pytest.raises(ValueError, match="at least one root"):
        PathSandbox(roots=())


def test_build_sandbox_keeps_the_root_order(workspace: Path, second_root: Path) -> None:
    built = build_sandbox([workspace, second_root], 1024)
    assert built.roots == (workspace, second_root)
    assert built.max_bytes == 1024
