"""Media kind detection and LinkedIn size/length validation. No network calls.

The warnings here are advisory. They mirror LinkedIn's documented limits so the owner sees a
problem before publishing, but LinkedIn does the real enforcement when the post goes out.
"""

from pathlib import Path

from . import config

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
ALLOWED_EXTS = IMAGE_EXTS | VIDEO_EXTS


def kind(path) -> str:
    """Return 'video' or 'image' from the file extension (images are the default)."""
    return "video" if Path(path).suffix.lower() in VIDEO_EXTS else "image"


def _parse_mvhd_duration(buf: bytes) -> float | None:
    """Read duration (seconds) from an MP4/MOV `mvhd` atom inside buf, or None."""
    idx = buf.find(b"mvhd")
    if idx == -1:
        return None
    p = idx + 4
    try:
        version = buf[p]
        if version == 0:
            timescale = int.from_bytes(buf[p + 12 : p + 16], "big")
            duration = int.from_bytes(buf[p + 16 : p + 20], "big")
        else:
            timescale = int.from_bytes(buf[p + 20 : p + 24], "big")
            duration = int.from_bytes(buf[p + 24 : p + 32], "big")
    except IndexError:
        return None
    return duration / timescale if timescale else None


def video_duration_seconds(path: Path) -> float | None:
    """Best-effort MP4/MOV duration. The moov atom sits near the start (fast-start files) or
    the end, so scan both without loading a multi-gigabyte file into memory. None if unknown.
    """
    window = 20 * 1024 * 1024
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            head = handle.read(min(size, window))
            found = _parse_mvhd_duration(head)
            if found is not None:
                return found
            if size > window:
                handle.seek(size - window)
                return _parse_mvhd_duration(handle.read())
    except OSError:
        return None
    return None


def _mb(num_bytes: int) -> float:
    return num_bytes / (1024 * 1024)


def warnings_for(path: Path) -> list[str]:
    """Return advisory warnings if a file breaks LinkedIn's documented media limits."""
    warns: list[str] = []
    try:
        size = path.stat().st_size
    except OSError:
        return warns

    if kind(path) == "video":
        if size > config.VIDEO_MAX_MB * 1024 * 1024:
            warns.append(f"Video is {_mb(size):.0f} MB; LinkedIn's limit is {config.VIDEO_MAX_MB:.0f} MB.")
        if size < config.VIDEO_MIN_KB * 1024:
            warns.append(f"Video is under LinkedIn's {config.VIDEO_MIN_KB:.0f} KB minimum.")
        duration = video_duration_seconds(path)
        if duration is not None:
            if duration > config.VIDEO_MAX_SECONDS:
                warns.append(
                    f"Video is {duration / 60:.1f} min; LinkedIn's limit is "
                    f"{config.VIDEO_MAX_SECONDS / 60:.0f} min."
                )
            elif duration < config.VIDEO_MIN_SECONDS:
                warns.append(f"Video is under LinkedIn's {config.VIDEO_MIN_SECONDS} second minimum.")
    else:
        if size > config.IMAGE_MAX_MB * 1024 * 1024:
            warns.append(f"Image is {_mb(size):.1f} MB; LinkedIn's limit is {config.IMAGE_MAX_MB:.0f} MB.")
    return warns


def post_warnings(kinds: list[str]) -> list[str]:
    """Warn about combinations a single LinkedIn post cannot hold, given each item's kind."""
    videos = kinds.count("video")
    images = kinds.count("image")
    warns: list[str] = []
    if videos and images:
        warns.append("A LinkedIn post cannot mix a video with photos. Keep one or the other.")
    if videos > 1:
        warns.append("A LinkedIn post can include only one video.")
    return warns
