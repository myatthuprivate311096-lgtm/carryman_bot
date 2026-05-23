# Version: 1.0 (Centralized Duplicate Check Module)
"""
Centralized module for all pickup duplicate checking and anti-spam logic.
Refactored from auto_pickup.py to eliminate scattered duplicate check code.
"""

from datetime import datetime, timedelta
import pytz
import db_manager
from logger import log

# Default statuses that count as "existing" (blocks new pickup)
DEFAULT_DUPLICATE_STATUSES = ['PENDING', 'PROCESSING', 'SUCCESS']

# Statuses that are "soft" — allow new pickup
SOFT_STATUSES = ['WAITING_CONFIRM', 'FAILED', 'CANCELLED', 'WAITING_SETUP']


def check_duplicate_pickup(chat_id, target_date_str, statuses=None):
    """
    Check if a pickup already exists for the given chat and target date.

    Args:
        chat_id: Telegram chat ID
        target_date_str: Date string in DD-MM-YYYY format
        statuses: List of statuses to check against (default: PENDING, PROCESSING, SUCCESS)

    Returns:
        True if duplicate exists, False otherwise.
    """
    if statuses is None:
        statuses = DEFAULT_DUPLICATE_STATUSES

    placeholders = ','.join(['?' for _ in statuses])
    conn = db_manager.get_connection()
    try:
        res = conn.execute(
            f"SELECT 1 FROM pickup_queue WHERE chat_id = ? AND target_date = ? AND status IN ({placeholders})",
            (chat_id, target_date_str, *statuses)
        ).fetchone()
        return res is not None
    except Exception as e:
        log.error(f"❌ duplicate_checker.check_duplicate_pickup Error: {e}")
        return False
    finally:
        conn.close()


def check_anti_spam(chat_id, minutes=3):
    """
    Anti-spam check: prevents multiple pickup triggers within a time window.

    Args:
        chat_id: Telegram chat ID
        minutes: Time window in minutes

    Returns:
        True if active session exists, False otherwise.
    """
    try:
        return db_manager.check_active_pickup_session(chat_id, minutes)
    except Exception as e:
        log.error(f"❌ duplicate_checker.check_anti_spam Error: {e}")
        return False


def get_existing_dates(chat_id):
    """
    Return a list of dates (DD-MM-YYYY) that have existing pickups
    for today AND/OR tomorrow. Used for split today/tomorrow duplicate alerts.

    Args:
        chat_id: Telegram chat ID

    Returns:
        dict with keys 'today' and 'tomorrow', each containing target_date or None
    """
    tz = pytz.timezone('Asia/Yangon')
    now = datetime.now(tz)
    today_str = now.strftime("%d-%m-%Y")
    tomorrow_str = (now + timedelta(days=1)).strftime("%d-%m-%Y")

    result = {
        'today': None,
        'tomorrow': None,
        'today_exists': False,
        'tomorrow_exists': False,
    }

    conn = db_manager.get_connection()
    try:
        for date_str, key in [(today_str, 'today'), (tomorrow_str, 'tomorrow')]:
            exists = conn.execute(
                "SELECT 1 FROM pickup_queue WHERE chat_id = ? AND target_date = ? AND status IN ('PENDING', 'PROCESSING', 'SUCCESS')",
                (chat_id, date_str)
            ).fetchone()
            if exists:
                result[f'{key}_exists'] = True
                result[key] = date_str
        return result
    except Exception as e:
        log.error(f"❌ duplicate_checker.get_existing_dates Error: {e}")
        return result
    finally:
        conn.close()


def check_any_duplicate(chat_id, date_type='today'):
    """
    Check if there's any duplicate for today OR tomorrow (and→or logic).

    Args:
        chat_id: Telegram chat ID
        date_type: 'today' or 'tomorrow' (for context)

    Returns:
        (is_duplicate: bool, existing_date_str: str or None)
    """
    tz = pytz.timezone('Asia/Yangon')
    now = datetime.now(tz)
    today_str = now.strftime("%d-%m-%Y")
    tomorrow_str = (now + timedelta(days=1)).strftime("%d-%m-%Y")

    target_date = today_str if date_type == 'today' else tomorrow_str

    # Check the primary date first
    if check_duplicate_pickup(chat_id, target_date):
        return True, target_date

    # 💡 For 'tomorrow', only check tomorrow — today's pickup does NOT block tomorrow's request
    if date_type == 'tomorrow':
        return False, None

    # For 'today', also check tomorrow (and→or logic, used in Mid-day Staff Decision Flow)
    other_date = tomorrow_str
    if check_duplicate_pickup(chat_id, other_date):
        return True, other_date

    return False, None


def get_date_label(target_date_str):
    """
    Return appropriate Burmese date label.

    Args:
        target_date_str: Date string in DD-MM-YYYY format

    Returns:
        String like "ယနေ့ (12-05-2026)" or "မနက်ဖြန် (13-05-2026)" or just the date
    """
    tz = pytz.timezone('Asia/Yangon')
    now = datetime.now(tz)
    today_str = now.strftime("%d-%m-%Y")
    tomorrow_str = (now + timedelta(days=1)).strftime("%d-%m-%Y")

    if target_date_str == today_str:
        return f"ယနေ့ ({target_date_str})"
    elif target_date_str == tomorrow_str:
        return f"မနက်ဖြန် ({target_date_str})"
    else:
        return target_date_str
