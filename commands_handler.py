import os
import time
from telebot import types
from logger import log
import db_manager
import group_creator
import alert_system

# Environment Variable
MANAGER_ID = int(os.getenv('MANAGER_ID'))

# --- [ အစ်ကို့ရဲ့ မူရင်း စာရင်းများ ] ---
BRANCHES = ["Yangon", "Insein", "Htauk Kyant", "Mandalay"]
DEPARTMENTS = [
    "OS Service", "Rider Service", "Rider Finance", "OS Finance", 
    "Accountant", "Data Entry", "Marketing", "HR", "Admin", 
    "Rider", "Agent", "Other"
]

staff_reg_data = {}

# ... (အပေါ်က စာကြောင်းတွေက အရင်အတိုင်းထားပါ) ...

staff_reg_data = {}

def register_handlers(bot):
    """ Bot Command အားလုံးကို ဤနေရာတွင် စုစည်းမှတ်ပုံတင်ပေးသည် """
    
    # --- [ Section: Maintenance Control (အသစ်ထည့်ရမည့်နေရာ) ] ---
    @bot.message_handler(commands=['off'])
    def handle_bot_off(message):
        """ Bot ကို ခေတ္တ အိပ်ပျော်စေခြင်း (Database ထဲတွင် သိမ်းမည်) """
        if message.from_user.id == MANAGER_ID:
            import db_manager
            db_manager.set_setting('bot_active', 'False')
            bot.reply_to(message, "💤 **Bot Maintenance Mode: ON**\n\nစနစ်ကို ပိတ်ထားလိုက်ပါပြီ။ ဝန်ထမ်းများ Command ရိုက်လျှင်လည်း အသိပေးစာ ပြန်ပါလိမ့်မည်။")

    @bot.message_handler(commands=['on'])
    def handle_bot_on(message):
        """ Bot ကို ပြန်လည် နိုးထစေခြင်း """
        if message.from_user.id == MANAGER_ID:
            import db_manager
            db_manager.set_setting('bot_active', 'True')
            bot.reply_to(message, "🚀 **Bot Maintenance Mode: OFF**\n\nစနစ်ကို ပုံမှန်အတိုင်း ပြန်လည်ဖွင့်လှစ်လိုက်ပါပြီ။")

    @bot.message_handler(commands=['start'])
    def send_welcome(message):
        if message.from_user.id == MANAGER_ID or db_manager.check_if_staff(message.from_user.id):
            bot.reply_to(message, "🤖 CarryMan AI Agent စနစ်မှ ကြိုဆိုပါတယ်။\nအကူအညီလိုပါက Manager ကို ဆက်သွယ်ပါ။")

    # ... (အောက်က /status တွေ, /restart တွေကတော့ အရင်အတိုင်း ဆက်ရှိနေမှာပါ) ...
    @bot.message_handler(commands=['on'])
    def handle_bot_on(message):
        """ Bot ကို ပြန်လည် နိုးထစေခြင်း """
        if message.from_user.id == MANAGER_ID:
            import db_manager
            db_manager.set_setting('bot_active', 'True')
            bot.reply_to(message, "🚀 **Bot Maintenance Mode: OFF**\n\nစနစ်ကို ပုံမှန်အတိုင်း ပြန်လည်ဖွင့်လှစ်လိုက်ပါပြီ။")
   
    @bot.message_handler(commands=['start'])
    def send_welcome(message):
        if message.from_user.id == MANAGER_ID or db_manager.check_if_staff(message.from_user.id):
            bot.reply_to(message, "🤖 CarryMan AI Agent စနစ်မှ ကြိုဆိုပါတယ်။\nအကူအညီလိုပါက Manager ကို ဆက်သွယ်ပါ။")

    @bot.message_handler(commands=['status'])
    def handle_status(message):
        if message.from_user.id == MANAGER_ID or db_manager.check_if_staff(message.from_user.id):
            staff_count = len(db_manager.get_all_staff())
            status_text = (
                "🟢 **Bot Status: Online**\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"👥 ဝန်ထမ်းအရေအတွက်: {staff_count} ဦး\n"
                "📡 SLA Watchdog: Active\n"
                "━━━━━━━━━━━━━━━━━━"
            )
            bot.reply_to(message, status_text, parse_mode="Markdown")

    @bot.message_handler(commands=['restart'])
    def handle_restart(message):
        if message.from_user.id == MANAGER_ID:
            bot.reply_to(message, "🔄 Bot ကို Restart ချနေပါပြီ... စက္ကန့်အနည်းငယ် စောင့်ပေးပါဗျ။")
            time.sleep(1)
            os._exit(0)

    @bot.message_handler(commands=['stafflist'])
    def handle_staff_list(message):
        if message.from_user.id == MANAGER_ID or db_manager.check_if_staff(message.from_user.id):
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
        if message.from_user.id == MANAGER_ID:
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
        if message.from_user.id == MANAGER_ID:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("📅 Today", callback_data="stat_today"),
                       types.InlineKeyboardButton("🗓️ Month", callback_data="stat_month"),
                       types.InlineKeyboardButton("📊 All Time", callback_data="stat_all"))
            bot.send_message(message.chat.id, "📊 **Performance Report**\nကာလကို ရွေးချယ်ပါ-", reply_markup=markup, parse_mode="Markdown")

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
        if message.from_user.id == MANAGER_ID:
            group_creator.create_new_group(bot, message)

    # --- [ Section ၆: Instant Alert (Reply Support ဖြင့် အဆင့်မြှင့်ထားသည်) ] ---
    @bot.message_handler(commands=['alert'])
    def handle_manual_alert(message):
        user_id = message.from_user.id
        chat_id = message.chat.id
        if db_manager.check_if_staff(user_id) or user_id == MANAGER_ID:
            if db_manager.check_if_os_group(chat_id):
                
                original_text = ""
                msg_link = ""
                # Customer စာကို Reply ဆွဲထားလျှင် ဖမ်းယူမည်
                if message.reply_to_message:
                    original_text = message.reply_to_message.text or message.reply_to_message.caption or "[Media / Sticker]"
                    link_chat_id = str(chat_id).replace("-100", "")
                    msg_link = f"https://t.me/c/{link_chat_id}/{message.reply_to_message.message_id}"
                    if message.reply_to_message.is_topic_message:
                        msg_link += f"?thread={message.reply_to_message.message_thread_id}"
                
                try: bot.delete_message(chat_id, message.message_id)
                except: pass
                
                topic_id = message.message_thread_id if message.is_topic_message else 0
                issue_text = message.text.replace('/alert', '').strip() or "အရေးပေါ် အကူအညီလိုပါသည် (Manual Alert)"
                
                try: 
                    # 💡 kwargs ဖြင့် original_text နှင့် msg_link ကို ထည့်ပို့သည်
                    alert_system.handle_instant_alert(bot, chat_id, topic_id, message.from_user.first_name, issue_text, original_text=original_text, msg_link=msg_link)
                except Exception as e: 
                    log.error(f"Alert System ခေါ်ယူရာတွင် Error: {e}")

    # --- [ Section ၇: OS Group စာရင်းနှင့် Register စနစ် ] ---
    @bot.message_handler(commands=['oslist'])
    def handle_os_list(message):
        user_id = message.from_user.id
        if user_id == MANAGER_ID or db_manager.check_if_staff(user_id):
            try:
                conn = db_manager.get_connection()
                c = conn.cursor()
                c.execute("SELECT shop_name FROM os_groups")
                groups = c.fetchall()
                conn.close()

                if not groups:
                    bot.reply_to(message, "⚠️ OS Group စာရင်း မရှိသေးပါ။")
                    return

                msg = "🏪 **OS Group စာရင်း:**\n\n"
                for idx, (shop_name,) in enumerate(groups, 1):
                    clean_name = str(shop_name).split('🤝')[0].strip()
                    clean_name = clean_name.replace('CarryMan', '').strip()
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
        if user_id == MANAGER_ID:
            if message.chat.type in ['group', 'supergroup']:
                text_parts = message.text.split(' ', 1)
                shop_name = text_parts[1].strip() if len(text_parts) > 1 else message.chat.title
                try:
                    db_manager.add_os_group(message.chat.id, shop_name)
                    bot.reply_to(message, f"✅ **{shop_name}** အား OS Group စာရင်းသို့ သွင်းလိုက်ပါပြီ။")
                except Exception as e:
                    bot.reply_to(message, f"⚠️ Error: {e}")
            else:
                bot.reply_to(message, "⚠️ ဤ Command ကို OS Group ထဲမှာပဲ ရိုက်ပေးပါ။")

    # ==========================================
    # 🔥 အသစ်ထပ်တိုး Command များ (New Features)
    # ==========================================

    @bot.message_handler(commands=['delos'])
    def handle_delos(message):
        if message.from_user.id == MANAGER_ID:
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
                conn = db_manager.get_connection()
                conn.execute("DELETE FROM os_groups WHERE chat_id=?", (chat_id,))
                conn.commit()
                conn.close()
                bot.reply_to(message, "✅ ထို OS Group ကို စာရင်းမှ အောင်မြင်စွာ ပယ်ဖျက်လိုက်ပါပြီ။")
            except Exception as e:
                log.error(f"DelOS Error: {e}")
                bot.reply_to(message, f"⚠️ Error: {e}")

    @bot.message_handler(commands=['pending'])
    def handle_pending(message):
        if message.from_user.id == MANAGER_ID or db_manager.check_if_staff(message.from_user.id):
            try:
                conn = db_manager.get_connection()
                c = conn.cursor()
                # Pending ဖြစ်နေတဲ့ Ticket အရေအတွက်ကို ဆိုင်အလိုက် ဆွဲထုတ်မည်
                c.execute("""
                    SELECT o.shop_name, COUNT(m.msg_id) 
                    FROM message_logs m 
                    JOIN os_groups o ON m.chat_id = o.chat_id 
                    WHERE m.status='pending' 
                    GROUP BY m.chat_id
                """)
                rows = c.fetchall()
                conn.close()

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
        if message.from_user.id == MANAGER_ID:
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
        if message.from_user.id == MANAGER_ID:
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
        if message.from_user.id == MANAGER_ID or db_manager.check_if_staff(message.from_user.id):
            keyword = message.text.replace('/findos', '').strip()
            if not keyword:
                bot.reply_to(message, "⚠️ ရှာဖွေလိုသော ဆိုင်အမည်ကို ထည့်ပါ။\nဥပမာ - `/findos lucky`")
                return
            
            try:
                conn = db_manager.get_connection()
                c = conn.cursor()
                # LIKE %keyword% ကိုသုံးပြီး ဆင်တူရာတွေ အကုန်ရှာမည်
                c.execute("SELECT chat_id, shop_name FROM os_groups WHERE shop_name LIKE ?", (f'%{keyword}%',))
                rows = c.fetchall()
                conn.close()

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