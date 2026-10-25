#!/bin/sh
set -eu

# Flow0.124 check is a foreground check_once operation, not bare flow status.
# Keep a separate temp root so cleanup never targets another Flow session.
command -v timeout >/dev/null
FLOW_TEMP_DIR=$(mktemp -d)
export FLOW_TEMP_DIR
cleanup() {
  result=$?
  trap - EXIT INT TERM HUP
  cleanup_result=0
  timeout --signal=TERM --kill-after=5s 20s node node_modules/flow-bin/cli.js stop --temp-dir "$FLOW_TEMP_DIR" >/dev/null 2>&1 || cleanup_result=$?
  rm -rf "$FLOW_TEMP_DIR"
  if [ "$cleanup_result" -ne 0 ]; then
    echo "Flow cleanup could not be verified" >&2
    if [ "$result" -eq 0 ]; then result=$cleanup_result; fi
  fi
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

node --version
npm --version
node scripts/test-ci-boundary.cjs
NODE_OPTIONS="--require=$(pwd)/scripts/ci-test-boundary.cjs"
export NODE_OPTIONS
npm run build
# The second -- reaches the nested npm test command, retaining the real entrypoint.
timeout --signal=TERM --kill-after=10s 480s npm run ci_validate -- -- --runInBand --watchman=false
