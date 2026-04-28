from playwright.sync_api import sync_playwright
import time
import os
import json
from datetime import datetime, timedelta
import pytz
from telebot import types
import db_manager
from logger import log
import ai_utils
from modules import location_service

def submit_pickup_order(target_date, os_name, remark, vehicle="Bicycle"):
    log.info("🚀 Auto Pickup စက်ရုပ် (Playwright) စတင်နေပါပြီ...")
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            # state.json path fix for module location
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            state_path = os.path.join(base_dir, "state.json")
            
            context = browser.new_context(storage_state=state_path)
            page = context.new_page()

            # --- ၁။ Order Page သို့ သွားခြင်း ---
            order_url = "https://www.carrymanexpress.com/neworder"
            log.info(f"🔗 {order_url} သို့ သွားနေပါသည်...")
            page.goto(order_url)
            page.wait_for_load_state('domcontentloaded')
            time.sleep(2) # Page သေချာပွင့်သည်အထိ ခဏစောင့်မည်

            log.info("📝 အချက်အလက်များ ဖြည့်သွင်းနေပါသည်...")

            # (က) Date (Escape မသုံးတော့ဘဲ အပြင်ဘက်ကို Click နှိပ်မည်)
            date_input = page.locator("input[name='order.receivedDate']")
            date_input.wait_for(state="attached", timeout=15000)
            date_input.evaluate(f"(el) => {{ el.value = '{target_date}'; el.dispatchEvent(new Event('input', {{ bubbles: true }})); el.dispatchEvent(new Event('change', {{ bubbles: true }})); }}")
            page.locator("body").click(force=True) # ပြက္ခဒိန်ကို ပိတ်ရန် အပြင်ကိုနှိပ်မည်
            log.info(f"   ✅ ရက်စွဲဖြည့်ပြီးပါပြီ ({target_date})")

            # (ခ) OS Name (Verification Logic ထည့်သွင်းခြင်း)
            os_input = page.locator("(//label[contains(text(), 'Os Name')]/following::input)[1]")
            os_input.click()
            time.sleep(1)
            os_input.fill("")
            os_input.press_sequentially(os_name, delay=100)
            time.sleep(3) # Dropdown ပေါ်လာသည်အထိ စောင့်မည်

            # Dropdown ထဲက စာသားများကို စစ်ဆေးမည်
            # Ant Design / Custom Dropdown များတွင် ပေါ်လေ့ရှိသော selector ကို သုံးပါမည်
            options = page.locator(".ant-select-item-option-content, .select-option, [role='option']")
            count = options.count()
            
            found = False
            if count > 0:
                for i in range(count):
                    opt_text = options.nth(i).inner_text().strip()
                    if os_name.lower() == opt_text.lower():
                        options.nth(i).click()
                        found = True
                        log.info(f"   ✅ OS Name အတိအကျတူသည်ကို တွေ့ရှိ၍ ရွေးချယ်လိုက်ပါသည် ({opt_text})")
                        break
                
                if not found:
                    # အတိအကျမတူရင် ပထမဆုံးတစ်ခုကို ရွေးမည်
                    first_opt = options.nth(0).inner_text().strip()
                    options.nth(0).click()
                    log.warning(f"   ⚠️ OS Name အတိအကျမတူသော်လည်း အနီးစပ်ဆုံး ({first_opt}) ကို ရွေးချယ်လိုက်ပါသည်")
                    found = True
            
            if not found:
                log.error(f"   ❌ OS Name ({os_name}) ကို Website ထဲတွင် ရှာမတွေ့ပါ။")
                browser.close()
                return False, f"Website ထဲတွင် ဆိုင်နာမည် ({os_name}) ကို ရှာမတွေ့ပါ။"

            log.info(f"   ✅ OS Name ရွေးပြီးပါပြီ")

            # (ဂ) Vehicle (Selenium အတိုင်း ArrowDown ပါ ထည့်သွင်းခြင်း)
            vehicle_input = page.locator("(//label[contains(text(), 'Vehicle')]/following::input)[1]")
            vehicle_input.click()
            time.sleep(1)
            vehicle_input.fill("")
            vehicle_input.press_sequentially(vehicle, delay=100)
            time.sleep(2)
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
            log.info(f"   ✅ ယာဉ်ရွေးပြီးပါပြီ ({vehicle})")

            # (ဃ) Remark
            remark_input = page.locator("textarea[name='order.remark']")
            remark_input.fill(remark)
            log.info(f"   ✅ မှတ်ချက်ဖြည့်ပြီးပါပြီ ({remark})")

            # အပြင်ဘက်ကို တစ်ချက်နှိပ်ပြီး Form ကို အတည်ပြုမည်
            page.locator("body").click(force=True)
            time.sleep(2) 

            # 📸 အရေးကြီးဆုံးအဆင့်: SAVE မနှိပ်ခင် Form အခြေအနေကို ဓာတ်ပုံရိုက်ထားမည်
            screenshot_path = os.path.join(base_dir, "before_save.png")
            page.screenshot(path=screenshot_path, full_page=True)
            log.info(f"📸 SAVE ခလုတ်မနှိပ်မီ မျက်နှာပြင်ကို '{screenshot_path}' အဖြစ် မှတ်တမ်းတင်ထားပါသည်။")

            # --- ၃။ Save ခလုတ်နှိပ်ခြင်း ---
            save_button = page.locator("//button[descendant::span[contains(text(), 'SAVE') or contains(text(), 'Save')]]")
            if save_button.count() > 0:
                save_button.first.click()
                log.info("💾 SAVE ခလုတ်ကို နှိပ်လိုက်ပါပြီ။")
            else:
                log.error("❌ Save button ကို ရှာမတွေ့ပါ။")
                browser.close()
                return False, "Save button ကို ရှာမတွေ့ပါ။"
            
            page.wait_for_load_state('domcontentloaded')
            time.sleep(3)
            log.info("🎉 အော်ဒါတင်ခြင်း အောင်မြင်ပါသည်!")

            browser.close()
            return True, "အော်ဒါတင်ခြင်း အောင်မြင်ပါသည်။"

    except Exception as e:
        log.error(f"❌ Playwright Error: {e}")
        return False, str(e)

