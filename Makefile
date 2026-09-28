CLUSTER  := spike
KCTX     := kind-$(CLUSTER)
FOR      ?= 10m
INTERVAL ?= 5

.PHONY: cluster record render open spike gate nuke

cluster:
	kind create cluster --name $(CLUSTER) --config rig/kind.yaml
	kubectl --context $(KCTX) apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
	kubectl --context $(KCTX) -n kube-system patch deployment metrics-server --type=json \
	  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
	kubectl --context $(KCTX) apply -f rig/workload.yaml
	@echo "victim pod should hit CrashLoopBackOff within ~1 min: kubectl --context $(KCTX) get pods -w"

record:
	python3 record.py --for $(FOR) --interval $(INTERVAL) --out pulse.jsonl --context $(KCTX)

render:
	python3 render.py pulse.jsonl -o replay.html

open:
	open replay.html 2>/dev/null || xdg-open replay.html

spike: cluster record render open

gate:
	./gate_check.sh

nuke:
	kind delete cluster --name $(CLUSTER)
