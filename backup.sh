#!/bin/bash

# Backup Script for CarryMan Bot with Telegram Upload (v3 - Flexible Token)

# Backup တွေကို သိမ်းမယ့်နေရာ (မရှိသေးရင် အသစ်ဆောက်ပေးမယ်)
BACKUP_DIR="/root/bot_backups"
mkdir -p $BACKUP_DIR

# Project ရှိတဲ့နေရာ
PROJECT_DIR="/root/carryman_bot"

# Backup file name မှာ ရက်စွဲထည့်ရန်
DATE=$(date +%Y-%m-%d_%H-%M-%S)
BACKUP_FILENAME="$BACKUP_DIR/carryman_backup_$DATE.tar.gz"

echo "Starting backup for CarryMan Bot..."

# Project directory ထဲကို သွားပါ
cd $PROJECT_DIR || exit

# (အရေးကြီး) Container တွေကို ခေတ္တခဏ အေးခဲလိုက်ပါ (Pause)
echo "Pausing containers to ensure data consistency..."
docker-compose pause

# ခေတ္တရပ်နေချိန်မှာ Project folder တစ်ခုလုံးကို tar file အဖြစ်ချုံ့ပါ (venv folder ကို ချန်လှပ်ခဲ့ပါ)
echo "Creating archive (excluding venv): $BACKUP_FILENAME"
tar --exclude='./venv' -czf $BACKUP_FILENAME .

# Container တွေကို ပြန် run စေပါ (Unpause)
echo "Resuming containers..."
docker-compose unpause

echo "Backup completed successfully!"
echo "Your backup is saved at: $BACKUP_FILENAME"

# --- Send backup to Telegram ---
echo "Attempting to send backup file to Telegram Manager..."

# Load environment variables from .env file if it exists
if [ -f "$PROJECT_DIR/.env" ]; then
    export $(grep -v '^#' "$PROJECT_DIR/.env" | xargs)
else
    echo "Error: .env file not found. Cannot send backup to Telegram."
    exit 1
fi

# Use TELEGRAM_BOT_TOKEN if BOT_TOKEN is not set
if [ -z "$BOT_TOKEN" ]; then
    BOT_TOKEN=$TELEGRAM_BOT_TOKEN
fi

# Check if variables are set
if [ -z "$BOT_TOKEN" ] || [ -z "$MANAGER_ID" ]; then
    echo "Error: BOT_TOKEN (or TELEGRAM_BOT_TOKEN) or MANAGER_ID is not set in .env file."
    exit 1
fi

# Telegram API URL and caption
URL="https://api.telegram.org/bot$BOT_TOKEN/sendDocument"
CAPTION="CarryMan Bot Backup (Light) - $(date +%Y-%m-%d_%H-%M-%S)"

# Use curl to send the file. The -s flag makes it silent.
curl -s -X POST "$URL" \
    -F "chat_id=$MANAGER_ID" \
    -F "document=@$BACKUP_FILENAME" \
    -F "caption=$CAPTION" > /dev/null

if [ $? -eq 0 ]; then
    echo "Backup file successfully queued for sending to Telegram Manager."
else
    echo "Error: Failed to send backup file to Telegram."
fi