#!/usr/bin/env bash
# run_grid_bot.sh — Start the Production Grid Bot (v2) with virtual environment and tmux support

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_NAME="grid-bot"
LOG_DIR="$SCRIPT_DIR/logs"
TMUX_LOG="$LOG_DIR/tmux_bot.log"

mkdir -p "$LOG_DIR"

# Parse execution mode and optional lifecycle action.
INSIDE_TMUX=false
if [ "${1:-}" = "--inside-tmux" ]; then
    INSIDE_TMUX=true
    shift
fi

ACTION="start"
case "${1:-}" in
    start|restart|stop|status|attach)
        ACTION="$1"
        shift
        ;;
esac

build_tmux_cmd() {
    local cmd
    cmd="$0 --inside-tmux"
    for arg in "$@"; do
        cmd+=" $(printf '%q' "$arg")"
    done
    printf "%s" "$cmd"
}

start_tmux_session() {
    local cmd
    cmd="$(build_tmux_cmd "$@")"
    tmux new-session -d -s "$SESSION_NAME" -c "$SCRIPT_DIR" "$cmd"
    tmux pipe-pane -t "$SESSION_NAME" -o "cat >> $TMUX_LOG"
    echo "Bot started in tmux session '$SESSION_NAME'. Logs at $TMUX_LOG"
    echo "To view live: tmux attach -t $SESSION_NAME"
}

# 1. Handle Tmux Logic
if command -v tmux >/dev/null 2>&1; then
    # If we are NOT already inside a tmux session, launch a new one
    if [ "$INSIDE_TMUX" = false ] && [ -z "$TMUX" ]; then
        case "$ACTION" in
            restart)
                if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
                    echo "Restarting tmux session '$SESSION_NAME'..."
                    tmux kill-session -t "$SESSION_NAME"
                else
                    echo "No existing tmux session '$SESSION_NAME' found. Starting fresh..."
                fi
                start_tmux_session "$@"
                exit 0
                ;;
            stop)
                if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
                    tmux kill-session -t "$SESSION_NAME"
                    echo "Stopped tmux session '$SESSION_NAME'."
                else
                    echo "No tmux session '$SESSION_NAME' is running."
                fi
                exit 0
                ;;
            status)
                if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
                    echo "tmux session '$SESSION_NAME' is running."
                else
                    echo "tmux session '$SESSION_NAME' is not running."
                fi
                exit 0
                ;;
            attach)
                if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
                    tmux attach-session -t "$SESSION_NAME"
                else
                    echo "No tmux session '$SESSION_NAME' found to attach."
                    exit 1
                fi
                exit 0
                ;;
            start)
                if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
                    echo "Session '$SESSION_NAME' already exists. Attaching..."
                    tmux attach-session -t "$SESSION_NAME"
                    exit 0
                fi
                echo "Starting new tmux session '$SESSION_NAME'..."
                start_tmux_session "$@"
                exit 0
                ;;
        esac
    fi
fi

# 2. Setup Environment (Inside Tmux or if no Tmux)
if [ "$ACTION" = "stop" ] || [ "$ACTION" = "status" ] || [ "$ACTION" = "attach" ]; then
    echo "Action '$ACTION' requires tmux and is only supported outside a tmux-managed process."
    exit 1
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
# Default to human-readable logs unless explicitly overridden by environment.
: "${LOG_STRUCTURED:=false}"
export LOG_STRUCTURED

# Start the webhook server which orchestrates the bot_v2 instance
python "$SCRIPT_DIR/webhook_server.py" "$@"
