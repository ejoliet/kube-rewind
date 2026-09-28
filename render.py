#!/usr/bin/env python3
"""kube-rewind Gate-0 renderer: inline pulse.jsonl into template.html -> replay.html."""
import argparse
import json
import pathlib
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("-o", "--out", default="replay.html")
    args = ap.parse_args()

    good: list[str] = []
    skipped = 0
    has_meta = False
    for line in pathlib.Path(args.jsonl).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if obj.get("type") == "meta":
            has_meta = True
        good.append(json.dumps(obj, separators=(",", ":")))
    if not has_meta:
        print("abort: no meta line in input", file=sys.stderr)
        return 1

    # AIDEV-NOTE: escape </ so embedded data can never close the <script> tag
    payload = (",\n".join(good)).replace("</", "<\\/")
    template = (pathlib.Path(__file__).parent / "template.html").read_text()
    html = template.replace("/*__DATA__*/", payload)
    pathlib.Path(args.out).write_text(html)
    size = pathlib.Path(args.out).stat().st_size
    print(f"wrote {args.out}: {size} bytes ({size / 1024 / 1024:.2f} MB), "
          f"{len(good)} lines embedded, {skipped} malformed skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
