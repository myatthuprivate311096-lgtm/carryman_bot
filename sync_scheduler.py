# Version: 1.0 — GSheet Sync Scheduler (restored after git reset)
"""
Periodic Google Sheet sync scheduler.
Runs export every 6 hours to keep GSheet in sync with DB.
"""
import time
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from logger import log
from gsheet_sync import GSheetSync

# Google Sheet URL from .env
from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, '.env'))

GSHEET_URL = os.getenv('GSHEET_URL', '')
SYNC_INTERVAL = 6 * 3600  # 6 hours

if __name__ == "__main__":
    log.info("🔄 GSheet Sync Scheduler started (interval: 6 hours)...")
    syncer = GSheetSync()

    while True:
        try:
            if GSHEET_URL:
                log.info("🔄 Running bidirectional GSheet sync...")
                # 1. Import from Sheet → DB (preserve manual edits from Sheet)
                success_map, result_map = syncer.sync_shop_mappings(GSHEET_URL)
                log.info(f"📥 Import: {result_map}")
                
                # 2. Export DB → Sheet (consolidate without clearing — append new rows only)
                appended_count = syncer.append_new_mappings_to_sheet(GSHEET_URL)
                if appended_count > 0:
                    log.info(f"📤 Appended {appended_count} new shop(s) to Sheet.")
            else:
                log.warning("⚠️ GSHEET_URL not set in .env. Skipping sync cycle.")
        except Exception as e:
            log.error(f"❌ GSheet Sync Error: {e}")

        time.sleep(SYNC_INTERVAL)
