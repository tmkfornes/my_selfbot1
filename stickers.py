"""Converts a video into a Telegram-compatible animated (video) sticker:
WEBM container, VP9 codec, no audio, capped at 3 seconds, longest side
512px, and kept under Telegram's ~256KB size limit for video stickers.

Uses ffmpeg to do the actual encoding. Rather than requiring the user to
install ffmpeg system-wide and add it to PATH (a common source of pain,
especially on Windows), this resolves a ready-to-use ffmpeg binary via the
`imageio-ffmpeg` pip package, which ships a static binary per platform —
`pip install -r requirements.txt` is enough, nothing extra to configure.
Falls back to a system `ffmpeg` on PATH if that package isn't available.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

MAX_DURATION_SECONDS = 3
MAX_DIMENSION = 512
STICKER_MAX_BYTES = 256 * 1024  # Telegram's real cap for video/animated stickers
# Highest quality first; only step down if the encode actually comes out
# too big. 620kbps is close to the theoretical max a 3s clip can use while
# staying under the 256KB cap (256KB*8bits/3s ≈ 683kbps) with some margin
# for container overhead.
BITRATE_ATTEMPTS_KBPS = (620, 480, 350, 250, 180, 120)
ENCODE_TIMEOUT_SECONDS = 60


class FfmpegNotFoundError(RuntimeError):
    pass


class StickerEncodeError(RuntimeError):
    pass


def _resolve_ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    raise FfmpegNotFoundError(
        "ffmpeg در دسترس نیست. `pip install imageio-ffmpeg` رو اجرا کن "
        "(تو requirements.txt هست) یا خودت ffmpeg رو نصب و به PATH اضافه کن."
    )


def is_available() -> bool:
    try:
        _resolve_ffmpeg()
        return True
    except FfmpegNotFoundError:
        return False


def video_to_sticker_webm(video_bytes: bytes) -> bytes:
    ffmpeg = _resolve_ffmpeg()

    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "in.mp4"
        in_path.write_bytes(video_bytes)

        last_output = None
        last_stderr = b""

        for i, bitrate in enumerate(BITRATE_ATTEMPTS_KBPS):
            out_path = Path(tmp) / f"out_{i}.webm"
            cmd = [
                ffmpeg, "-y",
                "-i", str(in_path),
                "-t", str(MAX_DURATION_SECONDS),
                "-an",
                "-vf", f"scale={MAX_DIMENSION}:{MAX_DIMENSION}:force_original_aspect_ratio=decrease,fps=30",
                "-c:v", "libvpx-vp9",
                "-b:v", f"{bitrate}k",
                "-deadline", "good",
                "-cpu-used", "2",
                "-row-mt", "1",
                "-pix_fmt", "yuva420p",
                str(out_path),
            ]

            try:
                result = subprocess.run(
                    cmd, capture_output=True, timeout=ENCODE_TIMEOUT_SECONDS
                )
            except subprocess.TimeoutExpired:
                raise StickerEncodeError("تبدیل ویدیو بیش از حد طول کشید (timeout).")

            if result.returncode != 0 or not out_path.exists():
                last_stderr = result.stderr
                continue

            data = out_path.read_bytes()
            last_output = data
            if len(data) <= STICKER_MAX_BYTES:
                return data
            # else: too big at this bitrate, try the next (lower) one

        if last_output is not None:
            # Every attempt (even the lowest bitrate) came out over the cap —
            # extremely unlikely for a 3s/512px clip, but return the smallest
            # one we managed rather than failing outright.
            return last_output

        stderr = last_stderr.decode(errors="ignore")[-500:]
        raise StickerEncodeError(f"ffmpeg شکست خورد: {stderr}")
