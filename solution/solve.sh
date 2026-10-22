#!/bin/sh
set -eu
cp "$(dirname "$0")/neo_audit.py" "${TASK_WORKDIR:-$(cd "$(dirname "$0")/../environment" && pwd)}/neo_audit.py"
