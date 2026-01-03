import json
import sys
from pathlib import Path
from typing import List, Dict, Any


def format_timestamp(seconds: float) -> str:
    """Convert seconds (float) to SRT timestamp (HH:MM:SS,mmm)."""
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def chunks_to_srt(chunks: List[Dict[str, Any]]) -> str:
    lines = []
    for idx, chunk in enumerate(chunks, start=1):
        ts = chunk.get("timestamp", [0, 0])
        if len(ts) != 2:
            continue  # skip malformed entries
        start, end = ts
        text = (chunk.get("text", "") or "").strip()
        lines.append(str(idx))
        lines.append(f"{format_timestamp(float(start))} --> {format_timestamp(float(end))}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output.txt")
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("output.srt")

    data = json.loads(input_path.read_text())
    chunks = data.get("chunks") or []
    srt_text = chunks_to_srt(chunks)
    output_path.write_text(srt_text)


if __name__ == "__main__":
    main()
