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

def get_best_shop_name(bot, chat_id):
    """
    Centralized helper to get the most accurate shop name.
    Checks DB first (with ID mismatch fix), then falls back to Chat Title.
    """
    try:
        _, _, shop_name = db_manager.get_topic_context(chat_id, 1)
        if shop_name and shop_name != "Unknown Shop":
            return shop_name
        
        # Fallback to Chat Title
        chat_info = bot.get_chat(chat_id)
        chat_title = chat_info.title or "Unknown Shop"
        if '🤝' in chat_title:
            return chat_title.split('🤝')[0].strip()
        return db_manager.clean_shop_name(chat_title)
    except Exception as e:
        log.error(f"❌ get_best_shop_name Error: {e}")
        return "Unknown Shop"

async def send_pickup_notification(text, is_alert=False):
    """
    Centralized notification helper for Topic 878 (Pickup Command Center)
    """
    bot = _bot
    if not bot:
        token = os.getenv('TELEGRAM_BOT_TOKEN')
        if token:
            bot = telebot.TeleBot(token, threaded=True)
        else:
            log.error("❌ send_pickup_notification: Bot instance not initialized and token missing.")
            return

    target_chat_id = -1003601049225
    topic_id = 878
    emoji = "⚠️" if is_alert else "✅"
    
    # Formatting
    formatted_text = f"{emoji} {text}"
    
    try:
        # Use asyncio.to_thread for sync telebot calls
        await asyncio.to_thread(
            bot.send_message,
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
        # 💡 Use perform_login_on_page to avoid deadlock
        success, msg = await auto_login.perform_login_on_page(page)
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
            popups = p.locator(".swal-button--confirm, .swal-button--cancel, .swal-button, .ant-modal-close, .ant-btn-primary, .ant-btn")
            count = await popups.count()
            if count > 0:
                for i in range(count):
                    try:
                        if await popups.nth(i).is_visible():
                            log.info(f"🛡️ Attempting to click popup button {i+1}/{count}...")
                            await popups.nth(i).click(timeout=3000)
                            await asyncio.sleep(1)
                    except:
                        continue
            
            # Wait for overlay to disappear or force remove it
            overlay = p.locator(".swal-overlay, .ant-modal-mask, .ant-modal-wrap")
            o_count = await overlay.count()
            if o_count > 0:
                log.info(f"🛡️ Found {o_count} popup overlays. Waiting for them to hide...")
                try:
                    await overlay.first.wait_for(state="hidden", timeout=5000)
                except:
                    log.warning("⚠️ Overlay still present, attempting force removal via JS...")
                    await p.evaluate("() => { document.querySelectorAll('.swal-overlay, .ant-modal-mask, .ant-modal-wrap').forEach(el => el.remove()); document.body.classList.remove('swal-shown', 'ant-scrolling-effect'); }")
                    await asyncio.sleep(1)
        except Exception as e:
            log.debug(f"Popup handling skip: {e}")

    async def wait_for_page_ready(p):
        await handle_popups(p)
        try:
            await p.wait_for_selector(".ant-spin, .loading-spinner, .spinner", state="hidden", timeout=10000)
        except:
            pass
        await p.wait_for_selector("form, .ant-form, input[id*='receivedDate'], label:has-text('Received Date')", state="visible", timeout=15000)

    try:
        await wait_for_page_ready(page)
    except Exception as e:
        log.warning(f"⚠️ Page ready check timed out: {e}. Attempting to proceed anyway.")

    log.info("📝 အချက်အလက်များ ဖြည့်သွင်းနေပါသည်...")
    await handle_popups(page)

    # (က) Date
    # 💡 Use a more robust label-based selector for the date field
    date_input = page.locator("//label[contains(text(), 'Received Date')]/following::input[1]")
    
    try:
        await date_input.wait_for(state="visible", timeout=15000)
    except Exception:
        log.warning("⚠️ Date field မတွေ့ပါ။ Page ကို Refresh လုပ်ပြီး တစ်ကြိမ် ထပ်ကြိုးစားကြည့်ပါမည်...")
        await page.reload()
        await page.wait_for_load_state('domcontentloaded')
        await wait_for_page_ready(page)
        await date_input.wait_for(state="visible", timeout=20000)

    # Set date using multiple methods to ensure it sticks (especially for readonly fields)
    log.info(f"📅 ရက်စွဲ သတ်မှတ်နေပါသည်: {target_date}")
    await date_input.evaluate("""(el, val) => {
        el.value = val;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }""", target_date)
    await asyncio.sleep(1)
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
    
    # Wait for dropdown options to appear with retries
    found = False
    for attempt in range(3):
        log.info(f"   ⏳ OS Name options ရှာဖွေနေပါသည် (Attempt {attempt+1}/3)...")
        await asyncio.sleep(2)
        options = page.locator(".ant-select-item-option-content, .select-option, [role='option']")
        count = await options.count()
        
        if count > 0:
            # 1. Try Exact Match first
            for i in range(count):
                opt_text = (await options.nth(i).inner_text()).strip()
                if os_name.lower() == opt_text.lower():
                    await options.nth(i).click()
                    found = True
                    log.info(f"   ✅ OS Name အတိအကျတူသည်ကို တွေ့ရှိ၍ ရွေးချယ်လိုက်ပါသည် ({opt_text})")
                    break
            
            if found: break

            # 2. Try Partial Match if exact fails
            for i in range(count):
                opt_text = (await options.nth(i).inner_text()).strip()
                if os_name.lower() in opt_text.lower() or opt_text.lower() in os_name.lower():
                    log.warning(f"   ⚠️ OS Name အတိအကျမတူသော်လည်း ဆင်တူသည်ကို တွေ့ရှိ၍ ရွေးချယ်လိုက်ပါသည် ({opt_text})")
                    await options.nth(i).click()
                    found = True
                    break
            
            if found: break
        
    if not found:
        log.error(f"   ❌ OS Name ({os_name}) ကို Website ထဲတွင် ရှာမတွေ့ပါ။")
        # Last resort: Try ArrowDown + Enter if there are any options at all
        options = page.locator(".ant-select-item-option-content, .select-option, [role='option']")
        if await options.count() > 0:
            log.warning("   ⚠️ Option တိုက်ရိုက်နှိပ်မရသဖြင့် Keyboard ဖြင့် ရွေးချယ်ကြည့်ပါမည်...")
            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.5)
            await page.keyboard.press("Enter")
            found = True
            log.info("   ✅ Keyboard ဖြင့် ပထမဆုံး option ကို ရွေးချယ်လိုက်ပါသည်")
        else:
            return False, f"Website ထဲတွင် ဆိုင်နာမည် ({os_name}) ကို ရှာမတွေ့ပါ။"

    log.info(f"   ✅ OS Name ရွေးပြီးပါပြီ")
    
    # 💡 Website Auto-fill ကို စောင့်ခြင်း (ဆိုင်ရွေးပြီးရင် အချက်အလက်အဟောင်းတွေ တက်လာတတ်လို့)
    log.info("⏳ Website auto-fill လုပ်ဆောင်ချက်ကို (၅) စက္ကန့် စောင့်နေပါသည်...")
    await asyncio.sleep(5)

    # (ဂ) Vehicle
    vehicle_input = page.locator("(//label[contains(text(), 'Vehicle')]/following::input)[1]")
    await handle_popups(page)
    
    log.info(f"🚲 ယာဉ်အမျိုးအစား ရွေးချယ်နေပါသည် ({vehicle})...")
    await vehicle_input.click()
    await asyncio.sleep(1)
    await vehicle_input.fill("")
    await vehicle_input.press_sequentially(vehicle, delay=100)
    await asyncio.sleep(2)
    
    # More robust dropdown selection for Ant Design
    v_found = False
    try:
        # Wait for dropdown to be visible
        await page.wait_for_selector(".ant-select-dropdown:not(.ant-select-dropdown-hidden)", timeout=5000)
        v_options = page.locator(".ant-select-item-option")
        v_count = await v_options.count()
        for i in range(v_count):
            v_text = await v_options.nth(i).inner_text()
            if vehicle.lower() in v_text.lower():
                log.info(f"   🎯 Found vehicle option: {v_text.strip()}. Clicking...")
                await v_options.nth(i).click(force=True)
                v_found = True
                await asyncio.sleep(1)
                break
    except Exception as ve:
        log.debug(f"Vehicle dropdown click failed: {ve}")

    if not v_found:
        log.warning(f"⚠️ Vehicle option '{vehicle}' not found via direct click. Trying keyboard fallback...")
        await page.keyboard.press("ArrowDown")
        await asyncio.sleep(1)
        await page.keyboard.press("Enter")
        await asyncio.sleep(1)

    # Final check: If dropdown is still open, force close it with Escape
    try:
        if await page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)").count() > 0:
            log.warning("⚠️ Dropdown still open. Forcing close with Escape...")
            await page.keyboard.press("Escape")
            await asyncio.sleep(1)
    except: pass
    
    log.info(f"   ✅ ယာဉ်ရွေးပြီးပါပြီ ({vehicle})")

    # (ဃ) Remark (Fill this LAST to ensure it's not overwritten by website auto-fill)
    log.info(f"📝 မှတ်ချက် နောက်ဆုံးမှ ထပ်မံဖြည့်သွင်းနေပါသည် ({remark})...")
    remark_input = page.locator("textarea[name='order.remark']")
    await remark_input.click(click_count=3) # Select all existing text
    await page.keyboard.press("Backspace")
    await remark_input.fill(remark)
    await asyncio.sleep(1)
    log.info(f"   ✅ မှတ်ချက်ဖြည့်ပြီးပါပြီ")

    await page.locator("body").click(force=True)
    await asyncio.sleep(2) 

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    screenshot_path = os.path.join(base_dir, "before_save.png")
    await page.screenshot(path=screenshot_path, full_page=True)
    log.info(f"📸 SAVE ခလုတ်မနှိပ်မီ မျက်နှာပြင်ကို '{screenshot_path}' အဖြစ် မှတ်တမ်းတင်ထားပါသည်။")

    # --- ၃။ Save ခလုတ်နှိပ်ခြင်း ---
    await handle_popups(page)
    await asyncio.sleep(1)
    
    # Try multiple selectors for the Save button
    save_selectors = [
        "button.ant-btn-primary",
        "button:has-text('SAVE')",
        "button:has-text('Save')",
        "//button[descendant::span[contains(text(), 'SAVE') or contains(text(), 'Save')]]"
    ]
    
    save_btn = None
    for selector in save_selectors:
        try:
            loc = page.locator(selector).first
            if await loc.is_visible():
                save_btn = loc
                break
        except:
            continue

    if save_btn:
        log.info("💾 SAVE ခလုတ်ကို နှိပ်လိုက်ပါပြီ။")
        await save_btn.click(force=True)
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