def run(data, event):
    """
    Standard Module Entry Point
    data: { "target_date": "...", "os_name": "...", "remark": "...", "vehicle": "..." }
    event: "submit_pickup"
    """
    if event == "submit_pickup":
        target_date = data.get("target_date")
        os_name = data.get("os_name")
        remark = data.get("remark")
        vehicle = data.get("vehicle", "Bicycle")
        
        if not all([target_date, os_name, remark]):
            return False, "Missing required data (target_date, os_name, remark)"
            
        return submit_pickup_order(target_date, os_name, remark, vehicle)
    
    return False, f"Unknown event: {event}"

def handle(bot, message):
    """
    Central Router မှတစ်ဆင့် ခေါ်ယူသည့် Entry Point
    """
    try:
        chat_id = message.chat.id
        text = message.text or message.caption
        if not text:
            return

        log.info(f"🚚 Auto Pickup module handling message: {message.message_id}")

        # 0. Flow Locking & UI Refresh
        # အကယ်၍ လက်ရှိ chat မှာ WAITING_CONFIRM ဖြစ်နေတဲ့ order ရှိရင် အရင်စာတွေကို ရှင်းပြီး UI အသစ်ပြန်ပို့ပေးမယ်
        waiting_order = db_manager.get_waiting_confirm_order(chat_id)
        if waiting_order:
            # Midnight Rule: ရက်စွဲကျော်နေရင် (ဥပမာ - မနေ့ကစာ ဖြစ်နေရင်) အဲ့ဒီ Lock ကို Reset ချပေးပါမယ်။
            # waiting_order format: (id, chat_id, orig_msg_id, target_date, os_name, remark, vehicle, status, created_at)
            created_at = waiting_order[8]
            if created_at:
                tz = pytz.timezone('Asia/Yangon')
                created_dt = datetime.fromtimestamp(created_at, tz).date()
                current_dt = datetime.now(tz).date()
                
                if created_dt < current_dt:
                    log.info(f"🌙 Midnight Rule: Resetting stale lock from {created_dt} for chat {chat_id}")
                    db_manager.update_queue_status(waiting_order[0], 'CANCELLED', error_msg="Midnight Rule: Stale Lock Reset")
                    db_manager.cancel_message(waiting_order[2], chat_id, reason='Midnight Rule: Auto-Expired')
                    cleanup_pickup_intermediate_msgs(bot, chat_id, waiting_order[2])
                    # Lock ဖြုတ်လိုက်ပြီဖြစ်၍ အောက်က flow အတိုင်း အသစ်ပြန်စမည်
                else:
                    log.info(f"🔒 Flow Locked: Found WAITING_CONFIRM order {waiting_order[0]} for chat {chat_id}")
                    from handlers import pickup_handler
                    # အရင်ပို့ထားတဲ့ intermediate messages တွေကို ရှင်းမယ်
                    cleanup_pickup_intermediate_msgs(bot, chat_id, waiting_order[2])
                    # UI အသစ်ပြန်ပို့မယ် (Reconfirmation)
                    pickup_handler.show_pickup_reconfirmation(bot, chat_id, waiting_order[0])
                    return

        # ၁။ OS Name Extraction (Group Title မှ ယူမည်)
        chat_title = message.chat.title or "Unknown Shop"
        os_name = db_manager.clean_shop_name(chat_title)
        
        # 🧪 Test Group Tagging
        TEST_GROUP_ID = -1003539520778
        if chat_id == TEST_GROUP_ID:
            os_name = f"[TEST] {os_name}"
            log.info(f"🧪 Sandbox Order detected. Tagging OS Name as: {os_name}")

        # ၂။ AI Extraction (Action, Vehicle & Date)
        extract_prompt = f"""
        Analyze the following message from a staff member.
        Message: "{text}"

        Decide the action:
        1. 'PICKUP': If the user is requesting a new pickup or asking about pickup availability.
        2. 'LOOKUP_LOCATION': If the user is asking for the township of a specific location name (e.g., "Hledan က ဘယ်မြို့နယ်လဲ", "Junction City က ဘယ်မြို့နယ်ထဲမှာလဲ").
        3. 'OTHER': If it's something else.

        Output ONLY a JSON object with:
        - action: "PICKUP", "LOOKUP_LOCATION", or "OTHER"
        - location_query: If action is 'LOOKUP_LOCATION', extract the location name they are asking about (e.g., "Hledan", "Junction City"). Otherwise null.
        - is_new_request: boolean (True if action is 'PICKUP', False otherwise)
        - vehicle: "Bicycle" or "Car" (Default to null if not mentioned)
        - date_type: "today" or "tomorrow" (If the user explicitly mentions "today" (ဒီနေ့) or "tomorrow" (မနက်ဖြန်), set accordingly. Otherwise, default to null)
        - clean_remark: Extract ONLY the additional instructions, notes, or specific details (like quantity, amount, location, or special requests) from the message in Burmese, EXCLUDING the core pickup request phrase (e.g., "pick up လာယူပေးပါ", "လာကောက်ပေးပါ").
          Example 1: "မနက်ဖြန် pick up လေးလာပေးပါ၊ ငွေသား (၁၀)သိန်းယူခဲ့ပါ" -> "ငွေသား (၁၀)သိန်းယူခဲ့ပါ"
          Example 2: "ဒီနေ့ pick up လာယူပေးပါ၊ ပစ္စည်း (၅)ထုပ်ရှိပါတယ်" -> "ပစ္စည်း (၅)ထုပ်ရှိပါတယ်"
          If there are no additional instructions beyond the pickup request itself, return null.
        """

        ai_res_content = ai_utils.get_ai_completion(
            prompt=extract_prompt,
            model="google/gemini-2.0-flash-001",
            response_format={ "type": "json_object" },
            timeout=30.0
        )
        
        if not ai_res_content:
            log.error("❌ AI Extraction failed in auto_pickup.")
            return

        extracted_data = json.loads(ai_res_content)
        
        action = extracted_data.get("action", "OTHER")

        # --- LOOKUP_LOCATION Action ---
        if action == 'LOOKUP_LOCATION':
            location_query = extracted_data.get("location_query")
            if location_query:
                log.info(f"🔍 Location Lookup triggered for: {location_query}")
                township, source = location_service.get_location_with_fallback(location_query)
                
                if township:
                    reply_text = f"📍 {location_query} သည် {township} အတွင်း တည်ရှိပါသည်။ (Source: {source})"
                else:
                    reply_text = f"📍 {location_query} ၏ မြို့နယ်ကို ရှာမတွေ့ပါခင်ဗျာ။"
                
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("💬 Admin နှင့်ပြောမည်", callback_data=f"ap_admin_0_{message.message_id}"))
                bot.reply_to(message, reply_text, reply_markup=markup)
                return
            else:
                log.warning("⚠️ LOOKUP_LOCATION action detected but no location_query found.")

        # ၃။ Validation: အကယ်၍ pickup အသစ်တင်တာမဟုတ်ရင် ရပ်တန့်မည်
        if action != 'PICKUP' or not extracted_data.get("is_new_request", True):
            log.info(f"ℹ️ Message {message.message_id} is not a pickup request (Action: {action}). Skipping auto_pickup.")
            return

        vehicle = extracted_data.get("vehicle")
        clean_remark = extracted_data.get("clean_remark")
        ai_date_type = extracted_data.get("date_type") # "today", "tomorrow" or null

        # AI မှ ထုတ်ပေးသော clean_remark ကို summary အဖြစ် သိမ်းဆည်းထားမည်
        if clean_remark:
            db_manager.update_message_status(message.message_id, chat_id, 'PENDING', summary=clean_remark)
        
        # ၃။ Time Check (MMT အချိန်အပိုင်းအခြားအလိုက် Logic ခွဲခြင်း)
        tz = pytz.timezone('Asia/Yangon')
        now = datetime.now(tz)
        current_time = now.hour * 100 + now.minute # e.g., 11:01 -> 1101
        
        # 🧪 Test Group Bypass: အလုပ်ချိန်ပြင်ပလည်း ပုံမှန်အတိုင်း အလုပ်လုပ်ရန်
        if chat_id == TEST_GROUP_ID:
            log.info(f"🧪 Test Group {chat_id} detected. Bypassing time restrictions.")
            # Test group အတွက် အမြဲတမ်း Window 1 (Today) သို့မဟုတ် Window 2 (Staff Decision) အတိုင်းသွားရန်
            # ညဘက်စမ်းရင်လည်း Staff Decision Alert တက်အောင် 1101 - 1500 ကြားထဲ ရောက်အောင် ခေတ္တပြောင်းပေးမည်
            current_time = 1200

        # Logic Windows:
        # 1. 12:01 AM - 11:00 AM (0001 - 1100) -> Today
        # 2. 11:01 AM - 03:00 PM (1101 - 1500) -> Staff Decision (Unless explicitly "tomorrow")
        # 3. 03:01 PM - 12:00 AM (1501 - 2400) -> Tomorrow

        remark = extracted_data.get("remark", text[:100])
        v_str = vehicle if vehicle else "none"

        # အကယ်၍ User က "မနက်ဖြန်" လို့ အတိအလင်းပြောထားရင် Time Window မစစ်တော့ဘဲ Tomorrow Flow သွားမည်
        if ai_date_type == "tomorrow":
            date_type = "tomorrow"
        elif 1100 <= current_time < 1500:
            # Window 2: Staff Decision Alert (Only if not explicitly tomorrow)
            send_staff_decision_alert(bot, message, os_name, v_str)
            return
        elif current_time >= 1500:
            # Window 3: Tomorrow (Direct)
            date_type = "tomorrow"
            # ၃ နာရီကျော်သွားကြောင်း အသိပေးချက်ပို့မည်
            late_msg = "နေ့လည် (၃) နာရီကျော်သွားပြီဖြစ်လို့ မနက်ဖြန် date နဲ့ပဲ တင်ပေးလို့ရပါမယ်ရှင်။"
            markup_late = types.InlineKeyboardMarkup()
            markup_late.add(types.InlineKeyboardButton("❌ Pickup မဟုတ်ပါ", callback_data=f"ap_cancel_{message.message_id}"))
            msg = bot.reply_to(message, late_msg, reply_markup=markup_late)
            db_manager.add_pickup_intermediate_msg(chat_id, message.message_id, msg.message_id)
        else:
            # Window 1: Today (Direct)
            date_type = "today"

        # ၄။ Duplicate Check (တင်ပြီးသားရှိမရှိ စစ်ဆေးခြင်း)
        target_date_str = (now if date_type == "today" else now + timedelta(days=1)).strftime("%d-%m-%Y")
        if db_manager.check_existing_pickup(chat_id, target_date_str):
            log.info(f"⚠️ Duplicate pickup detected for {chat_id} on {target_date_str}")
            show_duplicate_alert(bot, message, target_date_str, message.message_id)
            return

        # ယာဉ်အမျိုးအစား မပါလျှင် မေးမည်
        if not vehicle:
            ask_vehicle(bot, message, date_type, message.message_id)
            return

        # အကုန်ပြည့်စုံလျှင် မှတ်ချက်ရှိမရှိ မေးမည်
        ask_remark(bot, chat_id, date_type, vehicle, message.message_id)

        # Central Alert ပို့ခြင်း (Window 1 & 3 အတွက်)
        if date_type in ["today", "tomorrow"]:
            central_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))
            pickup_topic = -878
            clean_chat_id = str(chat_id).replace("-100", "")
            msg_link = f"https://t.me/c/{clean_chat_id}/{message.message_id}"
            
            alert_text = (
                f"🚚 **Pick Up alert** (Pending)\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🏪 ဆိုင်: <b>{os_name}</b>\n"
                f"📅 ရက်စွဲ: {target_date_str}\n"
                f"🚲 ယာဉ်: {vehicle if vehicle else '-'}\n"
                f"📝 မှတ်ချက်: {clean_remark if clean_remark else '-'}\n"
                f"🔗 <a href='{msg_link}'>View Message</a>\n"
                f"━━━━━━━━━━━━━━━━━━"
            )
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("✅ Done", callback_data=f"done_{message.message_id}_{chat_id}"))
            
            try:
                alert_msg = bot.send_message(central_chat, alert_text, parse_mode="HTML", message_thread_id=pickup_topic, reply_markup=markup)
                if alert_msg:
                    db_manager.save_alert_tracking(message.message_id, chat_id, alert_msg.message_id, central_chat)
                    db_manager.update_message_status(message.message_id, chat_id, 'ALERTED')
            except Exception as e:
                log.warning(f"⚠️ Failed to send central alert: {e}")

    except Exception as e:
        log.error(f"❌ Auto Pickup Handle Error: {e}")
        bot.reply_to(message, "⚠️ Auto Pickup လုပ်ဆောင်စဉ် အမှားတစ်ခု ဖြစ်သွားပါသည်။")

