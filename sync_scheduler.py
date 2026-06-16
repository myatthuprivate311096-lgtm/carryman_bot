# Version: 1.0 — GSheet Sync Scheduler (restored after git reset)
"""
Periodic Google Sheet sync scheduler.
Runs Sheet → DB import every 6 hours.
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

def run_sync_cycle(syncer):
    """Sheet → DB import: Knowledge Base + Staff Info + Shop Mappings."""
    if not GSHEET_URL:
        log.warning("⚠️ GSHEET_URL not set in .env. Skipping sync cycle.")
        return

    log.info("🔄 Running Sheet → DB sync (import only)...")

    success_kb, result_kb = syncer.sync_knowledge(GSHEET_URL)
    log.info(f"🧠 Knowledge: {result_kb}" if success_kb else f"⚠️ Knowledge: {result_kb}")

    success_staff, result_staff = syncer.sync_staff_info(GSHEET_URL)
    log.info(f"👥 Staff: {result_staff}" if success_staff else f"⚠️ Staff: {result_staff}")

    success_map, result_map = syncer.sync_shop_mappings(GSHEET_URL)
    log.info(f"📥 Mappings: {result_map}" if success_map else f"⚠️ Mappings: {result_map}")


if __name__ == "__main__":
    log.info("🔄 GSheet Sync Scheduler started (interval: 6 hours)...")
    syncer = GSheetSync()

    try:
        run_sync_cycle(syncer)
    except Exception as e:
        log.error(f"❌ GSheet Sync Error (startup): {e}")

    while True:
        time.sleep(SYNC_INTERVAL)
        try:
            run_sync_cycle(syncer)
        except Exception as e:
            log.error(f"❌ GSheet Sync Error: {e}")
