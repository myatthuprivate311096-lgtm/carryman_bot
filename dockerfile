# 1. Base Image: Python 3.10-slim version ကို အခြေခံပုံရိပ်အဖြစ်အသုံးပြုပါမယ်။
FROM python:3.10-slim

# 2. Environment Variables: Python output ကို buffer မလုပ်ဖို့ သတ်မှတ်ပေးခြင်း
ENV PYTHONUNBUFFERED 1

# 3. Working Directory: Container ထဲမှာ အလုပ်လုပ်မယ့် directory ကို သတ်မှတ်ခြင်း
WORKDIR /app

# 4. Install Dependencies: လိုအပ်တဲ့ library တွေကို install လုပ်ရန်အတွက် requirements.txt ကို အရင်ကူးထည့်ပါမယ်။
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4.5 Install Playwright Chromium Browser (required for Auto Pickup automation)
RUN playwright install chromium --with-deps

# 5. Copy Project Files: လက်ရှိ directory ထဲက ဖိုင်အားလုံးကို container ထဲက /app directory သို့ ကူးထည့်ပါမယ်။
COPY . .

# 6. Run the Application: Container စတင် run တဲ့အခါ main_bot.py ကို အလုပ်လုပ်စေဖို့ command သတ်မှတ်ခြင်း
CMD ["python", "main_bot.py"]
