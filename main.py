


import asyncio
import os
import time
import sqlite3
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from utils import notify_user
from sites.seek_adapter import extract_seek_jobs

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
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context()
    page = await context.new_page()

    new_jobs = await extract_seek_jobs(page)  # ✅ 修改这里

    if new_jobs:
        for job in new_jobs:
            msg = f"🆕 新职位：{job['title']} - {job['company']} ({job['location']})\n🔗 {job['link']}"
            notify_user(msg)
    else:
        print(f"[{time.strftime('%H:%M:%S')}] 无新职位。")

    await browser.close()


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
