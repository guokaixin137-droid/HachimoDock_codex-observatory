#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$root/runtime/bin/python3" -I -B "$root/manage.py" "$@"
