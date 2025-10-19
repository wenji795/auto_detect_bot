
# main.py
import os
import re
import time
import sqlite3
import asyncio
from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv
from playwright.async_api import async_playwright, BrowserContext

from utils import notify_user
from sites.seek_adapter import extract_seek_jobs
from sites.linkedin_adapter import extract_linkedin_jobs
from outputs import append_new_jobs_csv, build_html_from_db


# ========= 环境与配置 =========
load_dotenv()
HEADFUL_FLAG = os.getenv("HEADFUL", "0") == "1"
LOGIN_TARGET = os.getenv("LOGIN_TARGET", "seek").lower()
DB_PATH = "db.sqlite3"
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", "600"))  # 默认 10 分钟
USER_DATA_DIR = Path(__file__).parent / "user_data"

def build_seek_url() -> str:
    """
    从 .env 读取关键词/地区/分类生成 SEEK 搜索 URL。
    支持 OR，例如：graduate OR tester OR QA OR developer
    """
    keywords = os.getenv(
        "SEEK_KEYWORDS",
        "graduate OR tester OR QA OR developer OR junior"
    ).strip()
    where = os.getenv("SEEK_LOCATION", "").strip()
    classification = os.getenv("SEEK_CLASSIFICATION", "").strip()   # e.g. information-communication-technology
    subclass = os.getenv("SEEK_SUBCLASS", "").strip()               # e.g. testing-quality-assurance,developers-programmers

    qs = {"keywords": keywords}
    if where:
        qs["where"] = where
    if classification:
        qs["classification"] = classification
    if subclass:
        qs["subclassification"] = subclass
    return f"https://www.seek.co.nz/jobs?{urlencode(qs)}"

def build_linkedin_url() -> str:
    """
    生成 LinkedIn Jobs 搜索 URL（登录后更稳定）。
    f_TPR: r86400=24h, r604800=7天, r2592000=30天
    """
    keywords = os.getenv(
        "LINKEDIN_KEYWORDS",
        "graduate OR junior OR qa OR tester OR automation OR developer OR software"
    ).strip().replace(" ", "%20")
    location = os.getenv("LINKEDIN_LOCATION", "New Zealand").strip().replace(" ", "%20")
    time_range = os.getenv("LINKEDIN_TIME_RANGE", "r604800")  # 最近 7 天

    return (
        "https://www.linkedin.com/jobs/search/"
        f"?keywords={keywords}&location={location}&f_TPR={time_range}"
    )

SEEK_URL = build_seek_url()
LINKEDIN_URL = build_linkedin_url()


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

async def get_persistent_context(pw) -> BrowserContext:
    first_run = ensure_user_data_dir()
    headless = False if (first_run or HEADFUL_FLAG) else True

    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(USER_DATA_DIR),
        headless=headless,
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0 Safari/537.36"
        ),
        slow_mo=50 if not headless else 0,
    )
    await context.set_extra_http_headers({
        "Accept-Language": "en-NZ,en;q=0.9",
        "Referer": "https://www.google.com/",
    })

    page = context.pages[0] if context.pages else await context.new_page()

    # 只要是首次或你显式要求开窗，就引导登录
    if first_run or HEADFUL_FLAG:
        mode = LOGIN_TARGET  # 'seek' / 'linkedin' / 'both'
        print(f">>> Interactive session. HEADFUL={not headless}, LOGIN_TARGET={mode}")

        if mode == "linkedin":
            await page.goto(LINKEDIN_URL, wait_until="domcontentloaded")
        elif mode == "both":
            # 先开 Seek
            await page.goto(SEEK_URL, wait_until="domcontentloaded")
            # 再开一个标签到 LinkedIn，并切过去
            ln = await context.new_page()
            await ln.goto(LINKEDIN_URL, wait_until="domcontentloaded")
            try:
                await ln.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            page = ln  # 聚焦在 LinkedIn 标签
        else:  # 默认 seek
            await page.goto(SEEK_URL, wait_until="domcontentloaded")

        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        input(">>> 在打开的浏览器里完成登录/验证后，回到这里按 ENTER 继续…")
    return context


# ========= 数据库（含 source 列 & 唯一索引） =========
def init_db():
    """
    初始化或迁移 SQLite：
      - 增加 company/location/source 列
      - 建唯一索引 (source, job_id) 避免平台间冲突
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id   TEXT,
            title    TEXT,
            link     TEXT,
            company  TEXT,
            location TEXT,
            source   TEXT DEFAULT 'seek',
            seen_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 迁移：补列
    cur.execute("PRAGMA table_info(jobs)")
    cols = {row[1] for row in cur.fetchall()}
    if "company" not in cols:
        cur.execute("ALTER TABLE jobs ADD COLUMN company TEXT")
    if "location" not in cols:
        cur.execute("ALTER TABLE jobs ADD COLUMN location TEXT")
    if "source" not in cols:
        cur.execute("ALTER TABLE jobs ADD COLUMN source TEXT DEFAULT 'seek'")
        cur.execute("UPDATE jobs SET source='seek' WHERE source IS NULL OR source=''")
    # 唯一索引（若不存在）
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_source_jobid
        ON jobs(source, job_id)
    """)
    conn.commit()
    conn.close()

