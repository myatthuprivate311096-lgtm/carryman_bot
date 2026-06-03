"""
Staff ↔ OS Telegram Group membership sync (Telegram ToS aware).

- Rate-limited invites (no mass-add spam)
- Privacy-restricted users receive invite link via bot DM (consent-based join)
- PeerFlood protection with per-run cap
"""
import os
import json
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl import functions
from telethon.errors import (
    UserPrivacyRestrictedError,
    UserNotMutualContactError,
    PeerFloodError,
    ChatAdminRequiredError,
    UserAlreadyParticipantError,
    FloodWaitError,
)

import db_manager
from logger import log

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))
API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
SESSION_PATH = os.path.join(BASE_DIR, 'carryman')
STATE_FILE = os.path.join(BASE_DIR, 'staff_sync_state.json')

INVITE_DELAY_SEC = 3
MAX_ACTIONS_PER_RUN = int(os.getenv('STAFF_SYNC_MAX_ACTIONS', '80'))
BATCH_PAUSE_SEC = int(os.getenv('STAFF_SYNC_BATCH_PAUSE', '90'))
MAX_BATCHES_RUNALL = int(os.getenv('STAFF_SYNC_MAX_BATCHES', '25'))

# အရင်က /newgroup မှာ သုံးခဲ့တဲ့ @username များ (staff DB ထဲ မပါသေးရင် backup)
LEGACY_STAFF_USERNAMES = [
    '@cmsod1', '@cmmarketing1', '@cmfinance1', '@dataentrycm1',
]


def _load_sync_state():
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return int(data.get('group_index', 0))
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        return 0


def _save_sync_state(group_index):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'group_index': max(0, int(group_index))}, f)
    except Exception as e:
        log.warning(f"⚠️ Could not save staff sync state: {e}")


def reset_sync_cursor():
    _save_sync_state(0)


def _normalize_chat_id(chat_id):
    try:
        return int(chat_id)
    except (TypeError, ValueError):
        return chat_id


def _collect_target_groups():
    """All distinct OS groups + optional central admin group."""
    groups = {}
    for chat_id, shop_name in db_manager.get_distinct_os_group_chats():
        cid = _normalize_chat_id(chat_id)
        if cid:
            groups[cid] = shop_name or str(cid)

    central = os.getenv('CENTRAL_GROUP_ID')
    if central:
        cid = _normalize_chat_id(central)
        if cid and cid not in groups:
            groups[cid] = 'Central Admin Group'
    return list(groups.items())


def build_staff_invite_targets():
    """staff DB user_id + legacy @username စာရင်း (group ထဲ တိုက်ရိုက် add အတွက်)"""
    targets = []
    seen_ids = set()

    for row in db_manager.get_all_staff():
        uid = int(row[0])
        name = row[1] or str(uid)
        targets.append((uid, name))
        seen_ids.add(uid)

    for legacy in LEGACY_STAFF_USERNAMES:
        if isinstance(legacy, str) and legacy.startswith('@'):
            targets.append((legacy, legacy.lstrip('@')))
        elif isinstance(legacy, int) and legacy not in seen_ids:
            targets.append((legacy, str(legacy)))
            seen_ids.add(legacy)

    return targets


async def _try_invite(client, entity, user_ref):
    """user_ref = int user_id သို့မဟုတ် '@username' — group ထဲ တိုက်ရိုက် ထည့်ခြင်း"""
    try:
        peer = await client.get_input_entity(user_ref)
        await client(functions.channels.InviteToChannelRequest(channel=entity, users=[peer]))
        return 'invited', None
    except UserAlreadyParticipantError:
        return 'already', None
    except UserPrivacyRestrictedError:
        return 'privacy', 'User privacy settings block direct add'
    except UserNotMutualContactError:
        return 'not_contact', 'Not a mutual contact'
    except ChatAdminRequiredError:
        return 'no_admin', 'Session account lacks invite permission in this group'
    except PeerFloodError as e:
        return 'peer_flood', str(e)
    except FloodWaitError as e:
        return 'flood_wait', f'FloodWait {e.seconds}s'
    except ValueError as e:
        return 'error', f'Cannot resolve user {user_ref}: {e}'
    except Exception as e:
        return 'error', str(e)


