# Version: 1.0 (System Logger & Error Tracker)
import logging
from logging.handlers import RotatingFileHandler
import os

# မှတ်တမ်းများ သိမ်းရန် logs ဆိုသော Folder ကို အလိုအလျောက် တည်ဆောက်မည်
if not os.path.exists('logs'):
    os.makedirs('logs')

# Logger စနစ်ကို စတင်ခြင်း
log = logging.getLogger("CarryManAI")
log.setLevel(logging.INFO)

# ၁။ ဖိုင်ထဲသို့ သိမ်းဆည်းမည့်စနစ် (RotatingFileHandler)
# ဖိုင်အရွယ်အစား 5MB ပြည့်သွားတိုင်း ဖိုင်အသစ်တစ်ခု အလိုအလျောက်ခွဲမည် (အများဆုံး ၅ ခုအထိ သိမ်းမည်)
file_handler = RotatingFileHandler(
    'logs/carryman_system.log', 
    maxBytes=5 * 1024 * 1024, # 5 MB
    backupCount=5, 
    encoding='utf-8'
)
# ဖိုင်ထဲတွင် ရေးမှတ်မည့် ပုံစံ (ဥပမာ - 2026-04-14 10:30:00 | ERROR | alert_system | Connection timeout)
file_formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(module)s | %(message)s', datefmt='%Y-%m-%d %I:%M:%S %p')
file_handler.setFormatter(file_formatter)

# ၂။ PowerShell (Terminal) တွင် မြင်ရမည့်စနစ်
console_handler = logging.StreamHandler()
console_formatter = logging.Formatter('👉 %(levelname)s [%(module)s]: %(message)s')
console_handler.setFormatter(console_formatter)

# Handler များကို Logger ထဲသို့ ပေါင်းထည့်ခြင်း
if not log.handlers:
    log.addHandler(file_handler)
    log.addHandler(console_handler)