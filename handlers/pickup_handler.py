import telebot
from telebot import util
import pytz
import os
from datetime import datetime, timedelta
from logger import log
import db_manager
from modules import auditor

def register_pickup_handlers(bot: telebot.TeleBot):
    """ Auto Pickup Module အတွက် Callback များကို Register လုပ်ပေးသည် """

    @bot.callback_query_handler(func=lambda call: call.data.startswith('pdone_'))
    def handle_pickup_done_callback(call):
        """ Admin မှ Pickup Notification ရှိ Done Button ကို နှိပ်လိုက်သည့်အခါ """
        try:
            from modules import auto_pickup
            # format: pdone_{orig_msg_id}_{chat_id}
            parts = call.data.split('_')
            if len(parts) == 3:
                orig_msg_id = int(parts[1])
                chat_id = int(parts[2])
                
                # 1. Success Group သို့ Report ပို့ခြင်း
                auto_pickup.send_success_report(bot, orig_msg_id, chat_id, handled_by=f"ဝန်ထမ်း ({call.from_user.first_name})")

                # 2. ဆိုင် Group ကို Success ပြောင်းရန်
                auto_pickup.update_central_pickup_alert(bot, orig_msg_id, chat_id, "✅ Success (ဝန်ထမ်းမှ အတည်ပြုပြီး)", show_done=False)
                
                db_manager.resolve_message(orig_msg_id, chat_id, call.from_user.first_name, method='Manual', status='DONE')
            
            # 3. Admin Group ထဲက Alert စာကို ဖျက်ခြင်း
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.answer_callback_query(call.id, "✅ Pickup request marked as success.")
        except Exception as e:
            log.error(f"❌ Pickup Done Callback Error: {e}")
            try: bot.answer_callback_query(call.id, "❌ Error occurred")
            except Exception: pass

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
            # 💡 connection_scope() သည် auto-commit လုပ်ပေးသော်လည်း SELECT သာဖြစ်ပါက commit မလိုအပ်ပါ
            with db_manager.connection_scope() as conn:
                msg_data = conn.execute("SELECT text FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
            
            if not msg_data:
                log.warning(f"⚠️ Message {orig_msg_id} not found in logs for chat {chat_id}")
            
            if action == "dt" or not vehicle:
                if not vehicle:
                    auto_pickup.ask_vehicle(bot, call.message, date_type, orig_msg_id, show_cancel=False)
                    return
            
            auto_pickup.ask_remark(bot, chat_id, date_type, vehicle, orig_msg_id, show_cancel=False)
            bot.delete_message(chat_id, call.message.message_id)

        except Exception as e:
            log.error(f"❌ Auto Pickup Callback Error: {e}")
            try: bot.answer_callback_query(call.id, "❌ Error occurred", show_alert=True)
            except Exception: pass

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
            vehicle = parts[5] if parts[5] != "none" else "none"

            if date_type == "today":
                # 💡 Today ဆိုရင် OK/Admin မေးစရာမလိုဘဲ Interactive Setup တန်းပြမည်
                import pytz as _pytz
                from datetime import datetime as _dt, timedelta as _td
                _tz = _pytz.timezone('Asia/Yangon')
                msg_ctx = db_manager.get_message_context(orig_msg_id, chat_id)
                msg_ts = msg_ctx[4] if msg_ctx else _dt.now(_tz).timestamp()
                msg_dt = _dt.fromtimestamp(msg_ts, _tz)
                target_date_str = msg_dt.strftime("%d-%m-%Y")

                # 💡 အပေါ်က "၁၁ နာရီကျော်ပြီ" ဆိုတဲ့ စာကို ဖျက်ခြင်း
                old_msgs = db_manager.get_pickup_intermediate_msgs(chat_id, orig_msg_id)
                for m_id in old_msgs:
                    try: bot.delete_message(chat_id, m_id)
                    except Exception: pass
                db_manager.delete_pickup_intermediate_msgs(chat_id, orig_msg_id)

                with db_manager.connection_scope() as conn:
                    msg_data = conn.execute("SELECT text, summary FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()

                shop_name = auto_pickup.get_best_shop_name(bot, chat_id)
                if msg_data:
                    orig_text, clean_remark = msg_data
                    # Admin alert မရှိသေးရင် ပို့မည်
                    if not db_manager.get_alert_tracking(orig_msg_id, chat_id):
                        auto_pickup.send_admin_pickup_alert(bot, chat_id, orig_msg_id, shop_name, target_date_str, None, clean_remark, orig_text)

                db_manager.upsert_pickup_queue(chat_id, orig_msg_id, target_date_str, shop_name, None, None, status='WAITING_SETUP')
                auto_pickup.show_interactive_setup(bot, chat_id, orig_msg_id, date_type)

                status_admin = "📅 Today (Setting Up)"
                log_msg = "✅ **Today** — Interactive Setup တန်းပြလိုက်ပါပြီ အစ်ကို။"
                bot.edit_message_text(log_msg, call.message.chat.id, call.message.message_id)
                auto_pickup.update_central_pickup_alert(bot, orig_msg_id, chat_id, status_admin)
            else:
                # Tomorrow: Show actual date + OK/Admin buttons (OS အတည်ပြုချက် လိုအပ်)
                import pytz as _pytz
                from datetime import datetime as _dt, timedelta as _td
                _tz = _pytz.timezone('Asia/Yangon')
                msg_ctx = db_manager.get_message_context(orig_msg_id, chat_id)
                msg_ts = msg_ctx[4] if msg_ctx else _dt.now(_tz).timestamp()
                msg_dt = _dt.fromtimestamp(msg_ts, _tz)
                tomorrow_dt = msg_dt + _td(days=1)
                tomorrow_str = tomorrow_dt.strftime("%d-%m-%Y")
                text = f"ဒီနေ့ Pick up လေးကျော်သွားပြီမိုလို မနက်ဖြန် {tomorrow_str} နဲ့ pickup လေးတင်ပေးရမလားရှင့်"
                status_admin = f"📅 Tomorrow ({tomorrow_str}) (Waiting OS)"
                log_msg = f"✅ **Tomorrow ({tomorrow_str})** အတွက် OS ထံ အတည်ပြုချက် တောင်းခံထားပါသည် အစ်ကို။"
                markup = telebot.types.InlineKeyboardMarkup(row_width=1)
                markup.add(
                    telebot.types.InlineKeyboardButton("OK", callback_data=f"ap_pconf_{orig_msg_id}_{date_type}"),
                    telebot.types.InlineKeyboardButton("💬 Admin နှင့်ပြောမည်", callback_data=f"ap_admin_0_{orig_msg_id}")
                )

                # 💡 အပေါ်က "၁၁ နာရီကျော်ပြီ" ဆိုတဲ့ စာကို ဖျက်ခြင်း
                old_msgs = db_manager.get_pickup_intermediate_msgs(chat_id, orig_msg_id)
                for m_id in old_msgs:
                    try: bot.delete_message(chat_id, m_id)
                    except Exception: pass
                db_manager.delete_pickup_intermediate_msgs(chat_id, orig_msg_id)

                sent_msg = bot.send_message(chat_id, text, reply_to_message_id=orig_msg_id, reply_markup=markup)
                db_manager.add_pickup_intermediate_msg(chat_id, orig_msg_id, sent_msg.message_id)

                bot.edit_message_text(log_msg, call.message.chat.id, call.message.message_id)
                auto_pickup.update_central_pickup_alert(bot, orig_msg_id, chat_id, status_admin)

        except Exception as e:
            log.error(f"❌ Staff Pickup Decision Error: {e}")
            try: bot.answer_callback_query(call.id, "❌ Error occurred")
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_pconf_'))
    def handle_pickup_confirm_initial_callback(call):
        """ (၁၁)နာရီ အရှေ့ရော (၃)နာရီအနောက်ရော OK နှိပ်လိုက်သည့်အခါ """
        try:
            from modules import auto_pickup
            import pytz
            from datetime import datetime, timedelta
            # format: ap_pconf_{orig_msg_id}_{date_type}
            parts = call.data.split('_')
            orig_msg_id = int(parts[2])
            date_type = parts[3]
            chat_id = call.message.chat.id
            
            # 1. Admin Group (Topic 878) သို့ Alert ပို့ခြင်း
            tz = pytz.timezone('Asia/Yangon')
            
            # 💡 Midnight Bug Fix: Use original message timestamp instead of current time
            msg_ctx = db_manager.get_message_context(orig_msg_id, chat_id)
            msg_ts = msg_ctx[4] if msg_ctx else datetime.now(tz).timestamp()
            msg_dt = datetime.fromtimestamp(msg_ts, tz)
            
            target_date_str = (msg_dt if date_type == "today" else msg_dt + timedelta(days=1)).strftime("%d-%m-%Y")
            
            # Get context for alert
            with db_manager.connection_scope() as conn:
                msg_data = conn.execute("SELECT text, summary FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
            
            shop_name = auto_pickup.get_best_shop_name(bot, chat_id)
            if msg_data:
                orig_text, clean_remark = msg_data
                
                # 🛡️ Duplicate Alert Check: Mid-day flow မှာ Alert ပို့ထားပြီးသားဆိုရင် ထပ်မပို့တော့ပါ
                if not db_manager.get_alert_tracking(orig_msg_id, chat_id):
                    # Send Alert to Admin (Topic 878)
                    auto_pickup.send_admin_pickup_alert(bot, chat_id, orig_msg_id, shop_name, target_date_str, None, clean_remark, orig_text)
                else:
                    log.info(f"ℹ️ Admin Alert already exists for msg {orig_msg_id}. Skipping duplicate.")
            
            # 💡 Phase 1: Set status to WAITING_SETUP to prevent duplicates
            db_manager.upsert_pickup_queue(chat_id, orig_msg_id, target_date_str, shop_name, None, None, status='WAITING_SETUP')

            # 2. Show interactive setup
            auto_pickup.show_interactive_setup(bot, chat_id, orig_msg_id, date_type, edit_msg_id=call.message.message_id)
            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Pickup Confirm Initial Callback Error: {e}")
            try: bot.answer_callback_query(call.id, "❌ Error occurred")
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_tconf_'))
    def handle_tomorrow_confirm_callback(call):
        """ Legacy handler for ap_tconf_ (if any old messages exist) """
        try:
            from modules import auto_pickup
            orig_msg_id = int(call.data.split('_')[2])
            auto_pickup.show_interactive_setup(bot, call.message.chat.id, orig_msg_id, "tomorrow", edit_msg_id=call.message.message_id)
            bot.answer_callback_query(call.id)
        except Exception: pass
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_ivh_'))
    def handle_interactive_vehicle_callback(call):
        """ Interactive Setup: Vehicle Selection """
        try:
            from modules import auto_pickup
            # format: ap_ivh_{orig_msg_id}_{date_type}_{vehicle}
            parts = call.data.split('_')
            orig_msg_id = int(parts[2])
            date_type = parts[3]
            vehicle = parts[4]
            chat_id = call.message.chat.id

            # Update DB state
            tz = pytz.timezone('Asia/Yangon')
            
            # 💡 Midnight Bug Fix: Use original message timestamp instead of current time
            msg_ctx = db_manager.get_message_context(orig_msg_id, chat_id)
            msg_ts = msg_ctx[4] if msg_ctx else datetime.now(tz).timestamp()
            msg_dt = datetime.fromtimestamp(msg_ts, tz)
            
            target_date = (msg_dt if date_type == "today" else msg_dt + timedelta(days=1)).strftime("%d-%m-%Y")
            shop_name = auto_pickup.get_best_shop_name(bot, chat_id)
            
            db_manager.upsert_pickup_queue(chat_id, orig_msg_id, target_date, shop_name, None, vehicle, status='WAITING_SETUP')
            
            # Refresh Interactive Message
            auto_pickup.show_interactive_setup(bot, chat_id, orig_msg_id, date_type, vehicle=vehicle, edit_msg_id=call.message.message_id)
            bot.answer_callback_query(call.id, f"✅ {vehicle} ကို ရွေးချယ်လိုက်ပါသည်")
        except Exception as e:
            log.error(f"❌ Interactive Vehicle Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_irm_'))
    def handle_interactive_remark_callback(call):
        """ Interactive Setup: Remark Request """
        try:
            # format: ap_irm_{orig_msg_id}_{date_type}_write
            parts = call.data.split('_')
            orig_msg_id = int(parts[2])
            date_type = parts[3]
            
            msg = bot.send_message(call.message.chat.id, "📝 ထည့်ချင်တဲ့ **မှတ်ချက်** ကို ရိုက်ထည့်ပေးပါခင်ဗျာ။", reply_markup=telebot.types.ForceReply())
            db_manager.add_pickup_intermediate_msg(call.message.chat.id, orig_msg_id, msg.message_id)
            bot.register_next_step_handler(msg, save_manual_remark_interactive, bot, orig_msg_id, date_type, call.message.message_id, msg.message_id)
            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Interactive Remark Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_isb_'))
    def handle_interactive_submit_callback(call):
        """ Interactive Setup: Final Submit """
        try:
            from modules import auto_pickup
            # format: ap_isb_{orig_msg_id}_{date_type}
            parts = call.data.split('_')
            orig_msg_id = int(parts[2])
            date_type = parts[3]
            chat_id = call.message.chat.id

            order = db_manager.get_pickup_order_by_msg(orig_msg_id, chat_id)
            vehicle = order[6] if order else None
            remark = order[5] if order else None

            if not vehicle or vehicle == "none":
                bot.answer_callback_query(call.id, "⚠️ စက်ဘီး/ကား ကိုတော့မဖြစ်မနေရွေးပေးပါနော်", show_alert=True)
                return

            # Finalize and Submit directly to Robot Queue (No more confirm step)
            tz = pytz.timezone('Asia/Yangon')
            msg_ctx = db_manager.get_message_context(orig_msg_id, chat_id)
            msg_ts = msg_ctx[4] if msg_ctx else datetime.now(tz).timestamp()
            msg_dt = datetime.fromtimestamp(msg_ts, tz)
            target_date = (msg_dt if date_type == "today" else msg_dt + timedelta(days=1)).strftime("%d-%m-%Y")
            
            with db_manager.connection_scope() as conn:
                msg_data = conn.execute("SELECT summary FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
            ai_summary = msg_data[0] if msg_data else None
            final_remark = remark if remark else (ai_summary if ai_summary else "-")
            
            shop_name = auto_pickup.get_best_shop_name(bot, chat_id)
            
            # Create queue item with PENDING status (Robot will pick it up)
            queue_id = db_manager.upsert_pickup_queue(chat_id, orig_msg_id, target_date, shop_name, final_remark, vehicle, status='PENDING')
            
            # Save shop_msg_id so worker can update this message later
            db_manager.update_pickup_field(queue_id, 'shop_msg_id', call.message.message_id)

            # Update UI to Success/Processing state (Remove buttons by not passing reply_markup)
            status_text = (
                f"⏳ **Auto Pickup အချက်အလက်များ**\n"
                f"📅 ရက်စွဲ: {target_date}\n"
                f"🏪 ဆိုင်: <b>{util.escape(shop_name)}</b>\n"
                f"🚲 ယာဉ်: <b>{vehicle}</b>\n"
                f"📝 မှတ်ချက်: {final_remark}\n"
                f"📊 Status: <b>⏳ Pending</b>"
            )
            bot.edit_message_text(status_text, chat_id, call.message.message_id, parse_mode="HTML")
            
            # Update central alert
            auto_pickup.update_central_pickup_alert(bot, orig_msg_id, chat_id, "⏳ Pending", queue_id=queue_id)
            
            bot.answer_callback_query(call.id, "✅ Pickup တင်ရန် အတည်ပြုလိုက်ပါပြီ")
        except Exception as e:
            log.error(f"❌ Interactive Submit Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_cs_'))
    def handle_customer_pickup_decision(call):
        """ Legacy handler for ap_cs_ (if any old messages exist) """
        try:
            from modules import auto_pickup
            # format: ap_cs_{orig_msg_id}_{chat_id}_{action}_{vehicle}
            parts = call.data.split('_')
            orig_msg_id = int(parts[2])
            chat_id = int(parts[3])
            action = parts[4]

            if action == "ok":
                auto_pickup.show_interactive_setup(bot, chat_id, orig_msg_id, "tomorrow", edit_msg_id=call.message.message_id)
            else:
                log.info(f"🚨 Customer requested Admin support: msg_id={orig_msg_id}, chat={chat_id}")
                db_manager.set_manual_alert(orig_msg_id, chat_id)
                with db_manager.connection_scope() as conn:
                    msg_data = conn.execute("SELECT text, timestamp, media_id FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
                
                if msg_data:
                    text, ts, media_id = msg_data
                    _, _, shop_name = db_manager.get_topic_context(chat_id, 1)
                    
                    log.info(f"📢 Sending Urgent Alert for {shop_name} (Force=True)")
                    auditor.send_new_alert(
                        chat_id, 1, orig_msg_id, text, "Customer requested Admin", shop_name, ts,
                        media_id=media_id, title="🚨 **Urgent Alert (Customer Request)**", force=True
                    )
                auto_pickup.cleanup_pickup_intermediate_msgs(bot, chat_id, orig_msg_id)
                bot.send_message(chat_id, "👨‍💻 Admin ကို အကြောင်းကြားထားပေးပါတယ်ရှင်။ ခဏစောင့်ပေးပါနော်။", reply_to_message_id=orig_msg_id)

        except Exception as e:
            log.error(f"❌ Customer Pickup Decision Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_cancel_'))
    def handle_pickup_cancel_callback(call):
        """ AI မှ Pickup ဟု မှားယွင်းယူဆမိပါက Rider မှ ပယ်ဖျက်ခြင်း """
        import time as _time
        try:
            from modules import auto_pickup
            orig_msg_id = int(call.data.split('_')[2])
            chat_id = call.message.chat.id
            
            # 🔒 Step 0: Delete bot messages FIRST before any DB ops that might throw
            # This ensures the pickup inquiry disappears from the group even if later steps fail.
            try:
                bot.delete_message(chat_id, call.message.message_id)
                log.info(f"🗑️ Deleted OS Group bot message {call.message.message_id} in chat {chat_id}")
            except Exception as del_e:
                log.warning(f"⚠️ Failed to delete OS Group bot message {call.message.message_id}: {del_e}")

            # Clean up all intermediate bot messages
            auto_pickup.cleanup_pickup_intermediate_msgs(bot, chat_id, orig_msg_id)

            db_manager.cancel_pickup_queue_for_message(orig_msg_id, chat_id)

            # 💡 Phase 2: AI Learning from Cancellations
            msg_ctx = db_manager.get_message_context(orig_msg_id, chat_id)
            orig_text = msg_ctx[0] if msg_ctx else "[Unknown]"
            if msg_ctx:
                db_manager.log_feedback(chat_id, orig_msg_id, orig_text, category='NOT_PICKUP')

            topic_id = db_manager.get_message_topic(orig_msg_id, chat_id)
            _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)

            if db_manager.message_was_staff_handled(orig_msg_id, chat_id):
                staff_name = call.from_user.first_name
                with db_manager.connection_scope() as conn:
                    row = conn.execute(
                        "SELECT resolved_by FROM message_logs WHERE msg_id = ? AND chat_id = ?",
                        (orig_msg_id, chat_id)
                    ).fetchone()
                if row and row[0]:
                    staff_name = str(row[0]).split(' (')[0].strip() or staff_name

                auditor.resolve_and_cleanup(
                    orig_msg_id, chat_id, shop_name, orig_text,
                    f"{staff_name} (Not Pickup)", manual_resolve=True
                )
                db_manager.resolve_message(
                    orig_msg_id, chat_id, staff_name,
                    method='Not Pickup', topic_id=topic_id
                )
                log.info(f"✅ Pickup cancelled with staff-handled resolve for msg {orig_msg_id} in chat {chat_id}")
                bot.answer_callback_query(call.id, "✅ Staff မှ ဖြေရှင်းပြီးသား — Resolved အဖြစ် မှတ်သားပြီးပါပြီ။")
                return

            # Admin Group ရှိ Pickup Notification ကို ဖျက်ခြင်း (staff မဖြေရှင်းရသေးပါက)
            tracking = db_manager.get_alert_tracking(orig_msg_id, chat_id)
            if tracking:
                alert_msg_id = tracking[0]
                central_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))
                try:
                    bot.delete_message(central_chat, alert_msg_id)
                except Exception: pass
                db_manager.delete_alert_tracking(orig_msg_id, chat_id)

            # Status ကို PENDING သို့ ပြန်ပြောင်းခြင်း — 15-min alert clock အသစ်ပြန်စ
            db_manager.update_message_status(orig_msg_id, chat_id, 'PENDING')
            now_ts = int(_time.time())
            db_manager.reset_message_timestamp(orig_msg_id, chat_id, now_ts)
            log.info(f"🔄 Pickup cancelled: msg {orig_msg_id} in chat {chat_id} reset to PENDING with fresh timestamp {now_ts}")

            bot.answer_callback_query(call.id, "✅ Pickup မဟုတ်ကြောင်း မှတ်သားပြီး ပုံမှန်စာအဖြစ် ပြန်ပြောင်းလိုက်ပါပြီ။")
        except Exception as e:
            log.error(f"❌ Pickup Cancel Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

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
                bot.edit_message_text("🙏 ဟုတ်ကဲ့ပါခင်ဗျာ။", chat_id, call.message.message_id)
                finalize_pickup_queue(bot, chat_id, orig_msg_id, date_type, vehicle, None)

        except Exception as e:
            log.error(f"❌ Remark Selection Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_fix_'))
    def handle_fix_mapping_callback(call):
        """ Shop Mapping ကို Manual ပြင်ဆင်ရန် """
        try:
            # ၁။ Permission Check (Admin Level 3 or 4 only)
            user_level = db_manager.get_user_level(call.from_user.id, call.message.chat.id)
            if user_level < 3:
                bot.answer_callback_query(call.id, "⚠️ ဤလုပ်ဆောင်ချက်ကို Admin များသာ အသုံးပြုနိုင်ပါသည်။", show_alert=True)
                return

            chat_id = int(call.data.split('_')[2])
            from modules import auto_pickup
            
            # ၂။ Extract OS Name from Title (Source of Truth)
            chat_title = call.message.chat.title or ""
            if '🤝' in chat_title:
                raw_shop_name = chat_title.split('🤝')[0].strip()
            else:
                with db_manager.connection_scope() as conn:
                    shop_data = conn.execute("SELECT shop_name FROM os_groups WHERE chat_id = ?", (chat_id,)).fetchone()
                raw_shop_name = db_manager.clean_shop_name(shop_data[0]) if shop_data else "Unknown"

            # ၃။ Website မှ ဆိုင်စာရင်းများကို Browser ဖြင့် Sync လုပ်ခြင်း
            bot.answer_callback_query(call.id, "⏳ Website မှ ဆိုင်စာရင်းများကို Sync လုပ်နေပါသည်...")
            success, sync_msg = auto_pickup.sync_shops_from_website()
            if not success:
                log.warning(f"⚠️ Shop Sync during Fix Mapping: {sync_msg}")

            # ၄။ Fetch Suggestions (Sync ပြီးသား Data ထဲမှ ရှာမည်)
            suggestions = db_manager.get_website_suggestions(raw_shop_name[:5])
            shop_name_esc = telebot.util.escape(raw_shop_name)

            markup = telebot.types.InlineKeyboardMarkup(row_width=1)
            for s in suggestions:
                s_esc = telebot.util.escape(s)
                # Generate a short temp ID for the shop name to avoid callback data length limit (64 bytes)
                import hashlib
                temp_id = hashlib.md5(s.encode()).hexdigest()[:8]
                db_manager.save_temp_data(f"shop_{temp_id}", s)
                markup.add(telebot.types.InlineKeyboardButton(f"✅ {s_esc}", callback_data=f"ap_set_{chat_id}_{temp_id}"))
            
            markup.add(telebot.types.InlineKeyboardButton("⌨️ Manual Type (ကိုယ်တိုင်ရိုက်မည်)", callback_data=f"ap_manual_{chat_id}"))

            # ၅။ Edit Message with HTML Escaping
            bot.edit_message_text(
                f"🔍 **Shop Mapping Fix**\n━━━━━━━━━━━━━━━━━━\n"
                f"🏪 Telegram: <b>{shop_name_esc}</b>\n\n"
                f"အောက်ပါ Website ဆိုင်နာမည်များထဲမှ မှန်ကန်တာကို ရွေးပေးပါ-",
                call.message.chat.id, call.message.message_id,
                reply_markup=markup, parse_mode="HTML"
            )
        except Exception as e:
            log.error(f"❌ Fix Mapping Callback Error: {e}", exc_info=True)
            try: bot.answer_callback_query(call.id, "❌ အမှားတစ်ခု ဖြစ်သွားပါသည်။", show_alert=True)
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_set_'))
    def handle_set_mapping_callback(call):
        """ Suggestion ထဲမှ တစ်ခုကို ရွေးချယ်လိုက်သည့်အခါ """
        try:
            parts = call.data.split('_')
            chat_id = int(parts[2])
            temp_id = parts[3]
            
            website_name = db_manager.get_temp_data(f"shop_{temp_id}")
            if not website_name:
                bot.answer_callback_query(call.id, "⚠️ Session expired. Please try 'Fix Shop Mapping' again.", show_alert=True)
                return

            db_manager.set_shop_mapping(chat_id, website_name)
            db_manager.retry_failed_pickups(chat_id)
            bot.edit_message_text(f"✅ **Mapping သိမ်းဆည်းပြီးပါပြီ!**\n\n`{website_name}` အဖြစ် သတ်မှတ်လိုက်ပါသည်။ ကျရှုံးခဲ့သော Pickup များကို ပြန်လည်တင်ပေးနေပါပြီ။", call.message.chat.id, call.message.message_id)
        except Exception as e:
            log.error(f"❌ Set Mapping Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_manual_'))
    def handle_manual_mapping_callback(call):
        """ ကိုယ်တိုင်ရိုက်ထည့်ရန် ရွေးချယ်သည့်အခါ """
        try:
            chat_id = int(call.data.split('_')[2])
            msg = bot.send_message(call.message.chat.id, "📝 Website မှာရှိတဲ့ **ဆိုင်နာမည် အတိအကျ** ကို ရိုက်ထည့်ပေးပါခင်ဗျာ။", reply_markup=telebot.types.ForceReply())
            bot.register_next_step_handler(msg, save_manual_mapping, bot, chat_id)
        except Exception as e:
            log.error(f"❌ Manual Mapping Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_mantype_'))
    def handle_manual_type_mapping_callback(call):
        """ Pickup Alert ထဲက Manual Type ခလုတ်ကို နှိပ်လိုက်သည့်အခါ (Mapping Missing အတွက်) """
        try:
            # format: ap_mantype_{orig_msg_id}_{chat_id}_{queue_id}
            parts = call.data.split('_')
            orig_msg_id = int(parts[2])
            chat_id = int(parts[3])
            queue_id = int(parts[4]) if len(parts) > 4 and parts[4] != "0" else None
            topic_id = call.message.message_thread_id  # 💡 Forum topic ထဲမှာ ရှိနေစေရန်
            
            sent_msg = bot.send_message(
                call.message.chat.id,
                "📝 Website မှာရှိတဲ့ **OS Name အတိအကျ** ကို ရိုက်ထည့်ပေးပါခင်ဗျာ။",
                reply_markup=telebot.types.ForceReply(),
                message_thread_id=topic_id
            )
            bot.register_next_step_handler(sent_msg, save_manual_type_mapping, bot, chat_id, orig_msg_id, queue_id, sent_msg.message_id, topic_id)
        except Exception as e:
            log.error(f"❌ Manual Type Mapping Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_conf_'))
    def handle_pickup_confirm_callback(call):
        """ Rider မှ အချက်အလက်မှန်ကန်ကြောင်း အတည်ပြုသည့်အခါ """
        try:
            queue_id = int(call.data.split('_')[2])
            order = db_manager.get_pickup_order(queue_id)
            if not order:
                bot.answer_callback_query(call.id, "❌ Order not found")
                return

            chat_id = call.message.chat.id
            orig_msg_id = order[2]
            target_date = order[3]
            shop_name = order[4]
            remark = order[5]
            vehicle = order[6]

            # Detailed Status Message for Shop Group
            status_text = (
                f"✅ **အတည်ပြုပြီးပါပြီ!**\n\n"
                f"🏪 ဆိုင်: <b>{util.escape(shop_name)}</b>\n"
                f"📅 ရက်စွဲ: <b>{target_date}</b>\n"
                f"🚲 ယာဉ်: <b>{vehicle}</b>\n"
                f"📝 မှတ်ချက်: {remark if remark else '-'}\n"
                f"📊 Status: <b>⏳ Pending</b>\n\n"
                f"Pick up လေးတင်ပေးထားပါတယ်နော်"
            )

            bot.edit_message_text(status_text, chat_id, call.message.message_id, parse_mode="HTML")
            
            # Update status and save shop_msg_id
            db_manager.confirm_pickup_order(queue_id, shop_msg_id=call.message.message_id)

            from modules import auto_pickup
            
            # 💡 အတည်ပြုပြီးသည်နှင့် ကြားဖြတ်စာများကို ချက်ချင်းရှင်းလင်းမည် (Status Message ကိုတော့ ချန်ထားမည်)
            try:
                msg_ids = db_manager.get_pickup_intermediate_msgs(chat_id, orig_msg_id)
                for mid in msg_ids:
                    if mid != call.message.message_id: # လက်ရှိ Status ပြနေတဲ့စာကို မဖျက်ပါ
                        try: bot.delete_message(chat_id, mid)
                        except Exception: pass
                # DB ထဲက စာရင်းကို ရှင်းမည်
                db_manager.delete_pickup_intermediate_msgs(chat_id, orig_msg_id)
            except Exception as ce:
                log.error(f"❌ Cleanup during confirm error: {ce}")

            auto_pickup.update_central_pickup_alert(bot, orig_msg_id, chat_id, "⏳ Pending", queue_id=queue_id)
                
        except Exception as e:
            log.error(f"❌ Pickup Confirm Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_edit_'))
    def handle_pickup_edit_callback(call):
        """ Rider မှ ပြန်ပြင်ချသည့်အခါ (ဘယ်အချက်အလက်ကို ပြင်မလဲ ရွေးခိုင်းမည်) """
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
        except Exception as e:
            log.error(f"❌ Pickup Edit Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

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
        except Exception as e:
            log.error(f"❌ Edit Date Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

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
        except Exception as e:
            log.error(f"❌ Edit Vehicle Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

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
        except Exception as e:
            log.error(f"❌ Edit Remark Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

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
                
                # 💡 Midnight Bug Fix: Use original message timestamp instead of current time
                order = db_manager.get_pickup_order(queue_id)
                orig_msg_id = order[2] if order else 0
                chat_id = order[1] if order else 0
                
                msg_ctx = db_manager.get_message_context(orig_msg_id, chat_id)
                msg_ts = msg_ctx[4] if msg_ctx else datetime.now(tz).timestamp()
                msg_dt = datetime.fromtimestamp(msg_ts, tz)
                
                value = (msg_dt if value == "today" else msg_dt + timedelta(days=1)).strftime("%d-%m-%Y")
                db_manager.update_pickup_field(queue_id, 'target_date', value)
            elif field_type == "v":
                db_manager.update_pickup_field(queue_id, 'vehicle', value)
                
            bot.answer_callback_query(call.id, "✅ ပြင်ဆင်ပြီးပါပြီ")
            show_pickup_reconfirmation(bot, call.message.chat.id, queue_id, call.message.message_id)
        except Exception as e:
            log.error(f"❌ Update Field Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_ed_back_'))
    def handle_back_to_conf_callback(call):
        """ ပြင်ဆင်မှုမလုပ်ဘဲ မူလ Confirmation သို့ ပြန်သွားခြင်း """
        try:
            # format: ap_ed_back_{queue_id}
            parts = call.data.split('_')
            if len(parts) >= 4:
                queue_id = int(parts[3])
                show_pickup_reconfirmation(bot, call.message.chat.id, queue_id, call.message.message_id)
        except Exception as e:
            log.error(f"❌ Back to Conf Callback Error: {e}")
            bot.answer_callback_query(call.id, "❌ အမှားတစ်ခု ဖြစ်သွားပါသည်။")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_admin_'))
    def handle_pickup_admin_callback(call):
        """ Rider မှ Admin နှင့်ပြောရန် ရွေးချယ်သည့်အခါ """
        try:
            from modules import auto_pickup
            # format: ap_admin_{queue_id}_{orig_msg_id}
            parts = call.data.split('_')
            queue_id = int(parts[2])
            orig_msg_id = int(parts[3])
            chat_id = call.message.chat.id
            
            log.info(f"🚨 Admin Support Request: queue_id={queue_id}, msg_id={orig_msg_id}, chat={chat_id}")
            
            # 🛡️ Full Reset: Pickup နဲ့ ပတ်သက်သမျှ အကုန်ရှင်းမည် (Admin နှင့်ပြောမည် ဖြစ်သောကြောင့်)
            with db_manager.get_connection() as conn:
                # ၁။ Pickup Queue ထဲက ဒီ message နဲ့ ပတ်သက်တာတွေ အကုန်ဖျက်မည်
                conn.execute("DELETE FROM pickup_queue WHERE chat_id = ? AND orig_msg_id = ?", (chat_id, orig_msg_id))
                # ၂။ WAITING_SETUP ဖြစ်နေတာတွေပါ ထပ်ရှင်းမည် (Safety)
                conn.execute("DELETE FROM pickup_queue WHERE chat_id = ? AND status = 'WAITING_SETUP'", (chat_id,))
                
                # ၃။ Message Status ကို PENDING ပြန်ချပြီး is_manual ကို reset လုပ်မည်
                # ဒါမှသာ နောက်တစ်ခါ Pickup ပြန်ခေါ်ရင် အစကနေ ပြန် run နိုင်မှာဖြစ်ပါတယ်
                conn.execute(
                    "UPDATE message_logs SET status = 'PENDING', is_manual = 0, category = NULL WHERE msg_id = ? AND chat_id = ?",
                    (orig_msg_id, chat_id)
                )
                conn.commit()
                log.info(f"🧹 Full Reset: Cleared pickup state for msg {orig_msg_id} in chat {chat_id}")
            
            with db_manager.connection_scope() as conn:
                msg_data = conn.execute("SELECT text, timestamp, media_id FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
            
            if msg_data:
                text, ts, media_id = msg_data
                _, _, shop_name = db_manager.get_topic_context(chat_id, 1)
                
                log.info(f"📢 Sending Urgent Alert for {shop_name} (Force=True)")
                
                # 🗑️ Admin Group ရှိ Pick Up alert အဟောင်းကို အရင်ဖျက်ခြင်း (Alert အသစ်မပို့မီ)
                try:
                    tracking = db_manager.get_alert_tracking(orig_msg_id, chat_id)
                    if tracking:
                        admin_chat_id = tracking[1]
                        bot.delete_message(admin_chat_id, tracking[0])
                        db_manager.delete_alert_tracking(orig_msg_id, chat_id) # Clear old tracking
                        log.info(f"🗑️ Deleted old pickup alert {tracking[0]} and cleared tracking before sending new one")
                except Exception as de:
                    log.debug(f"Failed to delete old alert: {de}")

                # 💡 Auditor module ထဲမှာ bot instance ကို သေချာအောင် ပြန်ထည့်ပေးခြင်း
                auditor.set_bot(bot)
                
                res = auditor.send_new_alert(
                    chat_id, 1, orig_msg_id, text, "Rider requested Admin support", shop_name, ts,
                    media_id=media_id, title="💬 Admin နှင့်ပြောမည် (OS Request)", force=True,
                    target_topic_override=1 # Force to Topic 1 (General/Urgent)
                )
                log.info(f"✅ send_new_alert result: {res}")
    
                auto_pickup.cleanup_pickup_intermediate_msgs(bot, chat_id, orig_msg_id)
                bot.send_message(chat_id, "တာဝန်ရှိသူထံ အကြောင်းကြားပြီးပါပြီ။ အမြန်ဆုံး ပြန်လည်အကြောင်းပြန်ပေးပါ့မယ်နော်", reply_to_message_id=orig_msg_id)
            else:
                log.warning(f"⚠️ No message log found for msg_id {orig_msg_id} in chat {chat_id}")
                bot.answer_callback_query(call.id, "⚠️ မူရင်းစာသား ရှာမတွေ့တော့ပါ", show_alert=True)
        except Exception as e:
            log.error(f"❌ Pickup Admin Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_wrong_back_'))
    def handle_wrong_pickup_back_callback(call):
        """ Wrong Pickup ရွေးချယ်မှုမှ နောက်ပြန်ဆုတ်ခြင်း """
        try:
            # format: ap_wrong_back_{orig_msg_id}_{chat_id} OR ap_wrong_back_staff_{orig_msg_id}_{chat_id}_{vehicle}
            parts = call.data.split('_')
            
            if parts[3] == "staff":
                orig_msg_id = int(parts[4])
                chat_id = int(parts[5])
                vehicle = parts[6]
                
                clean_chat_id = str(chat_id).replace("-100", "")
                msg_link = f"https://t.me/c/{clean_chat_id}/{orig_msg_id}"

                markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                markup.row(
                    telebot.types.InlineKeyboardButton("📅 Today", callback_data=f"ap_st_{orig_msg_id}_{chat_id}_today_{vehicle}"),
                    telebot.types.InlineKeyboardButton("📅 Tomorrow", callback_data=f"ap_st_{orig_msg_id}_{chat_id}_tomorrow_{vehicle}")
                )
                markup.row(
                    telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link),
                    telebot.types.InlineKeyboardButton("❌ Wrong Pickup", callback_data=f"ap_wrong_staff_{orig_msg_id}_{chat_id}_{vehicle}")
                )
            else:
                orig_msg_id = int(parts[3])
                chat_id = int(parts[4])

                msg_link = f"https://t.me/c/{str(chat_id)[4:]}/{orig_msg_id}" if str(chat_id).startswith("-100") else None
                
                markup = telebot.types.InlineKeyboardMarkup(row_width=1)
                if msg_link:
                    markup.add(telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link))
                
                markup.add(
                    telebot.types.InlineKeyboardButton("✅ Done", callback_data=f"pdone_{orig_msg_id}_{chat_id}"),
                    telebot.types.InlineKeyboardButton("❌ Wrong Pickup", callback_data=f"ap_wrong_{orig_msg_id}_{chat_id}")
                )
            
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
        except Exception as e:
            log.error(f"❌ Wrong Pickup Back Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_wrong_'))
    def handle_wrong_pickup_callback(call):
        """ AI မှ Pickup ဟု မှားယွင်းယူဆမိပါက Admin မှ Feedback ပေးခြင်း """
        try:
            # format: ap_wrong_{orig_msg_id}_{chat_id} OR ap_wrong_staff_{orig_msg_id}_{chat_id}_{vehicle}
            parts = call.data.split('_')
            if parts[2] == "back":
                return # Handled by handle_wrong_pickup_back_callback
            
            if parts[2] == "staff":
                orig_msg_id = int(parts[3])
                chat_id = int(parts[4])
                vehicle = parts[5]
                back_callback = f"ap_wrong_back_staff_{orig_msg_id}_{chat_id}_{vehicle}"
            else:
                orig_msg_id = int(parts[2])
                chat_id = int(parts[3])
                back_callback = f"ap_wrong_back_{orig_msg_id}_{chat_id}"

            markup = telebot.types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                telebot.types.InlineKeyboardButton("📋 စာရင်းပေးရုံသာ (List only)", callback_data=f"ap_fb_{orig_msg_id}_{chat_id}_LIST"),
                telebot.types.InlineKeyboardButton("💬 စကားပြောရုံသာ (Casual)", callback_data=f"ap_fb_{orig_msg_id}_{chat_id}_CASUAL"),
                telebot.types.InlineKeyboardButton("❓ စုံစမ်းမေးမြန်းခြင်း (Inquiry)", callback_data=f"ap_fb_{orig_msg_id}_{chat_id}_INQUIRY"),
                telebot.types.InlineKeyboardButton("🚫 အခြား (Other)", callback_data=f"ap_fb_{orig_msg_id}_{chat_id}_OTHER"),
                telebot.types.InlineKeyboardButton("🔙 Back", callback_data=back_callback)
            )
            
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            bot.answer_callback_query(call.id, "AI ကို သင်ယူစေရန် အကြောင်းရင်း ရွေးပေးပါ အစ်ကို")
        except Exception as e:
            log.error(f"❌ Wrong Pickup Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_fb_'))
    def handle_pickup_feedback_callback(call):
        """ Admin မှ ရွေးချယ်လိုက်သော Feedback ကို သိမ်းဆည်းခြင်း """
        try:
            from modules import auto_pickup
            # format: ap_fb_{orig_msg_id}_{chat_id}_{category}
            parts = call.data.split('_')
            orig_msg_id = int(parts[2])
            chat_id = int(parts[3])
            category = parts[4]

            # ၁။ မူရင်းစာသားကို ယူခြင်း
            log.info(f"🔍 Feedback Debug: Searching for msg_id={orig_msg_id}, chat_id={chat_id}")
            with db_manager.connection_scope() as conn:
                msg_data = conn.execute("SELECT text, topic_id FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
            
            if not msg_data:
                # Try searching without -100 prefix if it exists, or vice versa
                alt_chat_id = int(str(chat_id).replace("-100", "")) if str(chat_id).startswith("-100") else int(f"-100{chat_id}")
                log.info(f"🔍 Feedback Debug: Not found, trying alternative chat_id={alt_chat_id}")
                with db_manager.connection_scope() as conn:
                    msg_data = conn.execute("SELECT text, topic_id FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, alt_chat_id)).fetchone()
                if msg_data:
                    chat_id = alt_chat_id # Update chat_id if found with alternative

            if msg_data:
                orig_text, topic_id = msg_data
                # ၂။ Feedback သိမ်းခြင်း
                db_manager.save_feedback(orig_msg_id, chat_id, topic_id, category, orig_text, call.from_user.id)
                
                # ၃။ Status ကို CANCELLED ပြောင်းခြင်း
                db_manager.update_message_status(orig_msg_id, chat_id, 'CANCELLED')
                
                # ၄။ Shop Group ရှိ Bot စာများကို ရှင်းလင်းခြင်း
                auto_pickup.cleanup_pickup_intermediate_msgs(bot, chat_id, orig_msg_id)
                
                # ၅။ Admin Alert ကို ဖျက်ခြင်း
                try:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                except Exception as e:
                    log.error(f"❌ Error deleting feedback message: {e}")
                
                bot.answer_callback_query(call.id, f"✅ AI သင်ယူပြီးပါပြီ ({category})။ ကျေးဇူးပါ အစ်ကို။")
            else:
                bot.answer_callback_query(call.id, "⚠️ မူရင်းစာသား ရှာမတွေ့တော့ပါ")
        except Exception as e:
            log.error(f"❌ Pickup Feedback Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except Exception: pass

# --- Helper Functions ---

def save_manual_remark_interactive(message, bot, orig_msg_id, date_type, edit_msg_id, prompt_msg_id=None):
    """ Interactive Setup အတွက် မှတ်ချက်ကို သိမ်းဆည်းပြီး မူလစာကို Update လုပ်ခြင်း """
    try:
        from modules import auto_pickup
        remark = message.text.strip()
        chat_id = message.chat.id
        
        # 💡 User ရိုက်လိုက်သော စာနှင့် မှတ်ချက်ရေးခိုင်းသည့်စာကို ချက်ချင်းဖျက်မည်
        try: bot.delete_message(chat_id, message.message_id)
        except Exception: pass
        if prompt_msg_id:
            try: bot.delete_message(chat_id, prompt_msg_id)
            except Exception: pass
        
        # Update DB state
        tz = pytz.timezone('Asia/Yangon')
        
        # 💡 Midnight Bug Fix: Use original message timestamp instead of current time
        msg_ctx = db_manager.get_message_context(orig_msg_id, chat_id)
        msg_ts = msg_ctx[4] if msg_ctx else datetime.now(tz).timestamp()
        msg_dt = datetime.fromtimestamp(msg_ts, tz)
        
        target_date = (msg_dt if date_type == "today" else msg_dt + timedelta(days=1)).strftime("%d-%m-%Y")
        _, _, shop_name = db_manager.get_topic_context(chat_id, 1)
        
        # Get existing vehicle if any
        order = db_manager.get_pickup_order_by_msg(orig_msg_id, chat_id)
        vehicle = order[6] if order else None
        
        db_manager.upsert_pickup_queue(chat_id, orig_msg_id, target_date, shop_name, remark, vehicle, status='WAITING_SETUP')
        
        # Refresh Interactive Message
        auto_pickup.show_interactive_setup(bot, chat_id, orig_msg_id, date_type, vehicle=vehicle, remark=remark, edit_msg_id=edit_msg_id)
        
    except Exception as e:
        log.error(f"❌ Save Manual Remark Interactive Error: {e}")

def save_manual_remark(message, bot, orig_msg_id, date_type, vehicle):
    """ User ရိုက်လိုက်သော မှတ်ချက်ကို သိမ်းဆည်းပြီး Queue ထဲထည့်ခြင်း """
    try:
        remark = message.text.strip()
        chat_id = message.chat.id
        
        # 💡 User ရိုက်လိုက်သော စာကိုပါ ဖျက်ရန်အတွက် intermediate messages ထဲထည့်မည်
        db_manager.add_pickup_intermediate_msg(chat_id, orig_msg_id, message.message_id)
        
        if not remark:
            sent_msg = bot.reply_to(message, "⚠️ မှတ်ချက် အလွတ်ဖြစ်နေလို့ မူရင်းစာသားအတိုင်းပဲ တင်ပေးလိုက်ပါမယ်။")
            db_manager.add_pickup_intermediate_msg(chat_id, orig_msg_id, sent_msg.message_id)
            remark = None
        finalize_pickup_queue(bot, chat_id, orig_msg_id, date_type, vehicle, remark)
    except Exception as e:
        log.error(f"❌ Save Manual Remark Error: {e}")

def finalize_pickup_queue(bot, chat_id, orig_msg_id, date_type, vehicle, manual_remark):
    """ အချက်အလက်အားလုံး စုံပြီဖြစ်၍ အတည်ပြုချက်တောင်းခံခြင်း """
    try:
        tz = pytz.timezone('Asia/Yangon')
        
        # 💡 Midnight Bug Fix: Use original message timestamp instead of current time
        msg_ctx = db_manager.get_message_context(orig_msg_id, chat_id)
        msg_ts = msg_ctx[4] if msg_ctx else datetime.now(tz).timestamp()
        msg_dt = datetime.fromtimestamp(msg_ts, tz)
        
        target_date = (msg_dt if date_type == "today" else msg_dt + timedelta(days=1)).strftime("%d-%m-%Y")
        
        with db_manager.connection_scope() as conn:
            msg_data = conn.execute("SELECT text, summary FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
        
        orig_text = msg_data[0] if msg_data else "Auto Pickup Request"
        ai_summary = msg_data[1] if msg_data and msg_data[1] else None
        
        # Priority: Manual Remark > AI Summary (Clean Remark)
        # If no specific remark, use "-" instead of falling back to original text (which might be just "Pick up")
        final_remark = manual_remark if manual_remark else (ai_summary if ai_summary else "-")
        from modules import auto_pickup
        shop_name = auto_pickup.get_best_shop_name(bot, chat_id)
        
        # Strict Validation: No vehicle = No queue
        if not vehicle or vehicle == "none":
            log.warning(f"⚠️ Attempted to finalize queue without vehicle for chat {chat_id}")
            from modules import auto_pickup
            auto_pickup.ask_vehicle(bot, telebot.types.Message(message_id=orig_msg_id, from_user=None, date=None, chat=telebot.types.Chat(id=chat_id, type='group'), text=""), date_type, orig_msg_id)
            return

        v_str = vehicle
        queue_id = db_manager.upsert_pickup_queue(chat_id, orig_msg_id, target_date, shop_name, final_remark, v_str, status='WAITING_CONFIRM')
        
        confirm_text = (
            f"⏳ **Auto Pickup အချက်အလက်များ**\n"
            f"📅 ရက်စွဲ: {target_date}\n"
            f"🏪 ဆိုင်: {shop_name}\n"
            f"🚲 ယာဉ်: {v_str}\n"
            f"📝 မှတ်ချက်: {final_remark}\n"
            f"🔔 <b>အလိုအလျှောက် Pickup တင်ပေးနိုင်ရန် အောက်ပါအချက်များကို အတည်ပြုပေးပါဦးနော်။</b>"
        )

        markup = telebot.types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            telebot.types.InlineKeyboardButton("✅ Right (အတည်ပြုသည်)", callback_data=f"ap_conf_{queue_id}"),
            telebot.types.InlineKeyboardButton("✏️ ပြင်ဦးမည်", callback_data=f"ap_edit_{queue_id}"),
            telebot.types.InlineKeyboardButton("👨‍💼 Admin နှင့်ပြောမည်", callback_data=f"ap_admin_{queue_id}_{orig_msg_id}")
        )
        
        sent_msg = bot.send_message(chat_id, confirm_text, reply_to_message_id=orig_msg_id, reply_markup=markup)
        db_manager.add_pickup_intermediate_msg(chat_id, orig_msg_id, sent_msg.message_id)
        
        # Update central alert with latest info (Remark/Vehicle might have changed)
        from modules import auto_pickup
        auto_pickup.update_central_pickup_alert(bot, orig_msg_id, chat_id, "⏳ Waiting Confirmation (Rider)", queue_id=queue_id)
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

        # order format: (id, chat_id, orig_msg_id, target_date, os_name, remark, vehicle, status, created_at)
        _, _, orig_msg_id, target_date, shop_name, remark, vehicle, status, _ = order
        confirm_text = (
            f"⏳ **Auto Pickup အချက်အလက်များ (ပြင်ဆင်ပြီး)**\n"
            f"📅 ရက်စွဲ: {target_date}\n"
            f"🏪 ဆိုင်: {shop_name}\n"
            f"🚲 ယာဉ်: {vehicle}\n"
            f"📝 မှတ်ချက်: {remark}\n"
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
            sent_msg = bot.send_message(chat_id, confirm_text, reply_markup=markup)
            db_manager.add_pickup_intermediate_msg(chat_id, orig_msg_id, sent_msg.message_id)
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
        bot.reply_to(message, f"✅ **Mapping သိမ်းဆည်းပြီးပါပြီ!**\n\nနောက်ပိုင်း ဒီ Group ကတက်လာတဲ့ Pick up တွေကို Website ထဲက `{website_name}` နာမည်နဲ့ တင်ပေးသွားပါမယ်။ ကျရှုံးခဲ့သော Pickup များကိုလည်း ပြလည်တင်ပေးနေပါပြီ။")
        log.info(f"🎯 Manager manually mapped {chat_id} to {website_name}")
    except Exception as e:
        log.error(f"❌ Save Manual Mapping Error: {e}")
        bot.reply_to(message, "❌ သိမ်းဆည်းစဉ် အမှားတစ်ခု ဖြစ်သွားပါသည်။")

def save_manual_type_mapping(message, bot, chat_id, orig_msg_id, queue_id, prompt_msg_id=None, topic_id=None):
    """ Manual Type ဖြင့် ရိုက်ထည့်လိုက်သော OS Name ကို Mapping သိမ်း၊ စာများဖျက်၊ Retry လုပ်ခြင်း """
    try:
        from modules import auto_pickup
        website_name = message.text.strip() if message.text else ""
        # 💡 Forum topic ထဲမှာ ရှိနေစေရန် thread_id သုံးမည်
        thread_id = topic_id if topic_id else message.message_thread_id
        
        # 💡 User ရိုက်လိုက်သော စာနှင့် Prompt စာကို ချက်ချင်းဖျက်မည်
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass
        if prompt_msg_id:
            try:
                bot.delete_message(message.chat.id, prompt_msg_id)
            except Exception:
                pass
        
        if not website_name:
            # နာမည်အလွတ်ဖြစ်နေရင် အသိပေးပြီး return
            err_msg = bot.send_message(
                message.chat.id,
                "❌ နာမည် အလွတ်ဖြစ်နေလို့ မသိမ်းပေးနိုင်ပါဘူး။ Manual Type ကို ပြန်နှိပ်ပြီး ထပ်ကြိုးစားပေးပါခင်ဗျာ။",
                message_thread_id=thread_id
            )
            # Auto-delete error message after 10 seconds
            import threading
            def _del():
                import time
                time.sleep(10)
                try:
                    bot.delete_message(message.chat.id, err_msg.message_id)
                except Exception:
                    pass
            threading.Thread(target=_del, daemon=True).start()
            return
        
        # ၁။ Mapping သိမ်းခြင်း
        db_manager.set_shop_mapping(chat_id, website_name)
        
        # ၂။ FAILED Pickup များကို PENDING ပြန်ပြောင်း၍ Retry
        db_manager.retry_failed_pickups(chat_id)
        
        # ၃။ Pickup Alert Status ကို Processing ပြောင်းခြင်း
        if orig_msg_id:
            auto_pickup.update_central_pickup_alert(
                bot, orig_msg_id, chat_id, "⏳ Processing",
                queue_id=queue_id if queue_id else None
            )
        
        log.info(f"🎯 Manual Type: Mapped chat {chat_id} to '{website_name}', retrying failed pickups. OS: {website_name}")
    except Exception as e:
        log.error(f"❌ Save Manual Type Mapping Error: {e}")
        try:
            bot.send_message(
                message.chat.id,
                "❌ သိမ်းဆည်းစဉ် အမှားတစ်ခု ဖြစ်သွားပါသည်။",
                message_thread_id=message.message_thread_id
            )
        except Exception:
            pass
