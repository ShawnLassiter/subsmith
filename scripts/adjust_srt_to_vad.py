import argparse
import math
import re
import subprocess
from pathlib import Path
from typing import List, Tuple

import webrtcvad

SRT_BLOCK_RE = re.compile(r"^(\d+)\s*$")
TIME_RE = re.compile(r"(\d\d):(\d\d):(\d\d),(\d\d\d)")


def parse_time(text: str) -> float:
    m = TIME_RE.search(text)
    if not m:
        raise ValueError(f"Bad timecode: {text}")
    h, mnt, s, ms = map(int, m.groups())
    return h * 3600 + mnt * 60 + s + ms / 1000.0


def fmt_time(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    mnt, rem = divmod(rem, 60_000)
    sec, ms = divmod(rem, 1000)
    return f"{h:02d}:{mnt:02d}:{sec:02d},{ms:03d}"


def parse_srt(path: Path) -> List[Tuple[float, float, List[str]]]:
    cues: List[Tuple[float, float, List[str]]] = []
    with path.open() as f:
        lines = [line.rstrip("\n") for line in f]

    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        if not SRT_BLOCK_RE.match(lines[i]):
            i += 1
            continue
        if i + 1 >= len(lines):
            break
        times = lines[i + 1]
        if "-->" not in times:
            i += 1
            continue
        start_text, end_text = [t.strip() for t in times.split("-->")]
        start = parse_time(start_text)
        end = parse_time(end_text)
        j = i + 2
        text_lines: List[str] = []
        while j < len(lines) and lines[j].strip():
            text_lines.append(lines[j])
            j += 1
        cues.append((start, end, text_lines))
        i = j + 1
    return cues


def write_srt(path: Path, cues: List[Tuple[float, float, List[str]]]) -> None:
    out_lines: List[str] = []
    for idx, (start, end, text_lines) in enumerate(cues, start=1):
        out_lines.append(str(idx))
        out_lines.append(f"{fmt_time(start)} --> {fmt_time(end)}")
        out_lines.extend(text_lines)
        out_lines.append("")
    path.write_text("\n".join(out_lines).rstrip() + "\n")


def load_pcm_from_audio(path: Path, sample_rate: int = 16000) -> bytes:
    """Decode audio to 16-bit mono PCM via ffmpeg."""
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-f",
        "s16le",
        "-",
    ]
    proc = subprocess.run(cmd, check=True, stdout=subprocess.PIPE)
    return proc.stdout


