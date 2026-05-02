import unittest
from datetime import datetime, timedelta
import pytz

def calculate_target_date(msg_timestamp, date_type, tz_name='Asia/Yangon'):
    """
    The fixed logic: Use original message timestamp
    """
    tz = pytz.timezone(tz_name)
    msg_dt = datetime.fromtimestamp(msg_timestamp, tz)
    
    if date_type == "today":
        return msg_dt.strftime("%d-%m-%Y")
    else:
        return (msg_dt + timedelta(days=1)).strftime("%d-%m-%Y")

class TestMidnightFix(unittest.TestCase):
    def test_midnight_scenario(self):
        # Scenario: Message sent at 11:59 PM on May 1st
        # User clicks OK at 12:01 AM on May 2nd
        
        tz = pytz.timezone('Asia/Yangon')
        # May 1st, 2026, 23:59:00
        msg_dt = tz.localize(datetime(2026, 5, 1, 23, 59, 0))
        msg_ts = msg_dt.timestamp()
        
        # Even if "now" is May 2nd
        # The logic should still result in May 2nd for "tomorrow"
        target_date = calculate_target_date(msg_ts, "tomorrow")
        
        print(f"\n[Test] Msg Time: {msg_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"[Test] Target Date (Tomorrow): {target_date}")
        
        self.assertEqual(target_date, "02-05-2026")

    def test_today_scenario(self):
        # Scenario: Message sent at 9:00 AM on May 1st
        tz = pytz.timezone('Asia/Yangon')
        msg_dt = tz.localize(datetime(2026, 5, 1, 9, 0, 0))
        msg_ts = msg_dt.timestamp()
        
        target_date = calculate_target_date(msg_ts, "today")
        self.assertEqual(target_date, "01-05-2026")

if __name__ == "__main__":
    unittest.main()
