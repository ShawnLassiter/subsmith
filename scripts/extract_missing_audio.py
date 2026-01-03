#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

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
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {res.stderr.strip()}")
    return json.loads(res.stdout or "{}" ).get("streams", [])


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


def extract_audio(src: Path, stream_index: int, lang: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem
    out_path = out_dir / f"{stem}.a{stream_index}.{lang or 'und'}.mka"
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(src),
        "-map",
        f"0:{stream_index}",
        "-c",
        "copy",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {src} stream {stream_index}: {res.stderr.strip()}")
    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="Extract audio tracks without matching subtitles",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./scripts/extract_missing_audio.py /mnt/media --out copied_audio\n"
            "  ./scripts/extract_missing_audio.py . --out /tmp/out"
        ),
    )
    parser.add_argument("root", nargs="?", default=".", help="Directory to scan for media files")
    parser.add_argument("--out", default="extracted_audio", help="Output directory for copied audio (.mka)")
    args = parser.parse_args()

    if len(sys.argv) == 1:
        parser.print_help()
        return

    root = Path(args.root).resolve()
    out_dir = Path(args.out)

    for media in iter_media(root):
        streams = ffprobe_streams(media)
        audio_streams = []
        sub_langs = set()
        for s in streams:
            ctype = s.get("codec_type")
            lang = lang_from_tags(s.get("tags", {}))
            if ctype == "audio":
                audio_streams.append((s.get("index"), lang))
            elif ctype == "subtitle":
                sub_langs.add(lang)

        for idx, lang in audio_streams:
            if lang not in sub_langs:
                try:
                    out_path = extract_audio(media, idx, lang, out_dir)
                    rel_media = media.relative_to(root)
                    print(f"Extracted {rel_media} stream {idx} ({lang}) -> {out_path}")
                except Exception as e:  # noqa: BLE001
                    print(f"ERROR extracting {media} stream {idx}: {e}")


if __name__ == "__main__":
    main()
