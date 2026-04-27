import telebot
from logger import log
import db_manager
import main_router
from modules import auditor

def register_message_handlers(bot: telebot.TeleBot, is_manager_func):
    """ Group များအတွင်း စာဝင်လာမှု အားလုံးကို ဖမ်းယူပြီး DB သို့ သိမ်းဆည်းခြင်း နှင့် Routing လုပ်ခြင်း """

    @bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'voice', 'video', 'document', 'video_note', 'audio'])
    def handle_all_messages(message):
        """ Group များအတွင်း စာဝင်လာမှု အားလုံးကို ဖမ်းယူပြီး DB သို့ သိမ်းဆည်းခြင်း """
        try:
            # 🧠 Central AI Router (Sandbox Logic Inside)
            main_router.route_message(bot, message)

            chat_id = message.chat.id
            user_id = message.from_user.id
            
            # 💡 Media Type အလိုက် စာသားပြောင်းလဲခြင်း
            media_id = None
            text = message.text or message.caption
            
            if not text:
                if message.photo:
                    text = "🖼️ Photo"
                    media_id = message.photo[-1].file_id
                elif message.voice:
                    text = "🎙️ Voice Message"
                    media_id = message.voice.file_id
                elif message.video:
                    text = "📹 Video"
                    media_id = message.video.file_id
                elif message.video_note:
                    text = "🎥 Video Note"
                    media_id = message.video_note.file_id
                elif message.document:
                    text = f"📄 Document: {message.document.file_name}"
                    media_id = message.document.file_id
                elif message.audio:
                    text = f"🎵 Audio: {message.audio.title}"
                    media_id = message.audio.file_id
                else:
                    text = "📦 Media Content"
            
            # 💡 ဝန်ထမ်းဖြစ်ကြောင်း စစ်ဆေးခြင်း (Database + Anonymous Admin + Group Owner)
            is_staff = db_manager.check_if_staff(user_id)
            is_mgr = is_manager_func(user_id)
            
            # Anonymous Admin သို့မဟုတ် Group Owner စစ်ဆေးခြင်း
            if not is_staff and not is_mgr:
                if message.sender_chat and message.sender_chat.id == chat_id:
                    is_staff = True # Anonymous Admin
                elif message.from_user and message.from_user.is_bot:
                    return # Bot စာများကို ကျော်မည်
                else:
                    # Admin List ထဲတွင် ပါ/မပါ စစ်ဆေးခြင်း (Safety Net)
                    try:
                        member = bot.get_chat_member(chat_id, user_id)
                        if member.status in ['administrator', 'creator']:
                            is_staff = True
                    except: pass

            is_os_group = db_manager.check_if_os_group(chat_id)
            
            if is_os_group:
                # 💡 General Topic Fallback: 0 သို့မဟုတ် None ဖြစ်ပါက 1 ဟု သတ်မှတ်မည်
                topic_id = message.message_thread_id if (message.is_topic_message and message.message_thread_id) else 1
                
                # Smart Polling အတွက် နောက်ဆုံးဖတ်ထားသော ID ကို အမြဲ Update လုပ်မည်
                db_manager.update_last_read_id(chat_id, topic_id, message.message_id)

                if is_staff or is_mgr:
                    # 💡 ဝန်ထမ်းမှ Reply ပြန်လျှင် ထိုစာကို RESOLVED အဖြစ် သတ်မှတ်မည်
                    if message.reply_to_message and message.reply_to_message.message_id != message.message_thread_id:
                        original_id = message.reply_to_message.message_id
                        
                        staff_data = db_manager.get_staff_info(user_id)
                        staff_name = staff_data[1] if staff_data else (message.from_user.first_name if message.from_user else "Staff")
                        
                        # 💡 Get the actual topic_id of the original message
                        orig_topic_id = db_manager.get_message_topic(original_id, chat_id)
                        
                        # 💡 Manual Alert ဖြစ်နေပါက Reply ဖြင့် Resolve လုပ်ခွင့်မပေးပါ
                        if db_manager.is_manual_alert(original_id, chat_id):
                            return

                        # ၁။ DB တွင် Resolve လုပ်ခြင်း
                        db_manager.resolve_message(original_id, chat_id, staff_name, method='Reply', topic_id=orig_topic_id)
                        
                        # ၂။ Alert Cleanup & Record Group သို့ ပို့ခြင်း (Alert ရှိမှသာ ပို့မည်)
                        _, _, shop_name = db_manager.get_topic_context(chat_id, topic_id)
                        
                        # မူရင်းစာသားကို db ကနေ ပြန်ယူရန်
                        conn = db_manager.get_connection()
                        msg_data = conn.execute("SELECT text FROM message_logs WHERE msg_id = ? AND chat_id = ?", (original_id, chat_id)).fetchone()
                        conn.close()
                        orig_text = msg_data[0] if msg_data else "[Unknown]"
                        
                        # 💡 resolve_and_cleanup ထဲတွင် Alert ရှိ/မရှိ စစ်ဆေးပြီးသားဖြစ်သည်
                        # manual_resolve=True ထည့်ပေးခြင်းဖြင့် Office Hours ပြင်ပဖြစ်စေ Record ပို့မည်
                        auditor.resolve_and_cleanup(original_id, chat_id, shop_name, orig_text, f"{staff_name} (Reply)", manual_resolve=True)
                        
                        log.info(f"✅ Message {original_id} marked as RESOLVED via Reply by {staff_name}")
                    return

                # Customer ဆီမှ စာဝင်လာခြင်း
                if not message.from_user.is_bot and not text.startswith('/'):
                    db_manager.log_message(message.message_id, chat_id, topic_id, user_id, text, message.date, media_id=media_id)
                    log.info(f"📩 New Pending Message from {user_id} in {chat_id} (Topic: {topic_id})")

        except Exception as e:
            log.error(f"❌ Message Handler Error: {e}")
