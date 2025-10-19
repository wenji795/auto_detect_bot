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


# =============== 适配器 ===============
async def extract_linkedin_jobs(page: Page) -> List[Dict]:
    """
    适配 LinkedIn Jobs 列表页：
    目标 URL 形如：
      https://www.linkedin.com/jobs/search/?keywords=qa%20OR%20tester&location=New%20Zealand&f_TPR=r86400
    建议：先在外部构造好搜索 URL，然后 page.goto(url) 后再调用本函数。
    """
    # 登录/风控页检测（需要你先手动登录一次）
    url_lc = (page.url or "").lower()
    html = (await page.content()).lower()
    if "linkedin.com/checkpoint" in url_lc or "linkedin.com/login" in url_lc or "sign in" in html:
        print("🔐 LinkedIn 需要登录或通过检查。请先在该上下文完成登录。")
        return []

    # 等待列表渲染
    await page.wait_for_selector("ul.jobs-search__results-list li", timeout=30000)

    # 无限滚动，尽量加载更多项
    for _ in range(10):
        await page.mouse.wheel(0, 2200)
        # “显示更多”按钮（偶尔出现）
        btn = await page.query_selector("button.infinite-scroller__show-more-button, button[aria-label*='Show more']")
        if btn:
            try:
                await btn.click()
            except Exception:
                pass
        await page.wait_for_timeout(700)

    # 抓取卡片
    cards = await page.query_selector_all("ul.jobs-search__results-list li")
    jobs: List[Dict] = []

    # 保存前几张卡片 HTML 便于排查
    Path("debug_cards").mkdir(exist_ok=True)
    for i, c in enumerate(cards[:6], 1):
        try:
            outer = await c.evaluate("el => el.outerHTML")
            Path(f"debug_cards/ln_card_{i}.html").write_text(outer, encoding="utf-8")
        except Exception:
            pass

    for card in cards:
        # 标题 + 链接（多重兜底）
        title_el = await card.query_selector(
            "a[data-control-name='job_card_title'], a.job-card-list__title, a[data-ember-action][href*='/jobs/view/']"
        )
        if not title_el:
            # 继续下一卡
            continue

        title = _norm(await title_el.inner_text()) or "Unknown title"
        href = await title_el.get_attribute("href")
        # href 可能是相对路径
        link = urljoin("https://www.linkedin.com", href or "")

        # 公司名（常见选择器）
        company = await _text_first(card, [
            "a[data-control-name='company_name']",
            "span.job-card-container__primary-description",
            "[data-test-reusables-job-card__company-name]",
            ".artdeco-entity-lockup__subtitle a",
            ".artdeco-entity-lockup__subtitle span",
        ]) or "Unknown"

        # 地点（常见选择器）
        location = await _text_first(card, [
            ".job-card-container__metadata-item--location",
            ".job-card-container__metadata-item",
            "[data-test-reusables-job-card__listdate] ~ span",  # 偶尔位置在时间之后
        ]) or "Unknown"

        # job id
        job_id = _job_id_from_link(link)

        jobs.append({
            "job_id": job_id,
            "title": title,
            "company": company,
            "location": location,
            "link": link
        })

    return jobs
