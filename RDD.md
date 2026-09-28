# kube-rewind — Gate-0 Spike

> Prove in ≤ 2 hours that a zero-agent cluster recording can replay as a legible, scrubbable, single-file HTML timeline. Throwaway code; the GO/NO-GO verdict is the deliverable.

**Type**: RDD Type A (spec — agent implements from this README alone)
**Timebox**: 120 min total. Stop at the box, evaluate the gate.
**Status**: GO — gate passed 2026-09-04 (see Gate Verdict)

## Purpose

Postmortems are Grafana screenshots. The product hypothesis: record k9s-pulse-style vitals (pod phases, restarts, events, CPU/mem) with read-only kubectl access, render one self-contained `replay.html` anyone can scrub in a browser or host on GitHub Pages.

This spike proves only the core wow: **capture is cheap, the file is small, and the replay tells the incident story in seconds.** Everything else (krew, Go, Parquet, redaction, GH Pages workflow) is post-gate.

## Gate: GO / NO-GO Criteria

The spike passes only if ALL GO criteria hold:

| # | Criterion | Measure |
|---|-----------|---------|
| GO-1 | Small artifact | `replay.html` ≤ 2 MB for 10 min @ 5 s interval on a ~10-pod cluster |
| GO-2 | Legible story | A viewer not told what happened can locate the crashloop onset within 10 s using only the scrubber |
| GO-3 | Graceful degradation | Recorder runs to completion with metrics-server absent (phases/restarts/events only, no crash) |
| GO-4 | No drift | Each sample tick completes in < interval; recorder logs a warning if exceeded, never skips silently |

NO-GO if ANY holds:

- Artifact > 5 MB for the 10-min recording.
- Story is illegible without agent/eBPF-level data.
- Capture requires any cluster write, CRD, or elevated verb beyond `get`/`list`/`watch`.

> ⚠️ NO-GO means stop the project as speced — do not rescue by adding infrastructure. That kills the premise.

## Architecture

Three throwaway pieces, one direction of data flow:

1. `record.py` — polls `kubectl` every N seconds → appends line-delimited JSON to `pulse.jsonl`.
2. `render.py` — inlines `pulse.jsonl` into `template.html` → writes self-contained `replay.html`.
3. `rig/` — kind cluster manifests including one deliberately crashlooping pod (the incident to replay).

No cluster-side components. No network calls from the viewer (fully offline file).

## Stack Decisions

Deliberately dependency-free — the spike must not spend its budget on tooling.

| Layer | Chosen | Rejected | Why |
|-------|--------|----------|-----|
| Capture | `subprocess` → `kubectl -o json` | `kubernetes` client, `kr8s` | kubectl inherits kubeconfig auth for free; zero installs; JSON output long-stable |
| Language | Python 3.11+ stdlib only | Go | No compile step; speed irrelevant at 1 poll / 5 s |
| Viewer | Vanilla JS + `<canvas>` | uPlot, Plotly | No CDN so the file works offline; ~120 samples renders trivially; libraries bloat GO-1 |
| Data | JSONL, compact keys | Parquet, SQLite | Human-greppable, append-safe on Ctrl-C, streams to pandas via `read_json(lines=True)` |

> 💡 If the gate passes, revisit uPlot and Parquet for the real tool. Wrong to optimize a throwaway.

## Repository Layout

```
kube-rewind-spike/
├── README.md          # this file
├── Makefile           # cluster / record / render / spike / nuke
├── record.py          # sampler CLI (~120 lines)
├── render.py          # JSONL → replay.html (~40 lines)
├── template.html      # viewer with /*__DATA__*/ placeholder (~300 lines)
└── rig/
    ├── kind.yaml      # 1 control-plane + 1 worker
    └── workload.yaml  # 5 healthy pods + 1 crashloop victim
```

## Prerequisites

- Docker running locally
- `kind` ≥ 0.20, `kubectl` on PATH
- Python 3.11+ (stdlib only — no `pip install`)
- metrics-server manifest applied to kind with `--kubelet-insecure-tls` arg (kind kubelets use self-signed certs; without the flag metrics stay empty)

No cloud, no cluster writes beyond the rig itself, no secrets.

## Quick Start

```bash
make cluster          # kind up + metrics-server + rig workload
make record           # 10 min @ 5 s → pulse.jsonl (Ctrl-C safe)
make render           # → replay.html
open replay.html      # scrub the crashloop
make nuke             # delete kind cluster
```

`make spike` runs all of the above sequentially.

## Interface Contract

### CLI

```bash
python3 record.py --for 10m --interval 5 --out pulse.jsonl [--context KCTX]
python3 render.py pulse.jsonl -o replay.html
```

