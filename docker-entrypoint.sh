#!/bin/sh
set -e
chown -R trader:trader /app/configs /app/data /app/logs
exec gosu trader "$@"
