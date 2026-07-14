from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_LINE = re.compile(r"^- (.+) ([^ ]+) \(([^)]+)\)$")
REVIEWED_LICENSES = {"Apache-2.0", "BSD-3-Clause", "ISC", "MIT"}


def test_frontend_notice_inventory_matches_production_lock() -> None:
    lock = json.loads((ROOT / "ui" / "package-lock.json").read_text(encoding="utf-8"))
    expected = {
        (
            path.rsplit("node_modules/", 1)[1],
            metadata["version"],
            metadata.get("license"),
        )
        for path, metadata in lock["packages"].items()
        if path and not metadata.get("dev", False)
    }

    notice = (ROOT / "THIRD_PARTY_NOTICES").read_text(encoding="utf-8")
    inventory = notice.split("Upstream license texts", 1)[0]
    actual = {
        match.groups()
        for line in inventory.splitlines()
        if (match := INVENTORY_LINE.fullmatch(line))
    }
    assert actual == expected
    assert {license_id for _, _, license_id in expected} <= REVIEWED_LICENSES


def test_frontend_notices_ship_as_wheel_license_file() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "THIRD_PARTY_NOTICES" in config["project"]["license-files"]