def show_duplicate_alert(bot, message, target_date, orig_msg_id):
    """ တင်ပြီးသားရှိနေပါက အသိပေးချက်ပြခြင်း """
    text = f"ဒီနေ့ ({target_date}) အတွက် pickup တင်ပြီးသားရှိပါတယ် အစ်ကို။"
    markup = types.InlineKeyboardMarkup()
    # format: ap_admin_{queue_id}_{orig_msg_id}
    markup.add(types.InlineKeyboardButton("💬 Admin နှင့်ပြောမည်", callback_data=f"ap_admin_0_{orig_msg_id}"))
    msg = bot.reply_to(message, text, reply_markup=markup)
    db_manager.add_pickup_intermediate_msg(message.chat.id, orig_msg_id, msg.message_id)

def ask_vehicle(bot, message, date_type, orig_msg_id):
    """ ယာဉ်အမျိုးအစား မေးမြန်းခြင်း """
    text = "ဒီနေ့ pick up လေးရပါတယ်နော်။ pick up တင်ပေးနိုင်ရန် ****လိုတဲ့အချက် (စက်ဘီး၊ကား)*** ကိုပြောပေးပါဦး"
    if date_type == "tomorrow":
        text = "မနက်ဖြန်အတွက် pick up တင်ပေးနိုင်ရန် ****လိုတဲ့အချက် (စက်ဘီး၊ကား)*** ကိုပြောပေးပါဦး"
        
    markup = types.InlineKeyboardMarkup(row_width=2)
    # format: ap_vh_{orig_msg_id}_{date_type}_{vehicle}
    markup.add(
        types.InlineKeyboardButton("🚲 စက်ဘီး (Bicycle)", callback_data=f"ap_vh_{orig_msg_id}_{date_type}_Bicycle"),
        types.InlineKeyboardButton("🚗 ကား (Car)", callback_data=f"ap_vh_{orig_msg_id}_{date_type}_Car")
    )
    markup.add(types.InlineKeyboardButton("❌ Pickup မဟုတ်ပါ", callback_data=f"ap_cancel_{orig_msg_id}"))
    msg = bot.reply_to(message, text, reply_markup=markup)
    db_manager.add_pickup_intermediate_msg(message.chat.id, orig_msg_id, msg.message_id)

