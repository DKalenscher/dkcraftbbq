#!/bin/bash
# Started by "DKCraftBBQ Recipe Sync.bat" on the Windows desktop. Checks TikTok for
# new playlist videos now and every 3 hours while the window is open. Press Enter
# to check right away; close the window to stop.
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
cd "$(dirname "$0")/.." || exit 1
INTERVAL=$((3 * 60 * 60))

while true; do
  echo
  echo "=== $(date '+%a %b %-d, %-I:%M %p') - checking TikTok for new recipes ==="
  python3 tools/pc_sync.py 2>&1 | tee -a data/pc_sync.log | sed -E 's/^[0-9-]+ [0-9:,]+ \[[A-Z]+\] //'
  echo "=== Next check at $(date -d "+$INTERVAL seconds" '+%-I:%M %p'). Press Enter to check now, or close this window to stop. ==="
  # Enter = check now; timeout (status > 128) = scheduled check; no keyboard at all = just wait
  read -r -t "$INTERVAL" || [ $? -gt 128 ] || sleep "$INTERVAL"
done
