#
# # main.py
# import asyncio
# import time
# import sqlite3
# import os
# from pathlib import Path
# from urllib.parse import urlencode
#
# from dotenv import load_dotenv
# from playwright.async_api import async_playwright
#
# from utils import notify_user
# from sites.seek_adapter import extract_seek_jobs
# from outputs import append_new_jobs_csv, build_html_from_db
#
#
# # ========= 环境与配置 =========
# load_dotenv()
#
# DB_PATH = "db.sqlite3"
# CHECK_INTERVAL = 600  # 每 10 分钟检查一次（秒）
# USER_DATA_DIR = Path(__file__).parent / "user_data"
#
# def build_seek_url() -> str:
#     """从 .env 读取关键词/地区生成搜索 URL"""
#     keywords = os.getenv(
#         "SEEK_KEYWORDS",
#         "graduate OR tester OR QA OR developer OR marketing OR sales OR junior"
#     )
#     where = os.getenv("SEEK_LOCATION", "")  # 为空则不加 where 参数
#     qs = {"keywords": keywords}
#     if where:
#         qs["where"] = where
#     return f"https://www.seek.co.nz/jobs?{urlencode(qs)}"
#
# SEARCH_URL = build_seek_url()
#
#
# # ========= 持久化浏览器上下文 =========
# def ensure_user_data_dir() -> bool:
#     """确保 user_data 是目录；返回是否首次运行（目录为空）"""
#     if USER_DATA_DIR.exists() and not USER_DATA_DIR.is_dir():
#         raise RuntimeError(
#             f"USER_DATA_DIR exists but is not a directory: {USER_DATA_DIR}\n"
#             f"Please delete/rename it, e.g. mv '{USER_DATA_DIR}' '{USER_DATA_DIR}.bak'"
#         )
#     USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
#     return not any(USER_DATA_DIR.iterdir())  # 目录为空 => 首次运行
#
# async def get_persistent_context(playwright):
#     first_run = ensure_user_data_dir()
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
#     await context.set_extra_http_headers({
#         "Accept-Language": "en-NZ,en;q=0.9",
#         "Referer": "https://www.google.com/",
#     })
#
#     page = context.pages[0] if context.pages else await context.new_page()
#
#     if first_run:
#         print(">>> First run detected. A browser window opened.")
#         print(">>> Complete the Cloudflare check on Seek, wait for job results,")
#         print(">>> then return here and press ENTER.")
#         await page.goto(SEARCH_URL, wait_until="domcontentloaded")
#         input(">>> Press ENTER to continue after results are visible...")
#     return context
#
#
# # ========= 数据库 =========
# def init_db():
#     """初始化 SQLite 数据库（如已存在则忽略）"""
#     conn = sqlite3.connect(DB_PATH)
#     conn.execute("""
#         CREATE TABLE IF NOT EXISTS jobs (
#             job_id TEXT PRIMARY KEY,
#             title  TEXT,
#             link   TEXT,
#             seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
#         )
#     """)
#     conn.commit()
#     conn.close()
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
#     # 这里加入“健壮性与可观测性”的 try/except
#     try:
#         jobs = await extract_seek_jobs(page)
#     except Exception as e:
#         html = await page.content()
#         Path("debug_seek.html").write_text(html, encoding="utf-8")
#         await page.screenshot(path="debug_seek.png", full_page=True)
#         print("Extractor error:", e)
#         # 可选：通知一次
#         # notify_user(f"❗抓取失败：{e}")
#         await context.close()
#         return
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
#         # (b) 追加写 CSV（outputs/new_jobs.csv）
#         append_new_jobs_csv(new_jobs)
#
#         # (c) 生成 HTML（outputs/latest.html）
#         html_path = build_html_from_db(DB_PATH, limit=100)
#         print(f"📄 已生成 HTML 列表：{html_path}")
#
#     else:
#         print("No new jobs this cycle.")
#
#     # 持久化上下文会自动保存 cookies
#     await context.close()
#
#
# # ========= 入口 =========
# async def main():
#     init_db()
#     while True:
#         print(f"[{time.strftime('%H:%M:%S')}] 开始检测新职位...")
#         async with async_playwright() as pw:
#             await monitor_seek_jobs(pw)
#         print(f"等待 {CHECK_INTERVAL // 60} 分钟后再次检查...\n")
#         time.sleep(CHECK_INTERVAL)
#
# if __name__ == "__main__":
#     asyncio.run(main())