async def _get_invite_link(client, entity):
    try:
        result = await client(functions.messages.ExportChatInviteRequest(peer=entity))
        return result.link
    except Exception as e:
        log.warning(f"⚠️ Could not export invite link: {e}")
        return None


async def _get_member_ids(client, channel):
    try:
        participants = await client.get_participants(channel, limit=None)
        return {p.id for p in participants}
    except Exception as e:
        log.warning(f"⚠️ Could not list participants: {e}")
        return set()


async def invite_all_staff_to_channel(client, channel, group_name, invite_link=None, new_group=False):
    """
    staff အားလုံးကို group ထဲ တိုက်ရိုက် ထည့်ခြင်း (InviteToChannel / Telegram add member).
    new_group=True: batch ထည့်ပြီး မအောင်မြင်သူကို တစ်ဦးချင်း ထပ်ကြိုးစား။
    """
    staff_list = build_staff_invite_targets()

    stats = {
        'staff_total': len(staff_list),
        'invited': 0,
        'already': 0,
        'pending_dms': [],
        'errors': [],
        'member_ids': [],
    }

    if not staff_list:
        stats['errors'].append('staff table မှာ ဝန်ထမ်းစာရင်း မရှိပါ — /addstaff ဖြင့် ထည့်ပါ')
        return stats

    if invite_link is None:
        invite_link = await _get_invite_link(client, channel)

    try:
        channel = await client.get_entity(channel)
    except Exception as e:
        log.warning(f"⚠️ Could not refresh channel entity: {e}")

    member_ids = await _get_member_ids(client, channel)

    def _is_present(user_ref):
        if isinstance(user_ref, int):
            return user_ref in member_ids
        return False

    to_invite = [(ref, name) for ref, name in staff_list if not _is_present(ref)]
    stats['already'] = len(staff_list) - len(to_invite)

    if new_group and len(to_invite) > 1:
        batch_peers = []
        for ref, name in to_invite:
            try:
                batch_peers.append(await client.get_input_entity(ref))
            except Exception as e:
                log.warning(f"⚠️ Skip batch — cannot resolve {name} ({ref}): {e}")
                stats['errors'].append(f"{name}: cannot resolve ({e})")

        if batch_peers:
            try:
                log.info(f"📨 Batch add {len(batch_peers)} staff → {group_name}")
                await client(functions.channels.InviteToChannelRequest(channel=channel, users=batch_peers))
                await asyncio.sleep(5)
                member_ids = await _get_member_ids(client, channel)
                still_missing = []
                for ref, name in to_invite:
                    if isinstance(ref, int) and ref in member_ids:
                        stats['invited'] += 1
                        log.info(f"✅ Batch add OK: {name} ({ref})")
                    elif not isinstance(ref, int):
                        still_missing.append((ref, name))
                    else:
                        still_missing.append((ref, name))
                to_invite = still_missing
            except Exception as e:
                log.warning(f"⚠️ Batch add failed, one-by-one: {e}")
                stats['errors'].append(f"Batch add: {e}")

    delay = 1 if new_group else INVITE_DELAY_SEC
    for user_ref, name in to_invite:
        status, detail = await _try_invite(client, channel, user_ref)
        await asyncio.sleep(delay)

        if status == 'invited':
            stats['invited'] += 1
            if isinstance(user_ref, int):
                member_ids.add(user_ref)
            log.info(f"✅ Direct add: {name} ({user_ref}) → {group_name}")
        elif status == 'already':
            stats['already'] += 1
        elif status in ('privacy', 'not_contact'):
            uid = user_ref if isinstance(user_ref, int) else None
            if uid:
                stats['pending_dms'].append((uid, name, group_name, invite_link, status))
            else:
                stats['errors'].append(f"{name} ({user_ref}): {detail}")
        elif status == 'error':
            stats['errors'].append(f"{name} ({user_ref}): {detail}")
        elif status in ('peer_flood', 'flood_wait'):
            stats['errors'].append(f"Telegram rate limit ({detail})")
            break
        elif status == 'no_admin':
            stats['errors'].append('carryman account မှာ group admin / add member permission မရှိပါ')
            break

    stats['member_ids'] = list(await _get_member_ids(client, channel))
    return stats


