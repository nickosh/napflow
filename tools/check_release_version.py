#!/usr/bin/env python3
"""Refuse release tags that cannot safely publish the project version.

The release workflow is the enforcement boundary, while the functions here
keep that boundary independently testable.  Development checkpoints may be
built and tested, but they are never publishable releases.
"""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYPROJECT = ROOT / "pyproject.toml"
_DEV_RELEASE = re.compile(r"dev\d*(?:$|[.+-])", re.IGNORECASE)


class ReleaseVersionError(ValueError):
    """The package version or release tag violates the release contract."""


def read_project_version(pyproject: Path = DEFAULT_PYPROJECT) -> str:
    """Read the project version without importing or installing napflow."""
    try:
        version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
            "version"
        ]
    except (KeyError, TypeError) as error:
        raise ReleaseVersionError(
            f"{pyproject}: project.version is missing or invalid"
        ) from error
    if not isinstance(version, str) or not version:
        raise ReleaseVersionError(
            f"{pyproject}: project.version must be a non-empty string"
        )
    return version


def validate_release_version(version: str, *, tag: str | None = None) -> None:
    """Require a final package version and, when present, its exact ``v`` tag."""
    if _DEV_RELEASE.search(version):
        raise ReleaseVersionError(
            f"development version {version!r} cannot be published"
        )
    if tag is not None and tag != (expected := f"v{version}"):
        raise ReleaseVersionError(
            f"package version {version!r} requires tag {expected!r}, got {tag!r}"
        )


def check_release_version(
    pyproject: Path = DEFAULT_PYPROJECT, *, tag: str | None = None
) -> str:
    """Validate and return the release-ready project version."""
    version = read_project_version(pyproject)
    validate_release_version(version, tag=tag)
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=DEFAULT_PYPROJECT,
        help="project metadata to validate (default: repository pyproject.toml)",
    )
    parser.add_argument(
        "--tag",
        help="release tag, which must equal v followed by project.version",
    )
    args = parser.parse_args()
    try:
        version = check_release_version(args.pyproject, tag=args.tag)
    except (OSError, tomllib.TOMLDecodeError, ReleaseVersionError) as error:
        parser.exit(1, f"release version check failed: {error}\n")
    suffix = f" and tag {args.tag}" if args.tag is not None else ""
    print(f"release version check passed: {version}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
