import time
import os
import json
import asyncio
from datetime import datetime, timedelta
import pytz
from telebot import types, util
import db_manager
from logger import log
import ai_utils
from modules import location_service, auto_login, browser_manager

_bot = None

async def send_pickup_notification(text, is_alert=False):
    """
    Centralized notification helper for Topic 878 (Pickup Command Center)
    """
    if not _bot:
        log.error("❌ send_pickup_notification: Bot instance not initialized.")
        return

    target_chat_id = -1003601049225
    topic_id = 878
    emoji = "⚠️" if is_alert else "✅"
    
    # Formatting
    formatted_text = f"{emoji} {text}"
    
    try:
        # Use asyncio.to_thread for sync telebot calls
        await asyncio.to_thread(
            _bot.send_message,
            target_chat_id,
            formatted_text,
            parse_mode="HTML",
            message_thread_id=topic_id
        )
    except Exception as e:
        log.error(f"❌ Failed to send centralized notification: {e}")

async def _sync_shops_task(page):
    """Website မှ OS Name စာရင်းများကို ဆွဲယူရန် Async Task"""
    order_url = "https://www.carrymanexpress.com/neworder"
    log.info(f"🔗 Syncing shops from {order_url}...")
    await page.goto(order_url)
    await page.wait_for_load_state('domcontentloaded')
    
    if "login" in page.url.lower():
        success, msg = auto_login.auto_login()
        if not success: return []
        await page.goto(order_url)
        await page.wait_for_load_state('networkidle')

    # OS Name dropdown ကို နှိပ်၍ စာရင်းပေါ်လာအောင်လုပ်ခြင်း
    os_input = page.locator("(//label[contains(text(), 'Os Name')]/following::input)[1]")
    await os_input.click()
    await asyncio.sleep(2)
    
    options = page.locator(".ant-select-item-option-content, .select-option, [role='option']")
    count = await options.count()
    shops = []
    for i in range(count):
        text = await options.nth(i).inner_text()
        if text: shops.append(text.strip())
    
    log.info(f"✅ Found {len(shops)} shops on website.")
    return shops