def handle(bot, message, force_pickup=False):
    global _bot
    _bot = bot
    try:
        chat_id = message.chat.id
        user_id = message.from_user.id
        is_private = chat_id > 0
        
        # 🛡️ Global Toggle Check (Early Exit)
        # Whitelist Group -1003539520778 bypasses this
        is_system_off = db_manager.get_auto_pickup_global_status() == 'OFF'
        # 💡 Note: We no longer exit early here even if OFF,
        # to allow the AI to detect pickup and send Admin Alerts.

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

        # 💡 Ensure message is logged in DB before processing (Fix for Race Condition)
        topic_id = message.message_thread_id if (getattr(message, 'is_topic_message', False) and message.message_thread_id) else 1
        db_manager.log_message(
            message.message_id, chat_id, topic_id, user_id,
            text, message.date, media_id=None
        )

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
                    status = waiting_order[7]
                    log.info(f"🔒 Flow Locked: Found {status} order {waiting_order[0]} for chat {chat_id}")
                    cleanup_pickup_intermediate_msgs(bot, chat_id, waiting_order[2])
                    
                    if status == 'WAITING_SETUP':
                        # 💡 Phase 1: If in setup phase, show interactive setup again
                        # We need to determine date_type from target_date
                        target_date_str = waiting_order[3]
                        tz = pytz.timezone('Asia/Yangon')
                        now = datetime.now(tz)
                        today_str = now.strftime("%d-%m-%Y")
                        date_type = "today" if target_date_str == today_str else "tomorrow"
                        
                        show_interactive_setup(bot, chat_id, waiting_order[2], date_type)
                    else:
                        from handlers import pickup_handler
                        pickup_handler.show_pickup_reconfirmation(bot, chat_id, waiting_order[0])
                    return

        # 💡 Get Best Shop Name (DB + Fallback)
        os_name = get_best_shop_name(bot, chat_id)

        # Fetch recent feedback and distilled rules for AI learning
        feedbacks = db_manager.get_isolated_feedback(chat_id, 1, limit=10)
        distilled_rules = db_manager.get_isolated_rules(chat_id, 1)
        
        not_pickup_examples = []
        is_pickup_examples = []
        for cat, txt in feedbacks:
            if cat == 'MISSING_PICKUP':
                is_pickup_examples.append(txt)
            else:
                not_pickup_examples.append(f"{cat}: {txt}")

        feedback_context = ""
        if distilled_rules:
            feedback_context += "\n[Distilled Lessons from Past Mistakes]:\n"
            for rule in distilled_rules[:5]: # Use top 5 rules
                feedback_context += f"- {rule}\n"
                
        if not_pickup_examples:
            feedback_context += "\n[Recent Examples of messages that are NOT Pickups]:\n"
            for txt in not_pickup_examples:
                feedback_context += f"- {txt}\n"
        if is_pickup_examples:
            feedback_context += "\n[Recent Examples of messages that ARE Pickups]:\n"
            for txt in is_pickup_examples:
                feedback_context += f"- {txt}\n"

        extract_prompt = f"""
        {feedback_context}
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
        - clean_remark: Extract ONLY the additional instructions, notes, or specific details (like quantity, amount, location, or special requests) from the message in Burmese.
          CRITICAL EXCLUSIONS:
          1. EXCLUDE the core pickup request phrases (e.g., "pick up လာယူပေးပါ", "လာကောက်ပေးပါ", "တင်ပေးပါ", "ခေါ်ပေးပါ", "လာခဲ့ပေးပါ").
          2. EXCLUDE vehicle mentions (e.g., "စက်ဘီးနဲ့", "ကားနဲ့", "Bicycle", "Car") as they are already captured in the 'vehicle' field.
          3. EXCLUDE date mentions (e.g., "ဒီနေ့", "မနက်ဖြန်") as they are already captured in 'date_type'.
          If NO additional instructions remain after these exclusions, set clean_remark to null.
        
        Note: You DO NOT need to extract the Shop/OS Name. We already have it from the group title.
        """

        ai_res_content = ai_utils.get_ai_completion(
            prompt=extract_prompt,
            model="google/gemini-3.1-flash-lite-preview",
            response_format={ "type": "json_object" },
            timeout=30.0
        )
        
        if not ai_res_content:
            log.error("❌ AI Extraction failed in auto_pickup.")
            return

        try:
            cleaned_json = ai_utils.clean_ai_json(ai_res_content)
            extracted_data = json.loads(cleaned_json)
        except Exception as je:
            log.error(f"❌ JSON Parsing Error in auto_pickup: {je}")
            log.error(f"📝 Raw AI Response: {ai_res_content}")
            return

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

        if not force_pickup:
            if action != 'PICKUP' or not extracted_data.get("is_new_request", True) or not extracted_data.get("is_pickup_request", True):
                log.info(f"ℹ️ Message {message.message_id} is not a pickup request (Action: {action}). Skipping auto_pickup.")
                return

        # 🛡️ Anti-Spam/Duplicate Trigger Check (Prevent multiple alerts for rapid-fire messages)
        if not force_pickup and db_manager.check_active_pickup_session(chat_id, minutes=3):
            log.info(f"⏳ Active pickup session detected for {chat_id}. Skipping duplicate trigger for msg {message.message_id}.")
            return

        vehicle = extracted_data.get("vehicle")
        clean_remark = extracted_data.get("clean_remark")
        ai_date_type = extracted_data.get("date_type")

        if clean_remark:
            db_manager.update_message_status(message.message_id, chat_id, 'PENDING', summary=clean_remark, category='PICKUP')
        else:
            db_manager.update_message_status(message.message_id, chat_id, 'PENDING', category='PICKUP')
        
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
        log.info(f"🕒 Auto Pickup Time Check: {current_time} (Yangon)")
        
        is_system_off = db_manager.get_auto_pickup_global_status() == 'OFF'
        is_whitelisted = chat_id == -1003539520778

        # --- [ Duplicate Check & Date Logic (Moved Up) ] ---
        # Time-based Date Logic (Strict)
        if 1 <= current_time <= 1100:
            date_type = "today"
            log.info(f"🕒 Time {current_time}: Auto-assigning TODAY (Strict)")
            if not is_system_off or is_whitelisted:
                ask_pickup_confirmation(bot, message, "today", message.message_id)
                return
        elif 1501 <= current_time <= 2359 or current_time == 0:
            date_type = "tomorrow"
            log.info(f"🕒 Time {current_time}: Auto-assigning TOMORROW (Strict)")
            if not is_system_off or is_whitelisted:
                ask_pickup_confirmation(bot, message, "tomorrow", message.message_id)
                return
        else: # 11:01 AM to 03:00 PM
            # ၁၁ နာရီနဲ့ ၃ နာရီကြားမှာ Staff အတည်ပြုချက် အမြဲတောင်းပါမည် (ဒီနေ့ ရ/မရ သိရန်)
            log.info(f"🕒 Time {current_time}: Entering Mid-day Staff Decision Flow")
            today_str = now.strftime("%d-%m-%Y")
            if db_manager.check_existing_pickup(chat_id, today_str) and ai_date_type != "tomorrow":
                log.info(f"⚠️ Duplicate pickup detected for {chat_id} on {today_str} (Late Morning). Skipping Staff Decision.")
                if not is_system_off or is_whitelisted:
                    show_duplicate_alert(bot, message, today_str, message.message_id)
                return
            
            date_type = "today" # Default for alert context

        # 💡 Midnight Bug Fix: Use original message timestamp instead of current time
        msg_ts = message.date # message.date is already a timestamp
        msg_dt = datetime.fromtimestamp(msg_ts, tz)
        
        target_date_str = (msg_dt if date_type == "today" else msg_dt + timedelta(days=1)).strftime("%d-%m-%Y")
        
        # 🛡️ Duplicate Check: Pending, Processing, Success ရှိနေရင် Admin Alert မပို့တော့ဘဲ Manual Flow သို့ လွှတ်မည်
        if db_manager.check_existing_pickup(chat_id, target_date_str):
            log.info(f"⚠️ Duplicate pickup detected for {chat_id} on {target_date_str}. Skipping Pick Up alert to allow Manual Flow.")
            if not is_system_off or is_whitelisted:
                show_duplicate_alert(bot, message, target_date_str, message.message_id)
            return

        # 1. Admin Group Notification (Unified Photo Message)
        log.info(f"🔔 Sending Admin Pickup Alert for {os_name}...")
        alert_msg = send_admin_pickup_alert(bot, chat_id, message.message_id, os_name, target_date_str, vehicle, clean_remark, text)
        if not alert_msg:
            log.error(f"❌ Failed to send Admin Pickup Alert for {os_name}. Staff Decision Alert might fail.")

        # 1.1 Mid-day Staff Decision Alert (Update the alert we just sent)
        if 1101 <= current_time <= 1500:
            log.info(f"🕒 Mid-day Flow: Triggering Staff Decision Alert for {os_name}")
            if not is_system_off or is_whitelisted:
                send_staff_decision_alert(bot, message, os_name, vehicle if vehicle else "none")
                return

        # 2. Silent Mode for Group Chat when Pickup is OFF
        if is_system_off and not is_whitelisted:
            log.info(f"🔇 Silent Mode: Pickup is OFF. Alert sent to Admin, but skipping customer interaction for group {chat_id}")
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

