#!/usr/bin/env bash
# Gate-0 acceptance run: records, deletes metrics-server mid-run (GO-3),
# renders, and checks GO-1/GO-3/GO-4 automatically. GO-2 is a human test.
# Usage: ./gate_check.sh            (full 10 min run)
#        FOR=2m ./gate_check.sh    (quick smoke)
set -u

KCTX=${KCTX:-kind-spike}
FOR=${FOR:-10m}
INTERVAL=${INTERVAL:-5}

case "$FOR" in
  *h) SECS=$(( ${FOR%h} * 3600 ));;
  *m) SECS=$(( ${FOR%m} * 60 ));;
  *s) SECS=${FOR%s};;
  *)  SECS=$FOR;;
esac
EXPECTED=$(( SECS / INTERVAL ))
FAIL=0

kubectl --context "$KCTX" get pods >/dev/null || { echo "cluster not reachable ($KCTX) — run 'make cluster' first"; exit 1; }

rm -f pulse.jsonl record.log replay.html
echo "[gate] recording $FOR @ ${INTERVAL}s (expect ~$EXPECTED samples)"
python3 record.py --for "$FOR" --interval "$INTERVAL" --out pulse.jsonl --context "$KCTX" 2>record.log &
REC=$!

sleep $(( SECS / 2 ))
echo "[gate] deleting metrics-server mid-recording (GO-3 test)"
kubectl --context "$KCTX" -n kube-system delete deployment metrics-server --ignore-not-found >/dev/null

wait "$REC"; RC=$?

python3 render.py pulse.jsonl -o replay.html || FAIL=1

SIZE=$(stat -f%z replay.html 2>/dev/null || stat -c%s replay.html)
SAMPLES=$(grep -c '"type":"sample"' pulse.jsonl)
DRIFT=$(grep -c 'drift' record.log || true)

echo
echo "=== Gate-0 results ==="

# GO-1: replay.html <= 2 MB; NO-GO if > 5 MB
if [ "$SIZE" -le 2097152 ]; then echo "GO-1 PASS  replay.html = $SIZE bytes (<= 2 MB)"
elif [ "$SIZE" -gt 5242880 ]; then echo "GO-1 NO-GO replay.html = $SIZE bytes (> 5 MB hard limit)"; FAIL=1
else echo "GO-1 FAIL  replay.html = $SIZE bytes (> 2 MB)"; FAIL=1; fi

# GO-3: recorder survived metrics-server deletion; late samples carry null metrics
if [ "$RC" -ne 0 ]; then echo "GO-3 FAIL  recorder exit code $RC after metrics-server delete"; FAIL=1
elif tail -n 3 pulse.jsonl | grep '"type":"sample"' | tail -1 | grep -q '"cm":null'; then
  echo "GO-3 PASS  recorder ran to completion, metrics null after delete"
else echo "GO-3 WARN  recorder exited 0 but last sample still has metrics (delete too late in run?)"; fi

# GO-4: no silent skips — a tick that overran by Xs consumes ceil(X/interval) sample
# slots but yields one sample; every missing sample must be covered by a logged overrun.
MISSING=$(( EXPECTED - SAMPLES ))
ALLOW=$(grep 'drift' record.log | sed -E 's/.*took ([0-9.]+)s.*/\1/' \
  | awk -v I="$INTERVAL" '{s += int($1/I + 0.999) - 1} END {print s + 0}')
if [ "$SAMPLES" -ge "$EXPECTED" ]; then echo "GO-4 PASS  $SAMPLES/$EXPECTED samples, $DRIFT drift warnings"
elif [ "$MISSING" -le "$ALLOW" ]; then echo "GO-4 PASS  $SAMPLES/$EXPECTED samples, $MISSING missing all covered by $DRIFT logged overruns"
else echo "GO-4 FAIL  $SAMPLES/$EXPECTED samples, logged overruns cover only $ALLOW of $MISSING missing — silent skip"; FAIL=1; fi

echo "GO-2 MANUAL open replay.html, hand it to someone uninvolved: can they locate the crashloop onset in 10 s with the scrubber?"
echo
[ "$FAIL" -eq 0 ] && echo "verdict: automated checks GO (pending GO-2 human test)" || echo "verdict: NO-GO / FAIL — see above"
exit "$FAIL"
