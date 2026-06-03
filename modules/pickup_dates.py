"""Pickup target date resolution (DD-MM-YYYY, Asia/Yangon)."""
import pytz
from datetime import datetime, timedelta

YANGON_TZ = pytz.timezone('Asia/Yangon')


def get_target_date_str(date_type, *, msg_ts=None, use_message_timestamp=False):
    """
    Resolve pickup target date string (DD-MM-YYYY).

    use_message_timestamp=True — AI / confirmation flow: base on originating message time
        (midnight edge: message at 23:59 still maps today/tomorrow to that calendar day).
    use_message_timestamp=False — explicit /pickup today|tom, interactive UI: Yangon calendar now.
    """
    if use_message_timestamp and msg_ts is not None:
        base_dt = datetime.fromtimestamp(msg_ts, YANGON_TZ)
    else:
        base_dt = datetime.now(YANGON_TZ)

    if date_type == "today":
        return base_dt.strftime("%d-%m-%Y")
    return (base_dt + timedelta(days=1)).strftime("%d-%m-%Y")
