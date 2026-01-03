import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

SENTENCE_END = {".", "?", "!"}


def format_timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def clean_text(parts: Sequence[str]) -> str:
    return " ".join(p.strip() for p in parts if p and p.strip()).replace("  ", " ").strip()


def flush_segment(out: List[str], idx: int, start: float, end: float, text: str) -> None:
    out.append(str(idx))
    out.append(f"{format_timestamp(start)} --> {format_timestamp(end)}")
    out.append(text)
    out.append("")


def chunk_text(chunks: List[Dict[str, Any]], max_duration: float = 7.0, max_chars: int = 90, max_cps: float = 18.0) -> str:
    output: List[str] = []
    buffer: List[str] = []
    seg_start: float | None = None
    seg_end: float = 0.0
    seg_idx = 1

    for chunk in chunks:
        ts = chunk.get("timestamp", [0, 0])
        if not isinstance(ts, list) or len(ts) != 2:
            continue
        start, end = float(ts[0]), float(ts[1])
        text = str(chunk.get("text", "")).strip()
        if not text:
            continue

        if seg_start is None:
            seg_start = start
        seg_end = end
        buffer.append(text)

        merged = clean_text(buffer)
        duration = max(seg_end - seg_start, 0.001)
        cps = len(merged) / duration if duration > 0 else math.inf
        ends_sentence = merged[-1] in SENTENCE_END if merged else False

        should_flush = False
        if ends_sentence and duration >= 1.0:
            should_flush = True
        if duration >= max_duration:
            should_flush = True
        if len(merged) >= max_chars:
            should_flush = True
        if cps > max_cps and len(buffer) > 1:
            should_flush = True

        if should_flush and merged:
            flush_segment(output, seg_idx, seg_start, seg_end, merged)
            seg_idx += 1
            buffer = []
            seg_start = None

    if buffer and seg_start is not None:
        merged = clean_text(buffer)
        flush_segment(output, seg_idx, seg_start, seg_end, merged)

    return "\n".join(output).rstrip() + "\n"


def main() -> None:
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output.txt")
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("output.smart.srt")

    data = json.loads(input_path.read_text())
    chunks = data.get("chunks") or []
    srt_text = chunk_text(chunks)
    output_path.write_text(srt_text)


if __name__ == "__main__":
    main()
