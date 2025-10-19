# outputs.py
from pathlib import Path
import csv
import sqlite3
from datetime import datetime

OUTPUT_DIR = Path("outputs")
CSV_PATH   = OUTPUT_DIR / "new_jobs.csv"
HTML_PATH  = OUTPUT_DIR / "latest.html"

def ensure_output_dir():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def append_new_jobs_csv(new_jobs: list[dict]):
    """将本轮新增职位追加写入 CSV（便于 Excel 查看）"""
    ensure_output_dir()
    file_exists = CSV_PATH.exists()
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["seen_at", "job_id", "title", "company", "location", "link"])
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for j in new_jobs:
            writer.writerow([now, j.get("job_id",""), j.get("title",""), j.get("company",""),
                             j.get("location",""), j.get("link","")])

def build_html_from_db(db_path: str, limit: int = 100):
    """
    从数据库读取最近职位，生成一个可浏览的 HTML 文件。
    默认显示最近 100 条，按时间倒序。
    """
    ensure_output_dir()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # 如果你的 jobs 表只有 job_id/title/link/seen_at，可以 left join 不了公司/地点；
    # 这里直接从 new_jobs.csv 取公司/地点更简单。为了稳，这里只用 DB，然后在 CSV 里看更全字段。
    rows = cur.execute("""
        SELECT job_id, title, link, seen_at
        FROM jobs
        ORDER BY datetime(seen_at) DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    # 生成简易 HTML
    html = [
        "<!doctype html>",
        "<meta charset='utf-8'>",
        "<title>Latest Jobs</title>",
        "<style>body{font-family: -apple-system, Segoe UI, Roboto, Arial; padding:16px;} a{color:#0b5fff;text-decoration:none} .time{color:#666;font-size:12px} li{margin:.5rem 0}</style>",
        f"<h1>Latest Jobs (top {limit})</h1>",
        f"<p class='time'>Generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>",
        "<ol>"
    ]
    for job_id, title, link, seen_at in rows:
        link = link or "#"
        safe_title = (title or "").replace("<","&lt;").replace(">","&gt;")
        html.append(f"<li><a href='{link}' target='_blank'>{safe_title}</a> "
                    f"<span class='time'>&nbsp;•&nbsp;{seen_at}</span></li>")
    html.append("</ol>")

    HTML_PATH.write_text("\n".join(html), encoding="utf-8")
    return str(HTML_PATH)
