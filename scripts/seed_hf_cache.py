#!/usr/bin/env python3
"""
Seed a Hugging Face cache directory with common WhisperX models and optionally sync to S3.
Requires: huggingface_hub, awscli (for S3 sync), and valid HF token if models are gated.
"""
import argparse
import subprocess
import sys
from pathlib import Path

from huggingface_hub import snapshot_download
import whisperx

MODELS = [
    "openai/whisper-large-v3",
    "openai/whisper-large-v3-turbo",
]

# Prefetch alignment models (small .pt) for common languages to avoid first-run downloads.
ALIGN_LANGS = [
    "en",
    "de",
    "es",
    "fr",
    "ru",
    "ja",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed HF cache and optionally sync to S3")
    parser.add_argument("--cache-dir", default="./hf-cache", help="Target HF cache directory")
    parser.add_argument("--bucket", default=None, help="Optional S3 bucket name to sync to")
    parser.add_argument("--prefix", default="", help="Optional S3 prefix within the bucket")
    return parser.parse_args()


def sync_to_s3(cache_dir: Path, bucket: str, prefix: str) -> None:
    target = f"s3://{bucket}/" if not prefix else f"s3://{bucket}/{prefix.rstrip('/')}/"
    print(f"Syncing {cache_dir} to {target} ...")
    subprocess.run(["aws", "s3", "sync", str(cache_dir), target], check=True)


def main() -> int:
    args = parse_args()
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    for model in MODELS:
        print(f"Downloading {model} ...")
        snapshot_download(repo_id=model, cache_dir=str(cache_dir), local_dir_use_symlinks=False)

    for lang in ALIGN_LANGS:
        print(f"Prefetching align model for {lang} ...")
        whisperx.load_align_model(language_code=lang, device="cpu", cache_dir=str(cache_dir))

    if args.bucket:
        sync_to_s3(cache_dir, args.bucket, args.prefix)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
