"""Prove a release sdist installs and serves the real napflow UI.

The UI is generated before the sdist is built. This check deliberately starts
from that sdist, blocks accidental Node/npm use while building its wheel,
installs the wheel into a fresh virtual environment, and drives the installed
``napf`` entry point. It is intentionally an explicit release-gate command,
not an ordinary pytest: dependency installation and a localhost server belong
at the artifact boundary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

SDIST_INDEX_SUFFIX = "src/napflow/server/static/index.html"
SDIST_ASSET_FRAGMENT = "src/napflow/server/static/assets/"
SDIST_NOTICE_SUFFIX = "THIRD_PARTY_NOTICES"
WHEEL_INDEX = "napflow/server/static/index.html"
WHEEL_ASSET_PREFIX = "napflow/server/static/assets/"
WHEEL_NOTICE_SUFFIX = ".dist-info/licenses/THIRD_PARTY_NOTICES"
PLACEHOLDER_MARKER = "napflow server is running"
_LOCAL_ASSET_REFERENCE = re.compile(
    r"""["'`]((?:\./|/assets/)[^"'`?#]+\.(?:js|css)(?:\?[^"'`]*)?)["'`]"""
)


class ArtifactSmokeError(RuntimeError):
    """The built artifact does not satisfy the supported install contract."""


class _BundleHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.has_root = False
        self.assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "div" and attributes.get("id") == "root":
            self.has_root = True
        if tag == "script" and (source := attributes.get("src")):
            self.assets.append(source)
        if tag == "link" and (href := attributes.get("href")):
            rel = (attributes.get("rel") or "").split()
            if "stylesheet" in rel:
                self.assets.append(href)


def _resolve_sdist(candidate: Path) -> Path:
    candidate = candidate.resolve()
    if candidate.is_file():
        return candidate
    if not candidate.is_dir():
        raise ArtifactSmokeError(f"sdist path does not exist: {candidate}")
    matches = sorted(
        path
        for path in candidate.iterdir()
        if path.is_file() and (path.name.endswith(".tar.gz") or path.suffix == ".zip")
    )
    if len(matches) != 1:
        raise ArtifactSmokeError(
            f"expected exactly one sdist in {candidate}, found {len(matches)}"
        )
    return matches[0]


def _sdist_members(path: Path) -> list[str]:
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    try:
        with tarfile.open(path, mode="r:*") as archive:
            return archive.getnames()
    except tarfile.TarError as error:
        raise ArtifactSmokeError(f"cannot read sdist {path}: {error}") from error


def _assert_sdist_bundle(members: list[str]) -> set[str]:
    normalized = [member.replace("\\", "/").removeprefix("./") for member in members]
    if not any(member.endswith(f"/{SDIST_INDEX_SUFFIX}") for member in normalized):
        raise ArtifactSmokeError("release sdist is missing the prebuilt UI index")
    if not any(
        f"/{SDIST_ASSET_FRAGMENT}" in member and member.endswith(".js")
        for member in normalized
    ):
        raise ArtifactSmokeError(
            "release sdist is missing a prebuilt UI JavaScript asset"
        )
    if not any(member.endswith(f"/{SDIST_NOTICE_SUFFIX}") for member in normalized):
        raise ArtifactSmokeError("release sdist is missing THIRD_PARTY_NOTICES")
    marker = "/src/napflow/server/static/"
    return {
        member.split(marker, 1)[1]
        for member in (f"/{item}" for item in normalized)
        if marker in member and not member.endswith("/")
    }


def _assert_wheel_members(members: set[str]) -> set[str]:
    if WHEEL_INDEX not in members:
        raise ArtifactSmokeError("wheel built from the sdist is missing the UI index")
    if not any(
        member.startswith(WHEEL_ASSET_PREFIX) and member.endswith(".js")
        for member in members
    ):
        raise ArtifactSmokeError(
            "wheel built from the sdist is missing a UI JavaScript asset"
        )
    if not any(member.endswith(WHEEL_NOTICE_SUFFIX) for member in members):
        raise ArtifactSmokeError(
            "wheel built from the sdist is missing THIRD_PARTY_NOTICES as a license"
        )
    return {
        member.removeprefix("napflow/server/static/")
        for member in members
        if member.startswith("napflow/server/static/") and not member.endswith("/")
    }


def _assert_wheel_bundle(path: Path) -> set[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            members = set(archive.namelist())
    except zipfile.BadZipFile as error:
        raise ArtifactSmokeError(f"cannot read wheel {path}: {error}") from error
    return _assert_wheel_members(members)


def _write_node_blockers(
    directory: Path,
    *,
    executable: Path,
    windows: bool | None = None,
) -> None:
    """Put loud Node-family traps first on PATH for the sdist build/run."""
    directory.mkdir()
    if windows is None:
        windows = os.name == "nt"
    for command in ("node", "npm", "npx"):
        if windows:
            # CreateProcess searches for .exe directly and may ignore a .cmd
            # shim when shell=False. A copied non-Node executable shadows any
            # later real Node installation and necessarily fails if asked to
            # execute frontend source. Keep the .cmd too for shell invocations.
            shutil.copyfile(executable, directory / f"{command}.exe")
            blocker = directory / f"{command}.cmd"
            blocker.write_text(
                "@echo off\r\n"
                f"echo ERROR: {command} was invoked during artifact smoke 1>&2\r\n"
                "exit /b 97\r\n",
                encoding="utf-8",
            )
        else:
            blocker = directory / command
            blocker.write_text(
                "#!/bin/sh\n"
                f"echo 'ERROR: {command} was invoked during artifact smoke' >&2\n"
                "exit 97\n",
                encoding="utf-8",
            )
            blocker.chmod(0o755)


def _clean_environment(node_blockers: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join(
        (str(node_blockers), environment.get("PATH", ""))
    )
    for name in ("PYTHONHOME", "PYTHONPATH", "UV_PROJECT", "VIRTUAL_ENV"):
        environment.pop(name, None)
    environment["NO_COLOR"] = "1"
    return environment


def _run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=environment, check=True)


def _venv_executable(venv: Path, name: str) -> Path:
    if os.name == "nt":
        suffix = ".exe" if name in {"python", "napf"} else ""
        return venv / "Scripts" / f"{name}{suffix}"
    return venv / "bin" / name


def _pick_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _read_url(url: str, *, timeout: float = 2.0) -> tuple[bytes, str]:
    request = Request(url, headers={"User-Agent": "napflow-artifact-smoke"})
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise ArtifactSmokeError(f"{url} returned HTTP {response.status}")
        return response.read(), response.headers.get_content_type()


def _local_asset_references(source: str) -> set[str]:
    return set(_LOCAL_ASSET_REFERENCE.findall(source))


def _probe_ui(
    base_url: str,
    *,
    timeout: float,
    expected_assets: set[str],
) -> None:
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            raw_html, content_type = _read_url(base_url)
            break
        except (ArtifactSmokeError, OSError, TimeoutError, URLError) as error:
            last_error = error
            time.sleep(0.05)
    else:
        raise ArtifactSmokeError(
            f"installed napf ui did not become reachable: {last_error}"
        )

    html = raw_html.decode("utf-8")
    if content_type != "text/html":
        raise ArtifactSmokeError(f"UI root returned unexpected type {content_type!r}")
    if PLACEHOLDER_MARKER in html:
        raise ArtifactSmokeError(
            "installed napf ui served the missing-bundle placeholder"
        )

    parser = _BundleHTMLParser()
    parser.feed(html)
    javascript = {
        asset
        for asset in parser.assets
        if "/assets/" in asset and asset.split("?", 1)[0].endswith(".js")
    }
    if not parser.has_root or not javascript:
        raise ArtifactSmokeError("UI root is not a compiled napflow bundle")

    # Fetch every packaged asset, every HTML reference (including CSS), and
    # every local lazy JS/CSS chunk referenced by another chunk. This catches a
    # wheel that serves index.html but omitted CodeMirror or another split chunk.
    base = urlsplit(base_url)
    pending = {
        urljoin(base_url, reference)
        for reference in set(parser.assets) | {f"/{asset}" for asset in expected_assets}
    }
    fetched: set[str] = set()
    while pending:
        url = pending.pop()
        parsed = urlsplit(url)
        if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
            raise ArtifactSmokeError(f"compiled UI references external asset {url!r}")
        relative = parsed.path.lstrip("/")
        if relative not in expected_assets:
            raise ArtifactSmokeError(
                f"compiled UI references asset missing from the wheel: {relative}"
            )
        canonical = parsed._replace(query="", fragment="").geturl()
        if canonical in fetched:
            continue
        raw, _ = _read_url(url)
        if not raw:
            raise ArtifactSmokeError(f"compiled UI asset is empty: {relative}")
        fetched.add(canonical)
        if relative.endswith(".js"):
            source = raw.decode("utf-8")
            pending.update(
                urljoin(url, reference) for reference in _local_asset_references(source)
            )

    if not any(urlsplit(url).path.endswith(".js") for url in fetched):
        raise ArtifactSmokeError("compiled UI JavaScript asset was not fetched")

    workspace_raw, workspace_type = _read_url(urljoin(base_url, "/api/workspace"))
    workspace = json.loads(workspace_raw)
    if workspace_type != "application/json" or not workspace.get("version"):
        raise ArtifactSmokeError("installed napf ui API did not identify napflow")


def _wait_for_exit(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def smoke_release_artifact(
    sdist_candidate: Path,
    *,
    uv: Path,
    python: Path,
    server_timeout: float,
) -> Path:
    sdist = _resolve_sdist(sdist_candidate)
    sdist_static = _assert_sdist_bundle(_sdist_members(sdist))

    with tempfile.TemporaryDirectory(prefix="napflow-artifact-smoke-") as raw_temp:
        temp = Path(raw_temp)
        wheels = temp / "wheels"
        wheels.mkdir()
        blockers = temp / "node-blockers"
        _write_node_blockers(blockers, executable=python)
        environment = _clean_environment(blockers)

        _run(
            [
                str(uv),
                "build",
                "--wheel",
                str(sdist),
                "--out-dir",
                str(wheels),
                "--clear",
                "--python",
                str(python),
                "--no-config",
            ],
            cwd=temp,
            environment=environment,
        )
        built_wheels = list(wheels.glob("*.whl"))
        if len(built_wheels) != 1:
            raise ArtifactSmokeError(
                f"expected one wheel built from the sdist, found {len(built_wheels)}"
            )
        wheel = built_wheels[0]
        wheel_static = _assert_wheel_bundle(wheel)
        if wheel_static != sdist_static:
            missing = sorted(sdist_static - wheel_static)
            extra = sorted(wheel_static - sdist_static)
            raise ArtifactSmokeError(
                "wheel static tree differs from release sdist: "
                f"missing={missing}, extra={extra}"
            )

        venv = temp / "venv"
        _run(
            [
                str(uv),
                "venv",
                "--no-project",
                "--python",
                str(python),
                str(venv),
                "--no-config",
            ],
            cwd=temp,
            environment=environment,
        )
        installed_python = _venv_executable(venv, "python")
        _run(
            [
                str(uv),
                "pip",
                "install",
                "--python",
                str(installed_python),
                "--strict",
                str(wheel),
                "--no-config",
            ],
            cwd=temp,
            environment=environment,
        )

        napf = _venv_executable(venv, "napf")
        _run([str(napf), "--version"], cwd=temp, environment=environment)
        workspace = temp / "workspace"
        _run(
            [str(napf), "init", str(workspace)],
            cwd=temp,
            environment=environment,
        )
        public_api_smoke = """
