#!/bin/bash

# Script to sync latest files from prod server to dev (macOS)
# Syncs ignored directories: data_futures, logs, bot_logs
# Assumes SSH key is set up for passwordless access to prod server
# Run from the NY_Bot directory on dev

# Configuration - Update these with your prod details
PROD_USER="root@212.24.105.227"  # e.g., sarmad@192.168.1.100
PROD_NY_BOT_PATH="/home/user/Grid-Bot"        # e.g., /home/sarmad/Grid-Bot

# Directories to sync (relative to NY_Bot root)
DIRS_TO_SYNC=("data_futures" "logs")

# Options
DRY_RUN=false  # Set to true for --dry-run (no actual changes)
DELETE=true    # Set to false to avoid --delete (keeps extra local files)

# Function to sync a directory
sync_dir() {
    local dir=$1
    local rsync_opts="-avz"
    
    if [ "$DRY_RUN" = true ]; then
        rsync_opts="$rsync_opts --dry-run"
        echo "DRY RUN MODE: No changes will be made"
    fi
    
    if [ "$DELETE" = true ]; then
        rsync_opts="$rsync_opts --delete"
    fi
    
    echo "Syncing $dir from prod to dev..."
    rsync $rsync_opts "$PROD_USER:$PROD_NY_BOT_PATH/$dir/" "./$dir/"
    
    if [ $? -eq 0 ]; then
        echo "✓ $dir synced successfully"
    else
        echo "✗ Error syncing $dir"
        exit 1
    fi
}

# Main sync process
echo "Starting sync from prod server ($PROD_USER) to dev..."
echo "Source path on prod: $PROD_NY_BOT_PATH"
echo "Target path on dev: $(pwd)"
echo ""

for dir in "${DIRS_TO_SYNC[@]}"; do
    sync_dir "$dir"
done

echo ""
echo "Sync complete!"
if [ "$DRY_RUN" = true ]; then
    echo "This was a dry run - no files were changed."
fi
echo "Check the output above for details."