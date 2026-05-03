import threading
import asyncio
import queue
from playwright.async_api import async_playwright
from logger import log
import os

class BrowserManager:
    def __init__(self):
        self.task_queue = queue.Queue()
        self.loop = None
        self.browser = None
        self.playwright = None
        self._max_tabs = 2
        self._semaphore = None
        self._ready_event = threading.Event() # Browser အဆင်သင့်ဖြစ်မှုကို စောင့်ရန်
        self.thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self.thread.start()

    def _run_event_loop(self):
        """Dedicated thread for Playwright async loop"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._start_browser_internal())
        self.loop.run_forever()

    async def _start_browser_internal(self):
        log.info("🌐 Starting shared Playwright (Async) instance...")
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-setuid-sandbox"
                ]
            )
            self._semaphore = asyncio.Semaphore(self._max_tabs)
            self._ready_event.set() # အောင်မြင်စွာ စတင်ပြီးကြောင်း အချက်ပြမည်
            log.info("✅ Shared Playwright instance is ready.")
        except Exception as e:
            log.error(f"❌ Failed to start browser: {e}")

    def run_task(self, coro_func, *args, **kwargs):
        """
        Synchronous wrapper to run async tasks in the browser thread.
        """
        # Browser အဆင်သင့်ဖြစ်သည်အထိ စောင့်မည် (Timeout 30s)
        if not self._ready_event.wait(timeout=30):
            raise Exception("Browser initialization timed out.")

        future = asyncio.run_coroutine_threadsafe(
            self._execute_with_resource_guard(coro_func, *args, **kwargs),
            self.loop
        )
        # 💡 Safety Timeout: 5 minutes max for any browser task
        try:
            return future.result(timeout=300)
        except TimeoutError:
            log.error(f"❌ Browser task timed out after 5 minutes: {coro_func.__name__}")
            raise Exception("Browser task timed out.")

    async def _execute_with_resource_guard(self, coro_func, storage_state=None, *args, **kwargs):
        """Wait for semaphore and execute the task in a new tab"""
        async with self._semaphore:
            active_tabs = self._max_tabs - self._semaphore._value
            log.info(f"📑 Opening new tab. (Resource Guard: {active_tabs}/{self._max_tabs})")
            
            context = await self.browser.new_context(storage_state=storage_state)
            page = await context.new_page()
            try:
                result = await coro_func(page, *args, **kwargs)
                if kwargs.get('save_state_path'):
                    await context.storage_state(path=kwargs.get('save_state_path'))
                return result
            finally:
                await context.close()
                active_tabs = self._max_tabs - self._semaphore._value
                log.info(f"🗑️ Tab closed. (Resource Guard: {active_tabs}/{self._max_tabs})")

    def shutdown(self):
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._stop_browser_internal(), self.loop)

    async def _stop_browser_internal(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        log.info("🛑 Shared Playwright instance stopped.")
        self.loop.stop()

# Global instance
browser_manager = BrowserManager()