def ask_pickup_confirmation(bot, message, date_type, orig_msg_id):
    """ (၁၁)နာရီ အရှေ့ရော (၃)နာရီအနောက်ရော အတည်ပြုချက် အရင်တောင်းခြင်း """
    try:
        tz = pytz.timezone('Asia/Yangon')
        
        # 💡 Midnight Bug Fix: Use original message timestamp instead of current time
        msg_ts = message.date
        msg_dt = datetime.fromtimestamp(msg_ts, tz)
        
        target_date = (msg_dt if date_type == "today" else msg_dt + timedelta(days=1))
        target_date_str = target_date.strftime("%d-%m-%Y")
        
        text = f"{target_date_str} ရက်နေ့အတွက် Pick up လေးတင်ပေးရမလားရှင့်"
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("OK", callback_data=f"ap_pconf_{orig_msg_id}_{date_type}"),
            types.InlineKeyboardButton("💬 Admin နှင့်ပြောမည်", callback_data=f"ap_admin_0_{orig_msg_id}"),
            types.InlineKeyboardButton("❌ Pick Up မဟုတ်ပါ", callback_data=f"ap_cancel_{orig_msg_id}")
        )
        
        msg = bot.reply_to(message, text, reply_markup=markup)
        db_manager.add_pickup_intermediate_msg(message.chat.id, orig_msg_id, msg.message_id)
    except Exception as e:
        log.error(f"❌ ask_pickup_confirmation Error: {e}")

