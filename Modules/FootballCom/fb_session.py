# fb_session.py: fb_session.py: Browser context and anti-detect management.
# Part of LeoBook Modules — Football.com
#
# Functions: cleanup_chrome_processes(), launch_browser_with_retry()

import asyncio
import os
import logging
import subprocess
from pathlib import Path
from playwright.async_api import Playwright, BrowserContext
from Core.Utils.constants import FB_MOBILE_USER_AGENT, FB_MOBILE_VIEWPORT

logger = logging.getLogger(__name__)

async def cleanup_chrome_processes():
    """Automatically terminate conflicting Chrome processes before launch."""
    try:
        if os.name == 'nt':
            subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True)
            print("  [Cleanup] Cleaned up Chrome processes.")
        else:
            subprocess.run(["pkill", "-f", "chrome"], capture_output=True)
            print("  [Cleanup] Cleaned up Chrome processes.")
    except Exception as e:
        print(f"  [Cleanup] Warning: Could not cleanup Chrome processes: {e}")

async def launch_browser_with_retry(playwright: Playwright, user_data_dir: Path, max_retries: int = 3) -> BrowserContext:
    """Launch browser with retry logic and exponential backoff."""
    base_timeout = 60000
    backoff_multiplier = 1.2

    # Auto-detect headless environment
    # Codespace, CI, Docker, or any no-display env = force headless
    _is_headless_env = (
        os.environ.get("CODESPACES") == "true"
        or os.environ.get("CI") == "true"
        or os.environ.get("DISPLAY") is None
        or os.environ.get("LEOBOOK_HEADLESS", "").lower() == "true"
    )
    
    _launch_args = [
        "--no-sandbox", 
        "--disable-dev-shm-usage",
        "--disable-extensions",
        "--disable-infobars",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-service-autorun",
        "--password-store=basic",
        "--new-window"
    ]
    if _is_headless_env:
        _launch_args += [
            "--headless=new",
            "--disable-gpu",
            "--disable-software-rasterizer",
        ]
    
    env_label = "CODESPACE/CI" if _is_headless_env else "LOCAL"
    logger.info(f"[Browser] Environment: {env_label} | Headless: {_is_headless_env}")

    for attempt in range(max_retries):
        timeout = int(base_timeout * (backoff_multiplier ** attempt))
        print(f"  [Launch] Attempt {attempt + 1}/{max_retries} with {timeout}ms timeout...")

        try:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=_is_headless_env,
                args=_launch_args,
                ignore_default_args=["--enable-automation"],
                viewport=FB_MOBILE_VIEWPORT,
                user_agent=FB_MOBILE_USER_AGENT,
                timeout=timeout
            )

            print(f"  [Launch] Browser launched successfully on attempt {attempt + 1}!")
            return context

        except Exception as e:
            print(f"  [Launch] Attempt {attempt + 1} failed: {e}")

            if attempt < max_retries - 1:
                lock_file = user_data_dir / "SingletonLock"
                if lock_file.exists():
                    try:
                        lock_file.unlink()
                        print(f"  [Launch] Removed SingletonLock before retry.")
                    except Exception as lock_e:
                        print(f"  [Launch] Could not remove lock file: {lock_e}")

                wait_time = 2 ** attempt
                print(f"  [Launch] Waiting {wait_time}s before next attempt...")
                await asyncio.sleep(wait_time)
            else:
                print(f"  [Launch] All {max_retries} attempts failed.")
                raise e