# main.py
import os
import re
import time
import sqlite3
import asyncio
from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from utils import notify_user
from sites.seek_adapter import extract_seek_jobs
from outputs import append_new_jobs_csv, build_html_from_db


# ========= 环境与配置 =========
load_dotenv()

DB_PATH = "db.sqlite3"
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", "600"))  # 默认 10 分钟
USER_DATA_DIR = Path(__file__).parent / "user_data"

def build_seek_url() -> str:
    """
    从 .env 读取关键词/地区生成搜索 URL。
    支持 OR，例如：graduate OR tester OR QA OR developer
    """
    keywords = os.getenv(
        "SEEK_KEYWORDS",
        "graduate OR tester OR QA OR developer OR junior"
    )
    where = os.getenv("SEEK_LOCATION", "").strip()  # 为空则不加 where
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


# ========= 数据库：含自动迁移 company/location =========
def init_db():
    """初始化或迁移 SQLite：确保有 company/location 两列"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # 创建（若不存在）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id   TEXT PRIMARY KEY,
            title    TEXT,
            link     TEXT,
            company  TEXT,
            location TEXT,
            seen_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 迁移老表结构
    cur.execute("PRAGMA table_info(jobs)")
    cols = {row[1] for row in cur.fetchall()}
    if "company" not in cols:
        cur.execute("ALTER TABLE jobs ADD COLUMN company TEXT")
    if "location" not in cols:
        cur.execute("ALTER TABLE jobs ADD COLUMN location TEXT")
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
            "INSERT INTO jobs(job_id, title, link, company, location) VALUES(?,?,?,?,?)",
            (job_id, j.get("title",""), j.get("link",""), j.get("company",""), j.get("location",""))
        )
        new_jobs.append(j)
    conn.commit()
    return new_jobs


# ========= 过滤：剔除资深/管理岗 =========
_EXCLUDE_PAT = re.compile(
    r"\b(senior|intermediate|lead|leader|principal|head of|architect|director|manager)\b",
    re.IGNORECASE
)
def should_keep(job: dict) -> bool:
    title = (job.get("title") or "").lower()
    return not _EXCLUDE_PAT.search(title)


# ========= 核心监控流程 =========
async def monitor_seek_jobs(playwright):
    context = await get_persistent_context(playwright)
    page = context.pages[0] if context.pages else await context.new_page()

    try:
        # 搜索页
        await page.goto(SEARCH_URL, wait_until="domcontentloaded")

        # 懒加载滚动
        for _ in range(6):
            await page.mouse.wheel(0, 1500)
            await page.wait_for_timeout(800)

        # 抓取
        jobs = await extract_seek_jobs(page)

        # 过滤掉 Senior/Intermediate/Lead…
        jobs = [j for j in jobs if should_keep(j)]

        # 入库去重
        conn = sqlite3.connect(DB_PATH)
        new_jobs = upsert_and_get_new(conn, jobs)
        conn.close()

        # 通知 + 落地
        if new_jobs:
            print(f"本轮抓取 {len(jobs)} 条，新增 {len(new_jobs)} 条。")
            for job in new_jobs:
                notify_user(f"🆕 {job['title']} — {job['company']} · {job['location']}\n🔗 {job['link']}")
            # 追加 CSV
            append_new_jobs_csv(new_jobs)
            # 生成 HTML
            html_path = build_html_from_db(DB_PATH, limit=100)
            print(f"📄 已生成 HTML 列表：{html_path}")
        else:
            print("No new jobs this cycle.")

    except Exception as e:
        # 方便排查
        try:
            Path("debug_seek.html").write_text(await page.content(), encoding="utf-8")
            await page.screenshot(path="debug_seek.png", full_page=True)
        except Exception:
            pass
        print("❗ Extractor/monitor error:", e)
    finally:
        # 可选：保存会话状态（便于其它脚本复用）
        try:
            await context.storage_state(path="storage_state.json")
        except Exception:
            pass
        await context.close()


# ========= 入口 =========
async def main():
    init_db()
    while True:
        print(f"[{time.strftime('%H:%M:%S')}] 开始检测新职位...")
        async with async_playwright() as pw:
            await monitor_seek_jobs(pw)
        print(f"等待 {CHECK_INTERVAL // 60} 分钟后再次检查...\n")
        # 用异步 sleep，避免阻塞
        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