async def _submit_pickup_task(page, target_date, os_name, remark, vehicle):
    """Async task for pickup submission logic"""
    # --- ၁။ Order Page သို့ သွားခြင်း ---
    order_url = "https://www.carrymanexpress.com/neworder"
    log.info(f"🔗 {order_url} သို့ သွားနေပါသည်...")
    await page.goto(order_url)
    await page.wait_for_load_state('domcontentloaded')
    
    # Check if redirected to login page
    if "login" in page.url.lower():
        log.warning("⚠️ Session expired. Re-logging in...")
        success, msg = auto_login.auto_login()
        if not success:
            return False, f"Login failed: {msg}"
        
        log.info(f"🔄 Re-navigating to {order_url} after login...")
        await page.goto(order_url)
        await page.wait_for_load_state('networkidle')

    # --- ၂။ Page Ready Check & Popup Handling ---
    async def handle_popups(p):
        """SweetAlert သို့မဟုတ် အခြား Popup များရှိပါက ပိတ်ခြင်း"""
        try:
            # SweetAlert buttons (OK, Confirm, Cancel)
            popups = p.locator(".swal-button--confirm, .swal-button--cancel, .ant-modal-close, .ant-btn-primary")
            count = await popups.count()
            if count > 0:
                log.info(f"🛡️ Found {count} popup buttons. Attempting to close...")
                for i in range(count):
                    if await popups.nth(i).is_visible():
                        await popups.nth(i).click(timeout=2000)
                        await asyncio.sleep(1)
        except Exception as e:
            log.debug(f"Popup handling skip: {e}")

    async def wait_for_page_ready(p):
        await handle_popups(p)
        try:
            await p.wait_for_selector(".ant-spin, .loading-spinner, .spinner", state="hidden", timeout=10000)
        except:
            pass
        await p.wait_for_selector("form, .ant-form, input[name='order.receivedDate']", state="visible", timeout=15000)

    try:
        await wait_for_page_ready(page)
    except Exception as e:
        log.warning(f"⚠️ Page ready check timed out: {e}. Attempting to proceed anyway.")

    log.info("📝 အချက်အလက်များ ဖြည့်သွင်းနေပါသည်...")
    await handle_popups(page)

    # (က) Date
    date_input = page.locator("input[name='order.receivedDate']")
    try:
        await date_input.wait_for(state="attached", timeout=10000)
    except Exception:
        log.warning("⚠️ Date field မတွေ့ပါ။ Page ကို Refresh လုပ်ပြီး တစ်ကြိမ် ထပ်ကြိုးစားကြည့်ပါမည်...")
        await page.reload()
        await page.wait_for_load_state('networkidle')
        await wait_for_page_ready(page)
        await date_input.wait_for(state="attached", timeout=15000)

    await date_input.evaluate(f"(el) => {{ el.value = '{target_date}'; el.dispatchEvent(new Event('input', {{ bubbles: true }})); el.dispatchEvent(new Event('change', {{ bubbles: true }})); }}")
    await page.locator("body").click(force=True)
    log.info(f"   ✅ ရက်စွဲဖြည့်ပြီးပါပြီ ({target_date})")

    # (ခ) OS Name
    os_input = page.locator("(//label[contains(text(), 'Os Name')]/following::input)[1]")
    
    # Ensure no popup is blocking the click
    await handle_popups(page)
    
    await os_input.click()
    await asyncio.sleep(1)
    await os_input.fill("")
    await os_input.press_sequentially(os_name, delay=100)
    await asyncio.sleep(3)

    options = page.locator(".ant-select-item-option-content, .select-option, [role='option']")
    count = await options.count()
    
    found = False
    if count > 0:
        for i in range(count):
            opt_text = (await options.nth(i).inner_text()).strip()
            if os_name.lower() == opt_text.lower():
                await options.nth(i).click()
                found = True
                log.info(f"   ✅ OS Name အတိအကျတူသည်ကို တွေ့ရှိ၍ ရွေးချယ်လိုက်ပါသည် ({opt_text})")
                break
        
    if not found:
        log.error(f"   ❌ OS Name ({os_name}) ကို Website ထဲတွင် ရှာမတွေ့ပါ။")
        return False, f"Website ထဲတွင် ဆိုင်နာမည် ({os_name}) ကို ရှာမတွေ့ပါ။"

    log.info(f"   ✅ OS Name ရွေးပြီးပါပြီ")

    # (ဂ) Vehicle
    vehicle_input = page.locator("(//label[contains(text(), 'Vehicle')]/following::input)[1]")
    await vehicle_input.click()
    await asyncio.sleep(1)
    await vehicle_input.fill("")
    await vehicle_input.press_sequentially(vehicle, delay=100)
    await asyncio.sleep(2)
    await page.keyboard.press("ArrowDown")
    await page.keyboard.press("Enter")
    log.info(f"   ✅ ယာဉ်ရွေးပြီးပါပြီ ({vehicle})")

    # (ဃ) Remark
    remark_input = page.locator("textarea[name='order.remark']")
    await remark_input.fill(remark)
    log.info(f"   ✅ မှတ်ချက်ဖြည့်ပြီးပါပြီ ({remark})")

    await page.locator("body").click(force=True)
    await asyncio.sleep(2) 

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    screenshot_path = os.path.join(base_dir, "before_save.png")
    await page.screenshot(path=screenshot_path, full_page=True)
    log.info(f"📸 SAVE ခလုတ်မနှိပ်မီ မျက်နှာပြင်ကို '{screenshot_path}' အဖြစ် မှတ်တမ်းတင်ထားပါသည်။")

    # --- ၃။ Save ခလုတ်နှိပ်ခြင်း ---
    save_button = page.locator("//button[descendant::span[contains(text(), 'SAVE') or contains(text(), 'Save')]]")
    if await save_button.count() > 0:
        await save_button.first.click()
        log.info("💾 SAVE ခလုတ်ကို နှိပ်လိုက်ပါပြီ။")
    else:
        log.error("❌ Save button ကို ရှာမတွေ့ပါ။")
        return False, "Save button ကို ရှာမတွေ့ပါ။"
    
    await page.wait_for_load_state('domcontentloaded')
    await asyncio.sleep(3)
    log.info("🎉 အော်ဒါတင်ခြင်း အောင်မြင်ပါသည်!")
    return True, "အော်ဒါတင်ခြင်း အောင်မြင်ပါသည်။"

def submit_pickup_order(target_date, os_name, remark, vehicle="Bicycle"):
    log.info("🚀 Auto Pickup စက်ရုပ် (Playwright) စတင်နေပါပြီ...")
    
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    state_path = os.path.join(base_dir, "state.json")
    
    try:
        return browser_manager.browser_manager.run_task(
            _submit_pickup_task, 
            storage_state=state_path,
            target_date=target_date,
            os_name=os_name,
            remark=remark,
            vehicle=vehicle
        )
    except Exception as e:
        log.error(f"❌ Playwright Error: {e}")
        return False, str(e)

