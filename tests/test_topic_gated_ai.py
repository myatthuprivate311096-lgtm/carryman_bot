"""Topic-gated /ai + tracking filter plan regression tests."""
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import config
import db_manager
from main_router import _classify_ai_topic
from modules import check_order


class TestTopicGate(unittest.TestCase):
    def test_status_topic_waybill(self):
        self.assertEqual(_classify_ai_topic("260615028 ရောက်ပြီလား"), "status_arrival")

    def test_status_topic_phone(self):
        self.assertEqual(_classify_ai_topic("09787176081 ရောက်မှာလား"), "status_arrival")

    def test_delivery_topic_fee(self):
        self.assertEqual(_classify_ai_topic("မော်လမြိုင်ပို့လို့ရလား"), "delivery_info")

    def test_delivery_wins_when_both_markers(self):
        """Delivery + status words → delivery_info (fee question with arrival word)."""
        topic = _classify_ai_topic("မော်လမြိုင်ပို့ခနဲ့ ရောက်မှာလား")
        self.assertEqual(topic, "delivery_info")


class TestTrackingHelpers(unittest.TestCase):
    def test_extract_waybill_skips_phone(self):
        self.assertEqual(check_order.extract_waybill("09 9787176081"), None)
        self.assertEqual(check_order.extract_waybill("ဝေး 260615028"), "260615028")

    def test_extract_phone(self):
        self.assertEqual(check_order.extract_phone("09772706624 ဒီဝေးအခြေအနေ"), "09772706624")

    def test_effective_tracking_query_uses_tail(self):
        merged = "09787176081 ရောက်ပြီလား — follow-up: 09772706624 ဒီဝေး"
        self.assertEqual(check_order._effective_tracking_query(merged), "09772706624 ဒီဝေး")

    def test_new_phone_skips_context_merge(self):
        from main_router import _resolve_ai_search_query
        uid, cid = 777, -1003539520778
        db_manager.append_ai_conversation_turn(
            uid, cid, "09787176081 ရောက်ပြီလား", "waybill 260615001",
            topic="status_arrival", waybill="260615001",
        )
        q = "09772706624 ဒီဝေးအခြေအနေပြောပေးပါ"
        sq, _, clarify = _resolve_ai_search_query(uid, cid, q, 1)
        self.assertEqual(sq, q)
        self.assertFalse(clarify)
        parsed = check_order._parse_status_query(sq)
        self.assertIn("09772706624", parsed["search_terms"])

    def test_parse_api_collector_vs_deliverer(self):
        sample = [{
            "itemDto": {
                "voucherCode": "260616002",
                "status": "COLLECTED",
                "phone": "09772706624",
                "customerName": "Min Khant",
                "receivedDate": "16-06-2026",
                "pickupDate": "16-06-2026",
                "riderAccountDto": {"profileName": "de rider"},
                "assignRiderAccountDto": {"userAccountId": 0},
                "townshipDto": {"townshipName": "Botahtaung"},
                "orderDto": {"osAccountDto": {"profileName": "Tina's Fashion Bar"}},
            },
            "customerGet": 54500,
        }]
        orders = check_order._parse_api_tracking_results(sample)
        self.assertEqual(orders[0]["collector"], "de rider")
        self.assertEqual(orders[0]["deliverer"], "")
        reply = check_order.format_tracking_reply(orders)
        self.assertIn("16-06-2026 ရက်နေ့လေးမှာ စာရင်းသွင်းပြီးပါတယ်ခင်ဗျ။", reply)
        self.assertNotIn("Collector", reply)
        self.assertNotIn("Deliverer", reply)

    def test_phone_search_skips_os_name(self):
        parsed = check_order._parse_status_query("09772706624 status")
        self.assertIn("09772706624", parsed["search_terms"])
        self.assertIsNone(parsed.get("waybill"))
        class _Msg:
            message_id = 1

        self.assertFalse(check_order.handle(None, _Msg()))

    def test_status_conversational_with_date(self):
        for status, expected in [
            ("COLLECTED", "10-06-2026 ရက်နေ့လေးမှာ စာရင်းသွင်းပြီးပါတယ်ခင်ဗျ။"),
            ("FINISHED", "10-06-2026 ရက်နေ့လေးမှာ ရောက်ပြီးပါတယ်ခင်ဗျ။"),
            ("ONWAY", "10-06-2026 ရက်နေ့လေးကတည်းက ခရီးပေါ်မှာ ရှိနေပါတယ်ခင်ဗျ။"),
        ]:
            line = check_order._format_status_conversational(status, "", "10-06-2026")
            self.assertEqual(line, expected, msg=status)

    def test_no_multi_waybill_footnote(self):
        orders = [
            {"waybill": "260615001", "status": "ASSIGNED", "receiver": "A", "township": "Mandalay",
             "pickup_date": "16-06-2026", "received_date": "16-06-2026",
             "os_name": "OS", "customer_get": 1000, "remark": ""},
            {"waybill": "260608153", "status": "FINISHED", "pickup_date": "10-06-2026", "remark": ""},
        ]
        reply = check_order.format_tracking_reply(orders)
        self.assertNotIn("နောက်ထပ် waybill", reply)
        self.assertNotIn("မှတ်ချက်:", reply)

    def test_remark_shown_only_when_present(self):
        orders = [{
            "waybill": "260615001", "status": "ASSIGNED", "receiver": "A", "township": "Mandalay",
            "pickup_date": "16-06-2026", "received_date": "16-06-2026", "status_date": "16-06-2026",
            "remark": "Fragile",
            "customer_get": 0,
        }]
        reply = check_order.format_tracking_reply(orders)
        self.assertIn("မှတ်ချက်: Fragile", reply)

    def test_finished_pickup_and_delivered_dates(self):
        sample = [{
            "itemDto": {
                "voucherCode": "260607582",
                "status": "FINISHED",
                "customerName": "Lu Dee",
                "receivedDate": "11-06-2026",
                "deliveredDate": "10-06-2026",
                "remark": "ccos9/10d",
                "townshipDto": {"townshipName": "Insein"},
                "orderDto": {"receivedDate": "07-06-2026", "osAccountDto": {"profileName": "Os"}},
            },
            "customerGet": 35500,
            "customerPaid": 35500,
        }]
        orders = check_order._parse_api_tracking_results(sample)
        reply = check_order.format_tracking_reply(orders)
        self.assertIn("Pickup Date: 07-06-2026", reply)
        self.assertIn("10-06-2026 ရက်နေ့လေးမှာ ရောက်ပြီးပါတယ်ခင်ဗျ။", reply)
        self.assertNotIn("11-06-2026", reply)
        self.assertIn("ဆက်သွယ်မရကြောင်း (9-Jun-2026)", reply)
        self.assertNotIn("10ရက်နေ့ကို deli", reply)

    def test_pickup_date_from_waybill(self):
        self.assertEqual(check_order._pickup_date_from_waybill("260607582"), "07-06-2026")

    def test_waybill_os_mismatch_message(self):
        orders = [{
            "waybill": "260615001", "status": "ASSIGNED", "receiver": "A", "township": "Mandalay",
            "pickup_date": "16-06-2026", "received_date": "16-06-2026", "os_name": "Skincare Next Step",
            "customer_get": 145000,
        }]
        owned = [o for o in orders if check_order._os_names_match(o.get("os_name"), "Tina's Fashion Bar")]
        self.assertFalse(owned)

    def test_phone_os_mismatch_message(self):
        orders = [{
            "waybill": "260615001", "status": "ASSIGNED", "receiver": "A", "township": "Mandalay",
            "pickup_date": "16-06-2026", "received_date": "16-06-2026", "os_name": "Skincare Next Step",
            "phone": "09772706624", "customer_get": 145000,
        }]
        owned = [o for o in orders if check_order._os_names_match(o.get("os_name"), "Tina's Fashion Bar")]
        self.assertFalse(owned)
        self.assertIn("Way Bill နံပါတ်လေးသိရမလားခင်ဗျ", check_order._PHONE_NOT_IN_OS_MESSAGE)

    def test_phone_os_match_keeps_order(self):
        orders = [{
            "waybill": "260616002", "status": "COLLECTED", "receiver": "Min Khant",
            "township": "Botahtaung", "pickup_date": "16-06-2026", "received_date": "16-06-2026",
            "os_name": "Tina's Fashion Bar", "phone": "09772706624", "customer_get": 54500,
        }]
        owned = [o for o in orders if check_order._os_names_match(o.get("os_name"), "Tina's Fashion Bar")]
        reply = check_order.format_tracking_reply(owned)
        self.assertIn("260616002", reply)
        self.assertNotIn(check_order._PHONE_NOT_IN_OS_MESSAGE, reply)

    def test_shop_mapping_short_chat_id(self):
        self.assertEqual(
            db_manager.get_shop_mapping(-1003806753663),
            db_manager.get_shop_mapping(3806753663),
        )

    def test_os_name_normalize_apostrophe(self):
        self.assertTrue(check_order._os_names_match("Pan's Catalogue", "Pans Catalogue"))

    def test_config_tracking_url(self):
        self.assertIn("tracking", config.TRACKING_LIST_URL.lower())


class TestDeliveryGrounding(unittest.TestCase):
    def test_mawlamyine_city_not_island(self):
        loc = db_manager.search_location_delivery("မော်လမြိုင်ပို့လို့ရလား", 1)
        self.assertIsNotNone(loc)
        self.assertIn("191", loc)

    def test_search_delivery_knowledge_combines(self):
        ctx = db_manager.search_delivery_knowledge("မော်လမြိုင်", 1)
        self.assertIsNotNone(ctx)
        self.assertIn("Location ID", ctx)


if __name__ == "__main__":
    unittest.main()