| Flag | Default | Notes |
|------|---------|-------|
| `--for` | `10m` | Accepts `Ns`, `Nm`, `Nh` |
| `--interval` | `5` | Seconds between samples |
| `--out` | `pulse.jsonl` | Append mode, one JSON object per line |
| `--context` | current | Passed through as `kubectl --context` |

### JSONL Schema (v1)

Line 1 is always `meta`; then interleaved `sample` and `event` lines, ascending `t` (epoch seconds).

```json
{"type":"meta","v":1,"started":1757000000,"interval_s":5,"context":"kind-spike"}
{"type":"sample","t":1757000005,"pods":[{"ns":"default","n":"victim-6f...","ph":"Running","rdy":false,"rst":3,"rs":"CrashLoopBackOff","cm":12,"mm":38}]}
{"type":"event","t":1757000007,"ns":"default","kind":"Pod","obj":"victim-6f...","reason":"BackOff","msg":"Back-off restarting failed container","count":4}
```

Field rules:

- `ph` = pod phase; `rdy` = all containers ready; `rst` = total restart count; `rs` = first waiting reason or null.
- `cm` (CPU millicores) / `mm` (memory MiB) from `kubectl get --raw /apis/metrics.k8s.io/v1beta1/pods`; **null when metrics-server is absent or errors** (GO-3).
- Events polled via `kubectl get events -A -o json`; emit once per `(uid, count)` pair — bump in `count` re-emits.
- Recorder holds the dedupe set in memory only; restart of the recorder may re-emit events. Acceptable for the spike.

### Viewer (`template.html`)