def ask_vehicle(bot, message, date_type, orig_msg_id, show_cancel=True):
    tz = pytz.timezone('Asia/Yangon')
    
    # 💡 Midnight Bug Fix: Use original message timestamp instead of current time
    msg_ts = message.date
    msg_dt = datetime.fromtimestamp(msg_ts, tz)
    
    target_date = (msg_dt if date_type == "today" else msg_dt + timedelta(days=1)).strftime("%d-%m-%Y")
    
    text = f"{target_date} pick up လေးရပါတယ်နော်။ pick up တင်ပေးနိုင်ရန် ****လိုတဲ့အချက် (စက်ဘီး၊ကား)*** ကိုပြောပေးပါဦး"
        
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

def show_interactive_setup(bot, chat_id, orig_msg_id, date_type, vehicle=None, remark=None, edit_msg_id=None):
    """ Unified Interactive Message for Pickup Setup (Today/Tomorrow) """
    try:
        # Get current state from DB if not provided
        order = db_manager.get_pickup_order_by_msg(orig_msg_id, chat_id)
        os_name = "Unknown"
        target_date_str = "-"
        
        if order:
            # order format: (id, chat_id, orig_msg_id, target_date, os_name, remark, vehicle, status, created_at)
            if not vehicle: vehicle = order[6]
            if not remark: remark = order[5]
            os_name = order[4]
            target_date_str = order[3]
        else:
            # Fallback to context if order not created yet
            tz = pytz.timezone('Asia/Yangon')
            msg_ctx = db_manager.get_message_context(orig_msg_id, chat_id)
            msg_ts = msg_ctx[4] if msg_ctx else datetime.now(tz).timestamp()
            msg_dt = datetime.fromtimestamp(msg_ts, tz)
            target_date = (msg_dt if date_type == "today" else msg_dt + timedelta(days=1))
            target_date_str = target_date.strftime("%d-%m-%Y")
            
            os_name = get_best_shop_name(bot, chat_id)

        v_display = vehicle if vehicle else "-"
        r_display = remark if remark else "-"
        
        text = (
            f"⏳ <b>Auto Pickup အချက်အလက်များ</b>\n"
            f"📅 ရက်စွဲ: {target_date_str}\n"
            f"🏪 ဆိုင်: <b>{util.escape(os_name)}</b>\n"
            f"🚲 ယာဉ်: <b>{v_display}</b>\n"
            f"📝 မှတ်ချက်: {r_display}\n"
            f"📊 Status: <b>✅ Pending</b>"
        )

        markup = types.InlineKeyboardMarkup(row_width=2)
        
        # Vehicle Toggle Buttons
        v_btns = [
            types.InlineKeyboardButton("🚲 စက်ဘီး", callback_data=f"ap_ivh_{orig_msg_id}_{date_type}_Bicycle"),
            types.InlineKeyboardButton("🚗 ကား", callback_data=f"ap_ivh_{orig_msg_id}_{date_type}_Car")
        ]
        markup.row(*v_btns)

        # Remark Button
        markup.add(types.InlineKeyboardButton("📝 မှတ်ချက်ရေးမည်", callback_data=f"ap_irm_{orig_msg_id}_{date_type}_write"))
        
        # Submit Button
        markup.add(types.InlineKeyboardButton("🚀 Pickup တင်ရန် (နှိပ်ပါ)", callback_data=f"ap_isb_{orig_msg_id}_{date_type}"))
        
        # Admin Button (Changed from Cancel Button as per user request)
        markup.add(types.InlineKeyboardButton("💬 Admin နှင့်ပြောမည်", callback_data=f"ap_admin_0_{orig_msg_id}"))

        if edit_msg_id:
            try:
                bot.edit_message_text(text, chat_id, edit_msg_id, reply_markup=markup, parse_mode="HTML")
            except Exception as ee:
                if "message is not modified" not in str(ee): raise ee
        else:
            sent_msg = bot.send_message(chat_id, text, reply_to_message_id=orig_msg_id, reply_markup=markup, parse_mode="HTML")
            db_manager.add_pickup_intermediate_msg(chat_id, orig_msg_id, sent_msg.message_id)
            
    except Exception as e:
        log.error(f"❌ show_interactive_setup Error: {e}")

