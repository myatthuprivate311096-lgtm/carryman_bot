# Version: 1.1 — Central AI / tracking configuration (env-driven)
"""
Topic keywords, tracking URLs, and sandbox IDs for topic-gated /ai routing.
"""
import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

SANDBOX_CHAT_ID = int(os.getenv("SANDBOX_CHAT_ID", "-1003539520778"))

TRACKING_LIST_URL = os.getenv(
    "TRACKING_LIST_URL",
    "https://www.carrymanexpress.com/trackinglist",
)

# delivery_info_topic — Google Sheet grounded Q&A
DELIVERY_TOPIC_MARKERS = (
    "ပို့ခ", "တန်ဆာခ", "cod", "gate drop", "home delivery",
    "ဘယ်မြို့", "မြို့နယ်", "ပို့လား", "ပို့နိုင်", "ရရလား",
    "ယူလဲ", " kg", "ပို့ဆောင်ခ", "ဘယ်လောက်ယူ", "ဘယ်နေရာ",
    "delivery fee", "location", "ပို့ဆောင်", "deliver", "delivery",
    "township", "မြို့", "ပြန်ရလား", "ပို့လို့", "ပို့ရ",
)

# status_arrival_topic — live website tracking
STATUS_TOPIC_MARKERS = (
    "ဝေးရောက်", "ရောက်ပြီ", "ရောက်မှာလား", "ရောက်ပြီလား", "ရောက်မလား",
    "ရောက်ပြီးပြီလား", "ရောက်ပြီးလား", "ရောက်ပါပြီလား", "ရောက်ပါသလား",
    "tracking", "track", "status", "onway", "delivered", "pending",
    "အော်ဒါ", "order", "ပစ္စည်း", "parcel", "way", "waybill", "voucher",
)

# Google Sheet tab name hints (gsheet_sync level mapping)
SHEET_LEVEL_CUSTOMER_KEYWORDS = ("inquire", "customer")
SHEET_LEVEL_OS_KEYWORDS = ("os",)
SHEET_LEVEL_STAFF_KEYWORDS = ("staff",)
SHEET_TONE_KEYWORDS = ("tone", "example")
