


import asyncio
import os
import time
import sqlite3
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from utils import notify_user
from sites.seek_adapter import extract_seek_jobs
# --- add near the top of main.py ---
import os
from pathlib import Path
from playwright.async_api import async_playwright
from utils import notify_user
from sites.seek_adapter import extract_seek_jobs


SEARCH_URL = "https://www.seek.co.nz/jobs?keywords=graduate+developer"
USER_DATA_DIR = Path(__file__).parent / "user_data"

def ensure_user_data_dir() -> bool:
    """确保 user_data 是个目录；返回是否首次运行（目录为空）"""
    if USER_DATA_DIR.exists() and not USER_DATA_DIR.is_dir():
        raise RuntimeError(
            f"USER_DATA_DIR exists but is not a directory: {USER_DATA_DIR}\n"
            f"Please delete/rename it, e.g. mv '{USER_DATA_DIR}' '{USER_DATA_DIR}.bak'"
        )
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 目录为空 => 首次运行
    return not any(USER_DATA_DIR.iterdir())

async def get_persistent_context(playwright):
    first_run = ensure_user_data_dir()

    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(USER_DATA_DIR),
        headless=False if first_run else True,  # 首次可见，之后无头
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0 Safari/537.36"
        ),
        slow_mo=50 if first_run else 0,
    )

    await context.set_extra_http_headers({
        "Accept-Language": "en-NZ,en;q=0.9",
        "Referer": "https://www.google.com/",
    })

    page = context.pages[0] if context.pages else await context.new_page()

    if first_run:
        print(">>> First run detected. A browser window opened.")
        print(">>> Complete the Cloudflare check on Seek, wait for job results,")
        print(">>> then return here and press ENTER.")
        await page.goto(SEARCH_URL, wait_until="domcontentloaded")
        input(">>> Press ENTER to continue after results are visible...")
    return context

# 加载环境变量（.env）
load_dotenv()

DB_PATH = "db.sqlite3"
CHECK_INTERVAL = 600  # 每10分钟检查一次（单位：秒）


def init_db():
    """初始化 SQLite 数据库"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            title TEXT,
            link TEXT,
            seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


async def monitor_seek_jobs(playwright):
    context = await get_persistent_context(playwright)
    page = context.pages[0] if context.pages else await context.new_page()

    # Now that the session is trusted, navigate and extract
    await page.goto(SEARCH_URL, wait_until="domcontentloaded")

    # Optional: a little scroll to trigger lazy-load
    for _ in range(6):
        await page.mouse.wheel(0, 1500)
        await page.wait_for_timeout(800)

    new_jobs = await extract_seek_jobs(page)  # your current adapter function

    if new_jobs:
        for job in new_jobs:
            notify_user(f"🆕 {job['title']} - {job['company']} ({job['location']})\n🔗 {job['link']}")
    else:
        print("No new jobs this cycle.")

    # Important: persistent context saves cookies automatically on close
    await context.close()



async def main():
    """主循环：定时监控 Seek"""
    init_db()

    while True:
        print(f"[{time.strftime('%H:%M:%S')}] 开始检测新职位...")
        async with async_playwright() as playwright:
            await monitor_seek_jobs(playwright)
        print(f"等待 {CHECK_INTERVAL // 60} 分钟后再次检查...\n")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
