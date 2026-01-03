import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".m4v", ".avi", ".ts", ".webm"}


def ffprobe_streams(path: Path) -> List[Dict]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=index,codec_type,codec_name,channels:stream_tags=language,title",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {result.stderr.strip()}")
    data = json.loads(result.stdout or "{}")
    return data.get("streams", [])


def lang_from_tags(tags: Dict) -> str:
    if not tags:
        return "und"
    for key in ("language", "LANGUAGE", "lang", "Lang"):
        if key in tags and tags[key]:
            return str(tags[key]).lower()
    return "und"


def collect(path: Path) -> Tuple[Counter, Counter]:
    streams = ffprobe_streams(path)
    audio = Counter()
    subs = Counter()
    for s in streams:
        ctype = s.get("codec_type")
        lang = lang_from_tags(s.get("tags", {}))
        if ctype == "audio":
            audio[lang] += 1
        elif ctype == "subtitle":
            subs[lang] += 1
    return audio, subs


def iter_media_files(root: Path) -> Iterable[Path]:
    for p in sorted(root.glob("**/*")):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            yield p


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    rows: List[Dict] = []
    for path in iter_media_files(root):
        try:
            audio, subs = collect(path)
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {path}: {e}", file=sys.stderr)
            continue
        rows.append({
            "file": path,
            "audio": dict(audio),
            "subs": dict(subs),
            "has_sub_for_audio_lang": {lang: (lang in subs) for lang in audio},
        })

    for row in rows:
        file_rel = row["file"].relative_to(root)
        audio = row["audio"]
        subs = row["subs"]
        print(f"\n{file_rel}")
        print(f"  audio langs: " + (", ".join(f"{k} x{v}" for k, v in audio.items()) or "none"))
        print(f"  subs  langs: " + (", ".join(f"{k} x{v}" for k, v in subs.items()) or "none"))
        missing = [k for k in audio if k not in subs]
        if missing:
            print(f"  missing subs for: {', '.join(missing)}")
        else:
            print("  subs present for all audio langs")


if __name__ == "__main__":
    main()