def send_staff_decision_alert(bot, message, os_name, vehicle):
    """ Admin Group ရှိ မူလ Alert ကို Waiting Decision အဖြစ် Update လုပ်ခြင်း """
    try:
        chat_id = message.chat.id
        orig_msg_id = message.message_id
        
        # ဆိုင် Group ထဲသို့ အကြောင်းကြားစာပို့ခြင်း
        late_pickup_text = "Pick up တင်တာ (၁၁)နာရီကျော်ပြီမိုလို Pick up လမ်းကြောင်းလေးရသေးလားဆိုတာ အတည်ပြုပြီးပြန်လည်အကြောင်းပြန်ပေးပါ့မယ်ရှင်။"
        markup_shop = types.InlineKeyboardMarkup(row_width=1)
        markup_shop.add(
            types.InlineKeyboardButton("💬 Admin နှင့်ပြောမည်", callback_data=f"ap_admin_0_{orig_msg_id}"),
            types.InlineKeyboardButton("❌ Pickup မဟုတ်ပါ", callback_data=f"ap_cancel_{orig_msg_id}")
        )
        msg = bot.reply_to(message, late_pickup_text, reply_markup=markup_shop)
        db_manager.add_pickup_intermediate_msg(chat_id, orig_msg_id, msg.message_id)

        # Admin Group ရှိ မူလ Alert ကို Update လုပ်ခြင်း
        clean_chat_id = str(chat_id).replace("-100", "")
        msg_link = f"https://t.me/c/{clean_chat_id}/{orig_msg_id}"

        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.row(
            types.InlineKeyboardButton("📅 Today", callback_data=f"ap_st_{orig_msg_id}_{chat_id}_today_{vehicle}"),
            types.InlineKeyboardButton("📅 Tomorrow", callback_data=f"ap_st_{orig_msg_id}_{chat_id}_tomorrow_{vehicle}")
        )
        markup.row(
            types.InlineKeyboardButton("🔗 View Message", url=msg_link),
            types.InlineKeyboardButton("❌ Wrong Pickup", callback_data=f"ap_wrong_staff_{orig_msg_id}_{chat_id}_{vehicle}")
        )
        
        log.info(f"🕒 Mid-day Flow: Updating central alert with Staff Decision buttons for {os_name}")
        update_central_pickup_alert(bot, orig_msg_id, chat_id, "⏳ Waiting Decision (Staff)", custom_markup=markup, show_done=False)

    except Exception as e:
        log.error(f"❌ send_staff_decision_alert Error: {e}")

def handle_pickup_error(bot, chat_id, orig_msg_id, os_name, target_date, error_msg):
    """ Pickup တင်စဉ် Error တက်ပါက Admin Group သို့ အကြောင်းကြားခြင်း """
    try:
        admin_chat_id = int(os.getenv('ALERT_CHAT_ID', -1003601049225))
        admin_topic_id = 878
        
        clean_chat_id = str(chat_id).replace("-100", "")
        msg_link = f"https://t.me/c/{clean_chat_id}/{orig_msg_id}"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔗 View Message", url=msg_link))
        
        # 💡 Logic: Mapping မရှိသေးရင် Fix Mapping ပြမည်။ Mapping ရှိပြီးသားဆိုရင် Done ပြမည်။
        mapped_name = db_manager.get_shop_mapping(chat_id)
        if not mapped_name or "Mapping" in error_msg:
            markup.add(types.InlineKeyboardButton("� Fix Shop Mapping", callback_data=f"ap_fix_{chat_id}"))
            instruction = "ဆိုင်နာမည် Mapping လွဲနေပါက အောက်ကခလုတ်ကိုနှိပ်၍ ပြင်ပေးပါရန်။"
        else:
            markup.add(types.InlineKeyboardButton("✅ Done", callback_data=f"pdone_{orig_msg_id}_{chat_id}"))
            instruction = "စက်ရုပ်ဖြင့် တင်မရပါက Manual တင်ပြီး Done နှိပ်ပေးပါရန်။"
        
        alert_text = (
            f"❌ **Auto Pickup Failed**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏪 ဆိုင်: <b>{util.escape(os_name)}</b>\n"
            f"📅 ရက်စွဲ: <b>{target_date}</b>\n"
            f"⚠️ အကြောင်းရင်း: {util.escape(error_msg)}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{instruction}"
        )
        
        bot.send_message(admin_chat_id, alert_text, parse_mode="HTML", message_thread_id=admin_topic_id, reply_markup=markup)
    except Exception as e:
        log.error(f"❌ handle_pickup_error Error: {e}")

