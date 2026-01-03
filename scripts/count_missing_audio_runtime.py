#!/usr/bin/env python3
"""
Summarize runtime of audio streams that lack matching subtitles.
Uses ffprobe to find media files, compares audio stream languages against subtitle languages,
and sums durations for audio streams with no matching subtitle language.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".m4v", ".avi", ".ts", ".webm"}


def ffprobe(path: Path) -> Dict:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-show_entries",
        "stream=index,codec_type,codec_name,channels,duration:stream_tags=language,title",
        "-of",
        "json",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {res.stderr.strip()}")
    return json.loads(res.stdout or "{}")


def lang_from_tags(tags: Dict) -> str:
    if not tags:
        return "und"
    for key in ("language", "LANGUAGE", "lang", "Lang"):
        if key in tags and tags[key]:
            return str(tags[key]).lower()
    return "und"


def iter_media(root: Path):
    for p in sorted(root.glob("**/*")):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            yield p


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sum runtimes of audio tracks without matching subtitles")
    parser.add_argument("root", nargs="?", default=".", help="Directory to scan for media files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()

    total_sec = 0.0
    media_files = list(iter_media(root))
    total_files = len(media_files)

    for idx, media in enumerate(media_files, 1):
        print(f"[{idx}/{total_files}] {media.relative_to(root)}")
        info = ffprobe(media)
        streams: List[Dict] = info.get("streams", [])
        format_dur = float(info.get("format", {}).get("duration", 0.0) or 0.0)

        sub_langs = set()
        audio_streams = []
        for s in streams:
            ctype = s.get("codec_type")
            lang = lang_from_tags(s.get("tags", {}))
            if ctype == "audio":
                audio_streams.append((s, lang))
            elif ctype == "subtitle":
                sub_langs.add(lang)

        for s, lang in audio_streams:
            if lang in sub_langs:
                continue
            dur = s.get("duration")
            dur_sec = float(dur) if dur not in (None, "N/A", "") else format_dur
            total_sec += dur_sec
            rel_media = media.relative_to(root)
            print(f"{rel_media} stream {s.get('index')} ({lang}) -> {dur_sec/60:.1f} min")

    hours = total_sec / 3600.0
    print(f"Total missing-audio runtime: {hours:.2f} hours")
    return 0


if __name__ == "__main__":
    sys.exit(main())
