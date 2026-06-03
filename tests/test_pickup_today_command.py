"""Tests: explicit /pickup today uses calendar now, not orig message date."""
import unittest
from datetime import datetime
from unittest.mock import patch

import pytz

from modules.pickup_dates import get_target_date_str

YANGON = pytz.timezone('Asia/Yangon')


class TestPickupTodayCommand(unittest.TestCase):
    def test_explicit_today_ignores_old_message_timestamp(self):
        """Reply to March 30 msg + /pickup today → must be action day (June 3), not 30-03."""
        old_ts = YANGON.localize(datetime(2026, 3, 30, 10, 0, 0)).timestamp()
        fake_now = YANGON.localize(datetime(2026, 6, 3, 17, 21, 0))

        with patch('modules.pickup_dates.datetime') as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.fromtimestamp = datetime.fromtimestamp
            result = get_target_date_str(
                "today", msg_ts=old_ts, use_message_timestamp=False
            )

        self.assertEqual(result, "03-06-2026")

    def test_explicit_tomorrow_uses_calendar_now(self):
        old_ts = YANGON.localize(datetime(2026, 3, 30, 10, 0, 0)).timestamp()
        fake_now = YANGON.localize(datetime(2026, 6, 3, 17, 21, 0))

        with patch('modules.pickup_dates.datetime') as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.fromtimestamp = datetime.fromtimestamp
            result = get_target_date_str(
                "tomorrow", msg_ts=old_ts, use_message_timestamp=False
            )

        self.assertEqual(result, "04-06-2026")

    def test_ai_flow_keeps_message_timestamp_midnight(self):
        """AI confirm at 00:01 after 23:59 msg: tomorrow from msg day = May 2."""
        msg_ts = YANGON.localize(datetime(2026, 5, 1, 23, 59, 0)).timestamp()
        result = get_target_date_str(
            "tomorrow", msg_ts=msg_ts, use_message_timestamp=True
        )
        self.assertEqual(result, "02-05-2026")


if __name__ == "__main__":
    unittest.main()