def detect_speech(mp3_path: Path, aggressiveness: int = 2, frame_ms: int = 30, min_speech_ms: int = 200) -> List[Tuple[float, float]]:
    sample_rate = 16000
    pcm = load_pcm_from_audio(mp3_path, sample_rate=sample_rate)
    frame_len = int(sample_rate * frame_ms / 1000)
    vad = webrtcvad.Vad(aggressiveness)

    segments: List[Tuple[int, int]] = []
    voiced = False
    seg_start = 0
    for i in range(0, len(pcm), frame_len * 2):  # 16-bit mono
        frame = pcm[i : i + frame_len * 2]
        if len(frame) < frame_len * 2:
            break
        ts_start = i // 2 / sample_rate
        is_speech = vad.is_speech(frame, sample_rate)
        if is_speech and not voiced:
            voiced = True
            seg_start = ts_start
        if not is_speech and voiced:
            voiced = False
            seg_end = ts_start
            segments.append((seg_start, seg_end))
    if voiced:
        segments.append((seg_start, len(pcm) // 2 / sample_rate))

    merged: List[Tuple[float, float]] = []
    for s, e in segments:
        if not merged:
            merged.append((s, e))
            continue
        prev_s, prev_e = merged[-1]
        if s - prev_e <= 0.15:  # merge close gaps
            merged[-1] = (prev_s, e)
        else:
            merged.append((s, e))

    filtered = [(s, e) for s, e in merged if (e - s) * 1000 >= min_speech_ms]
    return filtered


def snap_pair(start: float, end: float, speech: List[Tuple[float, float]], max_shift: float) -> Tuple[float, float]:
    if not speech or max_shift <= 0:
        return start, end
    best_seg = None
    for s, e in speech:
        if s - max_shift <= start <= e + max_shift:
            best_seg = (s, e)
            break
    if not best_seg:
        return start, end
    s, e = best_seg
    new_start = max(s, min(start, e))
    new_end = min(e, max(end, new_start + 0.2))
    return new_start, new_end


def apply_global_offset(cues: List[Tuple[float, float, List[str]]], speech: List[Tuple[float, float]], max_global: float) -> List[Tuple[float, float, List[str]]]:
    if not cues or not speech or max_global <= 0:
        return cues
    first_cue_start = cues[0][0]
    first_speech_start = speech[0][0]
    delta = first_speech_start - first_cue_start
    if abs(delta) > max_global:
        return cues
    shifted: List[Tuple[float, float, List[str]]] = []
    for s, e, t in cues:
        ns = max(0.0, s + delta)
        ne = max(ns + 0.2, e + delta)
        shifted.append((ns, ne, t))
    return shifted


def apply_forced_offset(cues: List[Tuple[float, float, List[str]]], target_start: float | None) -> List[Tuple[float, float, List[str]]]:
    if target_start is None or not cues:
        return cues
    first_cue_start = cues[0][0]
    delta = target_start - first_cue_start
    shifted: List[Tuple[float, float, List[str]]] = []
    for s, e, t in cues:
        ns = max(0.0, s + delta)
        ne = max(ns + 0.2, e + delta)
        shifted.append((ns, ne, t))
    return shifted


def adjust_cues(cues: List[Tuple[float, float, List[str]]], speech: List[Tuple[float, float]], max_shift: float = 1.5, max_global: float = 0.0, force_first_start: float | None = None) -> List[Tuple[float, float, List[str]]]:
    base = apply_forced_offset(cues, force_first_start)
    base = apply_global_offset(base, speech, max_global)
    adjusted: List[Tuple[float, float, List[str]]] = []
    for start, end, text in base:
        new_start, new_end = snap_pair(start, end, speech, max_shift)
        if new_end < new_start:
            mid = (new_start + new_end) / 2.0
            new_start, new_end = mid - 0.2, mid + 0.2
        adjusted.append((new_start, new_end, text))
    return adjusted


def normalize_cues(cues: List[Tuple[float, float, List[str]]]) -> List[Tuple[float, float, List[str]]]:
    # Sort by start time and enforce non-overlap with a small gap.
    cues_sorted = sorted(cues, key=lambda x: x[0])
    normalized: List[Tuple[float, float, List[str]]] = []
    prev_end = 0.0
    for start, end, text in cues_sorted:
        s = max(start, prev_end + 0.02)
        e = max(end, s + 0.20)
        normalized.append((s, e, text))
        prev_end = e
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(description="Snap SRT cue times to speech detected via WebRTC VAD")
    parser.add_argument("srt", type=Path, help="Input SRT (e.g., output.smart.srt)")
    parser.add_argument("audio", type=Path, help="Audio file (mp3/wav)")
    parser.add_argument("output", type=Path, nargs="?", default=Path("output.synced.srt"))
    parser.add_argument("--aggr", type=int, default=2, choices=range(0, 4), help="VAD aggressiveness 0-3")
    parser.add_argument("--max-shift", type=float, default=1.5, help="Max seconds to shift a cue when snapping")
    parser.add_argument("--max-global", type=float, default=5.0, help="If >0, apply uniform offset to align first cue to first speech when within this many seconds")
    parser.add_argument("--force-first-start", type=float, default=None, help="If set, force first cue to this start time (seconds) and shift all cues uniformly")
    parser.add_argument("--normalize", action="store_true", help="Sort cues and remove overlaps after adjustment")
    args = parser.parse_args()

    cues = parse_srt(args.srt)
    speech = detect_speech(args.audio, aggressiveness=args.aggr)
    adjusted = adjust_cues(cues, speech, max_shift=args.max_shift, max_global=args.max_global, force_first_start=args.force_first_start)
    if args.normalize:
        adjusted = normalize_cues(adjusted)
    write_srt(args.output, adjusted)


if __name__ == "__main__":
    main()
