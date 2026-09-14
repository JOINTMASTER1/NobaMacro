#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
umask 077
log_dir="${XDG_STATE_HOME:-$HOME/.local/state}/NobaMacro"
mkdir -p -- "$log_dir"
log_path="$log_dir/last-run.log"
printf 'NobaMacro 2.4 — errors are saved in %s\n' "$log_path"
# One log per launch; tee keeps errors visible in the terminal as well.
exec /usr/bin/python3 -X faulthandler -u nobamacro.py 2> >(tee "$log_path" >&2)
