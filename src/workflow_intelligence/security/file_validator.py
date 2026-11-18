"""Security module for path validation, traversal prevention, and ingestion safety."""

from __future__ import annotations

import os
from pathlib import Path

from workflow_intelligence.config import get_settings

# Standard supported extensions across event logs and tabular datasets
SUPPORTED_FILE_EXTENSIONS = {
    ".csv",
    ".json",
    ".parquet",
    ".pq",
    ".ocel",
    ".jsonocel",
}

# Sensitive filenames and patterns that should never be accessed
BLOCKED_FILENAMES = {
    ".env",
    ".env.example",
    "id_rsa",
    "id_dsa",
    "id_ed25519",
    "passwd",
    "shadow",
    "master.passwd",
    ".git",
    ".gitignore",
}


class SecurityError(Exception):
    """Raised when security boundaries, traversal, or size limits are violated."""


def get_default_allowed_directories() -> list[Path]:
    """Return resolved allowlisted directories for safe file access."""
    settings = get_settings()
    repo_root = Path(__file__).resolve().parents[3]

    allowed: list[Path] = []
    # User configured directories
    for d in settings.allowed_data_dirs:
        p = Path(d)
        if not p.is_absolute():
            p = repo_root / p
        try:
            allowed.append(p.resolve())
        except Exception:
            continue

    # Core application data directories
    core_dirs = [
        repo_root / "sample-data",
        repo_root / "data",
        Path("/app/sample-data"),
        Path("/app/data"),
    ]
    for cd in core_dirs:
        try:
            if cd.exists():
                allowed.append(cd.resolve())
        except Exception:
            continue

    # Under pytest/testing, allow the system temp directory so unit fixtures (tmp_path) work
    if os.environ.get("PYTEST_CURRENT_TEST") and settings.workflow_env != "production":
        import tempfile

        try:
            allowed.append(Path(tempfile.gettempdir()).resolve())
        except Exception:
            pass

    # Deduplicate while preserving order
    unique_allowed: list[Path] = []
    for dir_path in allowed:
        if dir_path not in unique_allowed:
            unique_allowed.append(dir_path)

    return unique_allowed


def validate_safe_path(
    source_uri: str,
    allowed_dirs: list[Path] | None = None,
    max_size_bytes: int | None = None,
    supported_extensions: set[str] | None = None,
) -> Path:
    """Validate and resolve a file path against traversal, symlink escapes, and size limits.

    Security checks:
    1. Rejects relative paths containing '..' components before canonicalization.
    2. Resolves path and verifies target is a regular file.
    3. Rejects sensitive/hidden files (.env, .git, passwd, etc.).
    4. Validates that the fully canonicalized path lies strictly inside an allowed directory.
    5. Validates file extension against supported formats.
    6. Validates file size against maximum allowable limit.

    Args:
        source_uri: Raw file path or URI string.
        allowed_dirs: List of allowed root directories (defaults to configured allowlist).
        max_size_bytes: Maximum allowed file size in bytes (defaults to settings).
        supported_extensions: Set of supported lowercase file extensions.

    Returns:
        Canonicalized Path object.

    Raises:
        SecurityError: If any security validation check fails.
    """
    if not source_uri or not isinstance(source_uri, str):
        raise SecurityError("source_uri must be a non-empty string.")

    raw_path = Path(source_uri.strip())

    # 1. Traversal check on raw components
    if ".." in raw_path.parts:
        raise SecurityError(
            f"Path traversal detected: '{source_uri}' contains parent-directory references ('..')."
        )

    # 2. Block sensitive filenames and hidden files
    filename = raw_path.name.lower()
    if filename in BLOCKED_FILENAMES or filename.startswith("."):
        raise SecurityError(
            f"Access to sensitive or hidden file '{filename}' is denied."
        )

    for part in raw_path.parts:
        if part.startswith(".") and part != ".":
            raise SecurityError(
                f"Access denied: Path component '{part}' is a hidden directory."
            )

    # 3. Canonicalize path and verify existence
    try:
        resolved = raw_path.expanduser().resolve()
    except Exception as exc:
        raise SecurityError(f"Cannot resolve path '{source_uri}': {exc}") from exc

    if not resolved.exists():
        raise SecurityError(f"File not found: '{resolved}'.")

    if not resolved.is_file():
        raise SecurityError(f"Path is not a regular file: '{resolved}'.")

    # 3b. Re-verify sensitive filenames and hidden components on canonical target
    resolved_filename = resolved.name.lower()
    if resolved_filename in BLOCKED_FILENAMES or resolved_filename.startswith("."):
        raise SecurityError(
            f"Access to sensitive or hidden file '{resolved_filename}' via resolved target is denied."
        )

    for part in resolved.parts:
        if part.startswith(".") and part != ".":
            raise SecurityError(
                f"Access denied: Resolved path component '{part}' is a hidden directory."
            )

    # 4. Check extension
    extensions = supported_extensions or SUPPORTED_FILE_EXTENSIONS
    suffix = resolved.suffix.lower()
    # Check for compound suffixes like .jsonocel
    if "".join(resolved.suffixes).lower() in extensions:
        suffix = "".join(resolved.suffixes).lower()

    if suffix not in extensions:
        raise SecurityError(
            f"Unsupported file format '{suffix}'. Supported formats: {sorted(extensions)}."
        )

    # 5. Verify path is within allowed directories
    if allowed_dirs is None:
        allowed_dirs = get_default_allowed_directories()

    is_allowed = False
    for allowed_dir in allowed_dirs:
        try:
            canonical_allowed = allowed_dir.resolve()
            if resolved.is_relative_to(canonical_allowed):
                is_allowed = True
                break
        except Exception:
            continue

    if not is_allowed:
        raise SecurityError(
            f"Access denied: File '{resolved}' is outside configured allowed data directories."
        )

    # 6. Check file size limit
    settings = get_settings()
    limit = max_size_bytes or settings.max_file_size_bytes
    if limit and limit > 0:
        file_size = resolved.stat().st_size
        if file_size > limit:
            raise SecurityError(
                f"File size ({file_size} bytes) exceeds maximum configured limit of {limit} bytes."
            )

    return resolved