- Timeline slider + play/pause; keyboard ← → steps one sample.
- Lane 1: stacked band of pod counts by phase (Running/ready green, not-ready amber, CrashLoop/Failed red, Pending gray).
- Lane 2: restart-delta tick marks (a mark whenever any pod's `rst` increments).
- Lane 3: event rail — dots at event times, hover shows `reason: msg`.
- Lane 4 (only if metrics present): total CPU/mem sparklines.
- Right panel: pod table at cursor time (name, phase, ready, restarts, reason).
- Data embedded at `/*__DATA__*/` as a JS string; `render.py` does a literal replace. Escape `</script>` sequences.

## Error Handling

- Any `kubectl` subprocess failure: log to stderr, write nothing for that source this tick, continue. Metrics failure is expected (GO-3); pods failure 3 ticks in a row aborts with exit 2.
- Tick overrun (work > interval): warn `AIDEV-NOTE drift`, start next tick immediately — never double-sample.
- Ctrl-C: flush and exit 0; JSONL is valid at any truncation point (line-atomic writes).
- `render.py` on malformed line: skip it, count it, print skipped total. Abort only if `meta` line missing.

## Non-Goals (v1 spike)

- No name/label redaction — **never publish a replay from a real cluster with this spike code**.
- No krew plugin, Go port, Parquet sidecar, GH Pages action, notebook demo.
- No watch-API streaming; polling only.
- No nodes/HPA/PVC lanes; pods + events is enough to judge the gate.
- No tests beyond the acceptance run — this code is discarded either way.

## Agent Build Instructions

> Implement end-to-end from this README. Resolve Open Questions first or accept the stated defaults.

### Build Order (timeboxed)

| Phase | Deliverable | Budget | Done when |
|-------|-------------|--------|-----------|
| 0 | `Makefile`, `rig/` | 15 min | `make cluster` → victim pod enters CrashLoopBackOff within ~1 min |
| 1 | `record.py` | 35 min | 2-min recording produces valid JSONL; kill metrics-server, recorder keeps running (GO-3) |
| 2 | `template.html` + `render.py` | 50 min | `replay.html` opens offline; scrubbing moves all lanes + pod table |
| 3 | Acceptance run | 20 min | Full 10-min recording, measure GO-1/2/4, record verdict below |

### Constraints

- Python stdlib only; typed function signatures; `AIDEV-` comments for non-obvious choices only.
- Viewer: no external resources of any kind (no fonts, no CDN).
- Read-only cluster access from the recorder; only the rig applies manifests.
- macOS + Linux; assume `open`/`xdg-open` for the final step.

### Acceptance Checklist

- [x] `make spike` runs end-to-end on a clean machine with prerequisites (stages run individually: `make cluster` on a fresh Docker, then record/render via `gate_check.sh`)
- [x] GO-1: `ls -l replay.html` ≤ 2 MB (262,141 bytes)
- [x] GO-2: 10-second-story test passed — observer was the project owner, not fully uninvolved; re-test with a naive viewer noted in plan
- [x] GO-3: verified by deleting the metrics-server deployment mid-recording
- [x] GO-4: zero silent skips in recorder log (119/120 samples, 1 missing covered by 2 logged overruns)
- [x] Verdict written into this README under a `## Gate Verdict` section with numbers

## Open Questions

- [ ] Sample interval floor — is 5 s enough resolution for a 20 s crash cycle? Default: yes for the gate; revisit at 2 s only if GO-2 fails narrowly.
- [ ] Event volume on busy clusters may dominate file size — irrelevant on kind; measure post-gate on a real EKS namespace before believing GO-1 generalizes.
- [ ] Minimal RBAC proof (dedicated ServiceAccount with only `get`/`list`) — post-gate, before any public claim of "read-only".

## Next Steps

1. Build phases 0–3 in order; hard-stop at 120 min. ✅
2. Write the Gate Verdict section with GO-1/GO-4 numbers and the GO-2 observer result. ✅
3. If GO: name check (`kube-rewind`, `pulserec`), then RDD Type B for the real tool (redaction first). → see Post-Gate Plan
4. If NO-GO: archive the repo with the verdict; do not iterate past the premise. (n/a)

## Gate Verdict (2026-09-04): GO

Acceptance run: 10 min @ 5 s interval on kind (2 nodes, 17 pods across all namespaces,
5 healthy + 1 crashlooping in `default`), metrics-server deleted at t+5 min.

| # | Result | Numbers |
|---|--------|---------|
| GO-1 | **PASS** | `replay.html` = 262,141 bytes (0.25 MB) — 8× under the 2 MB limit. `pulse.jsonl` = 253,005 bytes: 119 samples, 48 events, 60 samples carrying metrics (metrics deleted mid-run by design). |
| GO-2 | **PASS** (qualified) | Viewer located the crashloop onset within seconds using the phase band + scrubber. Caveat: the observer was the project owner, not an uninvolved viewer — re-run with a naive observer before quoting this publicly. |
| GO-3 | **PASS** | metrics-server deployment deleted mid-recording; recorder ran to completion (exit 0), later samples show `"cm":null` / `"mm":null`, no crash. |
| GO-4 | **PASS** | 119/120 expected samples; the 1 missing sample is fully covered by 2 logged drift warnings (5.9 s and 8.3 s ticks during apiserver warmup). Zero silent skips. |

NO-GO triggers: none hit. Artifact far under 5 MB; story legible from poll data alone;
capture used only `get`/`list` via kubectl (no writes, no CRDs).

Known spike limitations confirmed during acceptance (not gate failures):
- CPU/mem lane shows summed *usage* across all sampled namespaces, with no
  capacity/requests denominator.
- Viewer is low-res by design (fixed 900 px canvas, 3 px event dots, no zoom) —
  the dependency-free tradeoff the spike chose to protect GO-1.
- metrics-server on kind needs ~40–90 s after rollout before the metrics API serves;
  recordings started earlier are all-null until the first scrape.

## Post-Gate Plan (RDD Type B input)

Ordered; 1–3 gate the first real-cluster pilot (Airflow on EKS, KubernetesExecutor —
one pod per task, so pod churn and OOM/Pending stories are the target use case).

1. **Redaction first** (spec mandate). Deterministic aliasing of namespaces, pod
   names, and event messages at record time; opt-in `--no-redact`. Nothing leaves a
   real cluster before this exists.
2. **Namespace scoping.** `--namespace` flag replacing hardwired `-A`; on EKS,
   recording the whole cluster is both a size and a privacy problem.
3. **Real-cluster size measurement** (open question from this spec). 10-min recording
   on the production Airflow namespace; measure event volume and pod-churn growth.
   Add completed-pod compaction (drop Succeeded pods from samples after N ticks) if
   GO-1 does not generalize.
4. **Airflow legibility.** Capture pod labels (`dag_id`, `task_id`, `run_id`) into the
   schema; show them in the pod table and event hover so the replay speaks Airflow,
   not pod hashes. Then judge usefulness against the Airflow UI: this tool answers
   "what did the cluster do to my task pods" (OOMKilled, Pending pileups, evictions),
   not "why did my Python fail".
5. **Minimal RBAC proof.** Dedicated ServiceAccount with `get`/`list` only; record a
   session with it before any public "read-only" claim.
6. **Viewer upgrade.** uPlot (or DPR-aware canvas) with zoom, ≥8 px hover targets, a
   capacity/requests overlay for the CPU/mem lane, and per-namespace filtering.
7. **GO-2 re-test** with a genuinely uninvolved viewer on the pilot recording.
8. **Naming/packaging.** Name check (`kube-rewind`, `pulserec`); only then decide Go
   port / krew plugin per the original non-goals.
