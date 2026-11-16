#!/usr/bin/env bash
# Run every scenario in sim/scenarios/ against the running PLC.
#
# Each scenario gets a clean PLC state. A stop/start cycle reloads the uploaded
# program, which is what resets the counters and the box tracking - the program
# itself never clears its counters, on purpose.
#
# The runtime refuses a command with COMMAND:BUSY while the previous transition
# is still running, so every command is followed by a status poll instead of a
# fixed sleep.
#
# Usage:  bash sim/run-scenarios.sh            (all scenarios)
#         bash sim/run-scenarios.sh 'fault-*'  (only matching ones)
set -uo pipefail
cd "$(dirname "$0")/.."

set -a; . ./.env; set +a

TOKEN=$(curl -sk -X POST https://localhost:8443/api/login \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$OPENPLC_USER\",\"password\":\"$OPENPLC_PASSWORD\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

api() { curl -sk "https://localhost:8443/api/$1" -H "Authorization: Bearer $TOKEN"; }

wait_status() {   # $1 = RUNNING | STOPPED
  for _ in $(seq 1 60); do
    case "$(api status)" in *"STATUS:$1"*) return 0 ;; esac
    sleep 0.5
  done
  return 1
}

clean_cycle() {   # reload the program: fresh counters, fresh box tracking
  for _ in 1 2 3; do api stop-plc >/dev/null; wait_status STOPPED && break; done
  for _ in 1 2 3; do api start-plc >/dev/null; wait_status RUNNING && return 0; done
  return 1
}

mkdir -p sim/logs
pattern="${1:-*}"
rc=0
for f in sim/scenarios/$pattern.json; do
  [ -e "$f" ] || { echo "no scenario matches sim/scenarios/$pattern.json"; exit 2; }
  name=$(basename "$f" .json)
  clean_cycle || printf '%-32s %s\n' "$name" "could not cycle the PLC"
  .venv/bin/python sim/plant.py --scenario "$f" --log "sim/logs/$name.log" \
    >"sim/logs/$name.out" 2>&1
  code=$?
  if [ -s "sim/logs/$name.log" ]; then
    printf '%-32s %s\n' "$name" "$(tail -1 "sim/logs/$name.log")"
  else
    printf '%-32s %s\n' "$name" "NO LOG: $(tail -1 "sim/logs/$name.out")"
  fi
  [ $code -ne 0 ] && rc=1
done
exit $rc
