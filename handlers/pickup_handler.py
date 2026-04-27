import telebot
import pytz
from datetime import datetime, timedelta
from logger import log
import db_manager
from modules import auditor

def register_pickup_handlers(bot: telebot.TeleBot):
    """ Auto Pickup Module အတွက် Callback များကို Register လုပ်ပေးသည် """

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_dt_') or call.data.startswith('ap_vh_'))
    def handle_auto_pickup_callback(call):
        """ Auto Pickup Module အတွက် Callback များ (Date/Vehicle Selection) """
        try:
            from modules import auto_pickup
            # format: ap_dt_{msg_id}_{date_type}_{vehicle}
            # format: ap_vh_{msg_id}_{date_type}_{vehicle}
            parts = call.data.split('_')
            action = parts[1] # dt or vh
            orig_msg_id = int(parts[2])
            date_type = parts[3]
            vehicle = parts[4] if parts[4] != "none" else None

            chat_id = call.message.chat.id
            with db_manager.connection_scope() as conn:
                msg_data = conn.execute("SELECT text FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
            
            if action == "dt":
                if not vehicle:
                    auto_pickup.ask_vehicle(bot, call.message, date_type, orig_msg_id)
                    bot.answer_callback_query(call.id)
                    return
            
            auto_pickup.ask_remark(bot, chat_id, date_type, vehicle, orig_msg_id)
            bot.delete_message(chat_id, call.message.message_id)
            bot.answer_callback_query(call.id)

        except Exception as e:
            log.error(f"❌ Auto Pickup Callback Error: {e}")
            bot.answer_callback_query(call.id, "❌ Error occurred", show_alert=True)

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_st_'))
    def handle_staff_pickup_decision(call):
        """ ဝန်ထမ်းမှ Today/Tomorrow ရွေးချယ်မှုအား ကိုင်တွယ်ခြင်း """
        try:
            from modules import auto_pickup
            # format: ap_st_{orig_msg_id}_{chat_id}_{date_type}_{vehicle}
            parts = call.data.split('_')
            orig_msg_id = int(parts[2])
            chat_id = int(parts[3])
            date_type = parts[4]
            vehicle = parts[5] if parts[5] != "none" else None

            _, _, shop_name = db_manager.get_topic_context(chat_id, 1)

            if date_type == "today":
                sent_msg = bot.send_message(chat_id, "ဒီနေ့ရက်စွဲလေးနဲ့ pick up လေးတင်ပေးလိုက်ပါတယ်နော်", reply_to_message_id=orig_msg_id)
                db_manager.add_pickup_intermediate_msg(chat_id, orig_msg_id, sent_msg.message_id)
                auto_pickup.ask_remark(bot, chat_id, date_type, vehicle, orig_msg_id)
                bot.edit_message_text(f"✅ **Today** အဖြစ် သတ်မှတ်ပြီး Group ထဲသို့ အကြောင်းကြားလိုက်ပါပြီ။", call.message.chat.id, call.message.message_id)
            else:
                markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                v_str = vehicle if vehicle else "none"
                markup.add(
                    telebot.types.InlineKeyboardButton("✅ OK", callback_data=f"ap_cs_{orig_msg_id}_{chat_id}_ok_{v_str}"),
                    telebot.types.InlineKeyboardButton("💬 Admin နှင့်ပြောမည်", callback_data=f"ap_cs_{orig_msg_id}_{chat_id}_admin_{v_str}")
                )
                sent_msg = bot.send_message(
                    chat_id,
                    "ဒီနေ့ rider လေးကဝေးလမ်းကြောင်းလေးကျော်သွားပြီမို့လို့ မနက်ဖြန်လေးကောက်ပေးလို့ရမလားရှင့်",
                    reply_to_message_id=orig_msg_id,
                    reply_markup=markup
                )
                db_manager.add_pickup_intermediate_msg(chat_id, orig_msg_id, sent_msg.message_id)
                bot.edit_message_text(f"✅ **Tomorrow** အတွက် Customer ထံ ခွင့်ပြုချက် တောင်းခံထားပါသည်တ။", call.message.chat.id, call.message.message_id)

            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Staff Pickup Decision Error: {e}")
            bot.answer_callback_query(call.id, "❌ Error occurred")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_cs_'))
    def handle_customer_pickup_decision(call):
        """ Customer မှ OK/Admin ရွေးချယ်မှုအား ကိုင်တွယ်ခြင်း """
        try:
            from modules import auto_pickup
            # format: ap_cs_{orig_msg_id}_{chat_id}_{action}_{vehicle}
            parts = call.data.split('_')
            orig_msg_id = int(parts[2])
            chat_id = int(parts[3])
            action = parts[4]
            vehicle = parts[5] if parts[5] != "none" else "Bicycle"

            if action == "ok":
                auto_pickup.ask_remark(bot, chat_id, "tomorrow", vehicle, orig_msg_id)
                bot.edit_message_text("✅ မနက်ဖြန်အတွက် pick up တင်ပေးထားပါ့မယ်ရှင်။", chat_id, call.message.message_id)
            else:
                db_manager.set_manual_alert(orig_msg_id, chat_id)
                with db_manager.connection_scope() as conn:
                    msg_data = conn.execute("SELECT text, timestamp, media_id FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
                
                if msg_data:
                    text, ts, media_id = msg_data
                    _, _, shop_name = db_manager.get_topic_context(chat_id, 1)
                    auditor.send_new_alert(
                        chat_id, 1, orig_msg_id, text, "Customer requested Admin", shop_name, ts,
                        media_id=media_id, title="🚨 **Urgent Alert (Customer Request)**"
                    )
                auto_pickup.cleanup_pickup_intermediate_msgs(bot, chat_id, orig_msg_id)
                bot.send_message(chat_id, "👨‍💻 Admin ကို အကြောင်းကြားထားပေးပါတယ်ရှင်။ ခဏစောင့်ပေးပါနော်။", reply_to_message_id=orig_msg_id)

            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Customer Pickup Decision Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_cancel_'))
    def handle_pickup_cancel_callback(call):
        """ AI မှ Pickup ဟု မှားယွင်းယူဆမိပါက Rider မှ ပယ်ဖျက်ခြင်း """
        try:
            from modules import auto_pickup
            orig_msg_id = int(call.data.split('_')[2])
            chat_id = call.message.chat.id
            bot.delete_message(chat_id, call.message.message_id)
            auto_pickup.cleanup_pickup_intermediate_msgs(bot, chat_id, orig_msg_id)
            bot.answer_callback_query(call.id, "❌ Pickup မဟုတ်ကြောင်း မှတ်သားလိုက်ပါပြီ။")
        except Exception as e:
            log.error(f"❌ Pickup Cancel Callback Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_rm_'))
    def handle_remark_selection(call):
        """ မှတ်ချက်ရေးမည်/မရှိပါ ရွေးချယ်မှုအား ကိုင်တွယ်ခြင်း """
        try:
            from modules import auto_pickup
            # format: ap_rm_{orig_msg_id}_{date_type}_{vehicle}_{action}
            parts = call.data.split('_')
            orig_msg_id = int(parts[2])
            date_type = parts[3]
            vehicle = parts[4]
            action = parts[5]
            chat_id = call.message.chat.id

            if action == "write":
                msg = bot.send_message(chat_id, "📝 ထည့်ချင်တဲ့ **မှတ်ချက်** ကို ရိုက်ထည့်ပေးပါခင်ဗျာ။", reply_markup=telebot.types.ForceReply())
                db_manager.add_pickup_intermediate_msg(chat_id, orig_msg_id, msg.message_id)
                bot.register_next_step_handler(msg, save_manual_remark, bot, orig_msg_id, date_type, vehicle)
                bot.delete_message(chat_id, call.message.message_id)
            else:
                bot.edit_message_text("👍 ဟုတ်ကဲ့ပါခင်ဗျာ။", chat_id, call.message.message_id)
                finalize_pickup_queue(bot, chat_id, orig_msg_id, date_type, vehicle, None)

            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Remark Selection Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_fix_'))
    def handle_fix_mapping_callback(call):
        """ Shop Mapping ကို Manual ပြင်ဆင်ရန် """
        try:
            chat_id = int(call.data.split('_')[2])
            with db_manager.connection_scope() as conn:
                shop_data = conn.execute("SELECT shop_name FROM os_groups WHERE chat_id = ?", (chat_id,)).fetchone()
            
            shop_name = db_manager.clean_shop_name(shop_data[0]) if shop_data else ""
            suggestions = db_manager.get_website_suggestions(shop_name[:5])

            markup = telebot.types.InlineKeyboardMarkup(row_width=1)
            for s in suggestions:
                markup.add(telebot.types.InlineKeyboardButton(f"✅ {s}", callback_data=f"ap_set_{chat_id}_{s}"))
            
            markup.add(telebot.types.InlineKeyboardButton("⌨️ Manual Type (ကိုယ်တိုင်ရိုက်မည်)", callback_data=f"ap_manual_{chat_id}"))

            bot.edit_message_text(
                f"🔍 **Shop Mapping Fix**\n━━━━━━━━━━━━━━━━━━\n"
                f"🏪 Telegram: <b>{shop_name}</b>\n\n"
                f"အောက်ပါ Website ဆိုင်နာမည်များထဲမှ မှန်ကန်တာကို ရွေးပေးပါ-",
                call.message.chat.id, call.message.message_id,
                reply_markup=markup, parse_mode="HTML"
            )
            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Fix Mapping Callback Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_set_'))
    def handle_set_mapping_callback(call):
        """ Suggestion ထဲမှ တစ်ခုကို ရွေးချယ်လိုက်သည့်အခါ """
        try:
            parts = call.data.split('_')
            chat_id = int(parts[2])
            website_name = "_".join(parts[3:])
            
            db_manager.set_shop_mapping(chat_id, website_name)
            db_manager.retry_failed_pickups(chat_id)
            bot.edit_message_text(f"✅ **Mapping သိမ်းဆည်းပြီးပါပြီ!**\n\n`{website_name}` အဖြစ် သတ်မှတ်လိုက်ပါသည်။ ကျရှုံးခဲ့သော Pickup များကို ပြန်လည်တင်ပေးနေပါပြီ။", call.message.chat.id, call.message.message_id)
            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Set Mapping Callback Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_manual_'))
    def handle_manual_mapping_callback(call):
        """ ကိုယ်တိုင်ရိုက်ထည့်ရန် ရွေးချယ်သည့်အခါ """
        try:
            chat_id = int(call.data.split('_')[2])
            msg = bot.send_message(call.message.chat.id, "📝 Website မှာရှိတဲ့ **ဆိုင်နာမည် အတိအကျ** ကို ရိုက်ထည့်ပေးပါခင်ဗျာ။", reply_markup=telebot.types.ForceReply())
            bot.register_next_step_handler(msg, save_manual_mapping, bot, chat_id)
            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Manual Mapping Callback Error: {e}")
            bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_conf_'))
    def handle_pickup_confirm_callback(call):
        """ Rider မှ အချက်အလက်မှန်ကန်ကြောင်း အတည်ပြုသည့်အခါ """
        try:
            queue_id = int(call.data.split('_')[2])
            db_manager.confirm_pickup_order(queue_id)
            bot.edit_message_text("✅ **အတည်ပြုပြီးပါပြီ!**\n\nစက်ရုပ်မှ အော်ဒါတင်ပေးနေပါပြီ၊ ခဏစောင့်ပေးပါခင်ဗျာ။", call.message.chat.id, call.message.message_id)
            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Pickup Confirm Callback Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_edit_'))
    def handle_pickup_edit_callback(call):
        """ Rider မှ ပြန်ပြင်ချင်သည့်အခါ (ဘယ်အချက်အလက်ကို ပြင်မလဲ ရွေးခိုင်းမည်) """
        try:
            queue_id = int(call.data.split('_')[2])
            pickup = db_manager.get_pickup_order(queue_id)
            
            show_date_edit = True
            if pickup and len(pickup) > 8:
                created_at = pickup[8]
                if created_at:
                    tz = pytz.timezone('Asia/Yangon')
                    dt = datetime.fromtimestamp(created_at, tz)
                    if dt.hour >= 11:
                        show_date_edit = False

            markup = telebot.types.InlineKeyboardMarkup()
            row1 = []
            if show_date_edit:
                row1.append(telebot.types.InlineKeyboardButton("📅 ရက်စွဲပြင်မည်", callback_data=f"ap_ed_date_{queue_id}"))
            row1.append(telebot.types.InlineKeyboardButton("🚲/🚗 ယာဉ်အမျိုးအစား", callback_data=f"ap_ed_v_{queue_id}"))
            markup.row(*row1)
            markup.row(telebot.types.InlineKeyboardButton("📝 မှတ်ချက်ပြင်မည်", callback_data=f"ap_ed_rem_{queue_id}"))
            markup.row(telebot.types.InlineKeyboardButton("🔙 နောက်သို့", callback_data=f"ap_ed_back_{queue_id}"))

            bot.edit_message_text(
                "🛠 **ဘယ်အချက်အလက်ကို ပြင်ချင်ပါသလဲ?**\n\nပြင်ဆင်လိုသည့် ခလုတ်ကို နှိပ်ပေးပါခင်ဗျာ။",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )
            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Pickup Edit Callback Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_ed_date_'))
    def handle_edit_date_callback(call):
        """ ရက်စွဲပြင်ရန် ရွေးချယ်မှုပြခြင်း """
        try:
            queue_id = int(call.data.split('_')[3])
            markup = telebot.types.InlineKeyboardMarkup()
            markup.row(
                telebot.types.InlineKeyboardButton("📅 Today (ယနေ့)", callback_data=f"ap_upd_date_{queue_id}_today"),
                telebot.types.InlineKeyboardButton("📅 Tomorrow (မနက်ဖြန်)", callback_data=f"ap_upd_date_{queue_id}_tomorrow")
            )
            markup.row(telebot.types.InlineKeyboardButton("🔙 Back", callback_data=f"ap_edit_{queue_id}"))
            bot.edit_message_text("📅 **ဘယ်ရက်အတွက် ပြောင်းချင်ပါသလဲ?**", call.message.chat.id, call.message.message_id, reply_markup=markup)
            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Edit Date Callback Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_ed_v_'))
    def handle_edit_vehicle_callback(call):
        """ ယာဉ်အမျိုးအစားပြင်ရန် ရွေးချယ်မှုပြခြင်း """
        try:
            queue_id = int(call.data.split('_')[3])
            markup = telebot.types.InlineKeyboardMarkup()
            markup.row(
                telebot.types.InlineKeyboardButton("🚲 Bicycle", callback_data=f"ap_upd_v_{queue_id}_Bicycle"),
                telebot.types.InlineKeyboardButton("🚗 Car", callback_data=f"ap_upd_v_{queue_id}_Car")
            )
            markup.row(telebot.types.InlineKeyboardButton("🔙 Back", callback_data=f"ap_edit_{queue_id}"))
            bot.edit_message_text("🚲/🚗 **ယာဉ်အမျိုးအစား ရွေးပေးပါခင်ဗျာ။**", call.message.chat.id, call.message.message_id, reply_markup=markup)
            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Edit Vehicle Callback Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_ed_rem_'))
    def handle_edit_remark_callback(call):
        """ မှတ်ချက်ပြင်ရန် ForceReply ပြခြင်း """
        try:
            from modules import auto_pickup
            queue_id = int(call.data.split('_')[3])
            order = db_manager.get_pickup_order(queue_id)
            orig_msg_id = order[2] if order else 0
            
            msg = bot.send_message(call.message.chat.id, "📝 ပြင်ဆင်လိုသည့် **မှတ်ချက်** ကို ရိုက်ထည့်ပေးပါခင်ဗျာ။", reply_markup=telebot.types.ForceReply())
            if orig_msg_id:
                db_manager.add_pickup_intermediate_msg(call.message.chat.id, orig_msg_id, msg.message_id)
                
            bot.register_next_step_handler(msg, update_pickup_remark, bot, queue_id)
            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Edit Remark Callback Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_upd_'))
    def handle_update_field_callback(call):
        """ Field များကို Update လုပ်ပြီး Confirmation ပြန်ပြခြင်း """
        try:
            parts = call.data.split('_')
            field_type = parts[2] # date or v
            queue_id = int(parts[3])
            value = parts[4]
            
            if field_type == "date":
                tz = pytz.timezone('Asia/Yangon')
                now = datetime.now(tz)
                value = (now if value == "today" else now + timedelta(days=1)).strftime("%d-%m-%Y")
                db_manager.update_pickup_field(queue_id, 'target_date', value)
            elif field_type == "v":
                db_manager.update_pickup_field(queue_id, 'vehicle', value)
                
            bot.answer_callback_query(call.id, "✅ ပြင်ဆင်ပြီးပါပြီ")
            show_pickup_reconfirmation(bot, call.message.chat.id, queue_id, call.message.message_id)
        except Exception as e:
            log.error(f"❌ Update Field Callback Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_ed_back_'))
    def handle_back_to_conf_callback(call):
        """ ပြင်ဆင်မှုမလုပ်ဘဲ မူလ Confirmation သို့ ပြန်သွားခြင်း """
        try:
            queue_id = int(call.data.split('_')[3])
            show_pickup_reconfirmation(bot, call.message.chat.id, queue_id, call.message.message_id)
            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Back to Conf Callback Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_admin_'))
    def handle_pickup_admin_callback(call):
        """ Rider မှ Admin နှင့်ပြောရန် ရွေးချယ်သည့်အခါ """
        try:
            from modules import auto_pickup
            parts = call.data.split('_')
            queue_id = int(parts[2])
            orig_msg_id = int(parts[3])
            chat_id = call.message.chat.id
            
            db_manager.delete_pickup_order(queue_id)
            db_manager.set_manual_alert(orig_msg_id, chat_id)
            
            with db_manager.connection_scope() as conn:
                msg_data = conn.execute("SELECT text, timestamp, media_id FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
            
            if msg_data:
                text, ts, media_id = msg_data
                _, _, shop_name = db_manager.get_topic_context(chat_id, 1)
                auditor.send_new_alert(
                    chat_id, 1, orig_msg_id, text, "Rider requested Admin support", shop_name, ts,
                    media_id=media_id, title="🚨 **Urgent Alert (Rider Request)**"
                )
    
                auto_pickup.cleanup_pickup_intermediate_msgs(bot, chat_id, orig_msg_id)
                bot.send_message(chat_id, "👨‍💼 **Admin ထံ အကြောင်းကြားလိုက်ပါပြီ။**\n\nManager များမှ စစ်ဆေးပြီး အကြောင်းပြန်ပေးပါလိမ့်မည်။", reply_to_message_id=orig_msg_id)
            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Pickup Admin Callback Error: {e}")

# --- Helper Functions ---

def save_manual_remark(message, bot, orig_msg_id, date_type, vehicle):
    """ User ရိုက်လိုက်သော မှတ်ချက်ကို သိမ်းဆည်းပြီး Queue ထဲထည့်ခြင်း """
    try:
        remark = message.text.strip()
        chat_id = message.chat.id
        if not remark:
            bot.reply_to(message, "⚠️ မှတ်ချက် အလွတ်ဖြစ်နေလို့ မူရင်းစာသားအတိုင်းပဲ တင်ပေးလိုက်ပါမယ်။")
            remark = None
        finalize_pickup_queue(bot, chat_id, orig_msg_id, date_type, vehicle, remark)
    except Exception as e:
        log.error(f"❌ Save Manual Remark Error: {e}")

def finalize_pickup_queue(bot, chat_id, orig_msg_id, date_type, vehicle, manual_remark):
    """ အချက်အလက်အားလုံး စုံပြီဖြစ်၍ အတည်ပြုချက်တောင်းခံခြင်း """
    try:
        tz = pytz.timezone('Asia/Yangon')
        now = datetime.now(tz)
        target_date = (now if date_type == "today" else now + timedelta(days=1)).strftime("%d-%m-%Y")
        
        with db_manager.connection_scope() as conn:
            msg_data = conn.execute("SELECT text FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
        
        orig_text = msg_data[0] if msg_data else "Auto Pickup Request"
        final_remark = manual_remark if manual_remark else orig_text
        _, _, shop_name = db_manager.get_topic_context(chat_id, 1)
        v_str = vehicle if vehicle != "none" else "Bicycle"

        queue_id = db_manager.add_to_pickup_queue(chat_id, orig_msg_id, target_date, shop_name, final_remark, v_str, status='WAITING_CONFIRM')
        
        confirm_text = (
            f"⏳ **Auto Pickup အချက်အလက်များ**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📅 ရက်စွဲ: {target_date}\n"
            f"🏪 ဆိုင်: {shop_name}\n"
            f"🚲 ယာဉ်: {v_str}\n"
            f"📝 မှတ်ချက်: {final_remark}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"အထက်ပါအချက်များကို အတည်ပြုပေးပါဦးနော်။"
        )

        markup = telebot.types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            telebot.types.InlineKeyboardButton("✅ Right (အတည်ပြုသည်)", callback_data=f"ap_conf_{queue_id}"),
            telebot.types.InlineKeyboardButton("✏️ ပြင်ဦးမည်", callback_data=f"ap_edit_{queue_id}"),
            telebot.types.InlineKeyboardButton("👨‍💼 Admin နှင့်ပြောမည်", callback_data=f"ap_admin_{queue_id}_{orig_msg_id}")
        )
        
        sent_msg = bot.send_message(chat_id, confirm_text, reply_to_message_id=orig_msg_id, reply_markup=markup)
        db_manager.add_pickup_intermediate_msg(chat_id, orig_msg_id, sent_msg.message_id)
    except Exception as e:
        log.error(f"❌ Finalize Pickup Queue Error: {e}")

def update_pickup_remark(message, bot, queue_id):
    """ မှတ်ချက်ကို Update လုပ်ခြင်း """
    try:
        from modules import auto_pickup
        remark = message.text.strip()
        if remark:
            db_manager.update_pickup_field(queue_id, 'remark', remark)
            sent_msg = bot.reply_to(message, "✅ မှတ်ချက်ကို ပြင်ဆင်ပြီးပါပြီ။")
            order = db_manager.get_pickup_order(queue_id)
            if order:
                db_manager.add_pickup_intermediate_msg(message.chat.id, order[2], sent_msg.message_id)
        show_pickup_reconfirmation(bot, message.chat.id, queue_id)
    except Exception as e:
        log.error(f"❌ Update Pickup Remark Error: {e}")

def show_pickup_reconfirmation(bot, chat_id, queue_id, message_id=None):
    """ ပြင်ဆင်ပြီးနောက် အချက်အလက်များကို ပြန်လည်ပြသခြင်း """
    try:
        order = db_manager.get_pickup_order(queue_id)
        if not order:
            bot.send_message(chat_id, "❌ အချက်အလက် ရှာမတွေ့တော့ပါ။")
            return

        _, _, orig_msg_id, target_date, shop_name, remark, vehicle, status = order
        confirm_text = (
            f"⏳ **Auto Pickup အချက်အလက်များ (ပြင်ဆင်ပြီး)**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📅 ရက်စွဲ: {target_date}\n"
            f"🏪 ဆိုင်: {shop_name}\n"
            f"🚲 ယာဉ်: {vehicle}\n"
            f"📝 မှတ်ချက်: {remark}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"အချက်အလက်များ မှန်ကန်ပါက အတည်ပြုပေးပါဦးနော်။"
        )
        markup = telebot.types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            telebot.types.InlineKeyboardButton("✅ Right (အတည်ပြုသည်)", callback_data=f"ap_conf_{queue_id}"),
            telebot.types.InlineKeyboardButton("✏️ ပြင်ဦးမည်", callback_data=f"ap_edit_{queue_id}"),
            telebot.types.InlineKeyboardButton("👨‍💼 Admin နှင့်ပြောမည်", callback_data=f"ap_admin_{queue_id}_{orig_msg_id}")
        )
        if message_id:
            bot.edit_message_text(confirm_text, chat_id, message_id, reply_markup=markup)
        else:
            bot.send_message(chat_id, confirm_text, reply_markup=markup)
    except Exception as e:
        log.error(f"❌ Show Pickup Reconfirmation Error: {e}")

def save_manual_mapping(message, bot, chat_id):
    """ Manager ရိုက်ထည့်လိုက်သော နာမည်ကို Mapping အဖြစ် သိမ်းဆည်းခြင်း """
    try:
        website_name = message.text.strip()
        if not website_name:
            bot.reply_to(message, "❌ နာမည် အလွတ်ဖြစ်နေလို့ မသိမ်းပေးနိုင်ပါဘူး။")
            return
        db_manager.set_shop_mapping(chat_id, website_name)
        db_manager.retry_failed_pickups(chat_id)
        bot.reply_to(message, f"✅ **Mapping သိမ်းဆည်းပြီးပါပြီ!**\n\nနောက်ပိုင်း ဒီ Group ကတက်လာတဲ့ Pick up တွေကို Website ထဲက `{website_name}` နာမည်နဲ့ တင်ပေးသွားပါမယ်။ ကျရှုံးခဲ့သော Pickup များကိုလည်း ပြန်လည်တင်ပေးနေပါပြီ။")
        log.info(f"🎯 Manager manually mapped {chat_id} to {website_name}")
    except Exception as e:
        log.error(f"❌ Save Manual Mapping Error: {e}")
        bot.reply_to(message, "❌ သိမ်းဆည်းစဉ် အမှားတစ်ခု ဖြစ်သွားပါသည်။")
