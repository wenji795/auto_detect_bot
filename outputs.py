# outputs.py
from pathlib import Path
import csv
import sqlite3
from datetime import datetime
import html  # 新增：用于安全转义


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
    从数据库读取最近职位，生成一个可浏览的 HTML 文件（表格样式 + 轻量筛选/搜索）。
    """
    ensure_output_dir()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT job_id, title, company, location, source, link, seen_at
        FROM jobs
        ORDER BY datetime(seen_at) DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    # 简单预处理
    data = [{
        "job_id": r[0] or "",
        "title": r[1] or "",
        "company": r[2] or "",
        "location": r[3] or "",
        "source": (r[4] or "").lower(),
        "link": r[5] or "",
        "seen_at": r[6] or ""
    } for r in rows]

    # 唯一地点列表（用于下拉）
    locations = sorted({d["location"].strip() for d in data if d["location"] and d["location"].strip() and d["location"] != "Unknown"})

    # 生成 HTML（极简样式 + 原生 JS）
    css = """
    :root{
      --bg:#0b0c0f; --panel:#101217; --text:#e6e6e6; --muted:#9aa3af; --border:#23262d;
      --chip:#1f2937; --seek:#e11d48; --ln:#2563eb;
    }
    @media (prefers-color-scheme: light){
      :root{ --bg:#f7f8fa; --panel:#ffffff; --text:#1f2937; --muted:#6b7280; --border:#e5e7eb; --chip:#eef2f7; }
    }
    html,body{margin:0;background:var(--bg);color:var(--text);font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,Arial,sans-serif}
    .wrap{max-width:1000px;margin:28px auto;padding:0 16px}
    .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
    h1{font-size:20px;margin:0}
    .muted{color:var(--muted);font-size:12px}
    .panel{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:12px}
    .ctrl{display:grid;grid-template-columns:1fr 220px 160px;gap:10px}
    @media (max-width:800px){ .ctrl{grid-template-columns:1fr 1fr} }
    input,select{border:1px solid var(--border);background:transparent;color:var(--text);border-radius:10px;padding:8px 10px;outline:none}
    table{width:100%;border-collapse:collapse;margin-top:12px}
    th,td{border-bottom:1px solid var(--border);padding:10px 8px;text-align:left;font-size:14px;vertical-align:top}
    th{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
    a{color:#7c3aed;text-decoration:none}
    .src{display:inline-block;padding:2px 8px;border-radius:999px;border:1px solid var(--border);font-size:11px}
    .src.seek{background:#fee2e2;color:#991b1b;border-color:#fecaca}
    .src.linkedin{background:#dbeafe;color:#1d4ed8;border-color:#bfdbfe}
    .empty{padding:16px;text-align:center;color:var(--muted)}
    """

    # 轻量搜索/筛选脚本（-exclude / OR）
    js = """
    const $=s=>document.querySelector(s); const $$=s=>Array.from(document.querySelectorAll(s));
    const state={ q:"", src:"", loc:"" };

    function parseQuery(q){
      const tokens=(q||"").trim().split(/\\s+/);
      const must=[], or=[], not=[];
      let cur=must;
      for(const t of tokens){
        if(!t) continue;
        if(t.toUpperCase()==="OR"){ cur=or; continue; }
        if(t.startsWith("-")){ not.push(t.slice(1).toLowerCase()); continue; }
        cur.push(t.toLowerCase());
      }
      return {must,or,not};
    }

    function match(text,q){
      if(!q) return true;
      const hay=(text||"").toLowerCase();
      const {must,or,not}=parseQuery(q);
      if(must.some(k=>!hay.includes(k))) return false;
      if(or.length && !or.some(k=>hay.includes(k))) return false;
      if(not.some(k=>hay.includes(k))) return false;
      return true;
    }

    function render(){
      const q = state.q;
      const src = state.src;
      const loc = state.loc;

      let shown = 0;
      $$("#tbody tr").forEach(tr=>{
        const t = tr.dataset.title, c = tr.dataset.company, l = tr.dataset.location, s = tr.dataset.source;
        const hay = (t+" "+c+" "+l).toLowerCase();
        const passQ = match(hay,q);
        const passS = !src || s===src;
        const passL = !loc || l.toLowerCase()===loc.toLowerCase();
        const on = passQ && passS && passL;
        tr.style.display = on ? "" : "none";
        if(on) shown++;
      });
      $("#count").textContent = shown;
    }

    $("#q").addEventListener("input", e=>{ state.q=e.target.value; render(); });
    $("#source").addEventListener("change", e=>{ state.src=e.target.value; render(); });
    $("#location").addEventListener("change", e=>{ state.loc=e.target.value; render(); });

    render();
    """

    # 构建地点下拉 HTML
    loc_options = "<option value=''>All locations</option>" + "".join(
        f"<option value='{html.escape(l)}'>{html.escape(l)}</option>" for l in locations
    )

    # 构建表格行
    rows_html = []
    for d in data:
        title = html.escape(d["title"] or "Untitled")
        company = html.escape(d["company"] or "—")
        location = html.escape(d["location"] or "—")
        source = (d["source"] or "")
        seen = html.escape(d["seen_at"] or "")
        href = (d["link"] or "#")
        src_class = f"src {source}" if source in ("seek","linkedin") else "src"

        rows_html.append(
            f"<tr data-title='{html.escape(d['title'])}' data-company='{html.escape(d['company'])}' "
            f"data-location='{html.escape(d['location'])}' data-source='{html.escape(source)}'>"
            f"<td><a href='{html.escape(href)}' target='_blank' rel='noopener'>{title}</a></td>"
            f"<td>{company}</td>"
            f"<td>{location}</td>"
            f"<td><span class='{src_class}'>{html.escape(source or 'N/A')}</span></td>"
            f"<td><span class='muted'>{seen}</span></td>"
            f"</tr>"
        )

    html_out = f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Latest Jobs · {len(data)}</title>
<style>{css}</style>
<div class="wrap">
  <div class="row">
    <h1>Latest Jobs</h1>
    <div class="muted">showing <span id="count">{len(data)}</span> / {len(data)}</div>
  </div>

  <div class="panel">
    <div class="ctrl">
      <input id="q" placeholder="Search title / company / location (supports OR / -exclude)" />
      <select id="source">
        <option value="">Source: All</option>
        <option value="seek">Seek</option>
        <option value="linkedin">LinkedIn</option>
      </select>
      <select id="location">{loc_options}</select>
    </div>
  </div>

  <div class="panel" style="margin-top:10px; overflow:auto;">
    <table>
      <thead>
        <tr>
          <th>Title</th>
          <th>Company</th>
          <th>Location</th>
          <th>Source</th>
          <th>Seen at</th>
        </tr>
      </thead>
      <tbody id="tbody">
        {"".join(rows_html) if rows_html else '<tr><td colspan="5" class="empty">No data.</td></tr>'}
      </tbody>
    </table>
  </div>

  <p class="muted" style="margin-top:10px">Tip: e.g. <code class="muted">tester OR qa -senior -lead</code></p>
</div>
<script>{js}</script>
"""

    HTML_PATH.write_text(html_out, encoding="utf-8")
    return str(HTML_PATH)