def ask_remark(bot, chat_id, date_type, vehicle, orig_msg_id):
    """ မှတ်ချက် (Remark) ရှိမရှိ မေးမြန်းခြင်း """
    text = "pick up အချက်အလက်စုံရင် Pick up တင်ပေးပါတော့မယ်၊ ထည့်ချင်တဲ့မှတ်ချက်ရှိရင် ရေးပေးပါခင်ဗျ"
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    # format: ap_rm_{orig_msg_id}_{date_type}_{vehicle}_{action}
    markup.add(
        types.InlineKeyboardButton("📝 မှတ်ချက်ရေးမည်", callback_data=f"ap_rm_{orig_msg_id}_{date_type}_{vehicle}_write"),
        types.InlineKeyboardButton("❌ မှတ်ချက်မရှိပါ", callback_data=f"ap_rm_{orig_msg_id}_{date_type}_{vehicle}_none")
    )
    markup.add(types.InlineKeyboardButton("❌ Pickup မဟုတ်ပါ", callback_data=f"ap_cancel_{orig_msg_id}"))
    # bot.reply_to(message, text, reply_markup=markup)
    msg = bot.send_message(chat_id, text, reply_to_message_id=orig_msg_id, reply_markup=markup)
    db_manager.add_pickup_intermediate_msg(chat_id, orig_msg_id, msg.message_id)