def notify_staff_missing_from_group(bot, group_name, invite_link, staff_stats):
    """DM invite link to staff still not in the group after invite attempts."""
    if not bot or not invite_link or invite_link == 'Link Error':
        return 0, 0
    pending = staff_stats.get('pending_dms') or []
    if not pending:
        return 0, 0
    return send_invite_dms(bot, pending)


def send_invite_dms(bot, pending_dms):
    """Public wrapper for invite-link DMs."""
    return _send_invite_dms(bot, pending_dms)


async def _sync_staff_async(dry_run=False, resume=True, max_actions=None, start_index=None):
    staff_rows = db_manager.get_all_staff()
    if not staff_rows:
        return {'error': 'staff table မှာ ဝန်ထမ်းစာရင်း မရှိပါ။ /addstaff ဖြင့် ထည့်ပါ။'}

    staff_list = build_staff_invite_targets()
    groups = _collect_target_groups()
    if not groups:
        return {'error': 'os_groups table မှာ Group စာရင်း မရှိပါ။'}

    action_cap = MAX_ACTIONS_PER_RUN if max_actions is None else max_actions
    group_start = _load_sync_state() if (resume and start_index is None and not dry_run) else (start_index or 0)

    stats = {
        'dry_run': dry_run,
        'groups_checked': 0,
        'groups_skipped_resume': group_start,
        'staff_count': len(staff_list),
        'group_count': len(groups),
        'already_ok': 0,
        'invited': 0,
        'dm_sent': 0,
        'dm_failed': 0,
        'missing_lines': [],
        'errors': [],
        'stopped_early': None,
        'actions_used': 0,
        'complete': False,
        'next_group_index': group_start,
    }

    async with TelegramClient(SESSION_PATH, API_ID, API_HASH) as client:
        for idx, (chat_id, shop_name) in enumerate(groups):
            if idx < group_start:
                continue

            if not dry_run and action_cap and stats['actions_used'] >= action_cap:
                stats['stopped_early'] = f'Batch cap ({action_cap}) reached'
                stats['next_group_index'] = idx
                _save_sync_state(idx)
                break

            try:
                entity = await client.get_entity(chat_id)
            except Exception as e:
                stats['errors'].append(f"{shop_name}: group access failed — {e}")
                stats['next_group_index'] = idx + 1
                continue

            stats['groups_checked'] += 1
            try:
                participants = await client.get_participants(entity, limit=None)
                member_ids = {p.id for p in participants}
            except Exception as e:
                stats['errors'].append(f"{shop_name}: cannot list members — {e}")
                stats['next_group_index'] = idx + 1
                continue

            missing = [(ref, name) for ref, name in staff_list if isinstance(ref, int) and ref not in member_ids]
            if not missing:
                stats['already_ok'] += 1
                stats['next_group_index'] = idx + 1
                continue

            if dry_run:
                for ref, name in missing:
                    stats['missing_lines'].append((shop_name, chat_id, ref, name, 'missing'))
                stats['next_group_index'] = idx + 1
                continue

            invite_link = None
            group_done = True
            for user_ref, name in missing:
                if action_cap and stats['actions_used'] >= action_cap:
                    stats['stopped_early'] = f'Batch cap ({action_cap}) reached'
                    stats['next_group_index'] = idx
                    group_done = False
                    _save_sync_state(idx)
                    break

                status, detail = await _try_invite(client, entity, user_ref)
                await asyncio.sleep(INVITE_DELAY_SEC)

                if status == 'invited':
                    stats['invited'] += 1
                    stats['actions_used'] += 1
                    log.info(f"✅ Direct add {name} ({user_ref}) → {shop_name}")
                elif status == 'already':
                    pass
                elif status == 'no_admin':
                    stats['errors'].append(f"{shop_name}: no invite admin right — skipped remaining")
                    stats['next_group_index'] = idx + 1
                    break
                elif status in ('peer_flood', 'flood_wait'):
                    stats['stopped_early'] = detail or status
                    stats['errors'].append(f"Telegram rate limit: {detail}. Stopped to comply with ToS.")
                    stats['next_group_index'] = idx
                    _save_sync_state(idx)
                    return stats
                elif status in ('privacy', 'not_contact', 'error'):
                    stats['missing_lines'].append((shop_name, chat_id, user_ref, name, status))
                    stats['actions_used'] += 1
                    if invite_link is None:
                        invite_link = await _get_invite_link(client, entity)
                    if isinstance(user_ref, int):
                        stats['_pending_dms'] = stats.get('_pending_dms', [])
                        stats['_pending_dms'].append((user_ref, name, shop_name, invite_link, status))

            if not group_done:
                break
            stats['next_group_index'] = idx + 1
        else:
            stats['complete'] = True
            stats['next_group_index'] = 0
            if not dry_run:
                _save_sync_state(0)

    if stats['complete'] and not dry_run:
        _save_sync_state(0)

    stats['groups_remaining'] = max(0, stats['group_count'] - stats['next_group_index'])
    return stats


