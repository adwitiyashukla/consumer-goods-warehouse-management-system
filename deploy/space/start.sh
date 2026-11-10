#!/bin/sh
set -e
export PGDATA=/tmp/pgdata
BIN=$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1)
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  "$BIN/initdb" -D "$PGDATA" -U dockhand --auth=trust --locale=C.UTF-8 -E UTF8 > /dev/null
fi
"$BIN/pg_ctl" -D "$PGDATA" -o "-c listen_addresses=127.0.0.1 -k /tmp" -l /tmp/postgres.log -w start
"$BIN/createdb" -h 127.0.0.1 -U dockhand dockhand 2> /dev/null || true
cd /app
exec dockhand serve --demo
