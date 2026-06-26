# Version: 1.1 — Central AI / tracking configuration (env-driven)
"""
Topic keywords, tracking URLs, and sandbox IDs for topic-gated /ai routing.
"""
import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

SANDBOX_CHAT_ID = int(os.getenv("SANDBOX_CHAT_ID", "-1003539520778"))

# Alert Central Group target topics (878 = Pickup/CS Command Center; 0 = General)
ADMIN_ALERT_TOPIC_ID = int(os.getenv("ADMIN_ALERT_TOPIC_ID", "878"))
ALERT_TOPIC_CS = int(os.getenv("ALERT_TOPIC_CS", str(ADMIN_ALERT_TOPIC_ID)))
ALERT_TOPIC_FIN = int(os.getenv("ALERT_TOPIC_FIN", "35"))
ALERT_TOPIC_ERROR = int(os.getenv("ALERT_TOPIC_ERROR", "37"))
ALERT_TOPIC_DE = int(os.getenv("ALERT_TOPIC_DE", "6621"))

COMPLAINT_SLA_MINUTES = int(os.getenv("COMPLAINT_SLA_MINUTES", "8"))
INQUIRY_SLA_MINUTES = int(os.getenv("INQUIRY_SLA_MINUTES", "15"))
COMPLAINT_DEBOUNCE_SECONDS = int(os.getenv("COMPLAINT_DEBOUNCE_SECONDS", "45"))
MIRROR_ESCALATION_MINUTES = int(os.getenv("MIRROR_ESCALATION_MINUTES", "30"))

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

# /newgroup General topic — VPS folder assets/newgroup_general/ (1.jpg + 2.jpg)
NEWGROUP_GENERAL_IMAGES_DIR = os.path.join(BASE_DIR, "assets", "newgroup_general")
_NEWGROUP_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def get_newgroup_general_images():
    """Slot 1/2 images from VPS folder; replace files anytime (no restart)."""
    return [p for slot in (1, 2) if (p := get_newgroup_general_image(slot))]


def get_newgroup_general_image(slot):
    """Single slot image path (1=price list, 2=terms) or None."""
    for ext in _NEWGROUP_IMAGE_EXTENSIONS:
        candidate = os.path.join(NEWGROUP_GENERAL_IMAGES_DIR, f"{slot}{ext}")
        if os.path.isfile(candidate):
            return candidate
    return None


NEWGROUP_GENERAL_TOPIC_ID = 1
NEWGROUP_PRICE_CAPTION = "Price List လေးပါခင်ဗျာ"
NEWGROUP_TOS_CAPTION = (
    "လူကြီးမင်းခင်ဗျာ၊ CarryMan Delivery ၏ ဝန်ဆောင်မှု စည်းကမ်းချက်များကို "
    "ဖတ်ရှုနားလည်ပြီး သဘောတူပါက အောက်ပါ 'နားလည်သဘောတူပါသည်' ခလုတ်ကို "
    "နှိပ်ပေးပါရန် မေတ္တာရပ်ခံအပ်ပါသည်။"
)
NEWGROUP_TOS_BUTTON = "နားလည်သဘောတူပါသည်"
NEWGROUP_TOS_THANKYOU = (
    "ကျေးဇူးတင်ပါတယ်ခင်ဗျာ။ အကောင်းဆုံး ဝန်ဆောင်မှုများ ပေးအပ်နိုင်ရန် "
    "အမြဲကြိုးစားနေပါသည်။ အဆင်မပြေမှုတစ်စုံတစ်ရာ ရှိပါက အောက်ပါ Link "
    "မှတစ်ဆင့် Management Team သို့ အချိန်မရွေး တိုက်ရိုက် အကြံပြုတိုင်ကြားနိုင်ပါသည်။\n"
    "https://forms.gle/8Rp7QvgK3MfU67E57"
)