def _send_invite_dms(bot, pending_dms):
    sent, failed = 0, 0
    for uid, name, shop_name, link, reason in pending_dms:
        reason_note = {
            'privacy': 'Privacy settings ကြောင့် group ထဲ auto-add မလုပ်နိုင်ပါ',
            'not_contact': 'Telegram contact မဟုတ်သောကြောင့် invite link ပို့ပေးပါသည်',
            'error': 'Direct invite မအောင်မြင်ပါ',
            'not_in_group': 'New group ဖွင့်ပြီး — link မှ ဝင်ပါရှင့်',
            'new_group': 'New group ဖွင့်ပြီး — link မှ ဝင်ပါရှင့်',
        }.get(reason, 'Join link ပို့ပေးပါသည်')
        text = (
            f"📢 <b>CarryMan Office — Group Join</b>\n\n"
            f"👤 {name}\n"
            f"🏪 Group: <b>{shop_name}</b>\n\n"
            f"ℹ️ {reason_note}.\n"
            f"Office group သို့ ဝင်ရောက်ပါရန် link:\n"
        )
        if link:
            text += f"\n🔗 {link}\n"
        else:
            text += "\n⚠️ Invite link မရနိုင်ပါ — Manager ထံ ဆက်သွယ်ပါ.\n"
        text += (
            "\n<i>Telegram Terms: spam/mass-add မလုပ်ပါ — "
            "link မှ ကိုယ်တိုင် join လုပ်ပါရှင့်.</i>"
        )
        try:
            bot.send_message(uid, text, parse_mode='HTML', disable_web_page_preview=True)
            sent += 1
        except Exception as e:
            log.warning(f"⚠️ DM invite link to {uid} failed: {e}")
            failed += 1
    return sent, failed


def _format_report(stats):
    if stats.get('error'):
        return f"❌ {stats['error']}"

    mode = "🔍 <b>Check Only (Dry Run)</b>" if stats.get('dry_run') else "✅ <b>Staff Group Sync</b>"
    lines = [
        mode,
        f"👥 Staff: {stats['staff_count']} | 🏪 Groups: {stats['group_count']}",
        f"📂 Checked: {stats['groups_checked']} | ✔️ All staff present: {stats['already_ok']}",
    ]
    if not stats.get('dry_run'):
        lines.append(f"➕ Direct invited: {stats['invited']}")
        lines.append(f"📩 Invite link DM sent: {stats['dm_sent']} (failed: {stats['dm_failed']})")

    missing = stats.get('missing_lines') or []
    if missing:
        lines.append(f"\n⚠️ <b>Missing / needs join ({len(missing)}):</b>")
        for item in missing[:25]:
            shop, _cid, uid, name, reason = item
            lines.append(f"• {name} ({uid}) → {shop} [{reason}]")
        if len(missing) > 25:
            lines.append(f"... +{len(missing) - 25} more")

    errors = stats.get('errors') or []
    if errors:
        lines.append(f"\n❌ <b>Errors ({len(errors)}):</b>")
        for err in errors[:10]:
            lines.append(f"• {err}")

    if stats.get('complete'):
        lines.append("\n🎉 <b>All groups synced!</b> Cursor reset.")
    elif stats.get('stopped_early') and not stats.get('dry_run'):
        remaining = stats.get('groups_remaining', 0)
        lines.append(f"\n📍 Resume index: group #{stats.get('next_group_index', 0)} | ~{remaining} groups left")
        lines.append("▶️ Continue: <code>/syncstaff run</code> (same batch size)")
        lines.append("▶️ Auto-all: <code>/syncstaff runall</code> (background batches)")

    if stats.get('stopped_early'):
        lines.append(f"\n⏸ Stopped: {stats['stopped_early']}")

    if not stats.get('dry_run'):
        lines.append(
            f"\n<i>ToS: {INVITE_DELAY_SEC}s/invite, {MAX_ACTIONS_PER_RUN} invites/batch, "
            f"privacy-blocked → DM link.</i>"
        )
    else:
        lines.append("\n💡 Invite: <code>/syncstaff run</code> | All groups: <code>/syncstaff runall</code>")

    return "\n".join(lines)


