import os
import time
import subprocess
import psutil
import html
from modules import auditor
import gsheet_sync
from telebot import types
from logger import log
import db_manager
from modules import group_creator

# Environment Variable
MANAGER_ID = int(os.getenv('MANAGER_ID'))
MANAGER_IDS = [int(i.strip()) for i in os.getenv('MANAGER_IDS', str(MANAGER_ID)).split(',')]

def is_manager(user_id):
    return user_id in MANAGER_IDS

# --- [ အစ်ကို့ရဲ့ မူရင်း စာရင်းများ ] ---
BRANCHES = ["Yangon", "Insein", "Htauk Kyant", "Mandalay"]
DEPARTMENTS = [
    "OS Service", "Rider Service", "Rider Finance", "OS Finance", 
    "Accountant", "Data Entry", "Marketing", "HR", "Admin", 
    "Rider", "Agent", "Other"
]

staff_reg_data = {}
register_group_data = {}

# ... (အပေါ်က စာကြောင်းတွေက အရင်အတိုင်းထားပါ) ...

def register_handlers(bot):
    """ Bot Command အားလုံးကို ဤနေရာတွင် စုစည်းမှတ်ပုံတင်ပေးသည် """
    
    # --- [ Section: Maintenance Control (အသစ်ထည့်ရမည့်နေရာ) ] ---
    @bot.message_handler(commands=['gsupdate'])
    def handle_gs_update(message):
        """ Google Sheet မှ Data များကို Manual Sync လုပ်ခြင်း """
        if db_manager.get_user_level(message.from_user.id, message.chat.id) == 4:
            msg = bot.reply_to(message, "⏳ **Google Sheet မှ ဒေတာများကို ရယူနေပါသည်...**")
            
            # .env ထဲက URL ကို ယူခြင်း
            sheet_url = os.getenv('GSHEET_URL')
            if not sheet_url:
                bot.edit_message_text("❌ `.env` ထဲမှာ `GSHEET_URL` ထည့်သွင်းထားခြင်း မရှိသေးပါ အစ်ကို။",
                                     msg.chat.id, msg.message_id)
                return

            # Sync Module ကို ခေါ်ယူခြင်း
            syncer = gsheet_sync.GSheetSync()
            success, result_msg = syncer.sync_knowledge(sheet_url)
            
            if success:
                bot.edit_message_text(f"✅ **Sync Success!**\n\n{result_msg}",
                                     msg.chat.id, msg.message_id)
            else:
                bot.edit_message_text(f"❌ **Sync Failed!**\n\n{result_msg}",
                                     msg.chat.id, msg.message_id)
        else:
            bot.reply_to(message, "⚠️ ဤ Command ကို Manager သာ အသုံးပြုခွင့်ရှိပါသည်။")

    @bot.message_handler(commands=['aion', 'aioff'])
    def handle_ai_global_toggle(message):
        if db_manager.get_user_level(message.from_user.id, message.chat.id) == 4:
            if message.text.startswith('/aion'):
                db_manager.set_ai_global_status('ON')
                bot.reply_to(message, '✅ **AI Answer (Global): ON**\nAI မှ စာပြန်ခြင်းစနစ်ကို ဖွင့်လိုက်ပါပြီ။')
            else:
                db_manager.set_ai_global_status('OFF')
                bot.reply_to(message, '❌ **AI Answer (Global): OFF**\nAI မှ စာပြန်ခြင်းစနစ်ကို ပိတ်လိုက်ပါပြီ။')

    @bot.message_handler(commands=['pickupon', 'pickupoff'])
    def handle_pickup_global_toggle(message):
        if db_manager.get_user_level(message.from_user.id, message.chat.id) == 4:
            if message.text.startswith('/pickupon'):
                db_manager.set_auto_pickup_global_status('ON')
                bot.reply_to(message, '✅ **Auto Pickup (Global): ON**\nအော်ဒါအလိုအလျောက်ကောက်သည့်စနစ်ကို ဖွင့်လိုက်ပါပြီ။')
            else:
                db_manager.set_auto_pickup_global_status('OFF')
                bot.reply_to(message, '❌ **Auto Pickup (Global): OFF**\nအော်ဒါအလိုအလျောက်ကောက်သည့်စနစ်ကို ပိတ်လိုက်ပါပြီ။')

    @bot.message_handler(commands=['alerton', 'alertoff'])
    def handle_alert_global_toggle(message):
        if db_manager.get_user_level(message.from_user.id, message.chat.id) == 4:
            if message.text.startswith('/alerton'):
                db_manager.set_alert_system_global_status('ON')
                bot.reply_to(message, '✅ **Alert System (Global): ON**\n၁၅ မိနစ် Alert ပေးသည့်စနစ်ကို ဖွင့်လိုက်ပါပြီ။')
            else:
                db_manager.set_alert_system_global_status('OFF')
                bot.reply_to(message, '❌ **Alert System (Global): OFF**\n၁၅ မိနစ် Alert ပေးသည့်စနစ်ကို ပိတ်လိုက်ပါပြီ။')

    @bot.message_handler(commands=['start'])
    def send_welcome(message):
        if is_manager(message.from_user.id) or db_manager.check_if_staff(message.from_user.id):
            bot.reply_to(message, "🤖 CarryMan AI Agent စနစ်မှ ကြိုဆိုပါတယ်။\nအကူအညီလိုပါက Manager ကို ဆက်သွယ်ပါ။")

    @bot.message_handler(commands=['status'])
    def handle_status(message):
        user_id = message.from_user.id
        chat_id = message.chat.id
        user_level = db_manager.get_user_level(user_id, chat_id)
        
        if user_level >= 3:
            # 1. Global Toggles
            ai_global = db_manager.get_ai_global_status()
            pickup_global = db_manager.get_auto_pickup_global_status()
            alert_global = db_manager.get_alert_system_global_status()
            # 2. Process Health (PM2)
            processes = {"carryman-ingestion": "🔴", "carryman-auditor": "🔴"}
            unhealthy_processes = []
            try:
                pm2_output = subprocess.check_output(["pm2", "jlist"]).decode('utf-8')
                import json
                pm2_data = json.loads(pm2_output)
                for proc in pm2_data:
                    name = proc.get('name')
                    status = proc.get('pm2_env', {}).get('status')
                    if name in processes:
                        if status == 'online':
                            processes[name] = "🟢 Online"
                        else:
                            processes[name] = f"🔴 {status.capitalize()}"
                            unhealthy_processes.append(name)
            except Exception as e:
                log.error(f"PM2 Status Error: {e}")
                for k in processes: processes[k] = "⚠️ Error"

            # 3. Resources
            cpu_usage = psutil.cpu_percent()
            ram_usage = psutil.virtual_memory().percent
            
            status_text = (
                "🤖 **CarryMan System v4.2 Diagnostics**\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "⚙️ **Global Toggles:**\n"
                f"🧠 AI Answer: **{ai_global}**\n"
                f"📦 Auto Pickup: **{pickup_global}**\n"
                f"🚨 Alert System: **{alert_global}**\n\n"
                "📡 **Process Health:**\n"
                f"📥 Ingestion: {processes['carryman-ingestion']}\n"
                f"🕵️ Auditor: {processes['carryman-auditor']}\n\n"
                "💻 **Resources:**\n"
                f"🖥 CPU: {cpu_usage}%\n"
                f"💾 RAM: {ram_usage}%\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "📡 SLA Watchdog: Active"
            )
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            if user_level == 4:
                # Contextual Buttons based on unhealthy processes
                for proc in unhealthy_processes:
                    if proc == "carryman-auditor":
                        markup.add(types.InlineKeyboardButton("🔇 Disable AI (aioff)", callback_data="sys_aioff"))
                    elif proc == "carryman-ingestion":
                        markup.add(types.InlineKeyboardButton("⏸️ Disable Pickup (pickupoff)", callback_data="sys_pickupoff"))

                markup.add(
                    types.InlineKeyboardButton("🔄 Restart All", callback_data="sys_restart_confirm"),
                    types.InlineKeyboardButton("📋 Last Logs", callback_data="sys_logs_20")
                )
            
            bot.reply_to(message, status_text, reply_markup=markup, parse_mode="Markdown")

    @bot.message_handler(commands=['restart'])
    def handle_restart(message):
        if db_manager.get_user_level(message.from_user.id, message.chat.id) == 4:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("✅ အတည်ပြုသည် (Confirm)", callback_data="sys_restart_all"))
            markup.add(types.InlineKeyboardButton("❌ မလုပ်တော့ပါ (Cancel)", callback_data="sys_cancel"))
            bot.reply_to(message, "🔄 **System Restart**\n\nစနစ်တစ်ခုလုံးကို Restart ချရန် သေချာပါသလား အစ်ကို?", reply_markup=markup)
        else:
            bot.reply_to(message, "⚠️ ဤ Command ကို Manager သာ အသုံးပြုခွင့်ရှိပါသည်။")

    @bot.message_handler(commands=['sys_update'])
    def handle_sys_update(message):
        if db_manager.get_user_level(message.from_user.id, message.chat.id) == 4:
            msg = bot.reply_to(message, "⏳ **System Updating...**\nGit Pull လုပ်နေပါသည်...")
            try:
                output = subprocess.check_output(["git", "pull"]).decode('utf-8')
                bot.edit_message_text(f"✅ **Git Pull Success!**\n\n`{output}`\n\n🔄 စနစ်ကို Restart ချနေပါပြီ...", msg.chat.id, msg.message_id)
                time.sleep(2)
                os.system("pm2 restart all")
            except Exception as e:
                bot.edit_message_text(f"❌ **Update Failed!**\n\nError: {e}", msg.chat.id, msg.message_id)
        else:
            bot.reply_to(message, "⚠️ ဤ Command ကို Manager သာ အသုံးပြုခွင့်ရှိပါသည်။")

    @bot.message_handler(commands=['sys_logs'])
    def handle_sys_logs(message):
        if db_manager.get_user_level(message.from_user.id, message.chat.id) == 4:
            try:
                # PM2 logs are usually in ~/.pm2/logs/
                # But we can use pm2 logs --nostream --lines 20
                output = subprocess.check_output(["pm2", "logs", "--nostream", "--lines", "20"]).decode('utf-8')
                # Telegram limit 4096
                if len(output) > 4000: output = output[-4000:]
                bot.reply_to(message, f"📋 **System Logs (Last 20 lines):**\n\n`<pre>{html.escape(output)}</pre>`", parse_mode="HTML")
            except Exception as e:
                bot.reply_to(message, f"❌ Log ဖတ်၍မရပါ: {e}")
        else:
            bot.reply_to(message, "⚠️ ဤ Command ကို Manager သာ အသုံးပြုခွင့်ရှိပါသည်။")

    @bot.message_handler(commands=['stafflist'])
    def handle_staff_list(message):
        if is_manager(message.from_user.id) or db_manager.check_if_staff(message.from_user.id):
            staffs = db_manager.get_all_staff()
            if staffs:
                msg = "👥 **ဝန်ထမ်းစာရင်း:**\n"
                for u, n, b, d in staffs:
                    msg += f"• {n} (`{u}`) | {b} | {d}\n"
            else:
                msg = "⚠️ ဝန်ထမ်းစာရင်း မရှိသေးပါ။"
            bot.reply_to(message, msg, parse_mode="Markdown")

    # --- [ Section ၄: အဆင့်မြင့် ဝန်ထမ်းသွင်းခြင်းစနစ် (/addstaff) ] ---
    @bot.message_handler(commands=['addstaff'])
    def start_add_staff(message):
        if db_manager.get_user_level(message.from_user.id, message.chat.id) == 4:
            text_parts = message.text.split(' ', 2)
            if len(text_parts) == 3:
                try:
                    user_id = int(text_parts[1].strip())
                    name = text_parts[2].strip()
                    staff_reg_data[message.chat.id] = {'user_id': user_id, 'name': name}
                    show_branch_buttons(message.chat.id)
                except ValueError:
                    bot.reply_to(message, "⚠️ ID သည် ဂဏန်းဖြစ်ရပါမည်။\nပုံစံ- `/addstaff ID နာမည်`")
            else:
                msg = bot.reply_to(message, "👤 **ဝန်ထမ်းအသစ် စာရင်းသွင်းခြင်း**\n\n၁။ 🆔 **User ID** ကို ရိုက်ထည့်ပါ:")
                bot.register_next_step_handler(msg, process_staff_id_step)

    def process_staff_id_step(message):
        try:
            user_id = int(message.text.strip())
            staff_reg_data[message.chat.id] = {'user_id': user_id}
            msg = bot.reply_to(message, "၂။ 📝 ဝန်ထမ်းရဲ့ **အမည်** ကို ရိုက်ထည့်ပါ:")
            bot.register_next_step_handler(msg, process_staff_name_step)
        except ValueError:
            bot.reply_to(message, "⚠️ ID သည် ဂဏန်းဖြစ်ရပါမည်။ /addstaff ပြန်နှိပ်ပါ။")

    def process_staff_name_step(message):
        chat_id = message.chat.id
        if chat_id in staff_reg_data:
            staff_reg_data[chat_id]['name'] = message.text.strip()
            show_branch_buttons(chat_id)

    def show_branch_buttons(chat_id):
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(b, callback_data=f"brn_{b}") for b in BRANCHES]
        markup.add(*buttons)
        bot.send_message(chat_id, "📍 **Branch (ရုံးခွဲ)** ကို ရွေးချယ်ပါ:", reply_markup=markup, parse_mode="Markdown")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('brn_'))
    def callback_branch(call):
        chat_id = call.message.chat.id
        branch_name = call.data.replace('brn_', '')
        if chat_id in staff_reg_data:
            staff_reg_data[chat_id]['branch'] = branch_name
            markup = types.InlineKeyboardMarkup(row_width=2)
            buttons = [types.InlineKeyboardButton(d, callback_data=f"dep_{d}") for d in DEPARTMENTS]
            markup.add(*buttons)
            bot.edit_message_text(f"📍 Branch: **{branch_name}**\n\n၄။ 🏢 **Department** ကို ရွေးပါ:", 
                                  chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('dep_'))
    def callback_dept(call):
        chat_id = call.message.chat.id
        dept_name = call.data.replace('dep_', '')
        if chat_id in staff_reg_data:
            data = staff_reg_data[chat_id]
            db_manager.add_staff(data['user_id'], data['name'], data['branch'], dept_name)
            del staff_reg_data[chat_id]
            bot.edit_message_text(f"✅ **အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ!**\n\n👤 ဝန်ထမ်း: {data['name']}\n📍 ဌာန: {dept_name} ({data['branch']})", 
                                  chat_id, call.message.message_id, parse_mode="Markdown")

    # --- [ Section ၅: Analytics & Group Creation ] ---
    @bot.message_handler(commands=['analytics'])
    def handle_analytics(message):
        if is_manager(message.from_user.id):
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("📅 Today", callback_data="stat_today"),
                       types.InlineKeyboardButton("🗓️ Month", callback_data="stat_month"),
                       types.InlineKeyboardButton("📊 All Time", callback_data="stat_all"))
            bot.send_message(message.chat.id, "📊 **Performance Report**\nကာလကို ရွေးချယ်ပါ-", reply_markup=markup, parse_mode="Markdown")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("mute_ai:"))
    def callback_mute_ai(call):
        chat_id = int(call.data.split(":")[1])
        user_name = call.from_user.first_name
        group_name = call.message.chat.title or "Unknown Group"
        
        # ၁။ DB Update
        db_manager.set_group_ai_status(chat_id, 'OFF')
        
        # ၂။ User Feedback
        bot.answer_callback_query(call.id, "🔇 ဤ Group အတွက် AI ကို ပိတ်လိုက်ပါပြီ။")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        bot.send_message(chat_id, "🔇 **AI System: OFF**\nဤ Group အတွက် AI အော်တိုစာပြန်ခြင်းကို ပိတ်လိုက်ပါပြီ။ ပြန်ဖွင့်လိုပါက Manager ကို အကြောင်းကြားပါ။")

        # ၃။ Admin Notification (Topic 920)
        try:
            import pytz
            from datetime import datetime
            tz = pytz.timezone('Asia/Yangon')
            now_str = datetime.now(tz).strftime('%Y-%m-%d %I:%M %p')
            
            admin_chat_id = -1003601049225
            admin_topic_id = 920
            
            alert_text = (
                "⚠️ **AI Disabled Alert**\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"🏪 Group: <b>{group_name}</b>\n"
                f"👤 By: <b>{user_name}</b>\n"
                f"📅 Date/Time: {now_str}\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "Status: Currently <b>OFF</b>."
            )
            
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("[ 🔓 ပြန်ဖွင့်မည် ]", callback_data=f"unmute_ai:{chat_id}"))
            
            bot.send_message(admin_chat_id, alert_text, message_thread_id=admin_topic_id, reply_markup=markup, parse_mode="HTML")
            log.info(f"📢 AI Mute Alert sent to Admin for {group_name}")
        except Exception as e:
            log.error(f"❌ Failed to send AI Mute Alert: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("unmute_ai:"))
    def callback_unmute_ai(call):
        chat_id = int(call.data.split(":")[1])
        
        # ၁။ DB Update
        db_manager.set_group_ai_status(chat_id, 'ON')
        
        # ၂။ Admin Feedback & Cleanup
        bot.answer_callback_query(call.id, "✅ AI ကို ပြန်လည်ဖွင့်လှစ်ပြီးပါပြီ။")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception as e:
            log.warning(f"⚠️ Could not delete admin notification: {e}")
            
        # ၃။ Group Notification
        try:
            bot.send_message(chat_id, "✅ ဤ Group အတွက် AI Auto-Answer ကို ပြန်လည်ဖွင့်လှစ်လိုက်ပါပြီ။")
        except Exception as e:
            log.error(f"❌ Failed to send unmute confirmation to group {chat_id}: {e}")

    @bot.callback_query_handler(func=lambda call: call.data == "sys_restart_confirm")
    def callback_sys_restart_confirm(call):
        if db_manager.get_user_level(call.from_user.id, call.message.chat.id) == 4:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("✅ အတည်ပြုသည် (Confirm)", callback_data="sys_restart_all"))
            markup.add(types.InlineKeyboardButton("❌ မလုပ်တော့ပါ (Cancel)", callback_data="sys_cancel"))
            bot.edit_message_text("🔄 **System Restart**\n\nစနစ်တစ်ခုလုံးကို Restart ချရန် သေချာပါသလား အစ်ကို?",
                                  call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "sys_restart_all")
    def callback_sys_restart_all(call):
        if db_manager.get_user_level(call.from_user.id, call.message.chat.id) == 4:
            bot.edit_message_text("🔄 **Restarting All Processes...**\n၁၀ စက္ကန့်ခန့် စောင့်ပေးပါဗျ။",
                                  call.message.chat.id, call.message.message_id)
            log.warning(f"⚠️ System Restart triggered by {call.from_user.id}")
            time.sleep(2)
            os.system("pm2 restart all")
            # No need to exit here as PM2 will restart the process

    @bot.callback_query_handler(func=lambda call: call.data == "sys_cancel")
    def callback_sys_cancel(call):
        bot.delete_message(call.message.chat.id, call.message.message_id)

    @bot.callback_query_handler(func=lambda call: call.data == "sys_aioff")
    def callback_sys_aioff(call):
        if db_manager.get_user_level(call.from_user.id, call.message.chat.id) == 4:
            db_manager.set_ai_global_status('OFF')
            bot.answer_callback_query(call.id, "🔇 AI Answer (Global): OFF")
            bot.send_message(call.message.chat.id, "❌ **AI Answer (Global): OFF**\nAI မှ စာပြန်ခြင်းစနစ်ကို ပိတ်လိုက်ပါပြီ။", message_thread_id=920)
            handle_status(call.message)

    @bot.callback_query_handler(func=lambda call: call.data == "sys_pickupoff")
    def callback_sys_pickupoff(call):
        if db_manager.get_user_level(call.from_user.id, call.message.chat.id) == 4:
            db_manager.set_auto_pickup_global_status('OFF')
            bot.answer_callback_query(call.id, "⏸️ Auto Pickup (Global): OFF")
            bot.send_message(call.message.chat.id, "❌ **Auto Pickup (Global): OFF**\nအော်ဒါအလိုအလျောက်ကောက်သည့်စနစ်ကို ပိတ်လိုက်ပါပြီ။", message_thread_id=920)
            handle_status(call.message)

    @bot.callback_query_handler(func=lambda call: call.data == "sys_logs_20")
    def callback_sys_logs_20(call):
        if db_manager.get_user_level(call.from_user.id, call.message.chat.id) == 4:
            try:
                output = subprocess.check_output(["pm2", "logs", "--nostream", "--lines", "20"]).decode('utf-8')
                if len(output) > 4000: output = output[-4000:]
                bot.send_message(call.message.chat.id, f"📋 **System Logs (Last 20 lines):**\n\n`<pre>{html.escape(output)}</pre>`", parse_mode="HTML")
            except Exception as e:
                bot.answer_callback_query(call.id, f"❌ Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data == "sys_copy_fix")
    def callback_sys_copy_fix(call):
        if db_manager.get_user_level(call.from_user.id, call.message.chat.id) == 4:
            try:
                fix_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'last_fix_prompt.txt')
                if os.path.exists(fix_file):
                    with open(fix_file, 'r', encoding='utf-8') as f:
                        fix_prompt = f.read()
                    bot.send_message(call.message.chat.id, f"📋 **Fix-Prompt (Copy-Paste this):**\n\n`{fix_prompt}`", parse_mode="Markdown")
                else:
                    bot.answer_callback_query(call.id, "⚠️ Fix-Prompt မတွေ့ပါ။")
            except Exception as e:
                bot.answer_callback_query(call.id, f"❌ Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("stat_"))
    def callback_analytics(call):
        period = call.data.split("_")[1]
        stats = db_manager.get_staff_stats(period)
        report = f"📊 **Performance ({period.capitalize()})**\n\n"
        if stats:
            for name, total, avg in stats:
                report += f"👤 {name}: {total} စောင် (Avg: {round(avg, 2)} mins)\n"
        else:
            report += "ဒေတာမရှိပါ။"
        bot.edit_message_text(report, call.message.chat.id, call.message.message_id, parse_mode="Markdown")

    @bot.message_handler(commands=['newgroup'])
    def handle_new_group(message):
        if is_manager(message.from_user.id):
            group_creator.create_new_group(bot, message)

    # --- [ Section ၆: Instant Alert ] ---
    @bot.message_handler(commands=['alert'])
    def handle_manual_alert(message):
        """ Reply ဆွဲပြီး /alert ရိုက်ပါက Office Hours မကြည့်ဘဲ ချက်ချင်း Alert ထုတ်ပေးခြင်း """
        # 💡 အစ်ကို့တောင်းဆိုချက်အရ ဘယ်သူမဆို သုံးခွင့်ပေးမည်
        if not message.reply_to_message:
            bot.reply_to(message, "⚠️ Alert ထုတ်လိုသော စာကို Reply ဆွဲပြီးမှ `/alert` ဟု ရိုက်ပေးပါဗျာ။")
            return

        orig_msg = message.reply_to_message
        chat_id = message.chat.id
        topic_id = orig_msg.message_thread_id if orig_msg.is_topic_message else 0
        
        # OS Group ဟုတ်မဟုတ် စစ်ဆေးခြင်း
        if not db_manager.check_if_os_group(chat_id):
            bot.reply_to(message, "⚠️ ဤ Command ကို OS Group များအတွင်းသာ အသုံးပြုနိုင်ပါသည်။")
            return

        # 💡 DB တွင် Manual Alert အဖြစ် မှတ်သားခြင်း (Strict Resolution အတွက်)
        db_manager.set_manual_alert(orig_msg.message_id, chat_id)

        _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
        
        # Media Type စစ်ဆေးခြင်း
        text = orig_msg.text or orig_msg.caption
        media_id = None
        if not text:
            if orig_msg.photo: text = "🖼️ Photo"; media_id = orig_msg.photo[-1].file_id
            elif orig_msg.voice: text = "🎙️ Voice Message"; media_id = orig_msg.voice.file_id
            elif orig_msg.video: text = "📹 Video"; media_id = orig_msg.video.file_id
            else: text = "📦 Media Content"

        # ချက်ချင်း Alert ပို့ခြင်း
        alert_id = auditor.send_new_alert(
            chat_id, topic_id, orig_msg.message_id,
            text, "Manual Alert", shop_name, orig_msg.date,
            media_id=media_id,
            title="🚨 **Urgent Alert (Manual)**"
        )
        
        # 💡 အစ်ကို့တောင်းဆိုချက်အရ Silent ဖြစ်စေရန် Confirmation Message ကို ပိတ်ထားပြီး Command ကို ပြန်ဖျက်ပေးမည်
        try:
            bot.delete_message(chat_id, message.message_id)
        except Exception as e:
            log.error(f"Error deleting /alert command: {e}")

        if not alert_id:
            # Alert မအောင်မြင်ပါက Admin သိစေရန် Log ထုတ်မည် (သို့မဟုတ်) လိုအပ်ပါက Reply ပြန်နိုင်သည်
            log.error(f"Manual Alert failed for chat_id: {chat_id}")

    # --- [ Section ၇: OS Group စာရင်းနှင့် Register စနစ် ] ---
    @bot.message_handler(commands=['oslist'])
    def handle_os_list(message):
        user_id = message.from_user.id
        if is_manager(user_id) or db_manager.check_if_staff(user_id):
            try:
                groups = db_manager.get_os_group_names()

                if not groups:
                    bot.reply_to(message, "⚠️ OS Group စာရင်း မရှိသေးပါ။")
                    return

                msg = "🏪 **OS Group စာရင်း:**\n\n"
                for idx, (shop_name,) in enumerate(groups, 1):
                    clean_name = db_manager.clean_shop_name(shop_name)
                    msg += f"{idx}။ {clean_name}\n"
                    if len(msg) > 3500:
                        bot.send_message(message.chat.id, msg)
                        msg = ""
                if msg:
                    bot.send_message(message.chat.id, msg)
            except Exception as e:
                log.error(f"❌ OSList Logic Error: {e}")
                bot.reply_to(message, f"❌ Error: {e}")

    @bot.message_handler(commands=['register'])
    def handle_register_group(message):
        user_id = message.from_user.id
        if is_manager(user_id):
            if message.chat.type not in ['group', 'supergroup']:
                bot.reply_to(message, "⚠️ ဤ Command ကို OS Group ထဲမှာပဲ ရိုက်ပေးပါ။")
                return

            chat_id = message.chat.id
            register_group_data[chat_id] = {
                "shop_name": db_manager.clean_shop_name(message.chat.title or "Unknown Shop"),
                "chat_id": chat_id
            }
            msg = bot.reply_to(message, "၁။ 🎧 Pick Up Topic ရဲ့ ID ကို ရိုက်ထည့်ပါ (မရှိလျှင် 0 ဟုရိုက်ပါ):")
            bot.register_next_step_handler(msg, process_reg_pickup)

    def process_reg_pickup(message):
        chat_id = message.chat.id
        if chat_id not in register_group_data:
            return
        try:
            register_group_data[chat_id]["pickup_topic_id"] = int(message.text.strip())
            msg = bot.reply_to(message, "၂။ ⚠️ Error Topic ရဲ့ ID ကို ရိုက်ထည့်ပါ (မရှိလျှင် 0 ဟုရိုက်ပါ):")
            bot.register_next_step_handler(msg, process_reg_error)
        except ValueError:
            bot.reply_to(message, "⚠️ Topic ID သည် ဂဏန်းဖြစ်ရပါမည်။ /register ကို ပြန်ရိုက်ပါ။")
            register_group_data.pop(chat_id, None)

    def process_reg_error(message):
        chat_id = message.chat.id
        if chat_id not in register_group_data:
            return
        try:
            register_group_data[chat_id]["error_topic_id"] = int(message.text.strip())
            msg = bot.reply_to(message, "၃။ 💰 Fin & Voc Topic ရဲ့ ID ကို ရိုက်ထည့်ပါ (မရှိလျှင် 0 ဟုရိုက်ပါ):")
            bot.register_next_step_handler(msg, process_reg_finance)
        except ValueError:
            bot.reply_to(message, "⚠️ Topic ID သည် ဂဏန်းဖြစ်ရပါမည်။ /register ကို ပြန်ရိုက်ပါ။")
            register_group_data.pop(chat_id, None)

    def process_reg_finance(message):
        chat_id = message.chat.id
        if chat_id not in register_group_data:
            return
        try:
            register_group_data[chat_id]["finance_topic_id"] = int(message.text.strip())
        except ValueError:
            bot.reply_to(message, "⚠️ Topic ID သည် ဂဏန်းဖြစ်ရပါမည်။ /register ကို ပြန်ရိုက်ပါ။")
            register_group_data.pop(chat_id, None)
            return

        data = register_group_data.get(chat_id, {})
        shop_name = db_manager.clean_shop_name(data.get("shop_name", "Unknown Shop"))

        topic_payload = []
        if data.get("pickup_topic_id", 0) != 0:
            topic_payload.append({
                "topic_name": "Pick Up/Urgent/စုံစမ်းရန်",
                "topic_id": data["pickup_topic_id"]
            })
        if data.get("error_topic_id", 0) != 0:
            topic_payload.append({
                "topic_name": "Error",
                "topic_id": data["error_topic_id"]
            })
        if data.get("finance_topic_id", 0) != 0:
            topic_payload.append({
                "topic_name": "Fin & Voc",
                "topic_id": data["finance_topic_id"]
            })

        if not topic_payload:
            bot.reply_to(message, "⚠️ Topic ID အားလုံး 0 ဖြစ်နေပါသည်။ သိမ်းဆည်းစရာ မရှိပါ။")
            register_group_data.pop(chat_id, None)
            return

        try:
            db_manager.save_manual_register(chat_id, shop_name, topic_payload)
            bot.reply_to(message, f"✅ {shop_name} အား မှတ်ပုံတင်ပြီးပါပြီ။")
        except Exception as e:
            log.error(f"Manual register failed: {e}")
            bot.reply_to(message, f"❌ Register Error: {e}")
        finally:
            register_group_data.pop(chat_id, None)

    # ==========================================
    # 🔥 အသစ်ထပ်တိုး Command များ (New Features)
    # ==========================================

    @bot.message_handler(commands=['delos'])
    def handle_delos(message):
        if is_manager(message.from_user.id):
            chat_id = message.chat.id
            text_parts = message.text.split()
            
            # /delos -100123456789 ဆိုပြီး ID နဲ့ ဖြုတ်လို့ရအောင် စစ်ဆေးခြင်း
            if len(text_parts) > 1:
                try:
                    chat_id = int(text_parts[1].strip())
                except ValueError:
                    bot.reply_to(message, "⚠️ Chat ID သည် ဂဏန်းဖြစ်ရပါမည်။ ဥပမာ - `/delos -100123456`")
                    return

            try:
                db_manager.delete_os_group_by_chat_id(chat_id)
                bot.reply_to(message, "✅ ထို OS Group ကို စာရင်းမှ အောင်မြင်စွာ ပယ်ဖျက်လိုက်ပါပြီ။")
            except Exception as e:
                log.error(f"DelOS Error: {e}")
                bot.reply_to(message, f"⚠️ Error: {e}")

    @bot.message_handler(commands=['pending'])
    def handle_pending(message):
        if is_manager(message.from_user.id) or db_manager.check_if_staff(message.from_user.id):
            try:
                rows = db_manager.get_pending_counts_by_shop()

                if not rows:
                    bot.reply_to(message, "✅ လက်ရှိတွင် Pending ဖြစ်နေသော Ticket လုံးဝမရှိပါ။ ဝန်ထမ်းများ အလုပ်ကြိုးစားကြပါသည်။")
                    return

                msg = "⏳ **လက်ရှိ ပြန်မဖြေရသေးသော (Pending) စာရင်း:**\n\n"
                for shop_name, count in rows:
                    msg += f"• {shop_name}: **{count}** စောင်\n"
                
                bot.reply_to(message, msg, parse_mode="Markdown")
            except Exception as e:
                log.error(f"Pending Check Error: {e}")
                bot.reply_to(message, f"⚠️ Error: {e}")

    @bot.message_handler(commands=['broadcast'])
    def handle_broadcast(message):
        if is_manager(message.from_user.id):
            text = message.text.replace('/broadcast', '').strip()
            if not text:
                bot.reply_to(message, "⚠️ ပို့ချင်သော စာသားကို ထည့်ပါ။\nဥပမာ - `/broadcast ဒီနေ့ နေ့လယ် ရုံးခဏပိတ်ပါမည်`")
                return
            
            staffs = db_manager.get_all_staff()
            success_count = 0
            
            for u_id, name, branch, dept in staffs:
                try:
                    bot.send_message(u_id, f"📢 **[Manager ထံမှ အသိပေးချက်]**\n\n{text}", parse_mode="Markdown")
                    success_count += 1
                except Exception as e:
                    log.warning(f"Broadcast failed for {name} ({u_id}): {e}")
            
            bot.reply_to(message, f"✅ ဝန်ထမ်းសរុប {success_count} ဦးဆီသို့ အသိပေးချက် ပို့ပြီးပါပြီ။")

    @bot.message_handler(commands=['logs'])
    def handle_logs(message):
        if is_manager(message.from_user.id):
            try:
                with open('logs/carryman_system.log', 'r', encoding='utf-8') as f:
                    lines = f.readlines()[-10:] # နောက်ဆုံး ၁၀ ကြောင်းကိုပဲ ယူမည်
                
                if not lines:
                    bot.reply_to(message, "မှတ်တမ်း (Log) ထဲတွင် ဘာမှ မရှိသေးပါ။")
                    return
                
                log_text = "".join(lines)
                bot.reply_to(message, f"🛠 **နောက်ဆုံး Log (၁၀) ကြောင်း:**\n\n`{log_text}`", parse_mode="Markdown")
            except FileNotFoundError:
                bot.reply_to(message, "⚠️ `logs/carryman_system.log` ဖိုင်ကို မတွေ့ပါ။")
            except Exception as e:
                bot.reply_to(message, f"⚠️ Log ဖိုင် ဖတ်၍မရပါ: {e}")

    @bot.message_handler(commands=['findos'])
    def handle_findos(message):
        if is_manager(message.from_user.id) or db_manager.check_if_staff(message.from_user.id):
            keyword = message.text.replace('/findos', '').strip()
            if not keyword:
                bot.reply_to(message, "⚠️ ရှာဖွေလိုသော ဆိုင်အမည်ကို ထည့်ပါ။\nဥပမာ - `/findos lucky`")
                return
            
            try:
                rows = db_manager.find_os_groups_by_keyword(keyword)

                if rows:
                    msg = f"🔍 **'{keyword}' ဖြင့် ရှာဖွေတွေ့ရှိမှုများ:**\n\n"
                    for chat_id, shop_name in rows:
                        msg += f"• {shop_name} (`{chat_id}`)\n"
                    bot.reply_to(message, msg, parse_mode="Markdown")
                else:
                    bot.reply_to(message, f"⚠️ '{keyword}' နှင့် တူသော ဆိုင်ကို မတွေ့ရှိပါ။")
            except Exception as e:
                log.error(f"Find OS Error: {e}")
                bot.reply_to(message, f"⚠️ Error: {e}")

    @bot.message_handler(commands=['toggle_env'])
    def handle_toggle_env(message):
        """ Sandbox နှင့် Production အကြား ပြောင်းလဲခြင်း """
        if db_manager.get_user_level(message.from_user.id, message.chat.id) == 4:
            current = db_manager.get_setting('env_mode', 'Sandbox')
            new_mode = 'Production' if current == 'Sandbox' else 'Sandbox'
            db_manager.set_setting('env_mode', new_mode)
            
            icon = "🚀" if new_mode == 'Production' else "🧪"
            bot.reply_to(message, f"{icon} **Environment Mode Switched!**\n\nCurrent Mode: `{new_mode}`", parse_mode="Markdown")
            log.warning(f"⚠️ Environment switched to {new_mode} by {message.from_user.id}")
        else:
            bot.reply_to(message, "⚠️ ဤ Command ကို Manager သာ အသုံးပြုခွင့်ရှိပါသည်။")

    @bot.message_handler(commands=['addfun'])
    def handle_add_function(message):
        """ Module အသစ်များကို Database တွင် Register လုပ်ခြင်း """
        if db_manager.get_user_level(message.from_user.id, message.chat.id) == 4:
            # Format: /addfun <name> <description> <module_path>
            # Description တွင် space များပါနိုင်သဖြင့် ပိုမိုကောင်းမွန်သော parsing ကို အသုံးပြုပါမည်။
            parts = message.text.split()
            
            if len(parts) < 4:
                bot.reply_to(message, "⚠️ **အသုံးပြုပုံ မှားယွင်းနေပါသည် အစ်ကို။**\n\nပုံစံ - `/addfun <name> <description> <module_path>`\nဥပမာ - `/addfun auto_pickup \"Auto Pickup System\" modules.auto_pickup`", parse_mode="Markdown")
                return
            
            name = parts[1].strip()
            module_path = parts[-1].strip()
            # ကြားထဲက အစိတ်အပိုင်းအားလုံးကို description အဖြစ် ယူပါမည်
            description = " ".join(parts[2:-1]).strip().strip("'").strip('"')
            
            # Database ထဲသို့ ထည့်သွင်းခြင်း
            success = db_manager.add_function(name, description, module_path)
            
            if success:
                bot.reply_to(message, f"✅ **Function Registered Successfully!**\n\n📌 Name: `{name}`\n📝 Description: {description}\n📂 Path: `{module_path}`", parse_mode="Markdown")
            else:
                bot.reply_to(message, "❌ Database ထဲသို့ ထည့်သွင်းရာတွင် အမှားအယွင်း ရှိသွားပါသည် အစ်ကို။")
        else:
            bot.reply_to(message, "⚠️ ဤ Command ကို Manager သာ အသုံးပြုခွင့်ရှိပါသည်။")

    @bot.message_handler(commands=['mapshops'])
    def handle_map_shops(message):
        """ Mapping မရှိသေးသော ဆိုင်များကို Manager ထံ ပို့ပေးခြင်း """
        if is_manager(message.from_user.id):
            unmapped = db_manager.get_unmapped_os_groups()
            if not unmapped:
                bot.reply_to(message, "✨ Mapping လုပ်ရန် လိုအပ်သော ဆိုင်မရှိပါ။ အားလုံး အဆင်ပြေပါသည်။")
                return

            bot.reply_to(message, f"🔍 Mapping မရှိသေးသော ဆိုင် {len(unmapped)} ခု တွေ့ရှိရပါသည်။ တစ်ခုချင်းစီ ပို့ပေးပါ့မယ်။")
            
            for chat_id, shop_name in unmapped[:20]: # တစ်ခါတည်း အများကြီး မပို့မိစေရန် ၂၀ ခုစီ ကန့်သတ်မည်
                clean_name = db_manager.clean_shop_name(shop_name)
                suggestions = db_manager.get_website_suggestions(clean_name[:5])

                markup = types.InlineKeyboardMarkup(row_width=1)
                for s in suggestions:
                    markup.add(types.InlineKeyboardButton(f"✅ {s}", callback_data=f"ap_set_{chat_id}_{s}"))
                
                markup.add(types.InlineKeyboardButton("⌨️ Manual Type", callback_data=f"ap_manual_{chat_id}"))

                bot.send_message(
                    message.chat.id,
                    f"🏪 Telegram: <b>{shop_name}</b>\n\nမှန်ကန်တဲ့ Website ဆိုင်နာမည်ကို ရွေးပေးပါ-",
                    reply_markup=markup, parse_mode="HTML"
                )
                time.sleep(1) # Telegram Flood Limit မမိစေရန်
        else:
            bot.reply_to(message, "⚠️ ဤ Command ကို Manager သာ အသုံးပြုခွင့်ရှိပါသည်။")