def send_staff_decision_alert(bot, message, os_name, vehicle):
    """ 11:01 AM - 03:00 PM အတွင်း Staff ဆီ Decision Alert ပို့ခြင်း """
    try:
        central_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))
        pickup_topic = -878 # Pick up Topic ID
        chat_id = message.chat.id
        orig_msg_id = message.message_id
        
        # ၁။ ဆိုင် Group ထဲသို့ အလိုအလျောက် အကြောင်းကြားစာ ပို့ခြင်း
        late_pickup_text = "Pick up တင်တာ (၁၁)နာရီကျော်ပြီမိုလို Pick up လမ်းကြောင်းလေးရသေးလားဆိုတာ အတည်ပြုပြီးပြန်လည်အကြောင်းပြန်ပေးပါ့မယ်ရှင်။"
        markup_shop = types.InlineKeyboardMarkup()
        markup_shop.add(types.InlineKeyboardButton("❌ Pickup မဟုတ်ပါ", callback_data=f"ap_cancel_{orig_msg_id}"))
        msg = bot.reply_to(message, late_pickup_text, reply_markup=markup_shop)
        db_manager.add_pickup_intermediate_msg(chat_id, orig_msg_id, msg.message_id)
        log.info(f"📢 Sent late pickup notification to shop group: {chat_id}")

        clean_chat_id = str(chat_id).replace("-100", "")
        msg_link = f"https://t.me/c/{clean_chat_id}/{orig_msg_id}"
        
        alert_text = (
            f"🚚 **Pick Up alert** (Waiting Decision)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏪 ဆိုင်: <b>{os_name}</b>\n"
            f"🔗 <a href='{msg_link}'>View Message</a>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"ဒီနေ့အတွက် pick up တင်ပေးမလား သို့မဟုတ် မနက်ဖြန်သို့ ရွှေ့မလား?"
        )
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        # format: ap_st_{orig_msg_id}_{chat_id}_{date_type}_{vehicle}
        markup.add(
            types.InlineKeyboardButton("📅 Today", callback_data=f"ap_st_{orig_msg_id}_{chat_id}_today_{vehicle}"),
            types.InlineKeyboardButton("📅 Tomorrow", callback_data=f"ap_st_{orig_msg_id}_{chat_id}_tomorrow_{vehicle}")
        )
        markup.add(types.InlineKeyboardButton("✅ Done", callback_data=f"done_{orig_msg_id}_{chat_id}"))
        
        try:
            alert_msg = bot.send_message(central_chat, alert_text, parse_mode="HTML", message_thread_id=pickup_topic, reply_markup=markup)
            if alert_msg:
                db_manager.save_alert_tracking(orig_msg_id, chat_id, alert_msg.message_id, central_chat)
                db_manager.update_message_status(orig_msg_id, chat_id, 'ALERTED')
        except Exception as e:
            log.warning(f"⚠️ Failed to send staff decision alert to topic: {e}")
            alert_msg = bot.send_message(central_chat, alert_text, parse_mode="HTML", reply_markup=markup)
            if alert_msg:
                db_manager.save_alert_tracking(orig_msg_id, chat_id, alert_msg.message_id, central_chat)
                db_manager.update_message_status(orig_msg_id, chat_id, 'ALERTED')

    except Exception as e:
        log.error(f"❌ send_staff_decision_alert Error: {e}")