def sync_shops_from_website():
    """Website မှ ဆိုင်စာရင်းများကို Browser ဖြင့် Sync လုပ်ခြင်း (Sync Wrapper)"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    state_path = os.path.join(base_dir, "state.json")
    try:
        shops = browser_manager.browser_manager.run_task(_sync_shops_task, storage_state=state_path)
        if shops:
            count = db_manager.sync_website_shops(shops)
            return True, f"Synced {count} shops."
        return False, "No shops found."
    except Exception as e:
        log.error(f"❌ Sync Shops Error: {e}")
        return False, str(e)

def run(data, event):
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
    global _bot
    _bot = bot
    try:
        chat_id = message.chat.id
        user_id = message.from_user.id
        is_private = chat_id > 0
        
        # 🛡️ Staff Safety Net (Rule #1): ဝန်ထမ်းများအတွက် Auto Pickup အလုပ်မလုပ်စေရ
        user_level = db_manager.get_user_level(user_id, chat_id)
        if user_level >= 3:
            log.info(f"🛡️ Staff Safety Net: Skipping Auto Pickup for staff {user_id}")
            return
 
        # Pickup is Group-only (Safety check in case router fails)
        if is_private:
            log.info(f"⏭️ Skipping Auto Pickup: Private Chat detected ({chat_id})")
            return

        text = message.text or message.caption
        if not text:
            return

        log.info(f"🚚 Auto Pickup module handling message: {message.message_id}")

        # Check for waiting orders in group
        waiting_order = db_manager.get_waiting_confirm_order(chat_id)
        if waiting_order:
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
                else:
                    log.info(f"🔒 Flow Locked: Found WAITING_CONFIRM order {waiting_order[0]} for chat {chat_id}")
                    from handlers import pickup_handler
                    cleanup_pickup_intermediate_msgs(bot, chat_id, waiting_order[2])
                    pickup_handler.show_pickup_reconfirmation(bot, chat_id, waiting_order[0])
                    return

        # Get Group Title for Admin Notification
        chat_title = message.chat.title or "Unknown Shop"
        
        # Extract OS Name for internal logic (before 🤝)
        if '🤝' in chat_title:
            os_name = chat_title.split('🤝')[0].strip()
        else:
            os_name = db_manager.clean_shop_name(chat_title)

        extract_prompt = f"""
        Analyze the following message.
        Message: "{text}"

        Decide the action:
        1. 'PICKUP': If the user is EXPLICITLY requesting a new pickup order OR inquiring about pickup availability (e.g., "pick up လာယူပေးပါ", "လာကောက်ပေးပါ", "မနက်ဖြန်အတွက် တင်ပေးပါ", "pick up ရဦးမလား", "ဒီနေ့ pickup ရှိလား").
           CRITICAL: If the message is just sharing a list (e.g., "စာရင်းလေးပါ", "pickup စာရင်း"), discussing a past order, or mentioning "pickup" without requesting a new one or inquiring about availability, set action to 'OTHER'.
        2. 'LOOKUP_LOCATION': If the user is asking for the township of a specific location name (e.g., "Hledan က ဘယ်မြို့နယ်လဲ", "Junction City က ဘယ်မြို့နယ်ထဲမှာလဲ").
        3. 'OTHER': If it's casual conversation, greetings, sharing a list, or unrelated to a new pickup request.

        Output ONLY a JSON object with:
        - action: "PICKUP", "LOOKUP_LOCATION", or "OTHER"
        - is_pickup_request: boolean (True if this is a request to place a NEW pickup order OR an inquiry about pickup availability. If it's just sharing a list or info, set to false)
        - location_query: If action is 'LOOKUP_LOCATION', extract the location name they are asking about (e.g., "Hledan", "Junction City"). Otherwise null.
        - is_new_request: boolean (True if action is 'PICKUP' and it's a clear request to start a new order or an inquiry about availability)
        - vehicle: "Bicycle" or "Car" (Default to null if not mentioned)
        - date_type: "today" or "tomorrow" (If the user explicitly mentions "today" (ဒီနေ့) or "tomorrow" (မနက်ဖြန်), set accordingly. Otherwise, default to null)
        - clean_remark: Extract ONLY the additional instructions, notes, or specific details (like quantity, amount, location, or special requests) from the message in Burmese, EXCLUDING the core pickup request phrase (e.g., "pick up လာယူပေးပါ", "လာကောက်ပေးပါ").
        
        Note: You DO NOT need to extract the Shop/OS Name. We already have it from the group title.
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

        if action != 'PICKUP' or not extracted_data.get("is_new_request", True) or not extracted_data.get("is_pickup_request", True):
            log.info(f"ℹ️ Message {message.message_id} is not a pickup request (Action: {action}). Skipping auto_pickup.")
            return

        vehicle = extracted_data.get("vehicle")
        clean_remark = extracted_data.get("clean_remark")
        ai_date_type = extracted_data.get("date_type")

        if clean_remark:
            db_manager.update_message_status(message.message_id, chat_id, 'PENDING', summary=clean_remark)
        
        # Update queue if exists (for vehicle/remark preservation)
        with db_manager.get_connection() as conn:
            conn.execute("""
                UPDATE pickup_queue
                SET vehicle = COALESCE(?, vehicle),
                    remark = COALESCE(?, remark)
                WHERE orig_msg_id = ? AND chat_id = ?
            """, (vehicle, clean_remark, message.message_id, chat_id))

        tz = pytz.timezone('Asia/Yangon')
        now = datetime.now(tz)
        current_time = now.hour * 100 + now.minute
        
        is_system_off = db_manager.get_auto_pickup_global_status() == 'OFF'

        # 1. Admin Group Notification (Unified Photo Message)
        admin_chat_id = -1003601049225
        admin_topic_id = 878
        
        clean_chat_id = str(chat_id).replace("-100", "")
        msg_link = f"https://t.me/c/{clean_chat_id}/{message.message_id}"

        admin_alert_text = (
            f"🚚 **Pick Up alert**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏪 ဆိုင်: <b>{os_name}</b>\n"
            f"📅 ရက်စွဲ: <b>-</b>\n"
            f"🚲 ယာဉ်: <b>{vehicle if vehicle else '-'}</b>\n"
            f"📝 မှတ်ချက်: {clean_remark if clean_remark else '-'}\n"
            f"📊 Status: <b>⏳ Pending</b>\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        
        admin_markup = types.InlineKeyboardMarkup()
        admin_markup.add(
            types.InlineKeyboardButton("🔗 View Message", url=msg_link),
            types.InlineKeyboardButton("✅ Done", callback_data=f"done_{message.message_id}_{chat_id}")
        )
        
        try:
            # Start with a Photo Message to allow editMessageMedia later
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            icon_path = os.path.join(base_dir, "appIcon.png")
            
            if os.path.exists(icon_path):
                with open(icon_path, 'rb') as photo:
                    alert_msg = bot.send_photo(
                        admin_chat_id, photo,
                        caption=admin_alert_text,
                        parse_mode="HTML",
                        message_thread_id=admin_topic_id,
                        reply_markup=admin_markup
                    )
            else:
                alert_msg = bot.send_message(
                    admin_chat_id, admin_alert_text,
                    parse_mode="HTML",
                    message_thread_id=admin_topic_id,
                    reply_markup=admin_markup
                )

            if alert_msg:
                db_manager.save_alert_tracking(message.message_id, chat_id, alert_msg.message_id, admin_chat_id)
                # 💡 Pick up request ကို Auditor က ၁၅ မိနစ် alert ထပ်မပို့အောင် ALERTED status ကို ချက်ချင်းပြောင်းပါမည်
                db_manager.update_message_status(message.message_id, chat_id, 'ALERTED')
            log.info(f"🔔 Unified Admin notification sent for pickup request from {chat_title}")
        except Exception as e:
            log.error(f"❌ Failed to send admin pickup notification: {e}")

        # 2. Silent Mode for Group Chat when Pickup is OFF
        # 2. Silent Mode for Group Chat when Pickup is OFF (Whitelist Group -1003539520778 bypasses this)
        if is_system_off and chat_id != -1003539520778:
            log.info(f"🔇 Silent Mode: Pickup is OFF. Returning silently for group {chat_id}")
            return

        # Time-based Date Logic (Strict)
        if 1 <= current_time <= 1100:
            date_type = "today"
            log.info(f"🕒 Time {current_time}: Auto-assigning TODAY (Strict)")
        elif 1501 <= current_time <= 2359 or current_time == 0:
            date_type = "tomorrow"
            log.info(f"🕒 Time {current_time}: Auto-assigning TOMORROW (Strict)")
        else: # 11:01 AM to 03:00 PM
            if ai_date_type == "tomorrow":
                date_type = "tomorrow"
                log.info(f"🕒 Time {current_time}: AI suggested TOMORROW")
            else:
                send_staff_decision_alert(bot, message, os_name, vehicle if vehicle else "none")
                return

        target_date_str = (now if date_type == "today" else now + timedelta(days=1)).strftime("%d-%m-%Y")
        if db_manager.check_existing_pickup(chat_id, target_date_str):
            log.info(f"⚠️ Duplicate pickup detected for {chat_id} on {target_date_str}")
            show_duplicate_alert(bot, message, target_date_str, message.message_id)
            return

        if not vehicle:
            ask_vehicle(bot, message, date_type, message.message_id, show_cancel=True)
            return
        else:
            ask_remark(bot, chat_id, date_type, vehicle, message.message_id, show_cancel=True)

        if date_type in ["today", "tomorrow"]:
            update_central_pickup_alert(bot, message.message_id, chat_id, "⏳ Pending")

    except Exception as e:
        log.error(f"❌ Auto Pickup Handle Error: {e}")
        bot.reply_to(message, "⚠️ Auto Pickup လုပ်ဆောင်စဉ် အမှားတစ်ခု ဖြစ်သွားပါသည်။")

def show_duplicate_alert(bot, message, target_date, orig_msg_id):
    text = f"ဒီနေ့ ({target_date}) အတွက် pickup တင်ပြီးသားရှိပါတယ် အစ်ကို။"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💬 Admin နှင့်ပြောမည်", callback_data=f"ap_admin_0_{orig_msg_id}"))
    msg = bot.reply_to(message, text, reply_markup=markup)
    db_manager.add_pickup_intermediate_msg(message.chat.id, orig_msg_id, msg.message_id)

def ask_vehicle(bot, message, date_type, orig_msg_id, show_cancel=True):
    text = "ဒီနေ့ pick up လေးရပါတယ်နော်။ pick up တင်ပေးနိုင်ရန် ****လိုတဲ့အချက် (စက်ဘီး၊ကား)*** ကိုပြောပေးပါဦး"
    if date_type == "tomorrow":
        text = "မနက်ဖြန်အတွက် pick up တင်ပေးနိုင်ရန် ****လိုတဲ့အချက် (စက်ဘီး၊ကား)*** ကိုပြောပေးပါဦး"
        
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🚲 စက်ဘီး (Bicycle)", callback_data=f"ap_vh_{orig_msg_id}_{date_type}_Bicycle"),
        types.InlineKeyboardButton("🚗 ကား (Car)", callback_data=f"ap_vh_{orig_msg_id}_{date_type}_Car")
    )
    if show_cancel:
        markup.add(types.InlineKeyboardButton("❌ Pickup မဟုတ်ပါ", callback_data=f"ap_cancel_{orig_msg_id}"))
    msg = bot.reply_to(message, text, reply_markup=markup)
    db_manager.add_pickup_intermediate_msg(message.chat.id, orig_msg_id, msg.message_id)

def ask_remark(bot, chat_id, date_type, vehicle, orig_msg_id, show_cancel=True):
    text = "pick up အချက်အလက်စုံရင် Pick up တင်ပေးပါတော့မယ်၊ ထည့်ချင်တဲ့မှတ်ချက်ရှိရင် ရေးပေးပါခင်ဗျ"
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📝 မှတ်ချက်ရေးမည်", callback_data=f"ap_rm_{orig_msg_id}_{date_type}_{vehicle}_write"),
        types.InlineKeyboardButton("❌ မှတ်ချက်မရှိပါ", callback_data=f"ap_rm_{orig_msg_id}_{date_type}_{vehicle}_none")
    )
    if show_cancel:
        markup.add(types.InlineKeyboardButton("❌ Pickup မဟုတ်ပါ", callback_data=f"ap_cancel_{orig_msg_id}"))
    msg = bot.send_message(chat_id, text, reply_to_message_id=orig_msg_id, reply_markup=markup)
    db_manager.add_pickup_intermediate_msg(chat_id, orig_msg_id, msg.message_id)

def send_staff_decision_alert(bot, message, os_name, vehicle):
    """ Admin Group ရှိ မူလ Alert ကို Waiting Decision အဖြစ် Update လုပ်ခြင်း """
    try:
        chat_id = message.chat.id
        orig_msg_id = message.message_id
        
        # ဆိုင် Group ထဲသို့ အကြောင်းကြားစာပို့ခြင်း
        late_pickup_text = "Pick up တင်တာ (၁၁)နာရီကျော်ပြီမိုလို Pick up လမ်းကြောင်းလေးရသေးလားဆိုတာ အတည်ပြုပြီးပြန်လည်အကြောင်းပြန်ပေးပါ့မယ်ရှင်။"
        markup_shop = types.InlineKeyboardMarkup()
        markup_shop.add(types.InlineKeyboardButton("❌ Pickup မဟုတ်ပါ", callback_data=f"ap_cancel_{orig_msg_id}"))
        msg = bot.reply_to(message, late_pickup_text, reply_markup=markup_shop)
        db_manager.add_pickup_intermediate_msg(chat_id, orig_msg_id, msg.message_id)

        # Admin Group ရှိ မူလ Alert ကို Update လုပ်ခြင်း
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("📅 Today", callback_data=f"ap_st_{orig_msg_id}_{chat_id}_today_{vehicle}"),
            types.InlineKeyboardButton("📅 Tomorrow", callback_data=f"ap_st_{orig_msg_id}_{chat_id}_tomorrow_{vehicle}")
        )
        
        update_central_pickup_alert(bot, orig_msg_id, chat_id, "⏳ Waiting Decision (Staff)", custom_markup=markup)

    except Exception as e:
        log.error(f"❌ send_staff_decision_alert Error: {e}")

