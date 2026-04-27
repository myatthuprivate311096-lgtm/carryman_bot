import os
import sys
import time
from playwright.sync_api import sync_playwright

# Add parent directory to path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.auto_login import auto_login
from logger import log

def test_auto_login_standalone():
    """
    Tests the auto_login module by itself.
    """
    log.info("🧪 Testing auto_login module...")
    
    # Ensure .env is loaded (auto_login does this internally)
    success, msg = auto_login()
    
    if success:
        log.info("✅ Standalone Auto Login Test: SUCCESS")
        # Check if state.json was created/updated
        state_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state.json")
        if os.path.exists(state_path):
            log.info(f"✅ state.json found at {state_path}")
        else:
            log.error("❌ state.json NOT found even after success report!")
    else:
        log.error(f"❌ Standalone Auto Login Test: FAILED - {msg}")

if __name__ == "__main__":
    test_auto_login_standalone()
