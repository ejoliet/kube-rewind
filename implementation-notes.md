# implementation notes — kube-rewind Gate-0 spike

- 2026-09-04 pause image (registry.k8s.io/pause:3.9) for 5 healthy pods: always Running/ready, near-zero resources; busybox sleep loops flap readiness.
- 2026-09-04 victim = Deployment (not bare Pod) so kind reschedules it and restarts accumulate cleanly; 20 s sleep + exit 1 gives ~20 s crash cycle per RDD open question.
- 2026-09-04 render.py embeds compact re-serialized JSON (not raw lines) so malformed lines are dropped and `</` escaped as `<\/` — prevents data closing the script tag.
- 2026-09-04 GO-4 check in gate_check.sh: missing samples <= drift warnings ⇒ pass; drifted ticks start next tick immediately so a long tick consumes wall clock without producing extra samples — not a silent skip if logged.
- 2026-09-04 gate_check.sh deletes metrics-server at run midpoint to prove GO-3 inside the same recording; verifies last sample has "cm":null.
- 2026-09-04 DEVIATION: added canvas click-to-seek beyond spec (slider + arrows required); ~10 lines, helps GO-2 ten-second test.
- 2026-09-04 GO-4 first version counted 1 missing sample per drift warning — wrong: a tick overrunning by Xs consumes ceil(X/I) slots for one sample. Fixed to sum logged overrun durations; live run then 24/24.
- 2026-09-04 cold kind apiserver: first ticks take 11-24 s (3 kubectl subprocesses); warms to <5 s. Drift warnings expected at recording start, harmless per spec.
- 2026-09-04 metrics-server on kind needs ~40-90 s after rollout before APIService serves; gate script deletes it mid-run, so a run started too early records all-null metrics. Verified live: metrics captured (pause pods legitimately 0m/0Mi), null after delete (GO-3).
- 2026-09-04 acceptance run (10 min @ 5 s): replay.html 262,141 B; 119/120 samples, 2 drift warnings cover the 1 missing; GO-3 pass at t+5 min delete. Verdict GO written into RDD.md with post-gate plan.
- 2026-09-04 DEVIATION: GO-2 observer was the project owner, not uninvolved — recorded as qualified pass, naive re-test queued in plan step 7.
- 2026-09-04 viewer smoke-tested headless: executed full script under node with stub DOM against 120-sample synthetic recording; render output 0.08 MB (GO-1 headroom ~25x).
