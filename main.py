#
# import asyncio#Python 的异步框架，用来执行异步操作（Playwright 就是异步的）。
# import time
# import sqlite3#轻量级数据库，用来保存已检测过的职位。
# from dotenv import load_dotenv#从 .env 文件加载环境变量（
# from pathlib import Path#处理文件路径，避免手写字符串。
# from playwright.async_api import async_playwright#Playwright 异步版 API。
# from utils import notify_user#自定义模块，负责发送通知（比如 Telegram）。
# from sites.seek_adapter import extract_seek_jobs
#
#
# SEARCH_URL = "https://www.seek.co.nz/jobs?keywords=graduate+developer"
# USER_DATA_DIR = Path(__file__).parent / "user_data"#保存浏览器的用户数据（cookies、缓存等）目录，防止每次启动都被 Cloudflare 验证。
#
# def ensure_user_data_dir() -> bool:
#     """确保 user_data 是个目录；返回是否首次运行（目录为空）
#     检查 user_data 是否存在。
#     如果是文件而不是文件夹 → 抛出异常提示你删除。若不存在则创建。
#     如果目录是空的，说明是第一次运行（first_run=True），否则表示已经保存了 session cookie。"""
#     if USER_DATA_DIR.exists() and not USER_DATA_DIR.is_dir():
#         raise RuntimeError(
#             f"USER_DATA_DIR exists but is not a directory: {USER_DATA_DIR}\n"
#             f"Please delete/rename it, e.g. mv '{USER_DATA_DIR}' '{USER_DATA_DIR}.bak'"
#         )
#     USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
#     # 目录为空 => 首次运行
#     return not any(USER_DATA_DIR.iterdir())
#
# async def get_persistent_context(playwright):
#     """launch_persistent_context() 创建持久化浏览器上下文，能保存登录信息、cookies。
#     headless=False：首次运行时让你看到浏览器窗口（方便手动通过 Cloudflare 验证）。
#     headless=True：之后自动后台运行。
#     slow_mo=50：模拟人类操作速度。
#     user_agent：伪装成真实用户浏览器。"""
#     first_run = ensure_user_data_dir()
#
#     context = await playwright.chromium.launch_persistent_context(
#         user_data_dir=str(USER_DATA_DIR),
#         headless=False if first_run else True,  # 首次可见，之后无头
#         viewport={"width": 1280, "height": 800},
#         user_agent=(
#             "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
#             "AppleWebKit/537.36 (KHTML, like Gecko) "
#             "Chrome/122.0 Safari/537.36"
#         ),
#         slow_mo=50 if first_run else 0,
#     )
#
#     #增加更“人类”的 HTTP 请求头，降低被检测为爬虫的概率。
#     await context.set_extra_http_headers({
#         "Accept-Language": "en-NZ,en;q=0.9",
#         "Referer": "https://www.google.com/",
#     })
#
#     """
#     获取一个可用的 page 对象。
#     如果是第一次运行，打开浏览器窗口：
#         让你手动通过 Cloudflare 验证；
#         加载职位页；
#         等你确认页面加载完后按 Enter。
#     验证完成后 cookies 会自动保存到 user_data/，下次运行就不再需要人工验证。"""
#     page = context.pages[0] if context.pages else await context.new_page()
#     if first_run:
#         print(">>> First run detected. A browser window opened.")
#         print(">>> Complete the Cloudflare check on Seek, wait for job results,")
#         print(">>> then return here and press ENTER.")
#         await page.goto(SEARCH_URL, wait_until="domcontentloaded")
#         input(">>> Press ENTER to continue after results are visible...")
#     return context
#
# # 加载环境变量（.env）
# load_dotenv()
#
# DB_PATH = "db.sqlite3"
# CHECK_INTERVAL = 600  # 每10分钟检查一次（单位：秒）
#
# """打开或创建一个 SQLite 数据库 db.sqlite3；
# 建表：保存职位信息（id、title、link、时间）；
# IF NOT EXISTS 避免重复创建。"""
# # ========= 数据库 =========
# def init_db():
#     """初始化 SQLite 数据库"""
#     conn = sqlite3.connect(DB_PATH)
#     conn.execute("""
#         CREATE TABLE IF NOT EXISTS jobs (
#             job_id TEXT PRIMARY KEY,
#             title TEXT,
#             link TEXT,
#             seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
#         )
#     """)
#     conn.commit()
#     conn.close()
#
#
# def upsert_and_get_new(conn: sqlite3.Connection, jobs: list[dict]) -> list[dict]:
#     """写入未见过的职位，返回本轮新增的职位列表"""
#     new_jobs = []
#     cur = conn.cursor()
#     for j in jobs:
#         job_id = j.get("job_id")
#         if not job_id:
#             continue
#         cur.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,))
#         if cur.fetchone():
#             continue
#         cur.execute(
#             "INSERT INTO jobs(job_id, title, link) VALUES(?,?,?)",
#             (job_id, j.get("title", ""), j.get("link", ""))
#         )
#         new_jobs.append(j)
#     conn.commit()
#     return new_jobs
#
# # ========= 核心监控流程 =========
# async def monitor_seek_jobs(playwright):
#     context = await get_persistent_context(playwright)
#     page = context.pages[0] if context.pages else await context.new_page()
#
#     # 可信会话下访问搜索页
#     await page.goto(SEARCH_URL, wait_until="domcontentloaded")
#
#     # 触发懒加载（滚动几次）
#     for _ in range(6):
#         await page.mouse.wheel(0, 1500)
#         await page.wait_for_timeout(800)
#
#     # 1) 抓取职位
#     jobs = await extract_seek_jobs(page)
#
#     # 2) 去重入库，获得“新职位”
#     conn = sqlite3.connect(DB_PATH)
#     new_jobs = upsert_and_get_new(conn, jobs)
#     conn.close()
#
#     # 3) 通知
#     if new_jobs:
#         print(f"本轮抓取 {len(jobs)} 条，新增 {len(new_jobs)} 条。")
#         for job in new_jobs:
#             notify_user(f"🆕 {job['title']} - {job['company']} ({job['location']})\n🔗 {job['link']}")
#     else:
#         print("No new jobs this cycle.")
#
#     # 持久化上下文会自动保存 cookies
#     await context.close()
#
#
# async def main():
#     """主循环：定时监控 Seek"""
#     init_db()
#
#     while True:
#         print(f"[{time.strftime('%H:%M:%S')}] 开始检测新职位...")
#         async with async_playwright() as playwright:
#             await monitor_seek_jobs(playwright)
#         print(f"等待 {CHECK_INTERVAL // 60} 分钟后再次检查...\n")
#         time.sleep(CHECK_INTERVAL)
#
#
# if __name__ == "__main__":
#     asyncio.run(main())
# main.py
import asyncio
import time
import sqlite3
import os
from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from utils import notify_user
from sites.seek_adapter import extract_seek_jobs

