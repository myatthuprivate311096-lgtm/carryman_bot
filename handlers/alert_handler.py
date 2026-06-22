import os
import telebot
import html
import pytz
import time
import json
from datetime import datetime
from logger import log
import db_manager
import config
from modules import auditor
import gsheet_sync

def _sync_routing_to_gsheet(chat_id, target_topic, topic_id):
    """ Routing set လုပ်ပြီးတိုင်း GSheet 'Shop Mappings' tab ကို auto-update လုပ်ခြင်း """
    try:
        sheet_url = os.getenv('GSHEET_URL')
        if not sheet_url:
            return
        
        syncer = gsheet_sync.GSheetSync()
        workbook = syncer.connect(sheet_url)
        if not workbook:
            return
        
        sheet = workbook.worksheet("Shop Mappings")
        all_vals = sheet.get_all_values()
        
        # Map target_topic → Sheet column letter
        # Column E = Pickup TID, Column F = Error TID, Column G = Finance TID
        col_map = {1: 'E', 37: 'F', 35: 'G'}
        col_letter = col_map.get(target_topic)
        if not col_letter:
            return
        
        # Find the row for this chat_id
        for i, row in enumerate(all_vals):
            if i == 0:
                continue
            if row and row[0].strip():
                try:
                    row_chat_id = int(row[0].strip())
                    clean_id = int(str(chat_id).replace("-100", ""))
                    if row_chat_id == chat_id or row_chat_id == clean_id:
                        row_num = i + 1
                        sheet.update(f'{col_letter}{row_num}', [[str(topic_id)]])
                        log.info(f"📤 GSheet auto-updated: row {row_num}, col {col_letter} = {topic_id} (target={target_topic})")
                        return
                except ValueError:
                    pass
        
        log.warning(f"⚠️ GSheet sync: chat_id {chat_id} not found in Sheet.")
    except Exception as e:
        log.error(f"❌ GSheet routing sync failed: {e}")