def update_central_pickup_alert(bot, orig_msg_id, chat_id, status_text, show_done=True):
    """ Central Group ရှိ Alert Message ကို Edit လုပ်၍ Status ပြောင်းလဲမှု ပြသခြင်း """
    try:
        central_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))
        tracking = db_manager.get_alert_tracking(orig_msg_id, chat_id)
        if not tracking:
            log.warning(f"⚠️ No alert tracking found for {orig_msg_id} in {chat_id}")
            return

        alert_msg_id = tracking[0]
        
        # မူရင်းစာသားကို ပြန်ယူရန် (သို့မဟုတ် အသစ်တည်ဆောက်ရန်)
        # ဤနေရာတွင် ရိုးရှင်းစေရန် အချက်အလက်အသစ်ဖြင့် ပြန်ရေးပါမည်
        order = None
        # pickup_queue ထဲမှာ ရှိမရှိ အရင်ရှာမည်
        with db_manager.get_connection() as conn:
            order = conn.execute("SELECT os_name, target_date, remark, vehicle, status FROM pickup_queue WHERE orig_msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
        
        if not order:
            # message_logs ထဲက context ယူမည်
            ctx = db_manager.get_message_context(orig_msg_id, chat_id)
            _, _, shop_name = db_manager.get_topic_context(chat_id, 0) # topic_id 0 for general
            os_name = shop_name
            target_date = "-"
            remark = ctx[1] if ctx and ctx[1] else "-"
            vehicle = "-"
        else:
            os_name, target_date, remark, vehicle, _ = order

        clean_chat_id = str(chat_id).replace("-100", "")
        msg_link = f"https://t.me/c/{clean_chat_id}/{orig_msg_id}"

        new_text = (
            f"🚚 **Pick Up alert**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏪 ဆိုင်: <b>{os_name}</b>\n"
            f"📅 ရက်စွဲ: {target_date}\n"
            f"🚲 ယာဉ်: {vehicle}\n"
            f"📝 မှတ်ချက်: {remark}\n"
            f"📊 Status: <b>{status_text}</b>\n"
            f"🔗 <a href='{msg_link}'>View Message</a>\n"
            f"━━━━━━━━━━━━━━━━━━"
        )

        markup = types.InlineKeyboardMarkup()
        if show_done:
            markup.add(types.InlineKeyboardButton("✅ Done", callback_data=f"done_{orig_msg_id}_{chat_id}"))

        try:
            bot.edit_message_text(new_text, central_chat, alert_msg_id, parse_mode="HTML", reply_markup=markup)
            log.info(f"📝 Updated central alert for {os_name} to: {status_text}")
        except Exception as edit_e:
            if "message is not modified" not in str(edit_e):
                log.error(f"❌ Failed to edit central alert: {edit_e}")

    except Exception as e:
        log.error(f"❌ update_central_pickup_alert Error: {e}")

def cleanup_pickup_intermediate_msgs(bot, chat_id, orig_msg_id):
    """ Pickup flow ပြီးဆုံးသွားသောအခါ ကြားဖြတ် Bot စာများကို ဖျက်ထုတ်ခြင်း """
    try:
        msg_ids = db_manager.get_pickup_intermediate_msgs(chat_id, orig_msg_id)
        for mid in msg_ids:
            try:
                bot.delete_message(chat_id, mid)
            except Exception:
                pass
        db_manager.delete_pickup_intermediate_msgs(chat_id, orig_msg_id)
        log.info(f"🧹 Cleaned up {len(msg_ids)} intermediate messages for orig_msg {orig_msg_id}")
    except Exception as e:
        log.error(f"❌ Cleanup Pickup Intermediate Messages Error: {e}")

def run_queue_worker(bot):
    """ Background Queue Worker """
    log.info("🚀 Auto Pickup Queue Worker စတင်နေပါပြီ...")
    while True:
        try:
            # Queue ထဲက အော်ဒါတစ်ခု ယူမည်
            item = db_manager.get_next_queued_pickup()
            if not item:
                time.sleep(5)
                continue

            queue_id, chat_id, orig_msg_id, target_date, os_name, remark, vehicle = item
            log.info(f"📦 Processing Queue Item {queue_id} for {os_name}")

            # ၁။ Shop Mapping ရှိမရှိ အရင်စစ်မည်
            mapped_name = db_manager.get_shop_mapping(chat_id)
            final_os_name = mapped_name if mapped_name else os_name

            # Status ကို PROCESSING ပြောင်းမည်
            db_manager.update_queue_status(queue_id, 'PROCESSING')
            update_central_pickup_alert(bot, orig_msg_id, chat_id, "⏳ Processing (စက်ရုပ်တင်နေပါသည်)")

            # အော်ဒါတင်မည်
            success, msg = submit_pickup_order(target_date, final_os_name, remark, vehicle)
            
            if success:
                db_manager.update_queue_status(queue_id, 'SUCCESS')
                update_central_pickup_alert(bot, orig_msg_id, chat_id, "✅ Success (အောင်မြင်ပါသည်)")
                
                # Status Sync: message_logs မှာပါ RESOLVED သွားပြောင်းမည်
                db_manager.resolve_message(orig_msg_id, chat_id, 'System (Auto-Pickup)', method='Auto')
                
                # Cleanup intermediate messages first
                cleanup_pickup_intermediate_msgs(bot, chat_id, orig_msg_id)
                
                bot.send_message(chat_id, f"✅ **Auto Pickup အောင်မြင်ပါသည်!**\n🏪 ဆိုင်: {final_os_name}\n📅 Pick Up Date: {target_date}\n📝 မှတ်ချက်: {remark if remark else '-'}", reply_to_message_id=orig_msg_id)

                # အကယ်၍ Mapping မရှိသေးဘဲ အောင်မြင်သွားတာဆိုရင် Mapping အလိုအလျောက် မှတ်သားမည်
                if not mapped_name and final_os_name != os_name:
                    db_manager.set_shop_mapping(chat_id, final_os_name)
                    log.info(f"📝 Auto-mapped {chat_id} to {final_os_name}")
            else:
                # Error တက်လျှင် Screenshot ရိုက်ပြီး Manager ဆီ ပို့မည်
                db_manager.update_queue_status(queue_id, 'FAILED', error_msg=msg)
                handle_pickup_error(bot, chat_id, orig_msg_id, final_os_name, target_date, msg)

            # Website ကို ဝန်မပိစေရန် ခဏနားမည်
            time.sleep(10)

        except Exception as e:
            log.error(f"❌ Queue Worker Error: {e}")
            time.sleep(10)

def handle_pickup_error(bot, chat_id, orig_msg_id, os_name, target_date, error_msg):
    """ Error တက်လျှင် Manager ဆီ Alert ပို့ခြင်း (Topic -878 & Sticky Resolution) """
    try:
        central_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))
        pickup_topic = -878 # Pick up Topic ID
        
        alert_text = (
            f"❌ **AUTO PICKUP ERROR**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏪 ဆိုင်: {os_name}\n"
            f"📅 ရက်စွဲ: {target_date}\n"
            f"⚠️ အမှား: {error_msg}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Manager များ Manual စစ်ဆေးပေးပါရန်။"
        )
        
        # Sticky Resolution အတွက် Done Button နှင့် Mapping Button ထည့်မည်
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("✅ Done (Fixed)", callback_data=f"done_{orig_msg_id}_{chat_id}"),
            types.InlineKeyboardButton("🔗 Fix Shop Mapping", callback_data=f"ap_fix_{chat_id}")
        )

        # Screenshot ရှိမရှိ စစ်မည်
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        screenshot_path = os.path.join(base_dir, "before_save.png")
        
        alert_msg = None
        if os.path.exists(screenshot_path):
            with open(screenshot_path, 'rb') as photo:
                try:
                    alert_msg = bot.send_photo(central_chat, photo, caption=alert_text, message_thread_id=pickup_topic, reply_markup=markup)
                except Exception as e:
                    log.warning(f"⚠️ Failed to send photo with thread_id, retrying without thread_id: {e}")
                    alert_msg = bot.send_photo(central_chat, photo, caption=alert_text, reply_markup=markup)
        else:
            try:
                alert_msg = bot.send_message(central_chat, alert_text, message_thread_id=pickup_topic, reply_markup=markup)
            except Exception as e:
                log.warning(f"⚠️ Failed to send message with thread_id, retrying without thread_id: {e}")
                alert_msg = bot.send_message(central_chat, alert_text, reply_markup=markup)

        if alert_msg:
            # Database တွင် Alert Tracking သိမ်းပြီး is_manual = 1 (Sticky) လုပ်မည်
            db_manager.save_alert_tracking(orig_msg_id, chat_id, alert_msg.message_id, central_chat)
            db_manager.set_manual_alert(orig_msg_id, chat_id)
            db_manager.update_message_status(orig_msg_id, chat_id, 'ALERTED', topic_id=1)

    except Exception as e:
        log.error(f"❌ Error Alert Sending Failed: {e}")

if __name__ == "__main__":
    print("🧪 တိုက်ရိုက် စမ်းသပ်ခြင်း စတင်ပါသည်...")
    run(
        data={
            "target_date": "25-04-2026", 
            "os_name": "os",
            "remark": "မနက်ဖြန် Akari ဆီမှာ ပစ္စည်းကောက်ပေးပါ (Playwright Module Test)"
        },
        event="submit_pickup"
    )
