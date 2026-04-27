import unittest
from datetime import datetime
import pytz

def get_date_type_and_action(current_hour, current_minute, ai_date_type=None):
    """
    Simplified version of the logic in auto_pickup.py for testing
    """
    current_time = current_hour * 100 + current_minute
    
    if ai_date_type == "tomorrow":
        return "tomorrow", "direct"
    elif 1100 <= current_time < 1500:
        return "today", "staff_decision"
    elif current_time >= 1500:
        return "tomorrow", "direct_with_late_msg"
    else:
        return "today", "direct"

class TestPickupTimeLogic(unittest.TestCase):
    def test_before_11am(self):
        # 10:59 AM
        date_type, action = get_date_type_and_action(10, 59)
        self.assertEqual(date_type, "today")
        self.assertEqual(action, "direct")

    def test_at_11am(self):
        # 11:00 AM - Should be Staff Decision
        date_type, action = get_date_type_and_action(11, 0)
        self.assertEqual(action, "staff_decision")

    def test_before_3pm(self):
        # 2:59 PM
        date_type, action = get_date_type_and_action(14, 59)
        self.assertEqual(action, "staff_decision")

    def test_at_3pm(self):
        # 3:00 PM - Should be Tomorrow
        date_type, action = get_date_type_and_action(15, 0)
        self.assertEqual(date_type, "tomorrow")
        self.assertEqual(action, "direct_with_late_msg")

    def test_after_3pm(self):
        # 4:00 PM
        date_type, action = get_date_type_and_action(16, 0)
        self.assertEqual(date_type, "tomorrow")
        self.assertEqual(action, "direct_with_late_msg")

    def test_explicit_tomorrow(self):
        # 9:00 AM but user says tomorrow
        date_type, action = get_date_type_and_action(9, 0, ai_date_type="tomorrow")
        self.assertEqual(date_type, "tomorrow")
        self.assertEqual(action, "direct")

if __name__ == "__main__":
    unittest.main()