def register_alert_handlers(bot: telebot.TeleBot, is_manager_func):
    """ Alert Management (Done, Wrong, Feedback, Routing) အတွက် Callback များကို Register လုပ်ပေးသည် """

    def _cleanup_alert_messages(orig_id: int, chat_id: int):
        """
        Wrong Alert feedback နှိပ်သောအခါ Alert copies အားလုံးကို ရှင်းလင်းပေးခြင်း။
        """
        try:
            tracking = db_manager.get_alert_tracking(orig_id, chat_id)
            if not tracking:
                return

            # alert_msg_id, alert_chat_id, created_at, esc_msg_id, linked_msg_ids, linked_customer_ids, esc_tier2_msg_id, updates_text
            alert_msg_id, alert_chat_id, _, esc_msg_id, linked_ids_json, _, esc_tier2_msg_id, _ = tracking

            try:
                bot.delete_message(alert_chat_id, alert_msg_id)
            except Exception as e:
                log.warning(f"⚠️ Failed to delete main alert {alert_msg_id}: {e}")

            if esc_msg_id:
                try:
                    manager_id = int(os.getenv('MANAGER_ID', 7261311241))
                    bot.delete_message(manager_id, esc_msg_id)
                except Exception as e:
                    log.warning(f"⚠️ Failed to delete manager escalation {esc_msg_id}: {e}")

            if esc_tier2_msg_id:
                try:
                    escalation_group_id = int(os.getenv('ESCALATION_GROUP_ID', -1003906164269))
                    bot.delete_message(escalation_group_id, esc_tier2_msg_id)
                except Exception as e:
                    log.warning(f"⚠️ Failed to delete tier2 escalation {esc_tier2_msg_id}: {e}")

            if linked_ids_json:
                try:
                    linked_ids = json.loads(linked_ids_json)
                    for linked_msg_id in linked_ids:
                        try:
                            bot.delete_message(alert_chat_id, linked_msg_id)
                        except Exception as e:
                            log.warning(f"⚠️ Failed to delete linked alert {linked_msg_id}: {e}")
                except Exception as e:
                    log.warning(f"⚠️ Failed to parse linked ids for {orig_id}/{chat_id}: {e}")

            db_manager.delete_alert_tracking(orig_id, chat_id)
        except Exception as e:
            log.error(f"❌ cleanup_alert_messages Error: {e}")

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
            if target_topic == 1:
                target_topic = config.ALERT_TOPIC_CS
            original_msg_id = int(parts[4]) if len(parts) > 4 else 0
            target_chat = int(os.getenv('CENTRAL_GROUP_ID', -1003601049225))

            # 💡 Step 1: Update Routing (Short Scope)
            db_manager.update_routing_entry(chat_id, topic_id, target_chat, target_topic)
            
            # 💡 Step 1.5: Auto-sync routing to GSheet
            _sync_routing_to_gsheet(chat_id, target_topic, topic_id)
            
            # 💡 Step 2: Gather Context & Send Alert (OUTSIDE DB Scope)
            if original_msg_id != 0:
                with db_manager.connection_scope() as conn:
                    ctx = db_manager.get_message_context(original_msg_id, chat_id)
                    _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
                
                if ctx:
                    text, summary, category, intent, ts, media_id = ctx
                    auditor.send_new_alert(
                        chat_id, topic_id, original_msg_id, text, summary, shop_name, ts,
                        category=category, intent=intent, media_id=media_id, force=True
                    )
                    
                    # 💡 Step 3: Final Status Update (Short Scope)
                    with db_manager.connection_scope() as conn:
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

    @bot.callback_query_handler(func=lambda call: call.data.startswith('wip_'))
    def handle_wip_button(call):
        """ Alert Message ရှိ 'ဖြေရှင်းနေပါသည်' Button — ဖြေရှင်းနေသူ နာမည်သာ ပြမည် (DB/Status မထိ) """
        try:
            user_id = call.from_user.id
            if not (db_manager.check_if_staff(user_id) or is_manager_func(user_id)):
                bot.answer_callback_query(call.id, "⚠️ ဝန်ထမ်းများသာ နှိပ်ခွင့်ရှိပါသည်။", show_alert=True)
                return

            parts = call.data.split('_')
            original_msg_id = int(parts[1])
            chat_id = int(parts[2])

            staff_data = db_manager.get_staff_info(user_id)
            staff_name = staff_data[1] if staff_data else call.from_user.first_name

            current_text = call.message.text or call.message.caption or ""
            new_text = auditor.inject_wip_handler_line(current_text, staff_name)
            markup = auditor.build_sla_alert_markup(original_msg_id, chat_id)

            alert_chat_id = call.message.chat.id
            alert_msg_id = call.message.message_id
            has_media = bool(call.message.photo or call.message.video or call.message.document or call.message.voice)

            if has_media:
                bot.edit_message_caption(
                    new_text,
                    alert_chat_id,
                    alert_msg_id,
                    reply_markup=markup
                )
            else:
                bot.edit_message_text(
                    new_text,
                    alert_chat_id,
                    alert_msg_id,
                    reply_markup=markup
                )
            bot.answer_callback_query(call.id, f"✅ {staff_name} — ဖြေရှင်းနေပါသည်")
        except Exception as e:
            if "message is not modified" in str(e).lower():
                try:
                    bot.answer_callback_query(call.id, "✅ စာရင်းသွင်းပြီးပါပြီ")
                except Exception:
                    pass
            else:
                log.error(f"❌ WIP Button Error: {e}")
                try:
                    bot.answer_callback_query(call.id, "❌ Error updating alert.", show_alert=True)
                except Exception:
                    pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('done_'))
    def handle_done_button(call):
        """ Alert Message ရှိ Done Button ကို နှိပ်လိုက်လျှင် ဖြေရှင်းပြီးအဖြစ် သတ်မှတ်ခြင်း """
        resolved = False
        should_delete = False
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
            auditor.resolve_and_cleanup(
                original_msg_id, chat_id, shop_name, orig_text,
                f"{staff_name} (Done Button)",
                manual_resolve=True,
                allow_manual_resolve=True
            )
            
            # 💡 Step 3: Final DB Update (Short Scope)
            db_manager.resolve_message(original_msg_id, chat_id, staff_name, method='Done Button', topic_id=topic_id)
            resolved = True
            should_delete = True
            bot.answer_callback_query(call.id, "✅ Resolved and Recorded!")
        except Exception as e:
            log.error(f"❌ Done Button Error: {e}", exc_info=True)
            try:
                bot.answer_callback_query(call.id, "❌ Error resolving alert.", show_alert=True)
            except Exception:
                pass
        finally:
            if should_delete:
                try:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                except Exception as e:
                    log.warning(f"⚠️ Failed to delete alert message after Done: {e}")
                if resolved:
                    try:
                        orig_id = db_manager.get_original_msg_id_by_alert(call.message.message_id, call.message.chat.id)
                        if orig_id:
                            parts = call.data.split('_')
                            chat_id = int(parts[2])
                            db_manager.delete_alert_tracking(orig_id, chat_id)
                    except Exception as e:
                        log.warning(f"⚠️ Failed to cleanup alert tracking after Done: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('wrong_back_'))
    def handle_wrong_back(call):
        """ Wrong Alert menu မှ မူလ menu သို့ ပြန်သွားခြင်း """
        try:
            parts = call.data.split('_')
            orig_id = parts[2]
            chat_id = parts[3]
            markup = auditor.build_sla_alert_markup(orig_id, chat_id)
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
                telebot.types.InlineKeyboardButton("⚪ Soft Complaint (အပျော့စား)", callback_data=f"fb_soft_{orig_id}_{chat_id}"),
                telebot.types.InlineKeyboardButton("🧩 Other Meaning (အဓိပ္ပာယ်ကွဲ)", callback_data=f"fb_other_{orig_id}_{chat_id}"),
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
                    telebot.types.InlineKeyboardButton("🚚 Pickup (General)", callback_data=f"route_{config.ALERT_TOPIC_CS}_{orig_id}_{chat_id}"),
                    telebot.types.InlineKeyboardButton("💰 Finance (Topic 35)", callback_data=f"route_{config.ALERT_TOPIC_FIN}_{orig_id}_{chat_id}"),
                    telebot.types.InlineKeyboardButton("⚠️ Error (Topic 37)", callback_data=f"route_{config.ALERT_TOPIC_ERROR}_{orig_id}_{chat_id}"),
                    telebot.types.InlineKeyboardButton("📝 Data Entry (Topic 6621)", callback_data=f"route_{config.ALERT_TOPIC_DE}_{orig_id}_{chat_id}")
                )
                try:
                    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
                    bot.answer_callback_query(call.id)
                except: pass
                return

            category_map = {
                "greet": "Greeting (နှုတ်ဆက်စာ)",
                "soft": "Soft Complaint (အပျော့စား / complain မပြင်း)",
                "other": "Other Meaning (အဓိပ္ပာယ်ကွဲ)",
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
            _cleanup_alert_messages(orig_id, chat_id)
            ignored_actions = {"greet", "soft", "other"}
            db_manager.update_message_status(orig_id, chat_id, 'IGNORED' if action in ignored_actions else 'RESOLVED', topic_id=topic_id)
            
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except: pass
            
            bot.answer_callback_query(call.id, f"✅ Feedback Recorded: {category}")
        except Exception as e:
            log.error(f"❌ Feedback Callback Error: {e}")

    _WRONG_TOPIC_TARGETS = (0, 1, config.ALERT_TOPIC_CS, config.ALERT_TOPIC_FIN, config.ALERT_TOPIC_ERROR, config.ALERT_TOPIC_DE)

    @bot.callback_query_handler(func=lambda call: call.data.startswith('route_'))
    def handle_rerouting(call):
        """ Wrong Topic အတွက် Re-routing လုပ်ဆောင်ခြင်း """
        try:
            user_id = call.from_user.id
            if not (db_manager.check_if_staff(user_id) or is_manager_func(user_id)):
                bot.answer_callback_query(call.id, "⚠️ ဝန်ထမ်းများသာ နှိပ်ခွင့်ရှိပါသည်။", show_alert=True)
                return

            try:
                bot.answer_callback_query(call.id, "⏳ Re-routing Alert...")
            except Exception:
                pass

            parts = call.data.split('_')
            if len(parts) < 4:
                bot.answer_callback_query(call.id, "❌ Invalid route data.", show_alert=True)
                return

            target_topic = int(parts[1])
            if target_topic == 1:
                target_topic = config.ALERT_TOPIC_CS
            orig_id = int(parts[2])
            chat_id = int(parts[3])

            if target_topic not in _WRONG_TOPIC_TARGETS:
                bot.answer_callback_query(call.id, "❌ Unknown target topic.", show_alert=True)
                return

            with db_manager.connection_scope() as conn:
                topic_id = db_manager.get_message_topic(orig_id, chat_id)
                _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
                msg_data = conn.execute(
                    "SELECT text, timestamp, media_id FROM message_logs WHERE msg_id = ? AND chat_id = ?",
                    (orig_id, chat_id)
                ).fetchone()

            if not msg_data:
                bot.answer_callback_query(call.id, "❌ Original message not found.", show_alert=True)
                return

            text, ts, media_id = msg_data
            tracking = db_manager.get_alert_tracking(orig_id, chat_id)
            if tracking:
                try:
                    bot.delete_message(tracking[1], tracking[0])
                except Exception:
                    pass
                db_manager.delete_alert_tracking(orig_id, chat_id)

            target_chat = int(os.getenv('ALERT_CHAT_ID', -1003601049225))
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

            markup = auditor.build_sla_alert_markup(orig_id, chat_id)
            thread_kw = {'message_thread_id': target_topic} if target_topic else {}

            try:
                if media_id:
                    msg = bot.send_photo(
                        target_chat, media_id,
                        caption=alert_text, parse_mode="HTML", reply_markup=markup, **thread_kw
                    )
                else:
                    msg = bot.send_message(
                        target_chat, alert_text, parse_mode="HTML", reply_markup=markup, **thread_kw
                    )
            except Exception as send_err:
                if "message thread not found" in str(send_err).lower() and thread_kw:
                    log.warning(f"⚠️ Topic {target_topic} not found. Re-routing to General.")
                    if media_id:
                        msg = bot.send_photo(
                            target_chat, media_id,
                            caption=alert_text, parse_mode="HTML", reply_markup=markup
                        )
                    else:
                        msg = bot.send_message(
                            target_chat, alert_text, parse_mode="HTML", reply_markup=markup
                        )
                else:
                    raise

            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass

            db_manager.save_alert_tracking(orig_id, chat_id, msg.message_id, target_chat)
            db_manager.update_message_status(orig_id, chat_id, 'ALERTED', topic_id=topic_id)
            log.info(f"✅ Re-routed alert {orig_id} from chat {chat_id} to topic {target_topic}")

            try:
                bot.answer_callback_query(call.id, f"✅ Re-routed to Topic {target_topic}")
            except Exception:
                pass
        except Exception as e:
            log.error(f"❌ Re-routing Error: {e}")
            try:
                bot.answer_callback_query(call.id, f"❌ Error: {str(e)}", show_alert=True)
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
