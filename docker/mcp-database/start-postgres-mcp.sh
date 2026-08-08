#!/bin/sh
# Starts a fresh, throwaway local Postgres instance (data dir lives inside
# THIS container, discarded with it - never the /workspace bind mount) and
# execs the real postgres-mcp server against it. Runs as root (the
# container's default user - see the image's own comment on this); `su
# postgres` for every actual Postgres operation, matching how the Debian
# postgresql package expects to be driven.
set -eu

PGBIN="$(ls -d /usr/lib/postgresql/*/bin | head -n1)"
# /tmp, not /var/lib/postgresql/data: the sandbox container's root
# filesystem is mounted --read-only (see _sandbox.py's _HARDENING_ARGS) -
# only /tmp (a writable tmpfs) and the /workspace bind mount are writable.
PGDATA=/tmp/pgdata

# su postgres -c mkdir, not root mkdir + chown: the sandbox's hardened
# capability set (--cap-drop ALL --cap-add DAC_OVERRIDE) does NOT include
# CAP_CHOWN, so a root-owned dir handed to postgres via chown fails
# ("Operation not permitted") - confirmed empirically. /tmp itself is a
# world-writable (mode 1777) tmpfs, so postgres can create its own
# subdirectory directly without ever needing chown.
su postgres -c "mkdir -p $PGDATA"

if [ ! -s "$PGDATA/PG_VERSION" ]; then
    su postgres -c "$PGBIN/initdb -D $PGDATA" >&2
fi

# -k /tmp: Postgres's default unix_socket_directories is /var/run/postgresql,
# not writable under the sandbox's --read-only root fs (confirmed
# empirically: "could not create lock file ... Read-only file system") -
# /tmp is the one writable location (tmpfs) available.
su postgres -c "$PGBIN/pg_ctl -D $PGDATA -l /tmp/postgres.log -o '-h 127.0.0.1 -k /tmp' start" >&2

i=0
while ! su postgres -c "$PGBIN/pg_isready -h 127.0.0.1 -q"; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
        echo "postgres did not become ready in time" >&2
        cat /tmp/postgres.log >&2
        exit 1
    fi
    sleep 0.5
done

su postgres -c "$PGBIN/psql -h 127.0.0.1 -c \"ALTER USER postgres PASSWORD 'optarena';\"" >&2

exec postgres-mcp "postgresql://postgres:optarena@127.0.0.1:5432/postgres" --access-mode unrestricted
