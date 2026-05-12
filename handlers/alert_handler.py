import os
import telebot
import html
import pytz
import time
from datetime import datetime
from logger import log
import db_manager
from modules import auditor

def register_alert_handlers(bot: telebot.TeleBot, is_manager_func):
    """ Alert Management (Done, Wrong, Feedback, Routing) အတွက် Callback များကို Register လုပ်ပေးသည် """

    @bot.callback_query_handler(func=lambda call: call.data.startswith('setrt_'))
    def handle_set_routing(call):
        """ Manager/Staff မှ Missing Route အတွက် Topic ရွေးချယ်ပေးခြင်း """
        start_time = time.time()
        try:
            user_id = call.from_user.id
            if not (is_manager_func(user_id) or db_manager.check_if_staff(user_id)):
                bot.answer_callback_query(call.id, "⚠️ Admin/Staff သာ လုပ်ဆောင်ခွင့်ရှိပါသည်။", show_alert=True)
                return

            # 💡 Immediate answer to stop the loading spinner
            try:
                bot.answer_callback_query(call.id, "⏳ Processing Routing...")
            except: pass
            
            parts = call.data.split('_')
            chat_id = int(parts[1])
            topic_id = int(parts[2])
            target_topic = int(parts[3])
            original_msg_id = int(parts[4]) if len(parts) > 4 else 0
            target_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))

            # 💡 Step 1: Update Routing (Short Scope)
            db_manager.update_routing_entry(chat_id, topic_id, target_chat, target_topic)
            
            # 💡 Step 2: Gather Context & Send Alert (OUTSIDE DB Scope)
            if original_msg_id != 0:
                with db_manager.connection_scope() as conn:
                    ctx = db_manager.get_message_context(original_msg_id, chat_id)
                    _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
                
                if ctx:
                    text, summary, category, intent, ts, media_id = ctx
                    auditor.send_new_alert(
                        chat_id, topic_id, original_msg_id, text, summary, shop_name, ts,
                        category=category, intent=intent, media_id=media_id
                    )
                    
                    # 💡 Step 3: Final Status Update (Short Scope)
                    with db_manager.write_scope() as conn:
                        conn.execute(
                            "UPDATE message_logs SET status='ALERTED' WHERE chat_id=? AND topic_id=? AND status='WAITING_ROUTE'",
                            (chat_id, topic_id)
                        )

            # 💡 Delete message early to avoid "stuck" UI
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except: pass

            log.info(f"✅ handle_set_routing completed in {time.time() - start_time:.3f}s")
        except Exception as e:
            log.error(f"❌ Set Routing Callback Error: {e}")
            try:
                bot.answer_callback_query(call.id, f"❌ Error: {str(e)}", show_alert=True)
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('done_'))
    def handle_done_button(call):
        """ Alert Message ရှိ Done Button ကို နှိပ်လိုက်လျှင် ဖြေရှင်းပြီးအဖြစ် သတ်မှတ်ခြင်း """
        try:
            user_id = call.from_user.id
            parts = call.data.split('_')
            original_msg_id = int(parts[1])
            chat_id = int(parts[2])
            
            # 💡 Step 1: Gather Data (Short Scope)
            with db_manager.connection_scope() as conn:
                if not (db_manager.check_if_staff(user_id) or is_manager_func(user_id)):
                    bot.answer_callback_query(call.id, "⚠️ ဝန်ထမ်းများသာ နှိပ်ခွင့်ရှိပါသည်။", show_alert=True)
                    return

                staff_data = db_manager.get_staff_info(user_id)
                staff_name = staff_data[1] if staff_data else call.from_user.first_name
                topic_id = db_manager.get_message_topic(original_msg_id, chat_id)
                
                msg_data = conn.execute("SELECT text FROM message_logs WHERE msg_id = ? AND chat_id = ?", (original_msg_id, chat_id)).fetchone()
                orig_text = msg_data[0] if msg_data else "[Unknown]"
                _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)

            # 💡 Step 2: API Cleanup (OUTSIDE DB Scope)
            auditor.resolve_and_cleanup(original_msg_id, chat_id, shop_name, orig_text, f"{staff_name} (Done Button)", manual_resolve=True)
            
            # 💡 Step 3: Final DB Update (Short Scope)
            db_manager.resolve_message(original_msg_id, chat_id, staff_name, method='Done Button', topic_id=topic_id)
            
            bot.answer_callback_query(call.id, "✅ Resolved and Recorded!")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except: pass
        except Exception as e:
            log.error(f"❌ Done Button Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('wrong_back_'))
    def handle_wrong_back(call):
        """ Wrong Alert menu မှ မူလ menu သို့ ပြန်သွားခြင်း """
        try:
            parts = call.data.split('_')
            orig_id = parts[2]
            chat_id = parts[3]
            clean_chat_id = str(chat_id).replace("-100", "")
            msg_link = f"tg://privatepost?channel={clean_chat_id}&post={orig_id}"
            
            markup = telebot.types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link),
                telebot.types.InlineKeyboardButton("✅ Done", callback_data=f"done_{orig_id}_{chat_id}"),
                telebot.types.InlineKeyboardButton("❌ Wrong Alert", callback_data=f"wrong_{orig_id}_{chat_id}")
            )
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Wrong Back Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('wrong_'))
    def handle_wrong_alert_button(call):
        """ Wrong Alert ခလုတ်ကို နှိပ်လိုက်လျှင် Option များ ပြပေးခြင်း """
        try:
            user_id = call.from_user.id
            if not (db_manager.check_if_staff(user_id) or is_manager_func(user_id)):
                bot.answer_callback_query(call.id, "⚠️ ဝန်ထမ်းများသာ နှိပ်ခွင့်ရှိပါသည်။", show_alert=True)
                return

            parts = call.data.split('_')
            orig_id = parts[1]
            chat_id = parts[2]

            markup = telebot.types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                telebot.types.InlineKeyboardButton("👋 Greeting (နှုတ်ဆက်စာ)", callback_data=f"fb_greet_{orig_id}_{chat_id}"),
                telebot.types.InlineKeyboardButton("🔄 Wrong Topic (Topic မှားနေသည်)", callback_data=f"fb_topic_{orig_id}_{chat_id}"),
                telebot.types.InlineKeyboardButton("📑 Duplicate (ကိစ္စဟောင်း)", callback_data=f"fb_dup_{orig_id}_{chat_id}"),
                telebot.types.InlineKeyboardButton("✅ Already Resolved (ဖြေရှင်းပြီး)", callback_data=f"fb_done_{orig_id}_{chat_id}"),
                telebot.types.InlineKeyboardButton("🔙 Back", callback_data=f"wrong_back_{orig_id}_{chat_id}")
            )
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"❌ Wrong Alert Button Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('fb_'))
    def handle_feedback_callback(call):
        """ Feedback Option တစ်ခုခုကို ရွေးလိုက်သည့်အခါ """
        try:
            user_id = call.from_user.id
            parts = call.data.split('_')
            action = parts[1]
            orig_id = int(parts[2])
            chat_id = int(parts[3])
            
            # 💡 Step 1: Gather Data (Short Scope)
            with db_manager.connection_scope() as conn:
                topic_id = db_manager.get_message_topic(orig_id, chat_id)
                _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
                
                msg_data = conn.execute("SELECT text FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_id, chat_id)).fetchone()
                orig_text = msg_data[0] if msg_data else "[Unknown]"

            if action == "topic":
                markup = telebot.types.InlineKeyboardMarkup(row_width=1)
                markup.add(
                    telebot.types.InlineKeyboardButton("🚚 Pickup (Topic 1)", callback_data=f"route_1_{orig_id}_{chat_id}"),
                    telebot.types.InlineKeyboardButton("💰 Finance (Topic 35)", callback_data=f"route_35_{orig_id}_{chat_id}"),
                    telebot.types.InlineKeyboardButton("⚠️ Error (Topic 37)", callback_data=f"route_37_{orig_id}_{chat_id}")
                )
                try:
                    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
                    bot.answer_callback_query(call.id)
                except: pass
                return

            category_map = {
                "greet": "Greeting (နှုတ်ဆက်စာ)",
                "dup": "Duplicate (ကိစ္စဟောင်း)",
                "done": "Already Resolved (ဖြေရှင်းပြီး)"
            }
            category = category_map.get(action, "Other")
            
            if action == "done":
                trailing = db_manager.get_messages_after(chat_id, topic_id, orig_id, limit=3)
                if trailing:
                    trailing_text = "\n[Trailing Pattern]:\n" + "\n".join([f"- {t[0]}" for t in trailing])
                    orig_text += trailing_text

            # 💡 Step 2: Save Feedback & Update Status (Short Scopes)
            db_manager.save_feedback(orig_id, chat_id, topic_id, category, orig_text, user_id)
            db_manager.delete_alert_tracking(orig_id, chat_id)
            db_manager.update_message_status(orig_id, chat_id, 'RESOLVED' if action != "greet" else 'IGNORED', topic_id=topic_id)
            
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except: pass
            
            bot.answer_callback_query(call.id, f"✅ Feedback Recorded: {category}")
        except Exception as e:
            log.error(f"❌ Feedback Callback Error: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('route_'))
    def handle_rerouting(call):
        """ Wrong Topic အတွက် Re-routing လုပ်ဆောင်ခြင်း """
        try:
            # 💡 Immediate answer to stop the loading spinner
            try:
                bot.answer_callback_query(call.id, "⏳ Re-routing Alert...")
            except: pass

            parts = call.data.split('_')
            target_topic = int(parts[1])
            orig_id = int(parts[2])
            chat_id = int(parts[3])
            
            with db_manager.connection_scope() as conn:
                topic_id = db_manager.get_message_topic(orig_id, chat_id)
                _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
                
                msg_data = conn.execute("SELECT text, timestamp, media_id FROM message_logs WHERE msg_id = ? AND chat_id = ?", (orig_id, chat_id)).fetchone()
            
                if msg_data:
                    text, ts, media_id = msg_data
                    tracking = db_manager.get_alert_tracking(orig_id, chat_id)
                    if tracking:
                        try: bot.delete_message(tracking[1], tracking[0])
                        except: pass
                        db_manager.delete_alert_tracking(orig_id, chat_id)

                    target_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))
                    tz = pytz.timezone('Asia/Yangon')
                    orig_time = datetime.fromtimestamp(ts, tz).strftime('%Y-%m-%d %I:%M %p')
                    safe_shop = html.escape(shop_name)
                    safe_text = html.escape(text)

                    alert_text = (
                        f"🔄 <b>RE-ROUTED ALERT</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🏪 ဆိုင်: <b>{safe_shop}</b>\n"
                        f"💬 စာသား: {safe_text}\n"
                        f"⏰ အချိန်: {orig_time}\n"
                        f"━━━━━━━━━━━━━━━━━━"
                    )
                    
                    clean_chat_id = str(chat_id).replace("-100", "")
                    msg_link = f"tg://privatepost?channel={clean_chat_id}&post={orig_id}"
                    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                    markup.add(
                        telebot.types.InlineKeyboardButton("🔗 View Message", url=msg_link),
                        telebot.types.InlineKeyboardButton("✅ Done", callback_data=f"done_{orig_id}_{chat_id}"),
                        telebot.types.InlineKeyboardButton("❌ Wrong Alert", callback_data=f"wrong_{orig_id}_{chat_id}")
                    )

                    if media_id:
                        msg = bot.send_photo(target_chat, media_id, caption=alert_text, message_thread_id=target_topic, parse_mode="HTML", reply_markup=markup)
                    else:
                        msg = bot.send_message(target_chat, alert_text, message_thread_id=target_topic, parse_mode="HTML", reply_markup=markup)

                    # 💡 Delete the routing menu message to avoid "stuck" UI
                    try:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                    except: pass
                    
                    db_manager.save_alert_tracking(orig_id, chat_id, msg.message_id, target_chat)
                    db_manager.update_message_status(orig_id, chat_id, 'ALERTED', topic_id=topic_id)
                
                try:
                    bot.answer_callback_query(call.id, f"✅ Re-routed to Topic {target_topic}")
                except: pass
        except Exception as e:
            log.error(f"❌ Re-routing Error: {e}")
            try:
                bot.answer_callback_query(call.id, f"❌ Error: {str(e)}", show_alert=True)
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except: pass

    @bot.message_reaction_handler(func=lambda message: True)
    def handle_reaction(message):
        """ ဝန်ထမ်းမှ Reaction ပေးလျှင် Message ကို RESOLVED အဖြစ် သတ်မှတ်ခြင်း """
        try:
            reaction = message
            user_id = reaction.user.id
            chat_id = reaction.chat.id
            message_id = reaction.message_id
            
            if not reaction.new_reaction:
                return
                
            has_emoji = any(r.type == 'emoji' for r in reaction.new_reaction)
            if not has_emoji:
                return

            # 💡 Step 1: Gather Data (Short Scope)
            with db_manager.connection_scope() as conn:
                if not (db_manager.check_if_staff(user_id) or is_manager_func(user_id)):
                    return

                staff_data = db_manager.get_staff_info(user_id)
                staff_name = staff_data[1] if staff_data else reaction.user.first_name
                emoji_used = next((r.emoji for r in reaction.new_reaction if r.type == 'emoji'), "Emoji")
                topic_id = db_manager.get_message_topic(message_id, chat_id)

                if db_manager.is_manual_alert(message_id, chat_id):
                    return

                msg_data = conn.execute("SELECT text, category, intent FROM message_logs WHERE msg_id = ? AND chat_id = ?", (message_id, chat_id)).fetchone()
                orig_text = msg_data[0] if msg_data else "[Unknown]"
                category = msg_data[1] if msg_data else None
                intent = msg_data[2] if msg_data else None

                # 💡 Pick Up Alert ဖြစ်ပါက Reaction ဖြင့် Resolve လုပ်ခွင့်မပေးပါ
                if category == 'PICKUP' or intent == 'PICKUP':
                    log.info(f"ℹ️ Message {message_id} is PICKUP. Skipping auto-resolve on reaction.")
                    return
                _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)

            # 💡 Step 2: API Cleanup (OUTSIDE DB Scope)
            auditor.resolve_and_cleanup(message_id, chat_id, shop_name, orig_text, f"{staff_name} (Reaction {emoji_used})", manual_resolve=True)
            
            # 💡 Step 3: Final DB Update (Short Scope)
            db_manager.resolve_message(message_id, chat_id, staff_name, method=f'Reaction ({emoji_used})', topic_id=topic_id)
            log.info(f"✅ Message {message_id} marked as RESOLVED via Reaction ({emoji_used}) by {staff_name}")
        except Exception as e:
            log.error(f"❌ Reaction Handler Error: {e}")
