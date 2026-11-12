"""Packaging to .fpkg with the PS4 Fake PKG Tools (orbis-pub-cmd.exe).

Chain:
    sfo_create  -> sce_sys/param.sfo
    (keystone skipped for themes)
    gp4 (generated here, plain XML)
    img_create  -> the .fpkg

What makes the PS4 see a THEME rather than an app is volume_type=pkg_ps4_theme
plus IRO_TAG=2 in param.sfo. Without IRO_TAG it installs as an app with a
"Start" button and throws CE-37695-0. CATEGORY must be 'ac' (gd crashes with
CE-36329-3).
"""

from __future__ import annotations

import datetime as _dt
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import constants
from . import tools
import os


class PackageError(RuntimeError):
    """Packaging failure carrying the useful part of the tool's output."""


BUILD_TIMEOUT = 30 * 60

CONTENT_ID_RE = re.compile(r"^[A-Z0-9]{6}-[A-Z0-9]{9}_00-[A-Z0-9]{16}$")
TITLE_ID_RE = re.compile(r"^[A-Z]{4}[0-9]{5}$")
PASSCODE_RE = re.compile(r"^[A-Za-z0-9]{32}$")


def orbis_cmd() -> Path | None:
    """Where orbis-pub-cmd.exe is.

    Looked up when it is needed rather than at import time, so the folders
    given with --tools are already known.
    """
    return tools.find_tool(constants.ORBIS_PUB_CMD_NAMES)


def tools_available() -> bool:
    return orbis_cmd() is not None


def _run(label: str, args: list[str], timeout: int = 300) -> str:
    exe = orbis_cmd()
    if exe is None:
        raise PackageError(
            "orbis-pub-cmd.exe not found: "
            + tools.describe_search(constants.ORBIS_PUB_CMD_NAMES) + ".")
    cmd = tools._windows_tool_cmd(exe) + args
    started = time.time()
    try:
        env = os.environ.copy()
        env["WINEDEBUG"] = "-all"
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise PackageError(f"{label}: the tool hung (more than {timeout}s).") from exc

    out = proc.stdout.decode("utf-8", "replace")
    out = "\n".join(
        line for line in out.splitlines()
        if not re.match(r"^(Fake PKG Tools|orbis-pub-cmd|Custom Patches|CyB1K)", line.strip())
    )
    errors = [ln for ln in out.splitlines() if ln.strip().startswith("[Error]")]
    if errors or proc.returncode != 0:
        detail = "\n".join(e.replace("[Error]", "").strip() for e in errors)
        raise PackageError(f"{label}: {detail or f'code {proc.returncode}'}")
    return out


def calc_loudness(path: Path) -> float | None:
    """Measure an ATRAC9 file using orbis-pub-cmd."""
    if orbis_cmd() is None:
        return None

    output = _run(
        "audio loudness measurement",
        ["file_calc_loudness", str(path)],
    )

    match = re.search(
        r"(-?\d+(?:\.\d+)?)\s*LKFS\b",
        output,
        re.IGNORECASE,
    )
    if match is None:
        raise PackageError(
            "Could not parse LKFS loudness from orbis-pub-cmd output:\n"
            + output
        )

    return float(match.group(1))


@dataclass
class PackageMeta:
    title: str
    title_id: str
    content_id: str
    category: str = constants.PKG_DEFAULTS["category"]
    passcode: str = constants.PKG_DEFAULTS["passcode"]
    #: The publisher name shown in the console's information screen. The
    #: checker requires the parameter to exist; any non-empty name works.
    provider: str = constants.PKG_DEFAULTS["provider"]
    bgm_loudness: float | None = None

    def validate(self) -> None:
        if not self.title.strip():
            raise PackageError("The title cannot be empty.")
        if len(self.title) > 128:
            raise PackageError("The title cannot exceed 128 characters.")
        if not (self.provider or "").strip():
            raise PackageError("The provider cannot be empty.")
        if len(self.provider) > 128:
            raise PackageError("The provider cannot exceed 128 characters.")
        if not TITLE_ID_RE.match(self.title_id):
            raise PackageError(
                f"Invalid Title ID: '{self.title_id}'. Must be 4 letters and 5 digits, e.g. CUSA00000."
            )
        if not CONTENT_ID_RE.match(self.content_id):
            raise PackageError(
                f"Invalid Content ID: '{self.content_id}'. "
                "Format: XXXXXX-CUSA00000_00-ABCDEFGHIJKLMNOP."
            )
        if self.category not in constants.PKG_MODE_THEME["categories"]:
            raise PackageError(
                f"Invalid category: '{self.category}'. A theme package only works with: "
                f"{', '.join(constants.PKG_MODE_THEME['categories'])}."
            )
        if not PASSCODE_RE.match(self.passcode):
            raise PackageError("The passcode must be exactly 32 alphanumeric characters.")
        if self.title_id not in self.content_id:
            raise PackageError("The Content ID must contain the Title ID.")


def _esc(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )


