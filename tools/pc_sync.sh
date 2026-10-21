#!/bin/bash
# Started by Windows Task Scheduler (see docs/IMPROVEMENTS.md). Logs to data/pc_sync.log.
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
cd "$(dirname "$0")/.." || exit 1
python3 tools/pc_sync.py "$@" >> data/pc_sync.log 2>&1