import sys
from pathlib import Path
from napflow.core import load_workspace, run_flow

workspace = load_workspace(Path(sys.argv[1]))
bound = workspace.flow('flows/smoke').run(history=False)
functional = run_flow(workspace, 'flows/smoke', history=False)
assert bound.state == functional.state == 'passed'
assert workspace.flows.smoke.identity == 'flows/smoke'
print('installed public API passed:', functional.state)
""".strip()
        _run(
            [str(installed_python), "-c", public_api_smoke, str(workspace)],
            cwd=temp,
            environment=environment,
        )

        port = _pick_port()
        base_url = f"http://127.0.0.1:{port}/"
        server_log = temp / "napf-ui.log"
        with server_log.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                [
                    str(napf),
                    "ui",
                    "--no-browser",
                    "--port",
                    str(port),
                ],
                cwd=workspace,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            try:
                _probe_ui(
                    base_url,
                    timeout=server_timeout,
                    expected_assets=wheel_static - {"index.html"},
                )
                if process.poll() is not None:
                    raise ArtifactSmokeError(
                        "installed napf ui exited unexpectedly with "
                        f"{process.returncode}"
                    )
            except BaseException as error:
                _wait_for_exit(process)
                output.flush()
                log = server_log.read_text(encoding="utf-8", errors="replace")
                raise ArtifactSmokeError(f"{error}\nnapf ui output:\n{log}") from error
            else:
                _wait_for_exit(process)

        print(
            "artifact smoke passed: release sdist -> no-Node wheel -> "
            "isolated public API + real napf ui",
            flush=True,
        )
        return sdist


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sdist",
        type=Path,
        help="release sdist file, or a directory containing exactly one sdist",
    )
    parser.add_argument(
        "--uv",
        type=Path,
        default=Path(shutil.which("uv") or "uv"),
        help="uv executable (default: resolve from PATH)",
    )
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="Python used for the isolated build/install environment",
    )
    parser.add_argument(
        "--server-timeout",
        type=float,
        default=30.0,
        help="seconds to wait for the installed napf ui server",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        smoke_release_artifact(
            args.sdist,
            uv=args.uv.resolve(),
            python=args.python.resolve(),
            server_timeout=args.server_timeout,
        )
    except (ArtifactSmokeError, OSError, subprocess.CalledProcessError) as error:
        print(f"artifact smoke failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
