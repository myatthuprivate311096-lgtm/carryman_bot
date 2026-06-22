"""
Refresh Telethon user session for /newgroup and /syncstaff.

Run interactively on VPS (SSH terminal):
    python3 telethon_login.py

Optional .env:
    TELEGRAM_PHONE=+959xxxxxxxxx
"""
import os
import sys
import asyncio

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
SESSION_PATH = os.path.join(BASE_DIR, 'carryman')
PHONE_ENV = (os.getenv('TELEGRAM_PHONE') or '').strip()


async def _login(client):
    phone = PHONE_ENV or input('📱 Telegram phone (+959...): ').strip()
    if not phone:
        raise ValueError('Phone number is required.')

    await client.send_code_request(phone)
    code = input('🔑 Telegram code: ').strip()
    try:
        await client.sign_in(phone=phone, code=code)
    except SessionPasswordNeededError:
        password = input('🔒 2FA password: ').strip()
        await client.sign_in(password=password)


async def main():
    if not API_ID or not API_HASH:
        print('❌ API_ID / API_HASH missing in .env')
        sys.exit(1)

    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.connect()

    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f'✅ Already authorized: {me.first_name} (@{me.username or me.id})')
            return

        print('=' * 50)
        print('CarryMan Telethon Login — /newgroup session refresh')
        print('=' * 50)
        await _login(client)
        me = await client.get_me()
        print(f'✅ Session saved: {SESSION_PATH}.session')
        print(f'✅ Logged in as: {me.first_name} (@{me.username or me.id})')
        print('▶️ Retry /newgroup in Telegram (no container restart needed).')
    finally:
        await client.disconnect()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\n👋 Cancelled.')
        sys.exit(0)