def update_central_pickup_alert(bot, orig_msg_id, chat_id, status_text, show_done=True, photo_path=None, custom_markup=None):
    """ Admin Group နှင့် ဆိုင် Group ရှိ Alert Message များကို Update လုပ်ခြင်း """
    try:
        central_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))
        tracking = db_manager.get_alert_tracking(orig_msg_id, chat_id)
        
        order = None
        with db_manager.get_connection() as conn:
            order = conn.execute("SELECT os_name, target_date, remark, vehicle, status, shop_msg_id FROM pickup_queue WHERE orig_msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
        
        if not order:
            ctx = db_manager.get_message_context(orig_msg_id, chat_id)
            _, _, shop_name = db_manager.get_topic_context(chat_id, 0)
            os_name = shop_name
            target_date = "-"
            remark = ctx[1] if ctx and ctx[1] else "-"
            vehicle = "-"
            shop_msg_id = None
        else:
            os_name, target_date, remark, vehicle, _, shop_msg_id = order

        clean_chat_id = str(chat_id).replace("-100", "")
        msg_link = f"https://t.me/c/{clean_chat_id}/{orig_msg_id}"

        # 1. Update Shop Group Message (If exists)
        if shop_msg_id:
            shop_status_text = (
                f"✅ **အတည်ပြုပြီးပါပြီ!**\n\n"
                f"🏪 ဆိုင်: <b>{os_name}</b>\n"
                f"📅 ရက်စွဲ: <b>{target_date}</b>\n"
                f"🚲 ယာဉ်: <b>{vehicle}</b>\n"
                f"📝 မှတ်ချက်: {remark if remark else '-'}\n"
                f"📊 Status: <b>{status_text}</b>\n\n"
                f"Pick up လေးတင်ပေးထားပါတယ်နော်"
            )
            try:
                bot.edit_message_text(shop_status_text, chat_id, shop_msg_id, parse_mode="HTML")
            except Exception as e:
                log.debug(f"Shop message update skip: {e}")

        # 2. Update Admin Group Message
        if not tracking:
            return

        alert_msg_id = tracking[0]
        new_caption = (
            f"🚚 **Pick Up alert**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏪 ဆိုင်: <b>{os_name}</b>\n"
            f"📅 ရက်စွဲ: <b>{target_date}</b>\n"
            f"🚲 ယာဉ်: <b>{vehicle}</b>\n"
            f"📝 မှတ်ချက်: {remark}\n"
            f"📊 Status: <b>{status_text}</b>\n"
            f"━━━━━━━━━━━━━━━━━━"
        )

        if custom_markup:
            markup = custom_markup
            # Ensure View Message and Done are always there if not already in custom_markup
            has_view = any(getattr(b, 'url', None) == msg_link for row in markup.keyboard for b in row)
            if not has_view:
                markup.add(types.InlineKeyboardButton("🔗 View Message", url=msg_link))
            
            has_done = any(getattr(b, 'callback_data', None) == f"done_{orig_msg_id}_{chat_id}" for row in markup.keyboard for b in row)
            if not has_done and show_done:
                markup.add(types.InlineKeyboardButton("✅ Done", callback_data=f"done_{orig_msg_id}_{chat_id}"))
        else:
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("🔗 View Message", url=msg_link),
                types.InlineKeyboardButton("✅ Done", callback_data=f"done_{orig_msg_id}_{chat_id}")
            )

        try:
            # Check if the original message was a photo or text
            # If we have a photo_path, we try to use editMessageMedia
            if photo_path and os.path.exists(photo_path):
                with open(photo_path, 'rb') as photo:
                    bot.edit_message_media(
                        media=types.InputMediaPhoto(photo, caption=new_caption, parse_mode="HTML"),
                        chat_id=central_chat,
                        message_id=alert_msg_id,
                        reply_markup=markup
                    )
            else:
                # Try editing as caption first (if it was a photo)
                try:
                    bot.edit_message_caption(
                        caption=new_caption,
                        chat_id=central_chat,
                        message_id=alert_msg_id,
                        parse_mode="HTML",
                        reply_markup=markup
                    )
                except Exception as e:
                    # If it fails, it might be a text message
                    bot.edit_message_text(
                        text=new_caption,
                        chat_id=central_chat,
                        message_id=alert_msg_id,
                        parse_mode="HTML",
                        reply_markup=markup
                    )
        except Exception as edit_e:
            if "message is not modified" not in str(edit_e):
                log.error(f"❌ Failed to edit central alert: {edit_e}")

    except Exception as e:
        log.error(f"❌ update_central_pickup_alert Error: {e}")

