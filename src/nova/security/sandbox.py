"""Read-only filesystem sandbox.

All file access from tools goes through :class:`PathSandbox`. Paths are fully
resolved (which also resolves symlinks) and must land inside one of the
configured roots; sensitive names are denied even when they do.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path

MAX_SEARCH_RESULTS = 50
MAX_SCANNED_ENTRIES = 20_000

# Extensions Nova is willing to read as text.
TEXT_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".rst",
        ".csv",
        ".tsv",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".log",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".sql",
        ".sh",
        ".bat",
        ".ps1",
        ".xml",
        ".svg",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".java",
        ".kt",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".lua",
        ".tex",
        ".srt",
        ".vtt",
    }
)

# Exact filenames that are never readable, whatever the extension says.
BLOCKED_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".netrc",
        "_netrc",
        ".npmrc",
        ".pypirc",
        ".htpasswd",
        ".git-credentials",
        "credentials",
        "credentials.json",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "known_hosts",
        "authorized_keys",
        "shadow",
        "passwd",
        "sam",
        "ntds.dit",
        "login data",
        "cookies",
        "cookies.sqlite",
        "logins.json",
        "key4.db",
        "key3.db",
        "keychain",
        "login.keychain-db",
        "secring.gpg",
        "wallet.dat",
        "token.json",
        "service-account.json",
    }
)

# Glob patterns denied anywhere in the tree.
BLOCKED_PATTERNS: tuple[str, ...] = (
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.kdbx",
    "*.ppk",
    "*.asc",
    "*.gpg",
    "*_rsa",
    "*_ed25519",
    "*.sqlite-wal",
    "id_rsa*",
    ".env.*",
)

# Directory names that are skipped and never traversed into.
BLOCKED_DIR_NAMES = frozenset(
    {
        ".ssh",
        ".aws",
        ".azure",
        ".gcloud",
        ".gnupg",
        ".git",
        ".svn",
        ".docker",
        ".kube",
        ".password-store",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "site-packages",
        "appdata",
        "application support",
        "library",
        "chrome",
        "chromium",
        "firefox",
        "mozilla",
        "edge",
        "brave-browser",
        "opera software",
        "keychains",
        "windows",
        "system32",
        "program files",
        "program files (x86)",
        "programdata",
        "$recycle.bin",
    }
)


class SandboxError(Exception):
    """Raised when a path is outside the sandbox or otherwise not readable."""


def _is_hidden(name: str) -> bool:
    return name.startswith(".") and name not in {".", ".."}


def _name_is_blocked(name: str) -> bool:
    lowered = name.lower()
    if lowered in BLOCKED_NAMES:
        return True
    return any(fnmatch.fnmatch(lowered, pattern) for pattern in BLOCKED_PATTERNS)


@dataclass(frozen=True)
class PathSandbox:
    """A read-only view of the filesystem restricted to ``roots``."""

    roots: tuple[Path, ...]
    max_bytes: int = 512 * 1024
    default_root_index: int = 0

    def __post_init__(self) -> None:
        if not self.roots:
            raise ValueError("a sandbox needs at least one root")
        if self.max_bytes < 1:
            raise ValueError("max_bytes must be positive")

    # ------------------------------------------------------------------ info
    @property
    def default_root(self) -> Path:
        return self.roots[self.default_root_index]

    def describe(self) -> list[str]:
        return [str(root) for root in self.roots]

    # ------------------------------------------------------------- resolving
    def _root_for(self, resolved: Path) -> Path | None:
        for root in self.roots:
            if resolved == root or root in resolved.parents:
                return root
        return None

    def resolve(self, candidate: str | Path) -> Path:
        """Resolve ``candidate`` and prove it is inside the sandbox.

        Rejects ``..`` escapes, absolute paths outside the roots, symlinks whose
        target leaves the roots, and sensitive file or directory names.
        """
        raw = str(candidate).strip()
        if not raw:
            raise SandboxError("empty path")
        if "\x00" in raw:
            raise SandboxError("path contains a null byte")

        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.default_root / path

        # resolve() collapses '..' and follows every symlink, so the result is
        # the real location on disk - exactly what must be inside a root.
        try:
            resolved = path.resolve()
        except OSError as exc:
            raise SandboxError(f"path cannot be resolved: {exc}") from exc

        root = self._root_for(resolved)
        if root is None:
            raise SandboxError(
                "path is outside the allowed folders: " + ", ".join(self.describe())
            )

        relative_parts = resolved.relative_to(root).parts

        for part in relative_parts[:-1] if relative_parts else ():
            if _is_hidden(part) or part.lower() in BLOCKED_DIR_NAMES:
                raise SandboxError(f"access to {part!r} is blocked")

        if relative_parts:
            leaf = relative_parts[-1]
            if _is_hidden(leaf) or _name_is_blocked(leaf):
                raise SandboxError(f"access to {leaf!r} is blocked")

        return resolved

    def is_in_default_root(self, resolved: Path) -> bool:
        """True when the path sits in the no-confirmation-needed root."""
        default = self.default_root
        return resolved == default or default in resolved.parents

    # --------------------------------------------------------------- reading
    def read_text(self, candidate: str | Path) -> tuple[Path, str, bool]:
        """Read a text file. Returns ``(path, text, truncated)``."""
        resolved = self.resolve(candidate)
        if not resolved.exists():
            raise SandboxError("file does not exist")
        if not resolved.is_file():
            raise SandboxError("path is not a regular file")

        suffix = resolved.suffix.lower()
        if suffix and suffix not in TEXT_SUFFIXES:
            raise SandboxError(f"{suffix} files are not readable as text")

        size = resolved.stat().st_size
        if size > self.max_bytes * 8:
            raise SandboxError(
                f"file is too large ({size} bytes); the limit is {self.max_bytes} bytes"
            )

        with resolved.open("rb") as handle:
            raw = handle.read(self.max_bytes + 1)

        truncated = len(raw) > self.max_bytes
        text = raw[: self.max_bytes].decode("utf-8", errors="replace")
        return resolved, text, truncated

    # -------------------------------------------------------------- searching
    def search(self, pattern: str, *, limit: int = 20) -> list[Path]:
        """Find files whose name matches a glob pattern, inside the roots."""
        pattern = pattern.strip()
        if not pattern:
            raise SandboxError("empty search pattern")
        if len(pattern) > 128:
            raise SandboxError("search pattern is too long")
        if any(sep in pattern for sep in ("/", "\\", "..")):
            raise SandboxError("search matches file names only, not paths")

        needle = pattern.lower()
        if not any(ch in needle for ch in "*?["):
            needle = f"*{needle}*"

        limit = max(1, min(limit, MAX_SEARCH_RESULTS))
        matches: list[Path] = []
        scanned = 0

        for root in self.roots:
            if not root.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                dirnames[:] = [
                    d for d in dirnames if not _is_hidden(d) and d.lower() not in BLOCKED_DIR_NAMES
                ]
                for filename in filenames:
                    scanned += 1
                    if scanned > MAX_SCANNED_ENTRIES:
                        return matches
                    if _is_hidden(filename) or _name_is_blocked(filename):
                        continue
                    if not fnmatch.fnmatch(filename.lower(), needle):
                        continue
                    candidate = Path(dirpath) / filename
                    try:
                        matches.append(self.resolve(candidate))
                    except SandboxError:
                        continue
                    if len(matches) >= limit:
                        return matches
        return matches


def build_sandbox(roots: list[Path], max_bytes: int) -> PathSandbox:
    """Construct a sandbox from configured roots."""
    return PathSandbox(roots=tuple(roots), max_bytes=max_bytes)
