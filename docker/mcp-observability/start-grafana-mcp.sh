#!/bin/sh
# Starts a fresh, throwaway local Grafana instance (all state under /tmp -
# the sandbox's root fs is read-only except /tmp and /workspace - discarded
# with the container) and execs the real mcp-grafana server against it.
set -eu

export GF_PATHS_DATA=/tmp/grafana/data
export GF_PATHS_LOGS=/tmp/grafana/logs
export GF_PATHS_PLUGINS=/tmp/grafana/plugins
export GF_PATHS_PROVISIONING=/tmp/grafana/provisioning
export GF_SECURITY_ADMIN_USER=admin
export GF_SECURITY_ADMIN_PASSWORD=optarena
# Runs as the container's default root user throughout (no su/privilege
# drop) - unlike Postgres, Grafana has no hardcoded refusal to run as root,
# confirmed empirically by just running it this way successfully.
mkdir -p "$GF_PATHS_DATA" "$GF_PATHS_LOGS" "$GF_PATHS_PLUGINS" "$GF_PATHS_PROVISIONING/datasources"

/usr/share/grafana/bin/grafana server \
    --homepath /usr/share/grafana \
    --config /etc/grafana/grafana.ini \
    >/tmp/grafana/logs/server-stdout.log 2>&1 &

i=0
while ! curl -sf http://127.0.0.1:3000/api/health >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
        echo "grafana did not become ready in time" >&2
        cat /tmp/grafana/logs/server-stdout.log >&2
        exit 1
    fi
    sleep 0.5
done

export GRAFANA_URL=http://127.0.0.1:3000
export GRAFANA_USERNAME=admin
export GRAFANA_PASSWORD=optarena
exec mcp-grafana -t stdio