def _apply_pending_dms(bot, stats):
    if not stats.get('error') and not stats.get('dry_run'):
        pending = stats.pop('_pending_dms', [])
        if pending and bot:
            sent, failed = _send_invite_dms(bot, pending)
            stats['dm_sent'] = sent
            stats['dm_failed'] = failed


def sync_staff_groups(bot, dry_run=False, resume=True):
    """Single batch sync — resumes from saved cursor."""
    stats = asyncio.run(_sync_staff_async(dry_run=dry_run, resume=resume))
    _apply_pending_dms(bot, stats)
    report = _format_report(stats)
    log.info(
        f"Staff sync batch: invited={stats.get('invited', 0)}, "
        f"next_index={stats.get('next_group_index', 0)}, complete={stats.get('complete')}"
    )
    return report


def sync_staff_groups_all(bot, chat_id, message_id):
    """Run multiple batches with pauses until all groups done or Telegram flood limit."""
    import time as _time

    totals = {
        'invited': 0, 'dm_sent': 0, 'dm_failed': 0,
        'groups_checked': 0, 'already_ok': 0, 'batches': 0,
    }
    last_report = ""

    for batch_num in range(1, MAX_BATCHES_RUNALL + 1):
        stats = asyncio.run(_sync_staff_async(dry_run=False, resume=True))
        _apply_pending_dms(bot, stats)

        if stats.get('error'):
            last_report = _format_report(stats)
            break

        totals['batches'] = batch_num
        totals['invited'] += stats.get('invited', 0)
        totals['dm_sent'] += stats.get('dm_sent', 0)
        totals['dm_failed'] += stats.get('dm_failed', 0)
        totals['groups_checked'] += stats.get('groups_checked', 0)
        totals['already_ok'] += stats.get('already_ok', 0)

        progress = (
            f"🔄 <b>Staff Sync — Run All</b> (batch {batch_num}/{MAX_BATCHES_RUNALL})\n"
            f"➕ Total invited: {totals['invited']} | 📂 Groups this batch: {stats.get('groups_checked', 0)}\n"
            f"📍 Progress: group #{stats.get('next_group_index', 0)} / {stats.get('group_count', 0)}\n"
        )
        if stats.get('complete'):
            progress += "\n🎉 <b>All groups synced!</b>"
            last_report = progress + "\n" + _format_report(stats)
            break

        if stats.get('stopped_early') and 'rate limit' in str(stats.get('stopped_early', '')).lower():
            last_report = progress + "\n" + _format_report(stats)
            break

        last_report = progress + f"\n⏳ Next batch in {BATCH_PAUSE_SEC}s..."
        try:
            bot.edit_message_text(last_report, chat_id, message_id, parse_mode="HTML")
        except Exception:
            pass

        if stats.get('complete'):
            break

        _time.sleep(BATCH_PAUSE_SEC)
    else:
        last_report = (
            f"⏸ Reached max batches ({MAX_BATCHES_RUNALL}). "
            f"Run <code>/syncstaff runall</code> again to continue.\n\n" + last_report
        )

    log.info(f"Staff sync runall finished: {totals}")
    return last_report
