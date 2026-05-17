import gspread
from oauth2client.service_account import ServiceAccountCredentials
import db_manager
import time
from logger import log
import os

def _detect_columns(header):
    """
    Auto-detect column indices from header row.
    8-column format:
    OS Group ID | Mapping ID | Telegram Name | Website Name | Pickup TID | Error TID | Finance TID | Last Updated
    """
    mapping = {
        'chat_id': 0,       # Column A: OS Group ID → os_groups table
        'mapping_id': 1,    # Column B: Mapping ID → shop_mappings table
        'tg_name': 2,       # Column C: Telegram Name → os_groups.shop_name
        'web_name': 3,      # Column D: Website Name → shop_mappings.website_os_name
        'pickup_tid': 4,    # Column E: Pickup Topic ID
        'error_tid': 5,     # Column F: Error Topic ID
        'finance_tid': 6,   # Column G: Finance Topic ID
    }
    
    # Try to detect by header keywords
    for i, col in enumerate(header):
        col_lower = col.lower().strip()
        
        # OS Group ID (Column A) — for os_groups table
        if any(kw in col_lower for kw in ['os group id', 'os group']):
            mapping['chat_id'] = i
        # Mapping ID (Column B) — for shop_mappings table
        elif any(kw in col_lower for kw in ['mapping id', 'mapping']):
            mapping['mapping_id'] = i
        # Chat ID / Group ID (generic)
        elif any(kw in col_lower for kw in ['chat id', 'group id']):
            mapping['chat_id'] = i
        # Telegram Name
        elif any(kw in col_lower for kw in ['telegram name', 'tg name', 'telegram group name']):
            mapping['tg_name'] = i
        # Website Name
        elif any(kw in col_lower for kw in ['website name', 'website os name', 'web name', 'os name']):
            mapping['web_name'] = i
        # Pickup Topic
        elif any(kw in col_lower for kw in ['pickup', 'pick up']):
            mapping['pickup_tid'] = i
        # Error Topic
        elif any(kw in col_lower for kw in ['error']):
            mapping['error_tid'] = i
        # Finance Topic
        elif any(kw in col_lower for kw in ['finance', 'fin']):
            mapping['finance_tid'] = i
    
    return mapping

def _safe_get(row, index):
    """Safely get value from row at index, return '' if out of range."""
    if index < len(row):
        return row[index] or ''
    return ''

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

            all_records = sheet.get_all_values()
            if len(all_records) <= 1:
                return False, "⚠️ 'Shop Mappings' tab တွင် ဒေတာမရှိသေးပါ (header only)။"
            
            # Detect column mapping from header (handles both 7-col and 8-col formats)
            header = all_records[0]
            col_map = _detect_columns(header)
            log.info(f"📋 Detected columns: chat_id={col_map['chat_id']}, tg_name={col_map['tg_name']}, web_name={col_map['web_name']}")
            
            all_records = all_records[1:] # Header ကျော်မည်
            data_to_db = []
            skipped = 0
            
            for row in all_records:
                # Filter out completely empty rows
                if not any(cell.strip() for cell in row):
                    continue
                
                # Get values by detected column index
                chat_id_str = _safe_get(row, col_map['chat_id']).strip()
                if not chat_id_str:
                    continue
                
                tg_name = _safe_get(row, col_map['tg_name']).strip()
                web_name = _safe_get(row, col_map['web_name']).strip()
                p_tid = _safe_get(row, col_map['pickup_tid']).strip() or "0"
                e_tid = _safe_get(row, col_map['error_tid']).strip() or "0"
                f_tid = _safe_get(row, col_map['finance_tid']).strip() or "0"
                
                # Column B: Mapping ID (for shop_mappings table, usually same as chat_id)
                mapping_id_str = _safe_get(row, col_map['mapping_id']).strip()
                try:
                    mapping_id = int(mapping_id_str) if mapping_id_str else None
                except ValueError:
                    mapping_id = None
                
                try:
                    chat_id = int(chat_id_str)
                    data_to_db.append((chat_id, mapping_id, tg_name, web_name, p_tid, e_tid, f_tid))
                except ValueError:
                    log.warning(f"⚠️ Skipping row with invalid chat_id: '{chat_id_str}' (tg_name: {tg_name})")
                    skipped += 1

            if data_to_db:
                log.info(f"📥 Found {len(data_to_db)} row(s) in 'Shop Mappings' tab (skipped: {skipped}).")
                success = db_manager.update_unified_shop_data(data_to_db)
                if success:
                    # Sync ပြီးတာနဲ့ DB ထဲက Manual Register လုပ်ထားတာတွေကို Sheet ထဲ Append လုပ်ပေးမည်
                    appended_count = self.append_new_mappings_to_sheet(sheet_url)
                    msg = f"✅ Shop Data {len(data_to_db)} ခုကို Sync လုပ်ပြီးပါပြီ။"
                    if skipped > 0:
                        msg += f"\n⚠️ Chat ID မမှန်သော row {skipped} ခုကို ကျော်သွားပါသည်။"
                    if appended_count > 0:
                        msg += f"\n🆕 ဆိုင်အသစ် {appended_count} ခုကို Sheet ထဲသို့ ထည့်သွင်းပေးခဲ့ပါသည်။"
                    return True, msg
            else:
                log.warning(f"⚠️ No valid rows found in 'Shop Mappings' tab (all rows: {len(all_records)}, skipped: {skipped}).")
            
            return False, "⚠️ 'Shop Mappings' tab တွင် မှန်ကန်သော Chat ID ပါဝင်သည့် ဒေတာမရှိပါ။ Chat ID column (A) ကို စစ်ဆေးပေးပါ။"

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
                # 8-col format: OS Group ID | Mapping ID | Telegram Name | Website Name | Pickup TID | Error TID | Finance TID | Last Updated
                rows.append([str(chat_id), str(chat_id), tg_name, web_name, str(p_tid), str(e_tid), str(f_tid), updated_str])
                chat_ids.append(chat_id)

            if rows:
                sheet.append_rows(rows, value_input_option='USER_ENTERED')
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
                sheet = workbook.add_worksheet(title="Shop Mappings", rows="1000", cols="8")

            header = ["OS Group ID", "Mapping ID", "Telegram Name", "Website Name", "Pickup TID", "Error TID", "Finance TID", "Last Updated"]
            sheet.clear()
            sheet.update('A1', [header])

            mappings = db_manager.get_unified_shop_data()
            rows = []
            chat_ids = []
            for m in mappings:
                chat_id, tg_name, web_name, p_tid, e_tid, f_tid, updated_at = m
                updated_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(updated_at)) if updated_at else "-"
                rows.append([str(chat_id), str(chat_id), tg_name, web_name, str(p_tid), str(e_tid), str(f_tid), updated_str])
                chat_ids.append(chat_id)

            if rows:
                rows.sort(key=lambda x: x[3])  # sort by Telegram Name (Column C → index 3)
                sheet.update(f'A2:H{len(rows)+1}', rows)
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