async def safe_dump_page(page, html_path: str, png_path: str):
    # 尝试等待一个稳定状态；如果在导航，会抛异常，忽略即可
    for state in ("networkidle", "domcontentloaded", "load"):
        try:
            await page.wait_for_load_state(state, timeout=3000)
            break
        except Exception:
            pass
    # 尝试用 evaluate 拿整页 HTML（比 page.content() 更不容易撞导航错误）
    try:
        html = await page.evaluate("() => document.documentElement.outerHTML")
        Path(html_path).write_text(html or "", encoding="utf-8")
    except Exception:
        pass
    # 截图也做保护
    try:
        await page.screenshot(path=png_path, full_page=True)
    except Exception:
        pass

async def interactive_login_if_needed(pw, context: BrowserContext, target_url: str, label: str = "LinkedIn"):
    """
    如果检测到网站需要登录：
    - 启动一个使用“临时 user_data 目录”的可见浏览器让你登录；
    - 导出 cookies/localStorage；
    - 注入到当前的 headless 持久化 context；
    - 关闭临时浏览器，继续无头抓取。
    """
    page = await context.new_page()
    await page.goto(target_url, wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass

    url_lc = (page.url or "").lower()
    try:
        html_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        html_lc = (html_text or "").lower()
    except Exception:
        html_lc = ""

    # 探测登录表单（LinkedIn：#username 或 name=session_key）
    login_form_present = False
    try:
        login_form_present = (await page.locator('input#username, input[name="session_key"]').count()) > 0
    except Exception:
        pass

    await page.close()

    def needs_login() -> bool:
        return (
            "linkedin.com/login" in url_lc
            or "checkpoint" in url_lc
            or login_form_present
            or ("sign in" in html_lc)
        )

    if not needs_login():
        return context

    print(f"🔐 检测到 {label} 需要登录，开启临时可见浏览器进行登录（不关闭当前上下文）。")

    # 1) 用“临时 user_data 目录”启一个 headful 持久化浏览器，避免与主 profile 冲突
    tmp_dir = USER_DATA_DIR.parent / "user_data_login_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    headful_ctx = await pw.chromium.launch_persistent_context(
        user_data_dir=str(tmp_dir),
        headless=False,
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0 Safari/537.36"
        ),
        slow_mo=50,
    )
    await headful_ctx.set_extra_http_headers({
        "Accept-Language": "en-NZ,en;q=0.9",
        "Referer": "https://www.google.com/",
    })

    hp = headful_ctx.pages[0] if headful_ctx.pages else await headful_ctx.new_page()
    await hp.goto(target_url, wait_until="domcontentloaded")
    try:
        await hp.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass

    print(f">>> 已打开 {label} 登录页，请在浏览器中完成登录，然后回到终端按 ENTER 继续。")
    input(">>> 登录完成后按 ENTER 继续…")

    # 2) 导出 storage_state
    storage = None
    try:
        storage = await headful_ctx.storage_state()
    except Exception:
        storage = None

    # 3) 注入到当前 headless 持久化 context
    if storage:
        # 3a) cookies
        try:
            cookies = storage.get("cookies", [])
            if cookies:
                await context.add_cookies(cookies)
        except Exception:
            pass

        # 3b) localStorage（按 origin 写入）
        try:
            origins = storage.get("origins", [])
            for origin in origins:
                origin_url = origin.get("origin")
                ls_items = origin.get("localStorage", [])
                if not origin_url or not ls_items:
                    continue

                p = await context.new_page()
                # 打开 origin，写入 localStorage
                await p.goto(origin_url, wait_until="domcontentloaded")
                for kv in ls_items:
                    key = kv.get("name", "")
                    val = kv.get("value", "")
                    if key:
                        await p.evaluate(
                            """([k, v]) => { try { localStorage.setItem(k, v); } catch(e){} }""",
                            [key, val],
                        )
                await p.close()
        except Exception:
            pass

    # 关闭临时 headful 浏览器
    try:
        await headful_ctx.close()
    except Exception:
        pass

    print(f"✅ {label} 登录态已注入，无头模式继续。")
    return context


