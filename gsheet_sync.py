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

    def sync_shop_mappings(self, sheet_url):
        """ Shop Mapping များကို Google Sheet မှ Database သို့ Sync လုပ်ခြင်း """
        log.info("🔄 Syncing Shop Mappings from GSheet...")
        workbook = self.connect(sheet_url)
        if not workbook:
            return False, "Google Sheet ချိတ်ဆက်မှု မအောင်မြင်ပါ။"

        try:
            # 'Shop Mappings' ဆိုသည့် Tab ကို ရှာမည်
            try:
                sheet = workbook.worksheet("Shop Mappings")
            except gspread.exceptions.WorksheetNotFound:
                return False, "⚠️ 'Shop Mappings' tab ကို ရှာမတွေ့ပါ။"

            all_records = sheet.get_all_values()[1:] # Header ကျော်မည်
            data_to_db = []
            
            for row in all_records:
                if len(row) >= 6:
                    chat_id_str = row[0].strip()
                    tg_name = row[1].strip()
                    web_name = row[2].strip()
                    p_tid = row[3].strip()
                    e_tid = row[4].strip()
                    f_tid = row[5].strip()
                    
                    if chat_id_str:
                        try:
                            chat_id = int(chat_id_str)
                            data_to_db.append((chat_id, tg_name, web_name, p_tid, e_tid, f_tid))
                        except ValueError:
                            continue

            if data_to_db:
                success = db_manager.update_unified_shop_data(data_to_db)
                if success:
                    # Sync ပြီးတာနဲ့ DB ထဲက Manual Register လုပ်ထားတာတွေကို Sheet ထဲ Append လုပ်ပေးမည်
                    appended_count = self.append_new_mappings_to_sheet(sheet_url)
                    msg = f"✅ Shop Data {len(data_to_db)} ခုကို Sync လုပ်ပြီးပါပြီ။"
                    if appended_count > 0:
                        msg += f"\n🆕 ဆိုင်အသစ် {appended_count} ခုကို Sheet ထဲသို့ ထည့်သွင်းပေးခဲ့ပါသည်။"
                    return True, msg
            
            return False, "⚠️ Sync လုပ်ရန် ဒေတာအသစ် မရှိပါ။"

        except Exception as e:
            log.error(f"❌ Shop Mapping Sync Error: {e}")
            return False, f"အမှားတစ်ခုရှိနေပါတယ်: {str(e)}"

    def append_new_mappings_to_sheet(self, sheet_url):
        """ Manual Register လုပ်ထားသော ဆိုင်အသစ်များကို Sheet အောက်ဆုံးတွင် သွားပေါင်းပေးခြင်း """
        log.info("📤 Appending New Shop Mappings to GSheet...")
        workbook = self.connect(sheet_url)
        if not workbook: return 0

        try:
            sheet = workbook.worksheet("Shop Mappings")
            new_data = db_manager.get_manual_register_data()
            if not new_data: return 0

            rows = []
            chat_ids = []
            for m in new_data:
                chat_id, tg_name, web_name, p_tid, e_tid, f_tid, updated_at = m
                updated_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(updated_at)) if updated_at else "-"
                rows.append([str(chat_id), tg_name, web_name, str(p_tid), str(e_tid), str(f_tid), updated_str])
                chat_ids.append(chat_id)

            if rows:
                sheet.append_rows(rows)
                db_manager.mark_os_groups_as_synced(chat_ids)
                return len(rows)
            return 0
        except Exception as e:
            log.error(f"❌ Append Mappings Error: {e}")
            return 0

    def export_mappings_to_sheet(self, sheet_url):
        """ Database ထဲရှိ Mapping အားလုံးကို Sheet ထဲသို့ အကုန်ပြန်ရေးခြင်း (Full Overwrite) """
        log.info("📤 Full Exporting Shop Mappings to GSheet...")
        workbook = self.connect(sheet_url)
        if not workbook: return False, "Connection Failed"

        try:
            try:
                sheet = workbook.worksheet("Shop Mappings")
            except gspread.exceptions.WorksheetNotFound:
                sheet = workbook.add_worksheet(title="Shop Mappings", rows="1000", cols="7")

            header = ["Chat ID", "Telegram Group Name", "Website OS Name", "Pickup Topic ID", "Error Topic ID", "Finance Topic ID", "Last Updated"]
            sheet.clear()
            sheet.update('A1', [header])

            mappings = db_manager.get_unified_shop_data()
            rows = []
            chat_ids = []
            for m in mappings:
                chat_id, tg_name, web_name, p_tid, e_tid, f_tid, updated_at = m
                updated_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(updated_at)) if updated_at else "-"
                rows.append([str(chat_id), tg_name, web_name, str(p_tid), str(e_tid), str(f_tid), updated_str])
                chat_ids.append(chat_id)

            if rows:
                rows.sort(key=lambda x: x[1])
                sheet.update(f'A2:G{len(rows)+1}', rows)
                db_manager.mark_os_groups_as_synced(chat_ids)
                return True, f"✅ Shop Data {len(rows)} ခုကို GSheet သို့ Export လုပ်ပြီးပါပြီ။"
            return False, "⚠️ Export လုပ်ရန် ဒေတာ မရှိပါ။"
        except Exception as e:
            log.error(f"❌ Export Mappings Error: {e}")
            return False, str(e)

if __name__ == "__main__":
    # အစ်ကို့ရဲ့ URL ကို ဒီနေရာမှာ ပြန်ထည့်ပေးပါ
    test_url = "https://docs.google.com/spreadsheets/d/1edlzgaWiQ8RdykYkiyQlnLHUo7GGL7apvxXg9Crnxyc/edit?gid=0#gid=0"
    syncer = GSheetSync()
    status, msg = syncer.sync_knowledge(test_url)
    print(msg)