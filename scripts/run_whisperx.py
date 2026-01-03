import argparse
import os
from pathlib import Path

import torch
import whisperx


def write_srt(segments, path: Path) -> None:
    lines = []
    for i, seg in enumerate(segments, 1):
        start = seg["start"]
        end = seg["end"]
        text = seg.get("text", "").strip()
        lines.append(str(i))
        lines.append(f"{format_ts(start)} --> {format_ts(end)}")
        lines.append(text)
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n")


def format_ts(t: float) -> str:
    total_ms = int(round(t * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _int_from_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"Warning: {name}='{raw}' is not an int; falling back to {default}")
        return default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run WhisperX on a single audio/video file.")
    parser.add_argument("input", type=Path, help="Audio or video file to transcribe")

    default_model = os.getenv("ASR_MODEL", "large-v3-turbo")
    parser.add_argument(
        "--model",
        default=default_model,
        help=f"Whisper model size/name (default: {default_model} from ASR_MODEL)",
    )

    default_batch = _int_from_env("ASR_BATCH_SIZE", 8)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=default_batch,
        help=f"Batch size for WhisperX transcribe (default: {default_batch} from ASR_BATCH_SIZE)",
    )
    parser.add_argument(
        "--compute-type",
        default=None,
        help="Override compute type (e.g., float16, int8, int8_float16). Default chooses based on device.",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Force device (cuda, cpu, mps). Default: auto-detect cuda else cpu.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output SRT path. Default: <input>.whisperx.srt",
    )
    return parser.parse_args()


def pick_device_and_compute_type(args: argparse.Namespace) -> tuple[str, str]:
    if args.device:
        device = args.device
    else:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    if args.compute_type:
        compute_type = args.compute_type
    else:
        compute_type = "float16" if device == "cuda" else "int8"

    if device == "mps":
        print("mps is not fully supported by whisperx; forcing cpu int8")
        device = "cpu"
        compute_type = "int8"
    return device, compute_type


def main() -> None:
    args = parse_args()
    device, compute_type = pick_device_and_compute_type(args)

    print(f"Device: {device}, compute_type: {compute_type}")
    model = whisperx.load_model(args.model, device, compute_type=compute_type)
    audio = whisperx.load_audio(str(args.input))
    result = model.transcribe(audio, batch_size=args.batch_size)

    align_model, metadata = whisperx.load_align_model(
        language_code=result["language"], device=device
    )
    aligned = whisperx.align(
        result["segments"], align_model, metadata, audio, device, return_char_alignments=False
    )

    out_path = args.output or args.input.with_suffix(".whisperx.srt")
    write_srt(aligned["segments"], out_path)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
