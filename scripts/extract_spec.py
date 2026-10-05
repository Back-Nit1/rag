#!/usr/bin/env python3
"""Split the canonical prompt into docs/spec/<name>.md by METIS:START/END markers.

Validates that every marker pair occurs exactly once. Never executes spec text.
Usage: python scripts/extract_spec.py [prompt.md] [--out docs/spec]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

START = re.compile(r"^<!--\s*METIS:START\s+([a-z0-9_-]+)\s*-->\s*$")
END = re.compile(r"^<!--\s*METIS:END\s+([a-z0-9_-]+)\s*-->\s*$")


def extract(text: str) -> dict[str, str]:
    starts: dict[str, int] = {}
    ends: dict[str, int] = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if m := START.match(line):
            name = m.group(1)
            if name in starts:
                raise ValueError(f"duplicate METIS:START {name} at line {i + 1}")
            starts[name] = i
        elif m := END.match(line):
            name = m.group(1)
            if name in ends:
                raise ValueError(f"duplicate METIS:END {name} at line {i + 1}")
            ends[name] = i

    if starts.keys() != ends.keys():
        missing = sorted(starts.keys() ^ ends.keys())
        raise ValueError(f"unpaired markers: {missing}")

    blocks: dict[str, str] = {}
    for name, s in starts.items():
        e = ends[name]
        if e <= s:
            raise ValueError(f"METIS:END {name} precedes its START")
        blocks[name] = "\n".join(lines[s + 1 : e]).strip() + "\n"
    return blocks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?", default="prompt (1).md")
    ap.add_argument("--out", default="docs/spec")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.is_file():
        print(f"spec source not found: {src}", file=sys.stderr)
        return 2

    blocks = extract(src.read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, body in sorted(blocks.items()):
        target = out / f"{name}.md"
        header = f"<!-- Extracted from {src.name}; block `{name}`. Do not edit here; edit the source spec. -->\n\n"
        target.write_text(header + body, encoding="utf-8")
        print(f"wrote {target} ({len(body.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