# ========= 环境与配置 =========
load_dotenv()

DB_PATH = "db.sqlite3"
CHECK_INTERVAL = 600  # 每 10 分钟检查一次（秒）
USER_DATA_DIR = Path(__file__).parent / "user_data"

def build_seek_url() -> str:
    """从 .env 读取关键词/地区生成搜索 URL"""
    keywords = os.getenv("SEEK_KEYWORDS", "graduate developer")
    where = os.getenv("SEEK_LOCATION", "")  # 为空则不加 where 参数
    qs = {"keywords": keywords}
    if where:
        qs["where"] = where
    return f"https://www.seek.co.nz/jobs?{urlencode(qs)}"

SEARCH_URL = build_seek_url()


# ========= 持久化浏览器上下文 =========
def ensure_user_data_dir() -> bool:
    """确保 user_data 是目录；返回是否首次运行（目录为空）"""
    if USER_DATA_DIR.exists() and not USER_DATA_DIR.is_dir():
        raise RuntimeError(
            f"USER_DATA_DIR exists but is not a directory: {USER_DATA_DIR}\n"
            f"Please delete/rename it, e.g. mv '{USER_DATA_DIR}' '{USER_DATA_DIR}.bak'"
        )
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return not any(USER_DATA_DIR.iterdir())  # 目录为空 => 首次运行

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


# ========= 数据库 =========
def init_db():
    """初始化 SQLite 数据库（如已存在则忽略）"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            title  TEXT,
            link   TEXT,
            seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def upsert_and_get_new(conn: sqlite3.Connection, jobs: list[dict]) -> list[dict]:
    """写入未见过的职位，返回本轮新增的职位列表"""
    new_jobs = []
    cur = conn.cursor()
    for j in jobs:
        job_id = j.get("job_id")
        if not job_id:
            continue
        cur.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,))
        if cur.fetchone():
            continue
        cur.execute(
            "INSERT INTO jobs(job_id, title, link) VALUES(?,?,?)",
            (job_id, j.get("title", ""), j.get("link", ""))
        )
        new_jobs.append(j)
    conn.commit()
    return new_jobs


# ========= 核心监控流程 =========
async def monitor_seek_jobs(playwright):
    context = await get_persistent_context(playwright)
    page = context.pages[0] if context.pages else await context.new_page()

    # 可信会话下访问搜索页
    await page.goto(SEARCH_URL, wait_until="domcontentloaded")

    # 触发懒加载（滚动几次）
    for _ in range(6):
        await page.mouse.wheel(0, 1500)
        await page.wait_for_timeout(800)

    # 这里加入“健壮性与可观测性”的 try/except
    try:
        jobs = await extract_seek_jobs(page)
    except Exception as e:
        html = await page.content()
        Path("debug_seek.html").write_text(html, encoding="utf-8")
        await page.screenshot(path="debug_seek.png", full_page=True)
        print("Extractor error:", e)
        # 可选：通知一次
        # notify_user(f"❗抓取失败：{e}")
        await context.close()
        return

    # 1) 抓取职位
    jobs = await extract_seek_jobs(page)

    # 2) 去重入库，获得“新职位”
    conn = sqlite3.connect(DB_PATH)
    new_jobs = upsert_and_get_new(conn, jobs)
    conn.close()

    # 3) 通知
    if new_jobs:
        print(f"本轮抓取 {len(jobs)} 条，新增 {len(new_jobs)} 条。")
        for job in new_jobs:
            notify_user(f"🆕 {job['title']} - {job['company']} ({job['location']})\n🔗 {job['link']}")
    else:
        print("No new jobs this cycle.")

    # 持久化上下文会自动保存 cookies
    await context.close()


# ========= 入口 =========
async def main():
    init_db()
    while True:
        print(f"[{time.strftime('%H:%M:%S')}] 开始检测新职位...")
        async with async_playwright() as pw:
            await monitor_seek_jobs(pw)
        print(f"等待 {CHECK_INTERVAL // 60} 分钟后再次检查...\n")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
