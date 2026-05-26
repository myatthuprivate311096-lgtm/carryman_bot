import gspread
from google.oauth2.service_account import Credentials
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
    def __init__(self, credentials_file='credentials.json', bot=None):
        # API Access အတွက် Scope သတ်မှတ်ခြင်း (google-auth format)
        self.scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        self.credentials_file = credentials_file
        self.bot = bot  # Optional: Telegram bot instance for resolving group titles

    def _resolve_unknown_shops(self, data_list):
        """ 'Unknown Shop' များကို Telegram API မှ group title အမှန်ဖြင့် resolve လုပ်ခြင်း """
        if not self.bot:
            return data_list
        
        resolved = []
        for m in data_list:
            chat_id, tg_name = m[0], m[1]
            if tg_name == "Unknown Shop" or not tg_name or tg_name.strip() == "":
                try:
                    chat_info = self.bot.get_chat(chat_id)
                    real_title = chat_info.title if chat_info.title else tg_name
                    if real_title and real_title != "Unknown Shop":
                        log.info(f"🔍 Resolved group title: {chat_id} → \"{real_title}\"")
                        db_manager.update_os_group_shop_name(chat_id, real_title)
                        # Replace tg_name in the tuple
                        m = (m[0], real_title) + m[2:]
                except Exception as e:
                    log.warning(f"⚠️ Could not resolve title for {chat_id}: {e}")
            resolved.append(m)
        return resolved

    def connect(self, sheet_url):
        """ Google Sheet သို့ ချိတ်ဆက်ခြင်း """
        try:
            if not os.path.exists(self.credentials_file):
                log.error(f"❌ Credentials file not found: {self.credentials_file}")
                return None
            creds = Credentials.from_service_account_file(self.credentials_file, scopes=self.scope)
            client = gspread.authorize(creds)
            return client.open_by_url(sheet_url)
        except FileNotFoundError as e:
            log.error(f"❌ GSheet Connection Failed - File not found: {self.credentials_file}")
            return None
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            if "invalid_grant" in error_msg.lower() or "invalid jwt" in error_msg.lower():
                log.error(f"❌ GSheet Auth Failed: Service Account Key is invalid/revoked. Regenerate credentials.json from Google Cloud Console.")
            elif "not found" in error_msg.lower():
                log.error(f"❌ GSheet Not Found: Check if the sheet URL is correct and shared with the service account.")
            else:
                log.error(f"❌ GSheet Connection Failed [{error_type}]: {error_msg}")
            return None

    @staticmethod
    def _is_delivery_flag_value(value):
        """Location table delivery flags (Yes/No) ဖြစ်မဖြစ် စစ်ဆေးခြင်း。"""
        val = str(value or "").strip().lower()
        if not val:
            return True
        return val in ("yes", "no", "y", "n")

    @staticmethod
    def _format_location_delivery_row(row):
        """
        Location/Delivery table row (numeric ID in col A) ကို AI-readable text အဖြစ် ပြောင်းခြင်း。
        Expected columns (Customer Inquire tab):
        ID, State, City, Township, MM_Name, HomeDel, COD, GateDrop,
        BaseFee, BaseKg, ExtraFee, Days, Special_Remarks, Rider Name
        """
        if len(row) < 8 or not str(row[0]).strip().isdigit():
            return None

        # Staff/Billing sheets also use numeric IDs — require Yes/No delivery flags in cols F-H
        for idx in (5, 6, 7):
            if idx < len(row) and not GSheetSync._is_delivery_flag_value(row[idx]):
                return None

        loc_id = row[0].strip()
        state = row[1].strip() if len(row) > 1 else ""
        city = row[2].strip() if len(row) > 2 else ""
        township = row[3].strip() if len(row) > 3 else ""
        mm_name = row[4].strip() if len(row) > 4 else ""
        home = row[5].strip() if len(row) > 5 else ""
        cod = row[6].strip() if len(row) > 6 else ""
        gate = row[7].strip() if len(row) > 7 else ""
        base_fee = row[8].strip() if len(row) > 8 else ""
        base_kg = row[9].strip() if len(row) > 9 else ""
        extra_fee = row[10].strip() if len(row) > 10 else ""
        days = row[11].strip() if len(row) > 11 else ""
        special_remarks = row[12].strip() if len(row) > 12 else ""
        rider_name = row[13].strip() if len(row) > 13 else ""

        if not (state or city or township):
            return None

        question = f"{state} | {city} | {township}"
        answer = (
            f"Township: {township} | City: {city} | State: {state} ({mm_name})\n"
            f"Home Delivery: {home} | COD: {cod} | Gate Drop: {gate}\n"
            f"Delivery Fee: {base_fee} MMK (base {base_kg}kg, extra {extra_fee} MMK per kg)\n"
            f"Estimated Days: {days}"
        )
        if special_remarks:
            answer += f"\nSpecial Remarks: {special_remarks}"
        if rider_name:
            answer += f"\nRider Name: {rider_name}"

        tag_parts = [mm_name, township, city, state, loc_id, "delivery", "location"]
        if special_remarks:
            tag_parts.append(special_remarks)
        if rider_name:
            tag_parts.append(rider_name)
        tags = "|".join(filter(None, tag_parts))
        return loc_id, question, answer, tags

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

                # Staff HR sheet — not for AI knowledge_base (prevents numeric-ID corruption)
                if "staff" in name_lower and "info" in name_lower:
                    log.info(f"⏭️ Skipping sheet: {sheet_name} (Staff HR data — excluded from knowledge_base)")
                    continue
                
                # 💡 Sheet နာမည်ပေါ် မူတည်ပြီး Level သတ်မှတ်ခြင်း
                # Customer Inquire sheet MUST be Level 1 (checked before OS keyword)
                if "staff" in name_lower:
                    level = 3
                elif "inquire" in name_lower or "customer" in name_lower:
                    level = 1
                elif "os" in name_lower:
                    level = 2
                else:
                    # သတ်မှတ်ထားသော Keyword မပါလျှင် ကျော်သွားမည် (ဥပမာ - Sheet4 လိုမျိုး)
                    log.info(f"⏭️ Skipping sheet: {sheet_name} (Keyword မပါဝင်ပါ)")
                    continue

                all_records = sheet.get_all_values()[1:] # Header ကို ကျော်မည်
                count = 0
                
                for row in all_records:
                    if len(row) < 3:
                        continue

                    parsed = self._format_location_delivery_row(row)
                    if parsed:
                        category, question, answer, tags = parsed
                        data_to_db.append((category, question, answer, tags, level, timestamp))
                        count += 1
                        continue

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
                    msg = f"✅ Shop Data {len(data_to_db)} ခုကို Sync လုပ်ပြီးပါပြီ။"
                    if skipped > 0:
                        msg += f"\n⚠️ Chat ID မမှန်သော row {skipped} ခုကို ကျော်သွားပါသည်။"
                    # Policy: Sheet is source of truth, so importer does not write back to Sheet.
                    return True, msg
            else:
                log.warning(f"⚠️ No valid rows found in 'Shop Mappings' tab (all rows: {len(all_records)}, skipped: {skipped}).")
            
            return False, "⚠️ 'Shop Mappings' tab တွင် မှန်ကန်သော Chat ID ပါဝင်သည့် ဒေတာမရှိပါ။ Chat ID column (A) ကို စစ်ဆေးပေးပါ။"

        except Exception as e:
            log.error(f"❌ Shop Mapping Sync Error: {e}")
            return False, f"အမှားတစ်ခုရှိနေပါတယ်: {str(e)}"

    def _get_existing_sheet_chat_ids(self, sheet):
        """ Sheet ထဲမှာ ရှိပြီးသား chat_id များကို စုစည်းခြင်း (duplicate ကာကွယ်ရန်) """
        try:
            all_values = sheet.get_all_values()
            if len(all_values) <= 1:
                return set()
            existing_ids = set()
            for row in all_values[1:]:  # skip header
                if row and row[0].strip():
                    try:
                        existing_ids.add(int(row[0].strip()))
                    except ValueError:
                        pass
            return existing_ids
        except Exception as e:
            log.warning(f"⚠️ Could not read existing chat_ids from Sheet: {e}")
            return set()

    def _fix_existing_unknown_shops_in_sheet(self, sheet):
        """ Sheet ထဲမှာ ရှိပြီးသား 'Unknown Shop' row များကို Telegram API မှ resolve လုပ်ပြီး Sheet cell update လုပ်ခြင်း """
        if not self.bot:
            return 0
        
        try:
            all_values = sheet.get_all_values()
            if len(all_values) <= 1:
                return 0
            
            updates = []  # List of (row_number, resolved_name)
            for i, row in enumerate(all_values):
                if i == 0:  # skip header
                    continue
                if not row or len(row) < 3:
                    continue
                tg_name = row[2].strip() if len(row) > 2 else ""
                if tg_name == "Unknown Shop" or tg_name == "":
                    try:
                        chat_id = int(row[0].strip())
                        chat_info = self.bot.get_chat(chat_id)
                        real_title = chat_info.title if chat_info.title else None
                        if real_title and real_title != "Unknown Shop":
                            row_num = i + 1  # 1-based for gspread
                            updates.append((row_num, real_title))
                            db_manager.update_os_group_shop_name(chat_id, real_title)
                            log.info(f"🔧 Fixed existing row {row_num}: {chat_id} → \"{real_title}\"")
                    except ValueError:
                        pass
                    except Exception as e:
                        log.warning(f"⚠️ Could not fix existing row {i+1}: {e}")
            
            # Batch update cells (one API call per row, but grouped by close rows if possible)
            fixed = 0
            for row_num, name in updates:
                try:
                    sheet.update(f'C{row_num}', [[name]])
                    fixed += 1
                except Exception as e:
                    log.error(f"❌ Failed to update row {row_num}: {e}")
            
            if fixed > 0:
                log.info(f"🔧 Fixed {fixed} existing 'Unknown Shop' row(s) in Sheet.")
            return fixed
        except Exception as e:
            log.error(f"❌ _fix_existing_unknown_shops_in_sheet Error: {e}")
            return 0

    def append_new_mappings_to_sheet(self, sheet_url):
        """ Manual Register + Unmapped Groups များကို Sheet အောက်ဆုံးတွင် သွားပေါင်းပေးခြင်း (Duplicate ကာကွယ်) """
        log.info("📤 Appending New Shop Mappings to GSheet...")
        workbook = self.connect(sheet_url)
        if not workbook: return 0

        try:
            sheet = workbook.worksheet("Shop Mappings")
            
            # ၁။ Sheet ထဲမှာ ရှိပြီးသား chat_id များကို စုစည်းမည် (duplicate ကာကွယ်ရန်)
            existing_ids = self._get_existing_sheet_chat_ids(sheet)
            log.info(f"📋 Found {len(existing_ids)} existing chat_ids in Sheet.")
            
            # ၂။ Manual Register data ယူမည်
            manual_data = db_manager.get_manual_register_data() or []
            
            # ၃။ Unmapped OS Groups (bot ရှိပေမယ့် Sheet ထဲမရှိသေး) ကိုပါ ယူမည်
            unmapped = db_manager.get_unmapped_os_groups() or []
            # Convert unmapped format: [(chat_id, shop_name), ...] → unified format
            unmapped_data = []
            for chat_id, shop_name in unmapped:
                unmapped_data.append((chat_id, shop_name, "", 0, 0, 0, 0))
            
            # ၄။ Merge + Deduplicate (unmapped data ကို ဦးစားပေး၊ duplicate chat_id ကျော်မည်)
            seen_ids = set()
            merged = []
            # Manual Register ကို အရင်ထည့် (ဒေတာပိုပြည့်စုံ)
            for m in manual_data:
                cid = m[0]
                if cid not in seen_ids:
                    seen_ids.add(cid)
                    merged.append(m)
            # Unmapped ကို ထပ်ထည့် (Manual Register မှာမပါတဲ့ ဟာတွေပဲ)
            for m in unmapped_data:
                cid = m[0]
                if cid not in seen_ids:
                    seen_ids.add(cid)
                    merged.append(m)
            
            # ၄.၅။ "Unknown Shop" များကို Telegram API မှ resolve လုပ်မည်
            merged = self._resolve_unknown_shops(merged)
            
            # ၄.၆။ Sheet ထဲမှာ ရှိပြီးသား "Unknown Shop" row များကိုလည်း resolve + update လုပ်မည်
            fixed_existing = self._fix_existing_unknown_shops_in_sheet(sheet)
            
            # ၅။ Sheet ထဲ မရှိသေးတာတွေကိုပဲ append လုပ်မည်
            rows = []
            chat_ids = []
            skipped = 0
            for m in merged:
                chat_id = m[0]
                if chat_id in existing_ids:
                    skipped += 1
                    continue
                tg_name, web_name, p_tid, e_tid, f_tid, updated_at = m[1], m[2], m[3], m[4], m[5], m[6]
                updated_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(updated_at)) if updated_at else "-"
                # 8-col format: OS Group ID | Mapping ID | Telegram Name | Website Name | Pickup TID | Error TID | Finance TID | Last Updated
                rows.append([str(chat_id), str(chat_id), tg_name, web_name, str(p_tid), str(e_tid), str(f_tid), updated_str])
                chat_ids.append(chat_id)

            if skipped > 0:
                log.info(f"⏭️ Skipped {skipped} already-existing chat_id(s) in Sheet.")
            
            total_changes = 0
            if rows:
                sheet.append_rows(rows, value_input_option='USER_ENTERED')
                db_manager.mark_os_groups_as_synced(chat_ids)
                log.info(f"✅ Appended {len(rows)} new row(s) to Sheet (Manual Register + Unmapped).")
                total_changes += len(rows)
            if fixed_existing > 0:
                total_changes += fixed_existing
            
            if total_changes == 0:
                log.info("ℹ️ No new groups to append, no Unknown Shop names to fix.")
            return total_changes
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