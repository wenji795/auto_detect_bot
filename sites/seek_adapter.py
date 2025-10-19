# sites/seek_adapter.py
from playwright.async_api import Page
import asyncio

async def extract_seek_jobs(page: Page):
    url = "https://www.seek.co.nz/jobs?keywords=graduate+developer"
    print(f"访问 {url}")
    await page.goto(url, wait_until="domcontentloaded")

    # 模拟人类滚动，触发动态加载
    print("正在滚动加载职位列表...")
    for i in range(8):  # 滚动多次加载更多内容
        await page.mouse.wheel(0, 2000)
        await page.wait_for_timeout(1500)

    # 重新等待可能加载出来的元素
    try:
        await page.wait_for_selector("a[data-automation='jobTitle']", timeout=20000)
    except Exception:
        print("⚠️ 页面加载超时，保存 HTML 以调试。")
        html = await page.content()
        with open("debug_seek.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("已保存 debug_seek.html，请打开搜索 jobTitle 检查。")
        return []

    job_cards = await page.query_selector_all("a[data-automation='jobTitle']")
    jobs = []

    print(f"共发现 {len(job_cards)} 个职位卡片。")

    for job in job_cards:
        title = (await job.inner_text()).strip()
        link = await job.get_attribute("href")
        # 获取父级节点找公司名
        parent = await job.evaluate_handle("el => el.closest('article')")
        company_el = await parent.query_selector("span[data-automation='jobCompany']")
        company = (await company_el.inner_text()).strip() if company_el else "Unknown"

        location_el = await parent.query_selector("strong[data-automation='jobLocation']")
        location = (await location_el.inner_text()).strip() if location_el else "Unknown"

        jobs.append({
            "title": title,
            "company": company,
            "location": location,
            "link": f"https://www.seek.co.nz{link}" if link else None
        })

    return jobs
