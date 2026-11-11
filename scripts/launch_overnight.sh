#!/bin/bash
# Launch an overnight runner in a detached screen session (nohup if screen is unavailable).
# Usage: scripts/launch_overnight.sh <runner.py> <results dir> <log name> <screen name> <label>
# Set PYTHON_BIN to choose the interpreter (default: python3).

set -u
cd "$(dirname "$0")/.." || exit 1

RUNNER="$1"; RESULTS_DIR="$2"; LOG="$2/$3"; SCREEN_NAME="$4"; LABEL="$5"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INPUT_FILE="data/classification/aiva_bench_input.jsonl"
CONCURRENCY=7

mkdir -p "$RESULTS_DIR"

screen_running() { command -v screen >/dev/null 2>&1 && [[ "$(screen -list 2>/dev/null)" == *".${SCREEN_NAME}"[[:space:]]* ]]; }

if screen_running; then
    echo "A screen session named '$SCREEN_NAME' is already running. Attach with: screen -r $SCREEN_NAME"
    exit 1
fi

echo "=========================================================="
echo " Launching $LABEL"
echo " Dataset:     2,894 variants ($INPUT_FILE)"
echo " Concurrency: $CONCURRENCY"
echo " Log file:    $LOG"
echo "=========================================================="

if command -v screen >/dev/null 2>&1; then
    # Paths go in as arguments, so quotes or spaces in them cannot break the command
    screen -dmS "$SCREEN_NAME" bash -c 'cd "$1" && "$2" -u "$3" --input "$4" --concurrency "$5" 2>&1 | tee -a "$6"' \
        _ "$PWD" "$PYTHON_BIN" "$RUNNER" "$INPUT_FILE" "$CONCURRENCY" "$LOG"
    sleep 1
fi

if screen_running; then
    echo "Screen session '$SCREEN_NAME' launched."
    echo "  Attach:          screen -r $SCREEN_NAME  (detach with Ctrl+A, D)"
    echo "  Follow the log:  tail -f $LOG"
    echo "  Stop:            screen -X -S $SCREEN_NAME quit"
else
    echo "screen unavailable; running in the background with nohup."
    nohup "$PYTHON_BIN" -u "$RUNNER" --input "$INPUT_FILE" --concurrency "$CONCURRENCY" >> "$LOG" 2>&1 &
    echo "Runner started, PID $!. Follow the log: tail -f $LOG"
fi
