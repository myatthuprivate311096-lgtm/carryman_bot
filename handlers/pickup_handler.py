import telebot
import pytz
import os
from datetime import datetime, timedelta
from logger import log
import db_manager
from modules import auditor

def register_pickup_handlers(bot: telebot.TeleBot):
    """ Auto Pickup Module အတွက် Callback များကို Register လုပ်ပေးသည် """

    @bot.callback_query_handler(func=lambda call: call.data.startswith('done_'))
    def handle_pickup_done_callback(call):
        """ Admin မှ Pickup Notification ရှိ Done Button ကို နှိပ်လိုက်သည့်အခါ """
        try:
            from modules import auto_pickup
            # format: done_{orig_msg_id}_{chat_id}
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
            try: bot.answer_callback_query(call.id, "❌ Error deleting message")
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_dt_') or call.data.startswith('ap_vh_'))
    def handle_auto_pickup_callback(call):
        """ Auto Pickup Module အတွက် Callback များ (Date/Vehicle Selection) """
        if db_manager.check_if_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "⚠️ Staff Account မှဖြေပေးလို့မရပါ", show_alert=True)
            return
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
            except: pass
        finally:
            try: bot.answer_callback_query(call.id)
            except: pass

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

            if date_type == "today":
                # Unified Interactive Message for Today
                auto_pickup.show_interactive_setup(bot, chat_id, orig_msg_id, "today", vehicle=vehicle)
                bot.edit_message_text(f"✅ **Today** အဖြစ် သတ်မှတ်ပြီး Group ထဲသို့ အကြောင်းကြားလိုက်ပါပြီ။", call.message.chat.id, call.message.message_id)
                auto_pickup.update_central_pickup_alert(bot, orig_msg_id, chat_id, "📅 Today (Staff Decision)")
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
                bot.edit_message_text(f"✅ **Tomorrow** အတွက် Customer ထံ ခွင့်ပြုချက် တောင်းခံထားပါသည် အစ်ကို။", call.message.chat.id, call.message.message_id)
                auto_pickup.update_central_pickup_alert(bot, orig_msg_id, chat_id, "📅 Tomorrow (Waiting Customer)")

        except Exception as e:
            log.error(f"❌ Staff Pickup Decision Error: {e}")
            try: bot.answer_callback_query(call.id, "❌ Error occurred")
            except: pass
        finally:
            try: bot.answer_callback_query(call.id)
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_tconf_'))
    def handle_tomorrow_confirm_callback(call):
        """ ညနေ ၃ နာရီနောက်ပိုင်း မနက်ဖြန်အတွက် OK နှိပ်လိုက်သည့်အခါ """
        try:
            from modules import auto_pickup
            # format: ap_tconf_{orig_msg_id}
            orig_msg_id = int(call.data.split('_')[2])
            chat_id = call.message.chat.id
            
            # Show interactive setup (Tomorrow)
            auto_pickup.show_interactive_setup(bot, chat_id, orig_msg_id, "tomorrow", edit_msg_id=call.message.message_id)
            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Tomorrow Confirm Callback Error: {e}")

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
            now = datetime.now(tz)
            target_date = (now if date_type == "today" else now + timedelta(days=1)).strftime("%d-%m-%Y")
            _, _, shop_name = db_manager.get_topic_context(chat_id, 1)
            
            db_manager.upsert_pickup_queue(chat_id, orig_msg_id, target_date, shop_name, None, vehicle, status='WAITING_SETUP')
            
            # Refresh Interactive Message
            auto_pickup.show_interactive_setup(bot, chat_id, orig_msg_id, date_type, vehicle=vehicle, edit_msg_id=call.message.message_id)
            bot.answer_callback_query(call.id, f"✅ {vehicle} ကို ရွေးချယ်လိုက်ပါသည်")
        except Exception as e:
            log.error(f"❌ Interactive Vehicle Callback Error: {e}")

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
            bot.register_next_step_handler(msg, save_manual_remark_interactive, bot, orig_msg_id, date_type, call.message.message_id)
            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Interactive Remark Callback Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_isb_'))
    def handle_interactive_submit_callback(call):
        """ Interactive Setup: Final Submit """
        try:
            # format: ap_isb_{orig_msg_id}_{date_type}
            parts = call.data.split('_')
            orig_msg_id = int(parts[2])
            date_type = parts[3]
            chat_id = call.message.chat.id

            order = db_manager.get_pickup_order_by_msg(orig_msg_id, chat_id)
            if not order or not order[6]: # No vehicle
                bot.answer_callback_query(call.id, "⚠️ ယာဉ်အမျိုးအစား အရင်ရွေးပေးပါဦး", show_alert=True)
                return

            # Finalize
            finalize_pickup_queue(bot, chat_id, orig_msg_id, date_type, order[6], order[5])
            bot.delete_message(chat_id, call.message.message_id)
            bot.answer_callback_query(call.id, "✅ Pickup တင်ရန် အတည်ပြုလိုက်ပါပြီ")
        except Exception as e:
            log.error(f"❌ Interactive Submit Callback Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_cs_'))
    def handle_customer_pickup_decision(call):
        """ Customer မှ OK/Admin ရွေးချယ်မှုအား ကိုင်တွယ်ခြင်း """
        if db_manager.check_if_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "⚠️ Staff Account မှဖြေပေးလို့မရပါ", show_alert=True)
            return
        try:
            from modules import auto_pickup
            # format: ap_cs_{orig_msg_id}_{chat_id}_{action}_{vehicle}
            parts = call.data.split('_')
            orig_msg_id = int(parts[2])
            chat_id = int(parts[3])
            action = parts[4]
            vehicle = parts[5] if parts[5] != "none" else "Bicycle"

            if action == "ok":
                auto_pickup.ask_remark(bot, chat_id, "tomorrow", vehicle, orig_msg_id, show_cancel=False)
                bot.edit_message_text("✅ မနက်ဖြန်အတွက် pick up တင်ပေးထားပါ့မယ်ရှင်။", chat_id, call.message.message_id)
                auto_pickup.update_central_pickup_alert(bot, orig_msg_id, chat_id, "📅 Tomorrow (Customer OK)")
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

        except Exception as e:
            log.error(f"❌ Customer Pickup Decision Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_cancel_'))
    def handle_pickup_cancel_callback(call):
        """ AI မှ Pickup ဟု မှားယွင်းယူဆမိပါက Rider မှ ပယ်ဖျက်ခြင်း """
        if db_manager.check_if_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "⚠️ Staff Account မှဖြေပေးလို့မရပါ", show_alert=True)
            return
        try:
            from modules import auto_pickup
            orig_msg_id = int(call.data.split('_')[2])
            chat_id = call.message.chat.id
            
            # 1. Admin Group ရှိ Notification ကို ဖျက်ခြင်း
            tracking = db_manager.get_alert_tracking(orig_msg_id, chat_id)
            if tracking:
                alert_msg_id = tracking[0]
                central_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))
                try:
                    bot.delete_message(central_chat, alert_msg_id)
                except: pass
                db_manager.delete_alert_tracking(orig_msg_id, chat_id)

            # 2. Status ကို PENDING သို့ ပြန်ပြောင်းခြင်း (15 mins alert ပြန်တက်စေရန်)
            db_manager.update_message_status(orig_msg_id, chat_id, 'PENDING')
            
            # 3. Shop Group ရှိ Bot စာများကို ရှင်းလင်းခြင်း
            bot.delete_message(chat_id, call.message.message_id)
            auto_pickup.cleanup_pickup_intermediate_msgs(bot, chat_id, orig_msg_id)
            
            bot.answer_callback_query(call.id, "✅ Pickup မဟုတ်ကြောင်း မှတ်သားပြီး ပုံမှန်စာအဖြစ် ပြန်ပြောင်းလိုက်ပါပြီ။")
        except Exception as e:
            log.error(f"❌ Pickup Cancel Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_rm_'))
    def handle_remark_selection(call):
        """ မှတ်ချက်ရေးမည်/မရှိပါ ရွေးချယ်မှုအား ကိုင်တွယ်ခြင်း """
        if db_manager.check_if_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "⚠️ Staff Account မှဖြေပေးလို့မရပါ", show_alert=True)
            return
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
            except: pass

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
            except: pass

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
            except: pass

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
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_conf_'))
    def handle_pickup_confirm_callback(call):
        """ Rider မှ အချက်အလက်မှန်ကန်ကြောင်း အတည်ပြုသည့်အခါ """
        if db_manager.check_if_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "⚠️ Staff Account မှဖြေပေးလို့မရပါ", show_alert=True)
            return
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
                f"🏪 ဆိုင်: <b>{shop_name}</b>\n"
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
            db_manager.add_pickup_intermediate_msg(chat_id, orig_msg_id, call.message.message_id)
            auto_pickup.update_central_pickup_alert(bot, orig_msg_id, chat_id, "⏳ Pending")
                
        except Exception as e:
            log.error(f"❌ Pickup Confirm Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_edit_'))
    def handle_pickup_edit_callback(call):
        """ Rider မှ ပြန်ပြင်ချသည့်အခါ (ဘယ်အချက်အလက်ကို ပြင်မလဲ ရွေးခိုင်းမည်) """
        if db_manager.check_if_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "⚠️ Staff Account မှဖြေပေးလို့မရပါ", show_alert=True)
            return
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
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_ed_date_'))
    def handle_edit_date_callback(call):
        """ ရက်စွဲပြင်ရန် ရွေးချယ်မှုပြခြင်း """
        if db_manager.check_if_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "⚠️ Staff Account မှဖြေပေးလို့မရပါ", show_alert=True)
            return
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
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_ed_v_'))
    def handle_edit_vehicle_callback(call):
        """ ယာဉ်အမျိုးအစားပြင်ရန် ရွေးချယ်မှုပြခြင်း """
        if db_manager.check_if_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "⚠️ Staff Account မှဖြေပေးလို့မရပါ", show_alert=True)
            return
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
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_ed_rem_'))
    def handle_edit_remark_callback(call):
        """ မှတ်ချက်ပြင်ရန် ForceReply ပြခြင်း """
        if db_manager.check_if_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "⚠️ Staff Account မှဖြေပေးလို့မရပါ", show_alert=True)
            return
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
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_upd_'))
    def handle_update_field_callback(call):
        """ Field များကို Update လုပ်ပြီး Confirmation ပြန်ပြခြင်း """
        if db_manager.check_if_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "⚠️ Staff Account မှဖြေပေးလို့မရပါ", show_alert=True)
            return
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
        finally:
            try: bot.answer_callback_query(call.id)
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_ed_back_'))
    def handle_back_to_conf_callback(call):
        """ ပြင်ဆင်မှုမလုပ်ဘဲ မူလ Confirmation သို့ ပြန်သွားခြင်း """
        if db_manager.check_if_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "⚠️ Staff Account မှဖြေပေးလို့မရပါ", show_alert=True)
            return
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
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_admin_'))
    def handle_pickup_admin_callback(call):
        """ Rider မှ Admin နှင့်ပြောရန် ရွေးချယ်သည့်အခါ """
        if db_manager.check_if_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "⚠️ Staff Account မှဖြေပေးလို့မရပါ", show_alert=True)
            return
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
                bot.send_message(chat_id, "တာဝန်ရှိသူထံ အကြောင်းကြားပြီးပါပြီ။ အမြန်ဆုံး ပြန်လည်အကြောင်းပြန်ပေးပါ့မယ်နော်", reply_to_message_id=orig_msg_id)
        except Exception as e:
            log.error(f"❌ Pickup Admin Callback Error: {e}")
        finally:
            try: bot.answer_callback_query(call.id)
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ap_wrong_'))
    def handle_wrong_pickup_callback(call):
        """ AI မှ Pickup ဟု မှားယွင်းယူဆမိပါက Admin မှ Feedback ပေးခြင်း """
        try:
            # format: ap_wrong_{orig_msg_id}_{chat_id}
            parts = call.data.split('_')
            orig_msg_id = int(parts[2])
            chat_id = int(parts[3])

            markup = telebot.types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                telebot.types.InlineKeyboardButton("📋 စာရင်းပေးရုံသာ (List only)", callback_data=f"ap_fb_{orig_msg_id}_{chat_id}_LIST"),
                telebot.types.InlineKeyboardButton("💬 စကားပြောရုံသာ (Casual)", callback_data=f"ap_fb_{orig_msg_id}_{chat_id}_CASUAL"),
                telebot.types.InlineKeyboardButton("❓ စုံစမ်းမေးမြန်းခြင်း (Inquiry)", callback_data=f"ap_fb_{orig_msg_id}_{chat_id}_INQUIRY"),
                telebot.types.InlineKeyboardButton("🚫 အခြား (Other)", callback_data=f"ap_fb_{orig_msg_id}_{chat_id}_OTHER")
            )
            
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            bot.answer_callback_query(call.id, "AI ကို သင်ယူစေရန် အကြောင်းရင်း ရွေးပေးပါ အစ်ကို")
        except Exception as e:
            log.error(f"❌ Wrong Pickup Callback Error: {e}")

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
            with db_manager.connection_scope() as conn:
                msg_data = conn.execute("SELECT text, topic_id FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
            
            if msg_data:
                orig_text, topic_id = msg_data
                # ၂။ Feedback သိမ်းခြင်း
                db_manager.save_feedback(orig_msg_id, chat_id, topic_id, category, orig_text, call.from_user.id)
                
                # ၃။ Status ကို CANCELLED ပြောင်းခြင်း
                db_manager.update_message_status(orig_msg_id, chat_id, 'CANCELLED')
                
                # ၄။ Shop Group ရှိ Bot စာများကို ရှင်းလင်းခြင်း
                auto_pickup.cleanup_pickup_intermediate_msgs(bot, chat_id, orig_msg_id)
                
                # ၅။ Admin Alert ကို Update လုပ်ခြင်း (သို့မဟုတ် ဖျက်ခြင်း)
                new_text = (call.message.caption or call.message.text or "") + f"\n\n❌ **Wrong Pickup** ({category})\nAI ကို သင်ယူခိုင်းလိုက်ပါပြီ အစ်ကို။"
                try:
                    bot.edit_message_caption(
                        caption=new_text,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        parse_mode="HTML",
                        reply_markup=None
                    )
                except:
                    bot.edit_message_text(
                        text=new_text,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        parse_mode="HTML",
                        reply_markup=None
                    )
                bot.answer_callback_query(call.id, "✅ AI သင်ယူပြီးပါပြီ။ ကျေးဇူးပါ အစ်ကို။")
            else:
                bot.answer_callback_query(call.id, "⚠️ မူရင်းစာသား ရှာမတွေ့တော့ပါ")
        except Exception as e:
            log.error(f"❌ Pickup Feedback Callback Error: {e}")

# --- Helper Functions ---

def save_manual_remark_interactive(message, bot, orig_msg_id, date_type, edit_msg_id):
    """ Interactive Setup အတွက် မှတ်ချက်ကို သိမ်းဆည်းပြီး မူလစာကို Update လုပ်ခြင်း """
    try:
        from modules import auto_pickup
        remark = message.text.strip()
        chat_id = message.chat.id
        
        # Update DB state
        tz = pytz.timezone('Asia/Yangon')
        now = datetime.now(tz)
        target_date = (now if date_type == "today" else now + timedelta(days=1)).strftime("%d-%m-%Y")
        _, _, shop_name = db_manager.get_topic_context(chat_id, 1)
        
        db_manager.upsert_pickup_queue(chat_id, orig_msg_id, target_date, shop_name, remark, None, status='WAITING_SETUP')
        
        # Refresh Interactive Message
        auto_pickup.show_interactive_setup(bot, chat_id, orig_msg_id, date_type, remark=remark, edit_msg_id=edit_msg_id)
        
        # Cleanup the reply message
        try: bot.delete_message(chat_id, message.message_id)
        except: pass
    except Exception as e:
        log.error(f"❌ Save Manual Remark Interactive Error: {e}")

def save_manual_remark(message, bot, orig_msg_id, date_type, vehicle):
    """ User ရိုက်လိုက်သော မှတ်ချက်ကို သိမ်းဆည်းပြီး Queue ထဲထည့်ခြင်း """
    try:
        remark = message.text.strip()
        chat_id = message.chat.id
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
        now = datetime.now(tz)
        target_date = (now if date_type == "today" else now + timedelta(days=1)).strftime("%d-%m-%Y")
        
        with db_manager.connection_scope() as conn:
            msg_data = conn.execute("SELECT text, summary FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_msg_id, chat_id)).fetchone()
        
        orig_text = msg_data[0] if msg_data else "Auto Pickup Request"
        ai_summary = msg_data[1] if msg_data and msg_data[1] else None
        
        # Priority: Manual Remark > AI Summary (Clean Remark) > Original Text
        final_remark = manual_remark if manual_remark else (ai_summary if ai_summary else orig_text)
        _, _, shop_name = db_manager.get_topic_context(chat_id, 1)
        
        # Strict Validation: No vehicle = No queue
        if not vehicle or vehicle == "none":
            log.warning(f"⚠️ Attempted to finalize queue without vehicle for chat {chat_id}")
            from modules import auto_pickup
            auto_pickup.ask_vehicle(bot, telebot.types.Message(message_id=orig_msg_id, from_user=None, date=None, chat=telebot.types.Chat(id=chat_id, type='group'), text=""), date_type, orig_msg_id)
            return

        v_str = vehicle
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
        
        # Update central alert with latest info (Remark/Vehicle might have changed)
        from modules import auto_pickup
        auto_pickup.update_central_pickup_alert(bot, orig_msg_id, chat_id, "⏳ Waiting Confirmation (Rider)")
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
        bot.reply_to(message, f"✅ **Mapping သိမ်းဆည်းပြီးပါပြီ!**\n\nနောက်ပိုင်း ဒီ Group ကတက်လာတဲ့ Pick up တွေကို Website ထဲက `{website_name}` နာမည်နဲ့ တင်ပေးသွားပါမယ်။ ကျရှုံးခဲ့သော Pickup များကိုလည်း ပြန်လည်တင်ပေးနေပါပြီ။")
        log.info(f"🎯 Manager manually mapped {chat_id} to {website_name}")
    except Exception as e:
        log.error(f"❌ Save Manual Mapping Error: {e}")
        bot.reply_to(message, "❌ သိမ်းဆည်းစဉ် အမှားတစ်ခု ဖြစ်သွားပါသည်။")
