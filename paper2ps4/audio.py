"""Audio handling for a PS4 theme.

A PS4 theme supports exactly one menu music (sound/bgm_home.at9) and three
sound effects (.vag). Wallpaper Engine scenes often bundle many MP3s
(ambient/playlist). Default: pick the largest/longest audio file as BGM and
omit SEs unless the user selects them.

ATRAC9 (.at9) is proprietary: ffmpeg decodes it but does not encode it, so we
convert via ps4_at9tool.exe (wine on Linux). If at9tool is missing we ship no
music (the theme still works, just silent).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import constants
from . import tools
from .media import MediaError, require_tools, to_wav, FFMPEG


def find_at9tool() -> Path | None:
    return tools.find_tool(constants.AT9_TOOL_NAMES)


def at9_available() -> bool:
    return find_at9tool() is not None


def select_bgm(audio_files: list[Path], preferred: Path | None = None) -> Path | None:
    """Pick the BGM source from a list of audio files.

    `preferred` (user choice) wins; otherwise the largest file wins.
    """
    if preferred and preferred in audio_files:
        return preferred
    if preferred and preferred.exists():
        return preferred
    if not audio_files:
        return None
    return max(audio_files, key=lambda p: p.stat().st_size)


def concatenate(sources: list[Path], dest_wav: Path, rate: int = constants.AT9_SAMPLE_RATE,
                gap_seconds: float = 0.35) -> Path:
    """Join several audio files into one WAV.

    Wallpaper Engine scenes often ship a whole playlist of tracks. The theme
    has room for a single menu music, so the tracks are joined into one file
    (with a short gap between them) and that becomes the .at9.
    """
    require_tools()
    if not sources:
        raise MediaError("no audio files to join")
    if len(sources) == 1:
        return to_wav(sources[0], dest_wav, rate)

    dest_wav.parent.mkdir(parents=True, exist_ok=True)

    # The concat demuxer takes a list file of paths, one per line.
    list_file = dest_wav.with_suffix(".concat.txt")
    lines = []
    for path in sources:
        escaped = str(path.resolve()).replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(list_file)]
    if gap_seconds > 0:
        # A short silence between the tracks keeps them from clicking into
        # each other.
        args += ["-af", f"apad=pad_dur={gap_seconds}"]
    args += ["-c:a", "pcm_s16le", "-ac", "2", "-ar", str(rate), str(dest_wav)]

    proc = subprocess.run(args, capture_output=True, text=True)
    list_file.unlink(missing_ok=True)
    if proc.returncode != 0 or not dest_wav.is_file():
        tail = proc.stderr.strip().splitlines()[-4:]
        raise MediaError("ffmpeg could not join the audio: " + ("\n".join(tail) or "no output"))
    return dest_wav


def convert_to_at9(wav_path: Path, dest: Path, bitrate: int = constants.AT9_BITRATE) -> Path:
    """WAV PCM -> .at9 via ps4_at9tool.exe. Raises MediaError if the tool is missing."""
    tool = find_at9tool()
    if tool is None:
        raise MediaError(
            "ps4_at9tool.exe not found: "
            + tools.describe_search(constants.AT9_TOOL_NAMES) + ". "
            "Install it to convert the music, or skip it with --no-music."
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()

    proc = subprocess.run(
        tools._windows_tool_cmd(tool) + [
            "-e",
            "-br", str(bitrate),
            "-fs", str(constants.AT9_SAMPLE_RATE),
            # Menu music loops. Without -wholeloop no smpl chunk is written and
            # the file comes out with no loop point (silent on console).
            "-wholeloop",
            str(wav_path), str(dest),
        ],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=900,
    )
    if not dest.is_file() or dest.stat().st_size == 0:
        tail = proc.stdout.decode("utf-8", "replace").strip().splitlines()
        raise MediaError("at9tool produced no file: " + ("\n".join(tail[-4:]) or "no output"))
    return dest


def prepare_bgm(audio_src: Path, work_dir: Path, out_at9: Path,
                max_seconds: float = constants.BGM_MAX_SECONDS,
                target_lufs: float = constants.BGM_TARGET_LUFS) -> Path:
    """Convert any audio file to a theme-ready .at9 via an intermediate WAV.

    The audio is levelled to `target_lufs` first: a normal song sits about
    13 dB above the music of a retail theme and drowns out the rest of the
    system, so it is brought down before it is encoded.
    """
    require_tools()
    wav = work_dir / "bgm_src.wav"
    to_wav(audio_src, wav, constants.AT9_SAMPLE_RATE, max_seconds=max_seconds,
           loudnorm_lufs=target_lufs)
    return convert_to_at9(wav, out_at9)
