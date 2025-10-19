#
# # sites/seek_adapter.py
# from __future__ import annotations
# from playwright.async_api import Page
# from typing import Optional, Dict, Any
# from pathlib import Path
# import re, json
#
# # ---------- 小工具 ----------
# def _norm(s: Optional[str]) -> Optional[str]:
#     if not s:
#         return None
#     s = " ".join(s.split())
#     return s or None
#
# def _job_id_from_url(url: str) -> str:
#     m = re.search(r"/job/(\d+)", url or "")
#     return m.group(1) if m else (url or "")
#
# async def _text_first(handle, selectors) -> Optional[str]:
#     for sel in selectors:
#         el = await handle.query_selector(sel)
#         if el:
#             try:
#                 t = await el.inner_text()
#                 t = _norm(t)
#                 if t:
#                     return t
#             except Exception:
#                 pass
#     return None
#
# def _parse_company_location_from_label(text: str) -> tuple[Optional[str], Optional[str]]:
#     if not text:
#         return None, None
#     t = _norm(text) or ""
#     # "... at COMPANY – LOCATION"
#     m = re.search(r"\bat\s+(.+?)\s+[–-]\s+(.+)$", t, flags=re.I)
#     if m:
#         return _norm(m.group(1)), _norm(m.group(2))
#     # "... at COMPANY in LOCATION"
#     m = re.search(r"\bat\s+(.+?)\s+in\s+(.+)$", t, flags=re.I)
#     if m:
#         return _norm(m.group(1)), _norm(m.group(2))
#     return None, None
#
# def _safe_json(txt: str):
#     try:
#         return json.loads(txt)
#     except Exception:
#         return None
#
# # ---------- 详情页兜底：优先 JSON-LD ----------
# async def _fetch_from_detail(context, link: str) -> tuple[Optional[str], Optional[str]]:
#     page = await context.new_page()
#     try:
#         await page.goto(link, wait_until="domcontentloaded")
#         # JSON-LD (JobPosting)
#         scripts = await page.query_selector_all('script[type="application/ld+json"]')
#         for s in scripts:
#             data = _safe_json(await s.inner_text())
#             if not data:
#                 continue
#             items = data if isinstance(data, list) else [data]
#             for it in items:
#                 if isinstance(it, dict) and it.get("@type") in ("JobPosting", "Posting", "Job"):
#                     company = None
#                     org = it.get("hiringOrganization") or {}
#                     if isinstance(org, dict):
#                         company = org.get("name") or (org.get("identifier") or {}).get("name")
#
#                     location = None
#                     jl = it.get("jobLocation")
#                     if isinstance(jl, list) and jl:
#                         jl = jl[0]
#                     if isinstance(jl, dict):
#                         addr = jl.get("address") or {}
#                         if isinstance(addr, dict):
#                             location = addr.get("addressLocality") or addr.get("addressRegion") or addr.get("addressCountry")
#
#                     if company or location:
#                         return _norm(company), _norm(location)
#
#         # 可见元素兜底
#         company = await _text_first(page, [
#             "[data-automation='advertiser-name']",
#             "[data-testid='advertiser-name']",
#             "a[href*='company-profile']",
#         ])
#         location = await _text_first(page, [
#             "[data-automation='job-detail-location']",
#             "[data-testid='job-detail-location']",
#             "li:has(span:has-text('Location')) + li",
#         ])
#         return _norm(company), _norm(location)
#     finally:
#         await page.close()
#
# # ---------- 主函数 ----------
# async def extract_seek_jobs(page: Page):
#     # 1) 页面加载 + 懒加载滚动
#     await page.wait_for_load_state("domcontentloaded")
#     for _ in range(8):
#         await page.mouse.wheel(0, 1800)
#         await page.wait_for_timeout(700)
#
#     # 2) 标题/链接：兼容两种写法（h3 a 或 data-automation）
#     title_selectors = [
#         "h3 a",
#         "a[data-automation='jobTitle']",
#         "a[href*='/job/']"  # 兜底
#     ]
#     anchors = []
#     seen = set()
#     for sel in title_selectors:
#         found = await page.query_selector_all(sel)
#         for a in found:
#             href = await a.get_attribute("href")
#             if not href:
#                 continue
#             full = href if href.startswith("http") else f"https://www.seek.co.nz{href}"
#             if full in seen:
#                 continue
#             seen.add(full)
#             anchors.append(a)
#
#     # 3) 保存前几张卡片的 outerHTML，便于核对
#     Path("debug_cards").mkdir(exist_ok=True)
#     for i, a in enumerate(anchors[:8], 1):
#         try:
#             outer = await a.evaluate("el => (el.closest('article') || el.closest('div') || el).outerHTML")
#             Path(f"debug_cards/card_{i}.html").write_text(outer, encoding="utf-8")
#         except Exception:
#             pass
#
#     jobs = []
#     detail_budget = 6  # 每轮最多开 6 个详情页兜底，别太激进以免触发风控
#
#     for a in anchors:
#         href = await a.get_attribute("href")
#         if not href:
#             continue
#         link = href if href.startswith("http") else f"https://www.seek.co.nz{href}"
#         title = _norm(await a.inner_text()) or "Unknown title"
#         jid = _job_id_from_url(link)
#
#         # 4) 就近找到卡片容器（你的截图显示 class 名是哈希，故用模糊）
#         card = await a.evaluate_handle("""
#             el => el.closest('div[class*="_1krzhmf0"]')
#                || el.closest('[data-testid*="job-card"]')
#                || el.closest('[data-automation*="job"]')
#                || el.closest('article')
#                || el.parentElement
#         """)
#
#         # 5) 公司：你截图里的写法是 <a data-automation="jobCompany">Halter</a>
#         company = await _text_first(card, [
#             "a[data-automation='jobCompany']",
#             "[data-type='company']",
#             "[data-testid='job-company']",
#             "[class*='company'] a, [class*='company']",
#         ])
#
#         # 6) 地点：公司后面的 span；用 ~（任意后续兄弟）比 +（紧邻）更稳
#         location = await _text_first(card, [
#             "a[data-automation='jobCompany'] ~ span",
#             "span[data-automation='jobLocation']",
#             "[data-testid='job-location']",
#             "[class*='location']",
#         ])
#
#         # 7) 从 aria-label/title 里兜底解析
#         if not (company and location):
#             label = (await a.get_attribute("aria-label")) or (await a.get_attribute("title")) or ""
#             p_company, p_location = _parse_company_location_from_label(label)
#             company = company or p_company
#             location = location or p_location
#
#         # 8) 详情页最终兜底
#         if detail_budget > 0 and (not company or not location):
#             try:
#                 d_company, d_location = await _fetch_from_detail(page.context, link)
#                 company = company or d_company
#                 location = location or d_location
#             except Exception:
#                 pass
#             finally:
#                 detail_budget -= 1
#
#         jobs.append({
#             "job_id": jid,
#             "title": title,
#             "company": company or "Unknown",
#             "location": location or "Unknown",
#             "link": link
#         })
#
#     return jobs


