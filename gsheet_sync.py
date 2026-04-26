import gspread
from oauth2client.service_account import ServiceAccountCredentials
import db_manager
import time
from logger import log
import os

class GSheetSync:
    def __init__(self, credentials_file='credentials.json'):
        # API Access အတွက် Scope သတ်မှတ်ခြင်း
        self.scope = [
            "https://spreadsheets.google.com/feeds", 
            "https://www.googleapis.com/auth/drive"
        ]
        self.credentials_file = credentials_file

    def connect(self, sheet_url):
        """ Google Sheet သို့ ချိတ်ဆက်ခြင်း """
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(self.credentials_file, self.scope)
            client = gspread.authorize(creds)
            return client.open_by_url(sheet_url)
        except Exception as e:
            log.error(f"❌ GSheet Connection Failed: {e}")
            return None

    def sync_knowledge(self, sheet_url):
        """ Sheet အမည်များကို ဖတ်ပြီး Level ကို အလိုအလျောက် ခွဲခြားကာ Sync လုပ်ခြင်း """
        log.info("🔄 Starting Dynamic Multi-Level Sync...")
        workbook = self.connect(sheet_url)
        if not workbook:
            return False, "Google Sheet ချိတ်ဆက်မှု မအောင်မြင်ပါ။"

        data_to_db = []
        timestamp = int(time.time())
        messages = []

        try:
            # Sheet ထဲမှာရှိသမျှ Tab တွေ အကုန်လုံးကို ဆွဲယူမည်
            all_sheets = workbook.worksheets()
            
            for sheet in all_sheets:
                sheet_name = sheet.title
                name_lower = sheet_name.lower()
                
                # 💡 Sheet နာမည်ပေါ် မူတည်ပြီး Level သတ်မှတ်ခြင်း (Dynamic Checking)
                if "staff" in name_lower:
                    level = 3
                elif "os" in name_lower:
                    level = 2
                elif "customer" in name_lower:
                    level = 1
                else:
                    # သတ်မှတ်ထားသော Keyword မပါလျှင် ကျော်သွားမည် (ဥပမာ - Sheet4 လိုမျိုး)
                    log.info(f"⏭️ Skipping sheet: {sheet_name} (Keyword မပါဝင်ပါ)")
                    continue

                all_records = sheet.get_all_values()[1:] # Header ကို ကျော်မည်
                count = 0
                
                for row in all_records:
                    if len(row) >= 3:
                        category = row[0].strip()
                        question = row[1].strip()
                        answer = row[2].strip()
                        tags = row[3].strip() if len(row) > 3 else ""
                        
                        if question and answer:
                            data_to_db.append((category, question, answer, tags, level, timestamp))
                            count += 1
                            
                messages.append(f"- {sheet_name} (Level {level}): {count} ခု")

            if data_to_db:
                success = db_manager.upsert_knowledge_batch(data_to_db)
                if success:
                    details = "\n".join(messages)
                    return True, f"✅ အလိုအလျောက် Sync လုပ်ပြီးပါပြီ။ (စုစုပေါင်း: {len(data_to_db)} ခု)\n\nအသေးစိတ်:\n{details}"
            
            return False, "⚠️ Sync လုပ်ရန် ဒေတာအသစ် မရှိပါ။"

        except Exception as e:
            log.error(f"❌ Dynamic Sync Error: {e}")
            return False, f"အမှားတစ်ခုရှိနေပါတယ်: {str(e)}"

if __name__ == "__main__":
    # အစ်ကို့ရဲ့ URL ကို ဒီနေရာမှာ ပြန်ထည့်ပေးပါ
    test_url = "https://docs.google.com/spreadsheets/d/1edlzgaWiQ8RdykYkiyQlnLHUo7GGL7apvxXg9Crnxyc/edit?gid=0#gid=0"
    syncer = GSheetSync()
    status, msg = syncer.sync_knowledge(test_url)
    print(msg)