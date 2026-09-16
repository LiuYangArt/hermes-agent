#!/bin/sh
set -eu

workspace="${HERMES_TEAM_WORKSPACE:-$HOME/HermesTeamWorkspace}"
marker="$workspace/.hermes-team-workspace"
log="${HERMES_TEAM_STATE_DIR:-$HOME/.local/share/hermes-team}/logs/cleanup.log"

test -d "$workspace"
test -f "$marker"

find "$workspace" -mindepth 1 -type f ! -name '.hermes-team-workspace' -mtime +7 -print -delete >> "$log" 2>&1
find "$workspace" -mindepth 1 -type l -mtime +7 -print -delete >> "$log" 2>&1
find "$workspace" -mindepth 1 -type d -empty -print -delete >> "$log" 2>&1
printf '%s cleanup complete\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$log"
