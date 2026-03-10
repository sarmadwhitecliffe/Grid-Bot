#!/usr/bin/env bash
# run_grid_bot.sh — Start the Production Grid Bot (v2) with virtual environment and tmux support

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_NAME="grid-bot"
LOG_DIR="$SCRIPT_DIR/logs"
TMUX_LOG="$LOG_DIR/tmux_bot.log"

mkdir -p "$LOG_DIR"

# 1. Handle Tmux Logic
if command -v tmux >/dev/null 2>&1; then
    # If we are NOT already inside a tmux session, launch a new one
    if [ -z "$TMUX" ]; then
        if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
            echo "Session '$SESSION_NAME' already exists. Attaching..."
            tmux attach-session -t "$SESSION_NAME"
            exit 0
        else
            echo "Starting new tmux session '$SESSION_NAME'..."
            # Create a new detached session
            tmux new-session -d -s "$SESSION_NAME" -c "$SCRIPT_DIR" "$0 --inside-tmux $@"
            # Enable logging via pipe-pane
            tmux pipe-pane -t "$SESSION_NAME" -o "cat >> $TMUX_LOG"
            echo "Bot started in tmux session '$SESSION_NAME'. Logs at $TMUX_LOG"
            echo "To view live: tmux attach -t $SESSION_NAME"
            exit 0
        fi
    fi
fi

# 2. Setup Environment (Inside Tmux or if no Tmux)
if [ "$1" == "--inside-tmux" ]; then
    shift # Remove the flag
fi

# Activate virtual environment
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
elif [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
else
    echo "ERROR: Virtual environment not found at $SCRIPT_DIR/.venv or $SCRIPT_DIR/venv"
    echo "Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

echo "Starting Production Grid Bot (Webhook Server) at $(date)"
# Start the webhook server which orchestrates the bot_v2 instance
python "$SCRIPT_DIR/webhook_server.py" "$@"
