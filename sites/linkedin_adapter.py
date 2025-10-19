# sites/linkedin_adapter.py
from __future__ import annotations
from playwright.async_api import Page
from typing import Optional, List, Dict
from urllib.parse import urljoin, urlparse, parse_qs
import re
from pathlib import Path

# =============== 小工具 ===============
def _norm(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = " ".join(s.split())
    return s or None

def _job_id_from_link(href: str) -> str:
    """从 /jobs/view/123456789/ 或 ?currentJobId=123456 提取 job_id"""
    if not href:
        return ""
    try:
        u = urlparse(href)
        qs = parse_qs(u.query or "")
        jid = qs.get("currentJobId", [None])[0]
        if jid:
            return jid
    except Exception:
        pass
    m = re.search(r"/jobs/view/(\d+)", href)
    return m.group(1) if m else (href or "")

async def _text_first(root, selectors: List[str]) -> Optional[str]:
    for sel in selectors:
        el = await root.query_selector(sel)
        if el:
            try:
                t = await el.inner_text()
                t = _norm(t)
                if t:
                    return t
            except Exception:
                pass
    return None

# 只要页面里出现“查看职位”的链接就算有卡片
JOB_LINK_SEL = 'a[href*="/jobs/view/"]'


# =============== 适配器 ===============
async def extract_linkedin_jobs(page: Page) -> List[Dict]:
    """
    更稳健的 LinkedIn 列表抓取：
    - 不依赖固定的 ul.jobs-search__results-list
    - 以 a[href*="/jobs/view/"] 为基准抓取
    - 处理懒加载/弹窗/无结果
    """
    # 避免用 page.content()（导航期易报错），改为 evaluate 读取可见文本
    url_lc = (page.url or "").lower()
    try:
        html_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        html_lc = (html_text or "").lower()
    except Exception:
        html_lc = ""

    # 登录/风控页检测
    if ("linkedin.com/checkpoint" in url_lc) or ("linkedin.com/login" in url_lc) or ("sign in" in html_lc):
        print("🔐 LinkedIn 需要登录或通过检查。请先在该上下文完成登录。")
        return []

    # 等“有卡片或无结果”任一成立
    try:
        await page.wait_for_function(
            """
            sel => {
              const hasCards = document.querySelectorAll(sel).length > 0;
              const noRes = !!document.querySelector('section.jobs-search__no-results, div.jobs-search-two-pane__no-results');
              return hasCards || noRes;
            }
            """,
            arg=JOB_LINK_SEL,
            timeout=30000
        )
    except Exception:
        # 再缓一缓
        await page.wait_for_timeout(1200)

    # 轻微滚动触发懒加载
    for _ in range(3):
        await page.mouse.wheel(0, 1000)
        await page.wait_for_timeout(400)

    # 第一次抓
    links = await page.query_selector_all(JOB_LINK_SEL)
    # 若还少，再滚几屏
    if not links:
        for _ in range(6):
            await page.mouse.wheel(0, 1400)
            await page.wait_for_timeout(500)
            links = await page.query_selector_all(JOB_LINK_SEL)
            if links:
                break

    jobs: List[Dict] = []
    seen_ids: set[str] = set()

    # 保存前几张卡片 HTML 便于排查
    Path("debug_cards").mkdir(exist_ok=True)

    for idx, a in enumerate(links[:6], 1):
        try:
            outer = await a.evaluate("el => (el.closest('li, .base-card, .job-card-container, .jobs-search-results__list-item') || el).outerHTML")
            Path(f"debug_cards/ln_card_{idx}.html").write_text(outer or "", encoding="utf-8")
        except Exception:
            pass

    for a in links:
        try:
            href = await a.get_attribute("href") or ""
            if not href:
                continue

            # job_id
            m = re.search(r"/jobs/view/(\d+)", href)
            if not m:
                continue
            job_id = m.group(1)
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            # 标题（链接文本）
            try:
                title = _norm(await a.inner_text()) or "Unknown title"
            except Exception:
                title = "Unknown title"

            # 找到卡片容器（向上找常见容器）
            card_js = await a.evaluate_handle(
                "el => el.closest('li, .base-card, .job-card-container, .jobs-search-results__list-item') || el"
            )
            card = card_js.as_element()

            # 公司
            company = "Unknown"
            if card:
                company = await _text_first(card, [
                    ".job-card-container__company-name",
                    ".base-search-card__subtitle a",
                    ".base-search-card__subtitle",
                    ".base-card__subtitle a",
                    ".base-card__subtitle",
                    ".artdeco-entity-lockup__subtitle a",
                    ".artdeco-entity-lockup__subtitle span",
                ]) or "Unknown"

            # 地点
            location = "Unknown"
            if card:
                location = await _text_first(card, [
                    ".job-card-container__metadata-item--location",
                    ".job-card-container__metadata-item",
                    ".base-search-card__metadata > span",
                    ".base-card__metadata > span",
                ]) or "Unknown"

            # 规范化链接（去掉追踪参数）
            link = href.split("?")[0]
            if not link.startswith("http"):
                link = urljoin("https://www.linkedin.com", link)

            jobs.append({
                "job_id": job_id,
                "title": title,
                "company": company,
                "location": location,
                "link": link,
            })
        except Exception:
            # 单个卡片失败不影响整体
            continue

    return jobs
