import unittest
from datetime import datetime
import pytz

def get_date_type_and_action(current_hour, current_minute, ai_date_type=None):
    """
    Simplified version of the logic in auto_pickup.py for testing
    """
    current_time = current_hour * 100 + current_minute
    
    # Time-based Date Logic (Strict)
    if 1 <= current_time <= 1100:
        return "today", "direct"
    elif 1501 <= current_time <= 2359 or current_time == 0:
        return "tomorrow", "direct"
    else: # 11:01 AM to 03:00 PM
        if ai_date_type == "tomorrow":
            return "tomorrow", "direct"
        else:
            return "today", "staff_decision"

class TestPickupTimeLogic(unittest.TestCase):
    def test_before_11am(self):
        # 10:59 AM - Should be Today
        date_type, action = get_date_type_and_action(10, 59)
        self.assertEqual(date_type, "today")
        self.assertEqual(action, "direct")

    def test_at_11am(self):
        # 11:00 AM - Should be Today (Strict)
        date_type, action = get_date_type_and_action(11, 0)
        self.assertEqual(date_type, "today")
        self.assertEqual(action, "direct")

    def test_after_11am(self):
        # 11:01 AM - Should be Staff Decision
        date_type, action = get_date_type_and_action(11, 1)
        self.assertEqual(action, "staff_decision")

    def test_before_3pm(self):
        # 2:59 PM - Should be Staff Decision
        date_type, action = get_date_type_and_action(14, 59)
        self.assertEqual(action, "staff_decision")

    def test_at_3pm(self):
        # 3:00 PM - Should be Staff Decision (Strict)
        date_type, action = get_date_type_and_action(15, 0)
        self.assertEqual(action, "staff_decision")

    def test_after_3pm(self):
        # 3:01 PM - Should be Tomorrow
        date_type, action = get_date_type_and_action(15, 1)
        self.assertEqual(date_type, "tomorrow")
        self.assertEqual(action, "direct")

    def test_midnight(self):
        # 12:00 AM (00:00) - Should be Tomorrow
        date_type, action = get_date_type_and_action(0, 0)
        self.assertEqual(date_type, "tomorrow")
        self.assertEqual(action, "direct")

    def test_early_morning(self):
        # 12:01 AM (00:01) - Should be Today
        date_type, action = get_date_type_and_action(0, 1)
        self.assertEqual(date_type, "today")
        self.assertEqual(action, "direct")

    def test_explicit_tomorrow_during_staff_hours(self):
        # 12:00 PM but user says tomorrow
        date_type, action = get_date_type_and_action(12, 0, ai_date_type="tomorrow")
        self.assertEqual(date_type, "tomorrow")
        self.assertEqual(action, "direct")

    def test_explicit_tomorrow_during_today_hours(self):
        # 9:00 AM but user says tomorrow - Should still be TODAY (Strict)
        date_type, action = get_date_type_and_action(9, 0, ai_date_type="tomorrow")
        self.assertEqual(date_type, "today")
        self.assertEqual(action, "direct")

if __name__ == "__main__":
    unittest.main()
