#!/usr/bin/env python3
"""kube-rewind Gate-0 recorder: poll kubectl every N seconds, append JSONL."""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def parse_duration(s: str) -> int:
    units = {"s": 1, "m": 60, "h": 3600}
    if s and s[-1] in units:
        return int(s[:-1]) * units[s[-1]]
    return int(s)


def kubectl(args: list[str], context: str | None) -> str | None:
    cmd = ["kubectl"] + (["--context", context] if context else []) + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"kubectl {args[0]}: {e}")
        return None
    if r.returncode != 0:
        log(f"kubectl {' '.join(args[:3])}: {r.stderr.strip()[:200]}")
        return None
    return r.stdout


def parse_cpu(q: str) -> int:
    """k8s quantity -> millicores."""
    if q.endswith("n"):
        return int(q[:-1]) // 1_000_000
    if q.endswith("u"):
        return int(q[:-1]) // 1_000
    if q.endswith("m"):
        return int(q[:-1])
    return int(float(q) * 1000)


def parse_mem(q: str) -> int:
    """k8s quantity -> MiB."""
    for suffix, factor in (("Ki", 1 / 1024), ("Mi", 1), ("Gi", 1024), ("Ti", 1024 * 1024)):
        if q.endswith(suffix):
            return int(int(q[: -len(suffix)]) * factor)
    return int(int(q) / (1024 * 1024))


def get_metrics(context: str | None) -> dict[tuple[str, str], tuple[int, int]] | None:
    """(ns, name) -> (cpu millicores, mem MiB). None when metrics-server absent (GO-3)."""
    raw = kubectl(["get", "--raw", "/apis/metrics.k8s.io/v1beta1/pods"], context)
    if raw is None:
        return None
    out: dict[tuple[str, str], tuple[int, int]] = {}
    try:
        for item in json.loads(raw).get("items", []):
            cm, mm = 0, 0
            for c in item.get("containers", []):
                cm += parse_cpu(c["usage"].get("cpu", "0"))
                mm += parse_mem(c["usage"].get("memory", "0"))
            out[(item["metadata"]["namespace"], item["metadata"]["name"])] = (cm, mm)
    except (KeyError, ValueError) as e:
        log(f"metrics parse: {e}")
        return None
    return out


def build_sample(pods_raw: str, metrics: dict | None, t: int) -> dict | None:
    try:
        items = json.loads(pods_raw).get("items", [])
    except ValueError as e:
        log(f"pods parse: {e}")
        return None
    pods = []
    for item in items:
        ns = item["metadata"]["namespace"]
        name = item["metadata"]["name"]
        statuses = item.get("status", {}).get("containerStatuses", [])
        reason = None
        for cs in statuses:
            waiting = cs.get("state", {}).get("waiting")
            if waiting and waiting.get("reason"):
                reason = waiting["reason"]
                break
        cm, mm = (metrics.get((ns, name), (None, None)) if metrics is not None else (None, None))
        pods.append({
            "ns": ns, "n": name,
            "ph": item.get("status", {}).get("phase", "Unknown"),
            "rdy": bool(statuses) and all(cs.get("ready") for cs in statuses),
            "rst": sum(cs.get("restartCount", 0) for cs in statuses),
            "rs": reason, "cm": cm, "mm": mm,
        })
    return {"type": "sample", "t": t, "pods": pods}


def event_time(item: dict) -> int:
    for key in ("lastTimestamp", "eventTime", "firstTimestamp"):
        ts = item.get(key)
        if ts:
            try:
                return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
            except ValueError:
                pass
    return int(time.time())


def collect_events(context: str | None, seen: set[tuple[str, int]]) -> list[dict]:
    raw = kubectl(["get", "events", "-A", "-o", "json"], context)
    if raw is None:
        return []
    out = []
    try:
        items = json.loads(raw).get("items", [])
    except ValueError:
        return []
    for item in items:
        uid = item["metadata"]["uid"]
        count = item.get("count") or 1
        if (uid, count) in seen:
            continue
        seen.add((uid, count))
        obj = item.get("involvedObject", {})
        out.append({
            "type": "event", "t": event_time(item),
            "ns": item["metadata"]["namespace"], "kind": obj.get("kind", ""),
            "obj": obj.get("name", ""), "reason": item.get("reason", ""),
            "msg": (item.get("message") or "").strip()[:200], "count": count,
        })
    return sorted(out, key=lambda e: e["t"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--for", dest="duration", default="10m")
    ap.add_argument("--interval", type=int, default=5)
    ap.add_argument("--out", default="pulse.jsonl")
    ap.add_argument("--context", default=None)
    args = ap.parse_args()

    duration = parse_duration(args.duration)
    started = int(time.time())
    deadline = started + duration
    seen: set[tuple[str, int]] = set()
    pods_failures = 0

    with open(args.out, "a") as f:
        def emit(obj: dict) -> None:
            f.write(json.dumps(obj, separators=(",", ":")) + "\n")

        emit({"type": "meta", "v": 1, "started": started,
              "interval_s": args.interval, "context": args.context})
        f.flush()
        try:
            while time.time() < deadline:
                tick_start = time.time()
                t = int(tick_start)
                pods_raw = kubectl(["get", "pods", "-A", "-o", "json"], args.context)
                if pods_raw is None:
                    pods_failures += 1
                    if pods_failures >= 3:
                        log("pods failed 3 ticks in a row, aborting")
                        return 2
                else:
                    pods_failures = 0
                    sample = build_sample(pods_raw, get_metrics(args.context), t)
                    if sample:
                        emit(sample)
                for ev in collect_events(args.context, seen):
                    emit(ev)
                f.flush()
                elapsed = time.time() - tick_start
                if elapsed > args.interval:
                    log(f"AIDEV-NOTE drift: tick took {elapsed:.1f}s > {args.interval}s interval")
                else:
                    time.sleep(args.interval - elapsed)
        except KeyboardInterrupt:
            log("interrupted, flushing")
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
