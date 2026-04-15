from telethon.sync import TelegramClient
import os
from dotenv import load_dotenv

# .env ဖိုင်ကို utf-8 ဖြင့် ဖတ်ရန် (Encoding Error မတက်စေရန်)
load_dotenv()

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")

if not API_ID or not API_HASH:
    print("❌ Error: .env ဖိုင်ထဲတွင် API_ID နှင့် API_HASH မရှိပါ။")
    exit()

print("🚀 Userbot Login စတင်နေပါပြီ...")
# carryman.session ဆိုသည့် ဖိုင်အသစ် တည်ဆောက်မည်
client = TelegramClient('carryman', int(API_ID), API_HASH)

# ဖုန်းနံပါတ် တောင်းပြီး Login ဝင်မည်
client.start()

print("✅ Login အောင်မြင်ပါပြီ! carryman.session ဖိုင်ကို သိမ်းဆည်းလိုက်ပါပြီ။")
client.disconnect()