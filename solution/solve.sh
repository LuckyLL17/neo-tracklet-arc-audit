#!/bin/sh
set -eu
cp "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/main.py" /app/main.py
chown runner:runner /app/main.py
chmod 0644 /app/main.py
