#!/usr/bin/env python3
"""
Seed a Hugging Face cache directory with common WhisperX models and optionally upload a tarball to S3.
Also prefetch torch-hub assets (Silero VAD zip + wav2vec2 fr alignment) to avoid cold-start downloads.
Requires: huggingface_hub, torch, awscli (for S3 upload), and valid HF token if models are gated.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from huggingface_hub import snapshot_download
import torch
import whisperx

MODELS = [
    # "openai/whisper-large-v3",
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


def download_torch_asset(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"Already have {dest}, skipping")
        return
    print(f"Downloading {url} -> {dest}")
    torch.hub.download_url_to_file(url, str(dest), progress=True)


def upload_tar_to_s3(cache_dir: Path, bucket: str, prefix: str) -> None:
    target = f"s3://{bucket}/hf-cache.tar.gz" if not prefix else f"s3://{bucket}/{prefix.rstrip('/')}/hf-cache.tar.gz"
    tar_path = cache_dir.parent / "hf-cache.tar.gz"
    print(f"Creating tarball {tar_path} from {cache_dir} ...")
    tar_bin = "gtar" if shutil.which("gtar") else "tar"
    tar_cmd = [tar_bin]
    if tar_bin == "gtar":
        tar_cmd += ["--checkpoint=10000", "--checkpoint-action=dot"]
    tar_cmd += ["-czf", str(tar_path), "-C", str(cache_dir), "."]
    subprocess.run(tar_cmd, check=True)
    print(f"Uploading tarball to {target} ...")
    subprocess.run(["aws", "s3", "cp", str(tar_path), target], check=True)


def main() -> int:
    args = parse_args()
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Ensure WhisperX/HF honor the provided cache dir.
    os.environ["HF_HOME"] = str(cache_dir)
    os.environ["TRANSFORMERS_CACHE"] = str(cache_dir)
    torch_home = cache_dir / "torch"
    os.environ["TORCH_HOME"] = str(torch_home)
    os.environ["XDG_CACHE_HOME"] = str(cache_dir / "xdg")

    for model in MODELS:
        print(f"Downloading {model} ...")
        snapshot_download(repo_id=model, cache_dir=str(cache_dir), local_dir_use_symlinks=False)

    for lang in ALIGN_LANGS:
        print(f"Prefetching align model for {lang} ...")
        whisperx.load_align_model(language_code=lang, device="cpu")

    # Torch hub assets (Silero VAD + wav2vec2 fr checkpoint used by aligner)
    download_torch_asset(
        "https://github.com/snakers4/silero-vad/zipball/master",
        torch_home / "hub" / "master.zip",
    )
    download_torch_asset(
        "https://download.pytorch.org/torchaudio/models/wav2vec2_voxpopuli_base_10k_asr_fr.pt",
        torch_home / "hub" / "checkpoints" / "wav2vec2_voxpopuli_base_10k_asr_fr.pt",
    )

    if args.bucket:
        upload_tar_to_s3(cache_dir, args.bucket, args.prefix)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
