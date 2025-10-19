# sites/seek_adapter.py
from playwright.async_api import Page
import re

def _extract_job_id(link: str) -> str | None:
    # 适配 SEEK 链接中的数字 id；必要时根据 debug_seek.html 调整
    m = re.search(r"/job/(\d+)", link)
    return m.group(1) if m else link  # 退化为链接充当 id

async def extract_seek_jobs(page: Page):
    # 1) 兜底等待：只要有任意 job card 的标题链接就开始解析
    await page.wait_for_load_state("domcontentloaded")
    for _ in range(8):  # 懒加载滚动
        await page.mouse.wheel(0, 1800)
        await page.wait_for_timeout(900)

    # 2) 用较稳的选择器抓链接（data-automation 常见；若失效用 a[href*="/job/"] 兜底）
    anchors = await page.query_selector_all("a[data-automation='jobTitle'], a[href*='/job/']")
    jobs = []
    for a in anchors:
        title = (await a.inner_text()).strip()
        href = await a.get_attribute("href")
        if not href or not title:
            continue

        # 定位父级卡片，再取公司/地点（两套选择器以防结构差异）
        card = await a.evaluate_handle("el => el.closest('article') || el.closest('[data-automation]')")
        company_el = await card.query_selector("span[data-automation='jobCompany'], [data-testid='job-company']")
        location_el = await card.query_selector("strong[data-automation='jobLocation'], [data-testid='job-location']")

        company = (await company_el.inner_text()).strip() if company_el else "Unknown"
        location = (await location_el.inner_text()).strip() if location_el else "Unknown"
        full_link = href if href.startswith("http") else f"https://www.seek.co.nz{href}"

        jobs.append({
            "job_id": _extract_job_id(full_link),
            "title": title,
            "company": company,
            "location": location,
            "link": full_link,
        })
    return jobs
