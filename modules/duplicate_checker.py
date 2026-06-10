# Version: 1.0 (Centralized Duplicate Check Module)
"""
Centralized module for all pickup duplicate checking and anti-spam logic.
Refactored from auto_pickup.py to eliminate scattered duplicate check code.
"""

from datetime import datetime, timedelta
import pytz
import db_manager
from logger import log

# Submitted / in-flight pickups — show "already exists" alert
SUBMITTED_DUPLICATE_STATUSES = ['WAITING_CONFIRM', 'PENDING', 'PROCESSING', 'SUCCESS']

# OS still filling the form (Submit not pressed) — re-show form, no duplicate alert
SETUP_IN_PROGRESS_STATUSES = ['WAITING_SETUP']

# Default statuses for generic duplicate queries (submitted only)
DEFAULT_DUPLICATE_STATUSES = SUBMITTED_DUPLICATE_STATUSES

# Statuses that are "soft" — allow new pickup
SOFT_STATUSES = ['FAILED', 'CANCELLED']


def _chat_id_variants(chat_id):
    """Return (full_id, clean_id) for Telegram supergroup ID matching."""
    try:
        clean_id = int(str(chat_id).replace("-100", ""))
    except (TypeError, ValueError):
        clean_id = chat_id
    return chat_id, clean_id


def check_duplicate_pickup(chat_id, target_date_str, statuses=None):
    """
    Check if a pickup already exists for the given chat and target date.

    Args:
        chat_id: Telegram chat ID
        target_date_str: Date string in DD-MM-YYYY format
        statuses: List of statuses to check against (default: active pickup lifecycle statuses)

    Returns:
        True if duplicate exists, False otherwise.
    """
    if statuses is None:
        statuses = DEFAULT_DUPLICATE_STATUSES

    placeholders = ','.join(['?' for _ in statuses])
    full_id, clean_id = _chat_id_variants(chat_id)
    conn = db_manager.get_connection()
    try:
        res = conn.execute(
            f"SELECT 1 FROM pickup_queue WHERE chat_id IN (?, ?) AND target_date = ? AND status IN ({placeholders})",
            (full_id, clean_id, target_date_str, *statuses)
        ).fetchone()
        return res is not None
    except Exception as e:
        log.error(f"❌ duplicate_checker.check_duplicate_pickup Error: {e}")
        return False
    finally:
        conn.close()


def _fetch_pickup_order(chat_id, target_date_str, statuses):
    """Return the newest pickup_queue row for chat/date/statuses, or None."""
    placeholders = ','.join(['?' for _ in statuses])
    full_id, clean_id = _chat_id_variants(chat_id)
    conn = db_manager.get_connection()
    try:
        return conn.execute(
            f"SELECT id, chat_id, orig_msg_id, target_date, os_name, remark, vehicle, status, created_at "
            f"FROM pickup_queue WHERE chat_id IN (?, ?) AND target_date = ? AND status IN ({placeholders}) "
            f"ORDER BY created_at DESC LIMIT 1",
            (full_id, clean_id, target_date_str, *statuses),
        ).fetchone()
    except Exception as e:
        log.error(f"❌ duplicate_checker._fetch_pickup_order Error: {e}")
        return None
    finally:
        conn.close()


def get_setup_in_progress_order(chat_id, target_date_str):
    """Return WAITING_SETUP order (form open, submit not pressed) or None."""
    return _fetch_pickup_order(chat_id, target_date_str, SETUP_IN_PROGRESS_STATUSES)


def check_submitted_duplicate(chat_id, target_date_str):
    """True if a submitted/in-flight pickup already exists for this date."""
    return _fetch_pickup_order(chat_id, target_date_str, SUBMITTED_DUPLICATE_STATUSES) is not None


def check_any_submitted_duplicate(chat_id, date_type='today'):
    """
    Check submitted duplicates for today and (when date_type is today) tomorrow.

    Returns:
        (is_duplicate: bool, existing_date_str or None)
    """
    tz = pytz.timezone('Asia/Yangon')
    now = datetime.now(tz)
    today_str = now.strftime("%d-%m-%Y")
    tomorrow_str = (now + timedelta(days=1)).strftime("%d-%m-%Y")

    target_date = today_str if date_type == 'today' else tomorrow_str

    if check_submitted_duplicate(chat_id, target_date):
        return True, target_date

    if date_type == 'tomorrow':
        return False, None

    if check_submitted_duplicate(chat_id, tomorrow_str):
        return True, tomorrow_str

    return False, None


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

    full_id, clean_id = _chat_id_variants(chat_id)
    placeholders = ','.join(['?' for _ in SUBMITTED_DUPLICATE_STATUSES])
    conn = db_manager.get_connection()
    try:
        for date_str, key in [(today_str, 'today'), (tomorrow_str, 'tomorrow')]:
            exists = conn.execute(
                f"SELECT 1 FROM pickup_queue WHERE chat_id IN (?, ?) AND target_date = ? AND status IN ({placeholders})",
                (full_id, clean_id, date_str, *SUBMITTED_DUPLICATE_STATUSES)
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
    if check_submitted_duplicate(chat_id, target_date):
        return True, target_date

    # 💡 For 'tomorrow', only check tomorrow — today's pickup does NOT block tomorrow's request
    if date_type == 'tomorrow':
        return False, None

    # For 'today', also check tomorrow (and→or logic, used in Mid-day Staff Decision Flow)
    other_date = tomorrow_str
    if check_submitted_duplicate(chat_id, other_date):
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