# sites/seek_adapter.py
from __future__ import annotations
from playwright.async_api import Page
from pathlib import Path
import re, json
from typing import Optional

def _norm(s: Optional[str]) -> Optional[str]:
    if not s: return None
    s = " ".join(s.split())
    return s or None

def _job_id_from_url(url: str) -> str:
    m = re.search(r"/job/(\d+)", url or "")
    return m.group(1) if m else (url or "")

async def extract_seek_jobs(page: Page):
    """极端稳健版：对每个标题向上回溯多层祖先搜索 company/location"""
    await page.wait_for_load_state("domcontentloaded")
    for _ in range(8):
        await page.mouse.wheel(0, 1800)
        await page.wait_for_timeout(700)

    # 标题/链接（多兜底）
    title_selectors = [
        "h3 a",
        "a[data-automation='jobTitle']",
        "a[href*='/job/']"  # 兜底
    ]
    anchors = []
    seen = set()
    for sel in title_selectors:
        for a in await page.query_selector_all(sel):
            href = await a.get_attribute("href")
            if not href:
                continue
            link = href if href.startswith("http") else f"https://www.seek.co.nz{href}"
            if link in seen:
                continue
            seen.add(link)
            anchors.append(a)

    Path("debug_cards").mkdir(exist_ok=True)

    jobs = []
    detail_budget = 4  # 如需打开详情页兜底可调大（此版先不启用详情页）

    for idx, a in enumerate(anchors, 1):
        href = await a.get_attribute("href")
        link = href if href.startswith("http") else f"https://www.seek.co.nz{href}"
        title = _norm(await a.inner_text()) or "Unknown title"
        jid = _job_id_from_url(link)

        # 在页面端执行：从 a 向上回溯最多 8 层，每层尝试多组选择器
        data = await a.evaluate("""
        (el) => {
          // 尽量找“包含这张卡所有字段”的容器
          const isCard = (n) => {
            if (!n || n.nodeType !== 1) return false;
            const ds = n.dataset || {};
            const cls = n.className || '';
            return (
              (ds.testid && String(ds.testid).includes('job-card')) ||
              (ds.automation && String(ds.automation).toLowerCase().includes('job')) ||
              (typeof cls === 'string' && (
                 cls.includes('job') || cls.includes('card') ||
                 cls.includes('_1kr') || cls.includes('_1bo7')   // 哈希类名片段
              ))
            );
          };

          // 收集祖先链
          const ancestors = [];
          let cur = el;
          for (let i = 0; i < 8 && cur; i++) {
            ancestors.push(cur);
            cur = cur.parentElement;
          }

          // 选一个“看起来像卡片”的祖先作为主容器；找不到就退化为最接近的 div
          let container = ancestors.find(isCard);
          if (!container) container = ancestors.find(n => n && n.tagName === 'DIV') || ancestors[0];

          const trySelectors = (root, sels) => {
            for (const s of sels) {
              const node = root.querySelector(s);
              if (node) {
                const t = (node.textContent || '').trim();
                if (t) return t;
              }
            }
            return null;
          };

          // 公司（多组选择器）
          const companySel = [
            "a[data-automation='jobCompany']",
            "[data-automation='jobCompany']",
            "[data-type='company']",
            "[data-testid='job-company']",
            "[class*='company'] a",
            "[class*='company']",
            "a[href*='company']"
          ];

          // 地点（多组选择器；用 ~ 允许“公司后续兄弟”而非紧邻）
          const locationSel = [
            "span[data-automation='jobLocation']",
            "[data-testid='job-location']",
            "[class*='location']",
            "a[data-automation='jobCompany'] ~ span",
            "[data-type='location']",
          ];

          // 先在“容器”里找
          let company = trySelectors(container, companySel);
          let location = trySelectors(container, locationSel);

          // 如果还没有，就在祖先链每一层尝试一次
          if (!company || !location) {
            for (const anc of ancestors) {
              if (!company) company = trySelectors(anc, companySel);
              if (!location) location = trySelectors(anc, locationSel);
              if (company && location) break;
            }
          }

          // 作为兜底：从标题链接自身的描述性属性解析
          if (!company || !location) {
            const label = el.getAttribute('aria-label') || el.getAttribute('title') || '';
            const t = label.trim();
            if (t) {
              // "... at COMPANY – LOCATION" 或 "... at COMPANY in LOCATION"
              const m1 = t.match(/\bat\s+(.+?)\s+[–-]\s+(.+)$/i);
              const m2 = t.match(/\bat\s+(.+?)\s+in\s+(.+)$/i);
              if (!company || !location) {
                if (m1) { company = company || m1[1].trim(); location = location || m1[2].trim(); }
                else if (m2) { company = company || m2[1].trim(); location = location || m2[2].trim(); }
              }
            }
          }

          // 导出这张卡片的 outerHTML（若需要调试）
          const cardHTML =
            (container && container.outerHTML) ||
            (ancestors[0] && ancestors[0].outerHTML) || '';

          return {
            company: company || null,
            location: location || null,
            cardHTML,
          };
        }
        """)

        company = _norm(data.get("company"))
        location = _norm(data.get("location"))

        # 调试：抓不到就把卡片落地
        if not company or not location:
            try:
                Path(f"debug_cards/miss_{idx}.html").write_text(data.get("cardHTML",""), encoding="utf-8")
            except Exception:
                pass

        jobs.append({
            "job_id": jid,
            "title": title,
            "company": company or "Unknown",
            "location": location or "Unknown",
            "link": link
        })

    return jobs
