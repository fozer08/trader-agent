#!/bin/sh
set -e
chown -R trader:trader /app/data /app/logs
exec gosu trader "$@"