def send_success_report(bot, orig_msg_id, chat_id, handled_by="System"):
    """ အောင်မြင်သွားသော Pickup စာရင်းကို သီးသန့် Success Group သို့ ပို့ဆောင်ခြင်း """
    try:
        success_chat = -1003906164269
        success_topic = 28
        
        order = None
        with db_manager.get_connection() as conn:
            order = conn.execute("SELECT os_name, target_date, remark, vehicle FROM pickup_queue WHERE orig_msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
        
        if not order:
            return

        os_name, target_date, remark, vehicle = order
        clean_chat_id = str(chat_id).replace("-100", "")
        msg_link = f"https://t.me/c/{clean_chat_id}/{orig_msg_id}"

        report_text = (
            f"✅ **Auto Pickup Success**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏪 ဆိုင်: <b>{os_name}</b>\n"
            f"📅 ရက်စွဲ: <b>{target_date}</b>\n"
            f"🚲 ယာဉ်: <b>{vehicle}</b>\n"
            f"📝 မှတ်ချက်: {remark}\n"
            f"👤 အတည်ပြုသူ: <b>{handled_by}</b>\n"
            f"━━━━━━━━━━━━━━━━━━"
        )

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔗 View Message", url=msg_link))

        bot.send_message(success_chat, report_text, parse_mode="HTML", message_thread_id=success_topic, reply_markup=markup)
        log.info(f"📤 Success Report sent to {success_chat}/{success_topic} for {os_name}")

    except Exception as e:
        log.error(f"❌ send_success_report Error: {e}")

def cleanup_pickup_intermediate_msgs(bot, chat_id, orig_msg_id):
    try:
        msg_ids = db_manager.get_pickup_intermediate_msgs(chat_id, orig_msg_id)
        for mid in msg_ids:
            try:
                bot.delete_message(chat_id, mid)
            except Exception:
                pass
        db_manager.delete_pickup_intermediate_msgs(chat_id, orig_msg_id)
    except Exception as e:
        log.error(f"❌ Cleanup Pickup Intermediate Messages Error: {e}")

def run_queue_worker(bot):
    global _bot
    _bot = bot
    log.info("🚀 Auto Pickup Queue Worker စတင်နေပါပြီ...")
    while True:
        try:
            item = db_manager.get_next_queued_pickup()
            if not item:
                time.sleep(5)
                continue

            queue_id, chat_id, orig_msg_id, target_date, os_name, remark, vehicle = item

            if db_manager.get_auto_pickup_global_status() == 'OFF' and chat_id != -1003539520778:
                log.warning(f"🛑 System is OFF. Aborting order {queue_id} for {os_name}")
                db_manager.update_queue_status(queue_id, 'CANCELLED', error_msg="System Shutdown (OFF)")
                update_central_pickup_alert(bot, orig_msg_id, chat_id, "❌ Aborted (System OFF)", show_done=False)
                continue

            log.info(f"📦 Processing Queue Item {queue_id} for {os_name}")
            
            # --- Strict Mapping Logic ---
            mapped_name = db_manager.get_shop_mapping(chat_id)
            final_os_name = None
            
            if mapped_name:
                final_os_name = mapped_name
                log.info(f"🎯 Using saved mapping: {final_os_name}")
            else:
                # Check if the current os_name exists exactly on the website
                if db_manager.is_website_shop_exists(os_name):
                    final_os_name = os_name
                    db_manager.set_shop_mapping(chat_id, os_name)
                    log.info(f"✨ Self-learned: Exact match found for {os_name}. Saved to mapping.")
                else:
                    log.warning(f"🛑 No mapping found for {os_name} and no exact match in website_shops.")
                    db_manager.update_queue_status(queue_id, 'FAILED', error_msg="Shop Mapping Missing")
                    update_central_pickup_alert(bot, orig_msg_id, chat_id, "❌ Failed (Mapping Missing)")
                    
                    alert_msg = f"<b>Shop Mapping Missing</b>\n🏪 ဆိုင်: {os_name}\n⚠️ ဆိုင်နာမည် Mapping မရှိသေးပါ။ Manager မှ Fix Shop Mapping ကိုနှိပ်၍ အရင်ပြင်ပေးပါရန်။"
                    asyncio.run(send_pickup_notification(alert_msg, is_alert=True))
                    
                    handle_pickup_error(bot, chat_id, orig_msg_id, os_name, target_date, "ဆိုင်နာမည် Mapping မရှိသေးပါ။ Manager မှ Fix Shop Mapping ကိုနှိပ်၍ အရင်ပြင်ပေးပါရန်။")
                    continue

            if not all([target_date, final_os_name, vehicle]) or vehicle == "none":
                log.error(f"❌ Strict Validation Failed for Queue {queue_id}: Missing required fields.")
                db_manager.update_queue_status(queue_id, 'FAILED', error_msg="Missing required fields (Date, Shop, or Vehicle)")
                update_central_pickup_alert(bot, orig_msg_id, chat_id, "❌ Failed (Missing Data)")
                
                if not vehicle or vehicle == "none":
                    tz = pytz.timezone('Asia/Yangon')
                    now = datetime.now(tz)
                    today_str = now.strftime("%d-%m-%Y")
                    date_type = "today" if target_date == today_str else "tomorrow"
                    ask_vehicle(bot, types.Message(message_id=orig_msg_id, from_user=None, date=None, chat=types.Chat(id=chat_id, type='group'), text=remark), date_type, orig_msg_id)
                continue

            db_manager.update_queue_status(queue_id, 'PROCESSING')
            update_central_pickup_alert(bot, orig_msg_id, chat_id, "⏳ Processing (စက်ရုပ်တင်နေပါသည်)")

            success, msg = submit_pickup_order(target_date, final_os_name, remark, vehicle)
            
            if success:
                db_manager.update_queue_status(queue_id, 'SUCCESS')
                
                # 1. ဆိုင် Group ထဲသို့ အောင်မြင်ကြောင်း စာပို့ခြင်း
                try:
                    bot.send_message(chat_id, "✅ Pickup တင်ခြင်း အောင်မြင်ပါသည်။", reply_to_message_id=orig_msg_id)
                except Exception as e:
                    log.error(f"❌ Failed to send success message to shop group: {e}")

                # 2. ဆိုင် Group ကို Success ပြောင်းရန် (Admin Alert ကိုပါ update လုပ်ရန် ကြိုးစားမည်)
                update_central_pickup_alert(bot, orig_msg_id, chat_id, "✅ Success (စက်ရုပ်တင်ပြီးပါပြီ)")

                # 3. Success Group သို့ Report ပို့ခြင်း
                send_success_report(bot, orig_msg_id, chat_id, handled_by="စက်ရုပ် (Auto)")

                # 4. Admin Group ရှိ Alert Message ကို ဖျက်ခြင်း
                try:
                    tracking = db_manager.get_alert_tracking(orig_msg_id, chat_id)
                    if tracking:
                        central_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))
                        bot.delete_message(central_chat, tracking[0])
                except: pass

                db_manager.resolve_message(orig_msg_id, chat_id, 'System (Auto-Pickup)', method='Auto', status='HANDLED_BY_AI')
                cleanup_pickup_intermediate_msgs(bot, chat_id, orig_msg_id)

                if not mapped_name and final_os_name != os_name:
                    db_manager.set_shop_mapping(chat_id, final_os_name)
                    log.info(f"📝 Auto-mapped {chat_id} to {final_os_name}")
            else:
                db_manager.update_queue_status(queue_id, 'FAILED', error_msg=msg)
                
                # Update central alert status (Keep as Processing in Shop Group, but show error in Admin)
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                screenshot_path = os.path.join(base_dir, "before_save.png")
                
                update_central_pickup_alert(
                    bot, orig_msg_id, chat_id,
                    "⏳ Processing (စက်ရုပ်တင်နေပါသည်)", # Keep Shop Group as Processing
                    photo_path=screenshot_path if os.path.exists(screenshot_path) else None
                )
                
                # Log error to Admin Topic 878 separately if needed,
                # but the unified alert already shows the screenshot.

            time.sleep(10)

        except Exception as e:
            log.error(f"❌ Queue Worker Error: {e}")
            time.sleep(10)

# handle_pickup_error is now integrated into update_central_pickup_alert

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