def update_central_pickup_alert(bot, orig_msg_id, chat_id, status_text, show_done=True, photo_path=None, custom_markup=None, queue_id=None):
    """ Admin Group နှင့် ဆိုင် Group ရှိ Alert Message များကို Update လုပ်ခြင်း """
    log.info(f"🔄 Updating central alert for msg {orig_msg_id} in chat {chat_id} (Status: {status_text}, Has Custom Markup: {custom_markup is not None})")
    try:
        # --- 1. Update Shop Group Message (Always try this first) ---
        order = None
        with db_manager.connection_scope() as conn:
            if queue_id:
                order = conn.execute("SELECT os_name, target_date, remark, vehicle, status, shop_msg_id FROM pickup_queue WHERE id = ?", (queue_id,)).fetchone()
            else:
                # If no queue_id, get the latest one for this message
                order = conn.execute("SELECT os_name, target_date, remark, vehicle, status, shop_msg_id FROM pickup_queue WHERE orig_msg_id = ? AND chat_id = ? ORDER BY id DESC", (orig_msg_id, chat_id)).fetchone()
        
        if not order:
            ctx = db_manager.get_message_context(orig_msg_id, chat_id)
            # 💡 Chat ID Mismatch Fix & Fallback to Chat Title
            clean_id = int(str(chat_id).replace("-100", ""))
            with db_manager.connection_scope() as conn:
                g_res = conn.execute("SELECT shop_name FROM os_groups WHERE chat_id IN (?, ?) LIMIT 1", (chat_id, clean_id)).fetchone()
            
            if g_res:
                os_name = db_manager.clean_shop_name(g_res[0])
            else:
                # Database မှာ မရှိရင် Telegram Group Title ကနေ တိုက်ရိုက်ယူမည်
                try:
                    chat_info = bot.get_chat(chat_id)
                    chat_title = chat_info.title or "Unknown Shop"
                    if '🤝' in chat_title:
                        os_name = chat_title.split('🤝')[0].strip()
                    else:
                        os_name = db_manager.clean_shop_name(chat_title)
                except:
                    os_name = "Unknown Shop"
            
            target_date = "-"
            remark = ctx[1] if ctx and ctx[1] else "-"
            vehicle = "-"
            shop_msg_id = None
        else:
            os_name, target_date, remark, vehicle, _, shop_msg_id = order

        # 💡 Fallback: If shop_msg_id is missing, try to find it in the database
        if not shop_msg_id:
            with db_manager.connection_scope() as conn:
                res = conn.execute("SELECT shop_msg_id FROM pickup_queue WHERE orig_msg_id = ? AND chat_id = ? AND shop_msg_id IS NOT NULL ORDER BY id DESC", (orig_msg_id, chat_id)).fetchone()
                if res:
                    shop_msg_id = res[0]
                    log.info(f"🔍 Found fallback shop_msg_id: {shop_msg_id}")

        if shop_msg_id:
            # Phase 1: Shop Group UI Logic
            display_status = status_text
            footer = ""
            
            status_str = str(status_text) if status_text else ""
            if "Processing" in status_str:
                display_status = "⏳ Processing"
                footer = "\nCarryMan AI Bot မှ Pickup လေးတင်ပေးနေပါတယ်နော်"
            elif "Failed" in status_str or "❌" in status_str:
                display_status = "⏳ Processing" # Do not show Failed to shop
                footer = "\nဝန်ထမ်းမှ တိုက်ရိုက် ဝင်ရောက်စီစဉ်ပေးနေပါတယ်နော်"
            elif "Success" in status_str or "✅" in status_str:
                display_status = "✅ Success"
                footer = "\nPickup လေးတင်ပြီးပါပြီနော်။ ပျော်ရွှင်စရာနေ့လေး ဖြစ်ပါစေ။ 🍀🚚"

            shop_status_text = (
                f"⏳ <b>Auto Pickup အချက်အလက်များ</b>\n"
                f"📅 ရက်စွဲ: {util.escape(str(target_date)) if target_date else '-'}\n"
                f"🏪 ဆိုင်: <b>{util.escape(str(os_name)) if os_name else '-'}</b>\n"
                f"🚲 ယာဉ်: <b>{util.escape(str(vehicle)) if vehicle else '-'}</b>\n"
                f"📝 မှတ်ချက်: {util.escape(str(remark)) if remark else '-'}\n"
                f"📊 Status: <b>{util.escape(str(display_status)) if display_status else '-'}</b>"
                f"{footer}"
            )
            try:
                bot.edit_message_text(shop_status_text, chat_id, shop_msg_id, parse_mode="HTML")
                log.info(f"✅ Shop Group Status updated to {display_status} for {os_name} (Msg: {shop_msg_id})")
            except Exception as e:
                log.error(f"❌ Shop message update failed for {os_name} (Msg: {shop_msg_id}): {e}")

        # --- 2. Update Admin Group Message (Requires Tracking) ---
        # 💡 Retry logic for tracking (to avoid race condition with save_alert_tracking)
        tracking = None
        # Reduced retries to 3 seconds to prevent long loading spinner
        for i in range(3):
            tracking = db_manager.get_alert_tracking(orig_msg_id, chat_id)
            if tracking:
                log.info(f"✅ Tracking found for msg {orig_msg_id} on attempt {i+1}")
                break
            log.debug(f"⏳ Waiting for tracking for msg {orig_msg_id} (Attempt {i+1}/3)...")
            time.sleep(1)

        if not tracking:
            log.warning(f"⚠️ update_central_pickup_alert: No tracking found for msg {orig_msg_id} in chat {chat_id}. Admin alert update skipped.")
            return

        alert_msg_id = tracking[0]
        alert_chat_id = tracking[1]
        
        clean_chat_id = str(chat_id).replace("-100", "")
        msg_link = f"https://t.me/c/{clean_chat_id}/{orig_msg_id}"
        if status_text and "Success" in str(status_text):
            try:
                bot.delete_message(alert_chat_id, alert_msg_id)
                log.info(f"🗑️ Deleted central alert {alert_msg_id} in {alert_chat_id} because status is Success")
                return # Exit early as we don't need to edit a deleted message
            except Exception as delete_e:
                log.debug(f"Failed to delete alert (might be already deleted): {delete_e}")
                # If deletion fails, we continue to edit it as a fallback

        # Get original text and updates for the caption
        tracking = db_manager.get_alert_tracking(orig_msg_id, chat_id)
        updates_text = tracking[7] if tracking and len(tracking) > 7 else ""

        with db_manager.connection_scope() as conn:
            orig_data = conn.execute("SELECT text FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
        orig_text = str(orig_data[0]) if orig_data and orig_data[0] else "-"

        new_caption = (
            f"🚚 <b>Pick Up alert</b>\n"
            f"🏪 ဆိုင်: <b>{util.escape(str(os_name)) if os_name else '-'}</b>\n"
            f"📅 ရက်စွဲ: <b>{util.escape(str(target_date)) if target_date else '-'}</b>\n"
            f"🚲 ယာဉ်: <b>{util.escape(str(vehicle)) if vehicle else '-'}</b>\n"
            f"📝 မှတ်ချက်: {util.escape(str(remark)) if remark else '-'}\n"
            f"📊 Status: <b>{util.escape(str(status_text)) if status_text else '-'}</b>\n"
            f"💬 မူရင်းစာ: <i>{util.escape(orig_text[:200])}{'...' if len(orig_text) > 200 else ''}</i>"
        )
        
        if updates_text:
            new_caption += f"\n➕ <b>Updates:</b>\n<i>{util.escape(updates_text)}</i>"

        is_processing = status_text and "Processing" in str(status_text)
        is_failed = status_text and ("Failed" in str(status_text) or "❌" in str(status_text))

        if custom_markup:
            markup = custom_markup
            # Ensure View Message is always there
            has_view = any(getattr(b, 'url', None) == msg_link for row in markup.keyboard for b in row)
            if not has_view:
                markup.add(types.InlineKeyboardButton("🔗 View Message", url=msg_link))
        else:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔗 View Message", url=msg_link))
            
            if is_processing:
                # ၁။ Processing status ဖြစ်နေချိန်: Done နှင့် Wrong Pickup ခလုတ်များကို ဖျောက်ထားမည်။ View Message တစ်ခုတည်းသာပြမည်။
                pass
            elif is_failed:
                # Error တက်ချိန် logic
                mapped_name = db_manager.get_shop_mapping(chat_id)
                # Mapping မရှိလျှင် သို့မဟုတ် status_text ထဲမှာ Mapping/ဆိုင်နာမည်/OS Name ပါနေလျှင် Fix Shop Mapping ပြမည်
                is_mapping_issue = not mapped_name or any(x in str(status_text) for x in ["Mapping", "ဆိုင်နာမည်", "OS Name"])
                
                if is_mapping_issue:
                    # ၂။ စက်ရုပ် ပထမအကြိမ် Error တက်ချိန် (သို့မဟုတ် Mapping လိုအပ်ချိန်): View Message နှင့် Fix Shop Mapping ခလုတ်များကို ပြမည်။
                    markup.add(types.InlineKeyboardButton("🔧 Fix Shop Mapping", callback_data=f"ap_fix_{chat_id}"))
                else:
                    # ၃။ Mapping ပြင်ပြီးသော်လည်း ထပ်မံ Error တက်ချိန် (သို့မဟုတ် အခြား Error များ): View Message နှင့် Done ခလုတ်များကို ပြမည်။
                    if show_done:
                        markup.add(types.InlineKeyboardButton("✅ Done", callback_data=f"pdone_{orig_msg_id}_{chat_id}"))
            else:
                # ပုံမှန် Pending status သို့မဟုတ် အခြား status များ
                if show_done:
                    markup.add(types.InlineKeyboardButton("✅ Done", callback_data=f"pdone_{orig_msg_id}_{chat_id}"))
                markup.add(types.InlineKeyboardButton("❌ Wrong Pickup", callback_data=f"ap_wrong_{orig_msg_id}_{chat_id}"))

        try:
            # Check if the original message was a photo or text
            # If we have a photo_path, we try to use editMessageMedia
            if photo_path and os.path.exists(photo_path):
                with open(photo_path, 'rb') as photo:
                    bot.edit_message_media(
                        media=types.InputMediaPhoto(photo, caption=new_caption, parse_mode="HTML"),
                        chat_id=alert_chat_id,
                        message_id=alert_msg_id,
                        reply_markup=markup
                    )
            else:
                # Try editing as caption first (if it was a photo)
                try:
                    bot.edit_message_caption(
                        caption=new_caption,
                        chat_id=alert_chat_id,
                        message_id=alert_msg_id,
                        parse_mode="HTML",
                        reply_markup=markup
                    )
                except Exception as e:
                    # If it fails, it might be a text message
                    bot.edit_message_text(
                        text=new_caption,
                        chat_id=alert_chat_id,
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
            f"🏪 ဆိုင်: <b>{util.escape(os_name)}</b>\n"
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

def send_admin_pickup_alert(bot, chat_id, msg_id, os_name, target_date, vehicle, remark, orig_text):
    """ Admin Group (Topic 878) သို့ Pickup Alert ပို့ဆောင်ခြင်း """
    try:
        admin_chat_id = int(os.getenv('ALERT_CHAT_ID', -1003601049225))
        admin_topic_id = 878
        
        clean_chat_id = str(chat_id).replace("-100", "")
        msg_link = f"https://t.me/c/{clean_chat_id}/{msg_id}"

        admin_alert_text = (
            f"🚚 <b>Pick Up alert</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏪 ဆိုင်: <b>{util.escape(os_name)}</b>\n"
            f"📅 ရက်စွဲ: <b>{util.escape(target_date)}</b>\n"
            f"🚲 ယာဉ်: <b>{util.escape(vehicle) if vehicle else '-'}</b>\n"
            f"📝 မှတ်ချက်: {util.escape(remark) if remark else '-'}\n"
            f"📊 Status: <b>⏳ Pending</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💬 မူရင်းစာ: <i>{util.escape(orig_text[:200])}{'...' if len(orig_text) > 200 else ''}</i>"
        )
        
        admin_markup = types.InlineKeyboardMarkup()
        admin_markup.add(
            types.InlineKeyboardButton("🔗 View Message", url=msg_link),
            types.InlineKeyboardButton("✅ Done", callback_data=f"pdone_{msg_id}_{chat_id}"),
            types.InlineKeyboardButton("❌ Wrong Pickup", callback_data=f"ap_wrong_{msg_id}_{chat_id}")
        )
        
        # 💡 Use Text Message by default to avoid confusing icons
        alert_msg = bot.send_message(
            admin_chat_id, admin_alert_text,
            parse_mode="HTML",
            message_thread_id=admin_topic_id,
            reply_markup=admin_markup
        )

        if alert_msg:
            db_manager.save_alert_tracking(msg_id, chat_id, alert_msg.message_id, admin_chat_id)
            # 💡 Pick up request ကို Auditor က ၁၅ မိနစ် alert ထပ်မပို့အောင် ALERTED status ကို ချက်ချင်းပြောင်းပါမည်
            db_manager.update_message_status(msg_id, chat_id, 'ALERTED', category='PICKUP')
        log.info(f"🔔 Unified Admin notification sent for pickup request from {os_name}")
        return alert_msg
    except Exception as e:
        log.error(f"❌ Failed to send admin pickup notification: {e}")
        return None

def cleanup_pickup_intermediate_msgs(bot, chat_id, orig_msg_id, exclude_msg_id=None):
    try:
        msg_ids = db_manager.get_pickup_intermediate_msgs(chat_id, orig_msg_id)
        for mid in msg_ids:
            if exclude_msg_id and mid == exclude_msg_id:
                continue
            try:
                bot.delete_message(chat_id, mid)
            except Exception:
                pass
        db_manager.delete_pickup_intermediate_msgs(chat_id, orig_msg_id)
    except Exception as e:
        log.error(f"❌ Cleanup Pickup Intermediate Messages Error: {e}")

def run_daily_cleanup(bot):
    """ မနက် ၄ နာရီ (Myanmar Time) တွင် မနေ့က request များကို အလိုအလျောက် ရှင်းလင်းပေးမည့် Scheduler """
    log.info("⏰ Daily Pickup Cleanup Scheduler is running (Target: 04:00 AM MMT)...")
    tz = pytz.timezone('Asia/Yangon')
    
    while True:
        try:
            now = datetime.now(tz)
            # မနက် ၄ နာရီ ဖြစ်မဖြစ် စစ်ဆေးခြင်း
            if now.hour == 4 and now.minute == 0:
                log.info("🧹 Starting Daily Pickup Cleanup...")
                stale_orders = db_manager.get_stale_pickup_orders()
                
                if stale_orders:
                    log.info(f"🔍 Found {len(stale_orders)} stale pickup orders to clean up.")
                    for order_id, chat_id, orig_msg_id, status in stale_orders:
                        try:
                            # ၁။ Group ထဲမှ ခလုတ်များကို ဖျက်ခြင်း
                            cleanup_pickup_intermediate_msgs(bot, chat_id, orig_msg_id)
                            
                            # ၂။ DB Status ကို CANCELLED ပြောင်းခြင်း
                            db_manager.update_queue_status(order_id, 'CANCELLED', error_msg="Daily Cleanup: Auto-Expired at 04:00 AM")
                            
                            log.info(f"✅ Cleaned up stale order {order_id} for chat {chat_id}")
                        except Exception as oe:
                            log.error(f"❌ Error cleaning up order {order_id}: {oe}")
                else:
                    log.info("ℹ️ No stale pickup orders found.")
                
                # တစ်မိနစ် စောင့်လိုက်ခြင်းဖြင့် ထပ်ခါထပ်ခါ မ Run စေရန်
                time.sleep(65)
            
            time.sleep(30) # ၃၀ စက္ကန့်တစ်ခါ စစ်မည်
        except Exception as e:
            log.error(f"⚠️ Daily Cleanup Scheduler Loop Error: {e}")
            time.sleep(60)

def run_queue_worker(bot):
    global _bot
    _bot = bot
    log.info("🚀 Auto Pickup Queue Worker စတင်နေပါပြီ...")
    
    # 💡 Recovery Logic: Reset stuck 'PROCESSING' orders to 'PENDING' on startup
    try:
        with db_manager.get_connection() as conn:
            res = conn.execute("UPDATE pickup_queue SET status = 'PENDING' WHERE status = 'PROCESSING'")
            if res.rowcount > 0:
                log.warning(f"🔄 Recovered {res.rowcount} stuck 'PROCESSING' orders to 'PENDING'.")
    except Exception as re:
        log.error(f"❌ Recovery Logic Error: {re}")

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
                update_central_pickup_alert(bot, orig_msg_id, chat_id, "❌ Aborted (System OFF)", show_done=False, queue_id=queue_id)
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
                    update_central_pickup_alert(bot, orig_msg_id, chat_id, "❌ Failed (Mapping Missing)", queue_id=queue_id)
                    
                    alert_msg = f"<b>Shop Mapping Missing</b>\n🏪 ဆိုင်: {os_name}\n⚠️ ဆိုင်နာမည် Mapping မရှိသေးပါ။ Manager မှ Fix Shop Mapping ကိုနှိပ်၍ အရင်ပြင်ပေးပါရန်။"
                    asyncio.run(send_pickup_notification(alert_msg, is_alert=True))
                    
                    handle_pickup_error(bot, chat_id, orig_msg_id, os_name, target_date, "ဆိုင်နာမည် Mapping မရှိသေးပါ။ Manager မှ Fix Shop Mapping ကိုနှိပ်၍ အရင်ပြင်ပေးပါရန်။")
                    continue

            if not all([target_date, final_os_name, vehicle]) or vehicle == "none":
                log.error(f"❌ Strict Validation Failed for Queue {queue_id}: Missing required fields.")
                db_manager.update_queue_status(queue_id, 'FAILED', error_msg="Missing required fields (Date, Shop, or Vehicle)")
                update_central_pickup_alert(bot, orig_msg_id, chat_id, "❌ Failed (Missing Data)", queue_id=queue_id)
                
                if not vehicle or vehicle == "none":
                    tz = pytz.timezone('Asia/Yangon')
                    now = datetime.now(tz)
                    today_str = now.strftime("%d-%m-%Y")
                    date_type = "today" if target_date == today_str else "tomorrow"
                    ask_vehicle(bot, types.Message(message_id=orig_msg_id, from_user=None, date=None, chat=types.Chat(id=chat_id, type='group'), text=remark), date_type, orig_msg_id)
                continue

            db_manager.update_queue_status(queue_id, 'PROCESSING')
            update_central_pickup_alert(bot, orig_msg_id, chat_id, "⏳ Processing", queue_id=queue_id)

            success, msg = submit_pickup_order(target_date, final_os_name, remark, vehicle)
            
            if success:
                db_manager.update_queue_status(queue_id, 'SUCCESS')
                
                # 1. ဆိုင် Group ကို Success ပြောင်းရန် (Admin Alert ကိုပါ update လုပ်ရန် ကြိုးစားမည်)
                update_central_pickup_alert(bot, orig_msg_id, chat_id, "✅ Success", queue_id=queue_id)

                # 2. Success Group သို့ Report ပို့ခြင်း
                send_success_report(bot, orig_msg_id, chat_id, handled_by="စက်ရုပ် (Auto)")

                # 3. Admin Group ရှိ Alert Message ကို ဖျက်ခြင်း
                try:
                    tracking = db_manager.get_alert_tracking(orig_msg_id, chat_id)
                    if tracking:
                        central_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))
                        bot.delete_message(central_chat, tracking[0])
                except: pass

                db_manager.resolve_message(orig_msg_id, chat_id, 'System (Auto-Pickup)', method='Auto', status='HANDLED_BY_AI')
                
                # 💡 Success ဖြစ်တဲ့အခါ ဆိုင် Group ထဲက Status ပြနေတဲ့စာကို မဖျက်ဘဲ ချန်ထားခဲ့ပါမည်
                order_data = db_manager.get_pickup_order(queue_id)
                shop_msg_id = order_data[10] if order_data and len(order_data) > 10 else None
                cleanup_pickup_intermediate_msgs(bot, chat_id, orig_msg_id, exclude_msg_id=shop_msg_id)

                if not mapped_name and final_os_name != os_name:
                    db_manager.set_shop_mapping(chat_id, final_os_name)
                    log.info(f"📝 Auto-mapped {chat_id} to {final_os_name}")
            else:
                db_manager.update_queue_status(queue_id, 'FAILED', error_msg=msg)
                
                # Update central alert status (Keep as Processing in Shop Group, but show error in Admin)
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                screenshot_path = os.path.join(base_dir, "before_save.png")
                
                # Error ဖြစ်ပါက update_central_pickup_alert မှ ခလုတ်များကို အလိုအလျောက် စီမံပေးပါမည်
                status_msg = "❌ Failed (Mapping Missing)" if ("ဆိုင်နာမည်" in str(msg) or "OS Name" in str(msg)) else "❌ Failed (Check Admin)"
                
                update_central_pickup_alert(
                    bot, orig_msg_id, chat_id,
                    status_msg,
                    photo_path=screenshot_path if os.path.exists(screenshot_path) else None,
                    queue_id=queue_id
                )

                # 💡 Manager ဆီသို့ Screenshot နှင့်တကွ Error Report ပို့ခြင်း
                if os.path.exists(screenshot_path):
                    report_caption = (
                        f"❌ <b>Auto Pickup Failed Report</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🏪 ဆိုင်: {os_name}\n"
                        f"📅 ရက်စွဲ: {target_date}\n"
                        f"⚠️ Error: {msg}\n"
                        f"━━━━━━━━━━━━━━━━━━"
                    )
                    ai_utils.send_manager_photo(screenshot_path, report_caption)
                
                # Also send a separate error notification with Fix button if it's a mapping issue
                if custom_markup:
                    handle_pickup_error(bot, chat_id, orig_msg_id, final_os_name or os_name, target_date, msg)

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
