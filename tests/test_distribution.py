import runpy
import zipfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[1]
_TOOL = runpy.run_path(str(_ROOT / "tools" / "smoke_release_artifact.py"))
ArtifactSmokeError = _TOOL["ArtifactSmokeError"]
_assert_sdist_bundle = _TOOL["_assert_sdist_bundle"]
_assert_matching_wheel_payloads = _TOOL["_assert_matching_wheel_payloads"]
_assert_wheel_members = _TOOL["_assert_wheel_members"]
_BundleHTMLParser = _TOOL["_BundleHTMLParser"]
_local_asset_references = _TOOL["_local_asset_references"]
_resolve_published_wheel = _TOOL["_resolve_published_wheel"]
_write_node_blockers = _TOOL["_write_node_blockers"]


def test_release_artifact_probe_recognizes_compiled_ui() -> None:
    parser = _BundleHTMLParser()
    parser.feed(
        '<div id="root"></div><script type="module" '
        'src="/assets/index-abc123.js"></script>'
    )

    assert parser.has_root
    assert parser.assets == ["/assets/index-abc123.js"]


def test_release_sdist_requires_prebuilt_ui_index_and_asset() -> None:
    prefix = "napflow-0.1.0/"
    assert _assert_sdist_bundle(
        [
            prefix + "src/napflow/server/static/index.html",
            prefix + "src/napflow/server/static/assets/index-abc123.js",
            prefix + "THIRD_PARTY_NOTICES",
        ]
    ) == {"index.html", "assets/index-abc123.js"}

    with pytest.raises(ArtifactSmokeError, match="JavaScript asset"):
        _assert_sdist_bundle(
            [
                prefix + "src/napflow/server/static/index.html",
                prefix + "THIRD_PARTY_NOTICES",
            ]
        )


def test_release_wheel_requires_ui_and_third_party_license() -> None:
    members = {
        "napflow/server/static/index.html",
        "napflow/server/static/assets/index-abc123.js",
        "napflow-0.1.0.dist-info/licenses/THIRD_PARTY_NOTICES",
    }
    assert _assert_wheel_members(members) == {
        "index.html",
        "assets/index-abc123.js",
    }

    members.remove("napflow-0.1.0.dist-info/licenses/THIRD_PARTY_NOTICES")
    with pytest.raises(ArtifactSmokeError, match="THIRD_PARTY_NOTICES"):
        _assert_wheel_members(members)


def test_release_directory_requires_one_direct_published_wheel(tmp_path: Path) -> None:
    with pytest.raises(ArtifactSmokeError, match="exactly one published wheel"):
        _resolve_published_wheel(tmp_path)

    wheel = tmp_path / "napflow-0.2.0-py3-none-any.whl"
    wheel.write_bytes(b"placeholder")
    assert _resolve_published_wheel(tmp_path) == wheel

    (tmp_path / "napflow-0.2.1-py3-none-any.whl").write_bytes(b"placeholder")
    with pytest.raises(ArtifactSmokeError, match="found 2"):
        _resolve_published_wheel(tmp_path)


def test_published_and_sdist_rebuilt_wheel_payloads_must_match(
    tmp_path: Path,
) -> None:
    published = tmp_path / "published.whl"
    rebuilt = tmp_path / "rebuilt.whl"

    def write_wheel(path: Path, module: bytes, record: bytes) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("napflow/__init__.py", module)
            archive.writestr("napflow-0.2.0.dist-info/RECORD", record)

    write_wheel(published, b"same package", b"published hashes")
    write_wheel(rebuilt, b"same package", b"rebuilt hashes")
    _assert_matching_wheel_payloads(published, rebuilt)

    write_wheel(rebuilt, b"different package", b"rebuilt hashes")
    with pytest.raises(ArtifactSmokeError, match="changed=.*napflow/__init__"):
        _assert_matching_wheel_payloads(published, rebuilt)


def test_release_probe_discovers_local_lazy_chunks_and_styles() -> None:
    source = 'import(`./CodeMirrorPane-abc.js`);const css = "/assets/index-def.css";'

    assert _local_asset_references(source) == {
        "./CodeMirrorPane-abc.js",
        "/assets/index-def.css",
    }


def test_windows_node_blockers_shadow_executable_lookup(tmp_path: Path) -> None:
    stand_in = tmp_path / "python.exe"
    stand_in.write_bytes(b"not actually an executable")
    blockers = tmp_path / "blockers"

    _write_node_blockers(blockers, executable=stand_in, windows=True)

    for command in ("node", "npm", "npx"):
        assert (blockers / f"{command}.exe").read_bytes() == stand_in.read_bytes()
        assert (blockers / f"{command}.cmd").is_file()


def test_install_docs_promise_release_artifacts_not_raw_source() -> None:
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")

    assert "PyPI or a GitHub release artifact" in readme
    assert "VCS (`git+https`) installs and PEP 517 builds" in readme
    assert "raw source checkout are\n> unsupported" in readme
    assert "deterministic Git installs are planned" not in readme