def upsert_and_get_new(conn: sqlite3.Connection, jobs: list[dict], source: str) -> list[dict]:
    """写入未见过的职位（按 source+job_id 去重），返回本轮新增"""
    new_jobs = []
    cur = conn.cursor()
    for j in jobs:
        job_id = j.get("job_id")
        if not job_id:
            continue
        # 查重
        cur.execute("SELECT 1 FROM jobs WHERE source=? AND job_id=?", (source, job_id))
        if cur.fetchone():
            continue
        # 插入
        cur.execute(
            "INSERT INTO jobs(job_id, title, link, company, location, source) VALUES(?,?,?,?,?,?)",
            (job_id, j.get("title",""), j.get("link",""), j.get("company",""), j.get("location",""), source)
        )
        new_jobs.append(j)
    conn.commit()
    return new_jobs


# ========= 过滤：剔除资深/管理岗（可叠加你的 IT 过滤） =========
_EXCLUDE_SENIORITY = re.compile(
    r"\b(senior|intermediate|lead|leader|principal|head of|architect|director|manager)\b",
    re.IGNORECASE
)
def should_keep(job: dict) -> bool:
    title = (job.get("title") or "").lower()
    return not _EXCLUDE_SENIORITY.search(title)


# ========= 统一的写库/通知/落地 =========
def finalize_batch(source: str, grabbed: list[dict]):
    # 过滤
    jobs = [j for j in grabbed if should_keep(j)]
    # 入库
    conn = sqlite3.connect(DB_PATH)
    new_jobs = upsert_and_get_new(conn, jobs, source=source)
    conn.close()
    # 通知 & 输出
    if new_jobs:
        print(f"[{source}] 抓取 {len(jobs)} 条，新增 {len(new_jobs)} 条。")
        for job in new_jobs:
            notify_user(f"🆕 [{source.capitalize()}] {job['title']} — {job['company']} · {job['location']}\n🔗 {job['link']}")
        append_new_jobs_csv(new_jobs)
        html_path = build_html_from_db(DB_PATH, limit=100)
        print(f"📄 已生成 HTML 列表：{html_path}")
    else:
        print(f"[{source}] No new jobs this cycle.")


# ========= 两个平台的 monitor =========
async def monitor_seek(context: BrowserContext):
    page = context.pages[0] if context.pages else await context.new_page()
    try:
        await page.goto(SEEK_URL, wait_until="domcontentloaded")
        for _ in range(6):
            await page.mouse.wheel(0, 1500)
            await page.wait_for_timeout(800)
        jobs = await extract_seek_jobs(page)
        finalize_batch("seek", jobs)
    except Exception as e:
        try:
            Path("debug_seek.html").write_text(await page.content(), encoding="utf-8")
            await page.screenshot(path="debug_seek.png", full_page=True)
        except Exception:
            pass
        print("❗ SEEK monitor error:", e)

async def monitor_linkedin(context):
    page = await context.new_page()
    try:
        await page.goto(LINKEDIN_URL, wait_until="domcontentloaded")
        # 兜底：如果被重定向到 feed，强制回到搜索页
        if "linkedin.com/feed" in (page.url or "").lower():
            await page.goto(LINKEDIN_URL, wait_until="domcontentloaded")

        # 尝试接受 cookie / 同意按钮（国际化兜底）
        for sel in [
            'button:has-text("Accept")',
            'button:has-text("同意")',
            'button:has-text("Agree")',
            'button[aria-label*="Accept"]',
            'button[aria-label*="accept"]'
        ]:
            try:
                await page.locator(sel).first.click(timeout=2000)
                break
            except Exception:
                pass

        # 等一等让 SPA 稳定
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        # 轻微滚动触发懒加载
        for _ in range(4):
            await page.mouse.wheel(0, 1200)
            await page.wait_for_timeout(600)

        jobs = await extract_linkedin_jobs(page)
        finalize_batch("linkedin", jobs)

    except Exception as e:
        await safe_dump_page(page, "debug_linkedin.html", "debug_linkedin.png")
        print("❗ LinkedIn monitor error:", e)
    finally:
        await page.close()



# ========= 入口 =========
async def main():
    init_db()
    while True:
        print(f"[{time.strftime('%H:%M:%S')}] 开始检测新职位...")
        async with async_playwright() as pw:
            context = await get_persistent_context(pw)
            # 一轮跑两个站点
            await monitor_seek(context)
            context = await interactive_login_if_needed(pw, context, LINKEDIN_URL, label="LinkedIn")
            await monitor_linkedin(context)

            # 保存登录态以便其它脚本复用
            try:
                await context.storage_state(path="storage_state.json")
            except Exception:
                pass
            await context.close()

        print(f"等待 {CHECK_INTERVAL // 60} 分钟后再次检查...\n")
        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
