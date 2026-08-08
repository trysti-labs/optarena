#!/bin/sh
# Creates a real, throwaway `kind` (Kubernetes-in-Docker) cluster - its node
# containers are created as SIBLINGS on the HOST's real Docker daemon via
# the mounted socket (docker-outside-of-docker, same trust boundary as
# docker/mcp-docker), not nested inside this sandbox - then execs the real
# mcp-server-kubernetes against it.
set -eu

export KUBECONFIG=/tmp/kubeconfig
# OPTARENA_SANDBOX_NAME (passed by build_sandboxed_service via exec -e) so
# the HOST side can derive this cluster's node-container name and reap it
# if this script's own cleanup trap never gets to run (B-2 in the tool-call
# audit: `docker stop` signals only PID 1, never an exec'd script, so a
# non-graceful teardown kills the trap with the container while the kind
# node - a HOST sibling container - lives on). Fallback $HOSTNAME keeps the
# script runnable by hand; NOT $$ (the shell's PID): each container's PID
# namespace restarts fresh, so two unrelated sandboxes really did collide
# on "optarena-8" ("node(s) already exist"), confirmed empirically.
CLUSTER_NAME="optarena-${OPTARENA_SANDBOX_NAME:-$HOSTNAME}"

kind create cluster --name "$CLUSTER_NAME" --kubeconfig "$KUBECONFIG" --wait 120s >&2

# kind's own kubeconfig points kubectl at 127.0.0.1:<host-published-port> -
# correct for a kubectl running ON THE HOST, but this sandbox runs with
# --network none (confirmed empirically: kubectl_get failed with "connect:
# connection refused" against that address - the sandbox's own loopback is
# fully isolated, not the host's). Fix: join THIS container to the same
# docker network kind's control-plane node is on (created as a side effect
# of `kind create cluster` above - we already have docker socket access via
# the docker-outside-of-docker mount, same as everything else here) and
# regenerate the kubeconfig using the control-plane's INTERNAL container IP
# instead of the host-published port.
docker network connect kind "$HOSTNAME" >&2
kind get kubeconfig --internal --name "$CLUSTER_NAME" > "$KUBECONFIG"

cleanup() {
    kind delete cluster --name "$CLUSTER_NAME" --kubeconfig "$KUBECONFIG" >&2 || true
}
# NOT `exec mcp-server-kubernetes`: exec would replace this shell's own
# process image, so the EXIT trap below would never fire when the server
# eventually exits (there'd be no shell left to run it). Plain foreground
# invocation still transparently inherits stdin/stdout/stderr - the only
# thing `exec` would have bought here is one fewer process, at the cost of
# the kind cluster (real sibling containers on the host) leaking forever.
trap cleanup EXIT INT TERM
mcp-server-kubernetes