def build_param_sfx(meta: PackageMeta) -> str:
    """Description of the param.sfo. Theme mode: IRO_TAG in, APP_* fields out."""
    rows = [
        '  <param key="ATTRIBUTE" fmt="int32">0</param>',
        f'  <param key="CATEGORY" fmt="utf8" max_len="4">{_esc(meta.category)}</param>',
        f'  <param key="CONTENT_ID" fmt="utf8" max_len="48">{_esc(meta.content_id)}</param>',
        '  <param key="FORMAT" fmt="utf8" max_len="4">obs</param>',
        f'  <param key="PROVIDER" fmt="utf8" max_len="128">{_esc(meta.provider)}</param>',
        f'  <param key="TITLE" fmt="utf8" max_len="128">{_esc(meta.title)}</param>',
        f'  <param key="TITLE_ID" fmt="utf8" max_len="12">{_esc(meta.title_id)}</param>',
        '  <param key="VERSION" fmt="utf8" max_len="8">01.00</param>',
        f'  <param key="IRO_TAG" fmt="int32">{constants.PKG_MODE_THEME["iro_tag"]}</param>',
        '  <param key="CONTENT_VER" fmt="utf8" max_len="8">01.00</param>',
    ]

    info = [f"c_date={_dt.date.today():%Y%m%d}"]
    if meta.bgm_loudness is not None:
        info.append(f"bgmhome_loud={meta.bgm_loudness:.2f}")
    rows.append(
        f'  <param key="PUBTOOLINFO" fmt="utf8" max_len="512">{_esc(",".join(info))}</param>'
    )

    return "\n".join([
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
        "<paramsfo>",
        *sorted(rows),
        "</paramsfo>",
        "",
    ])


def build_gp4(meta: PackageMeta, targets: list[str], content_dir_name: str) -> str:
    """Build the .gp4 by hand (plain XML)."""
    profile = constants.PKG_MODE_THEME

    dirs: dict[str, dict] = {}
    for target in targets:
        node = dirs
        for part in target.split("/")[:-1]:
            node = node.setdefault(part, {})

    def render_dirs(node: dict, indent: int) -> list[str]:
        out = []
        pad = " " * indent
        for name, children in node.items():
            if children:
                out.append(f'{pad}<dir targ_name="{_esc(name)}">')
                out += render_dirs(children, indent + 2)
                out.append(f"{pad}</dir>")
            else:
                out.append(f'{pad}<dir targ_name="{_esc(name)}"/>')
        return out

    files = [
        f'    <file targ_path="{_esc(t)}" '
        f'orig_path="{_esc(content_dir_name)}\\{_esc(t.replace("/", chr(92)))}"/>'
        for t in targets
    ]

    extra = "".join(f' {k}="{_esc(v)}"' for k, v in profile["extra_package_attrs"].items())

    return "\ufeff" + "\n".join([
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
        '<psproject fmt="gp4" version="1000">',
        "  <volume>",
        f"    <volume_type>{profile['volume_type']}</volume_type>",
        "    <volume_id></volume_id>",
        f"    <volume_ts>{time.strftime('%Y-%m-%d %H:%M:%S')}</volume_ts>",
        f'    <package content_id="{_esc(meta.content_id)}" '
        f'passcode="{_esc(meta.passcode)}"{extra}/>',
        "  </volume>",
        '  <files img_no="0">',
        *files,
        "  </files>",
        "  <rootdir>",
        *render_dirs(dirs, 4),
        "  </rootdir>",
        "</psproject>",
        "",
    ])


def build_package(work_dir: Path, content_dir: Path, meta: PackageMeta) -> Path:
    """Build the .fpkg from an already assembled content folder.

    `content_dir` must hold everything except param.sfo, which is generated
    here inside its sce_sys.
    """
    if not tools_available():
        raise PackageError(
            "orbis-pub-cmd.exe not found: "
            + tools.describe_search(constants.ORBIS_PUB_CMD_NAMES) + ".")
    meta.validate()

    sce_sys = content_dir / "sce_sys"
    sce_sys.mkdir(parents=True, exist_ok=True)

    # 1. param.sfo
    sfx_path = work_dir / "param.sfx"
    sfx_path.write_text(build_param_sfx(meta), encoding="utf-8")
    _run("param.sfo", ["sfo_create", str(sfx_path), str(sce_sys / "param.sfo")])

    # 2. gp4 with every file left in the content folder
    targets = sorted(
        p.relative_to(content_dir).as_posix()
        for p in content_dir.rglob("*") if p.is_file()
    )
    if not targets:
        raise PackageError("There is nothing to package.")

    gp4_path = work_dir / "theme.gp4"
    gp4_path.write_text(build_gp4(meta, targets, content_dir.name), encoding="utf-8")

    # 3. the package
    out_dir = work_dir / "pkg"
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    tmp_dir = work_dir / "tmp"
    tmp_dir.mkdir(exist_ok=True)

    _run(
        "img_create",
        [
            "img_create", "--no_progress_bar",
            "--tmp_path", str(tmp_dir),
            "--oformat", "pkg", str(gp4_path), str(out_dir),
        ],
        timeout=BUILD_TIMEOUT,
    )

    built = sorted(out_dir.glob("*.pkg"))
    if not built:
        raise PackageError("img_create finished without errors but produced no .pkg.")

    shutil.rmtree(tmp_dir, ignore_errors=True)
    return built[0]
