#!/usr/bin/env python3
"""
评分师 · 云端版 (for GitHub Actions)
====================================
自动从飞书多维表格读取问卷数据 → 计算评分 → 生成HTML诊断报告

环境变量通过GitHub Secrets传入（无需本地.env文件）
"""
import os, sys, json, time, math
from datetime import datetime
from pathlib import Path

# ===== 从环境变量读取（GitHub Secrets）=====
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
BITABLE_APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN", "VU3hbjRyuabLhAseoK3ckzOzndg")
TABLE_ID = os.environ.get("TABLE_ID", "tblofr6TCloHk5Zb")
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

REPORT_DIR = "reports"
os.makedirs(REPORT_DIR, exist_ok=True)

DIMENSION_WEIGHTS = {
    "生产效率": 0.20, "质量控制": 0.15, "库存物流": 0.15,
    "设备管理": 0.10, "人员效率": 0.10, "现场管理": 0.10,
    "计划交付": 0.10, "数字化": 0.10,
}

# 飞书字段映射
DIM_SCORE_FIELDS = [
    ("生产效率", "生产效率评分"), ("质量控制", "质量控制评分"),
    ("库存物流", "库存物流评分"), ("设备管理", "设备管理评分"),
    ("人员效率", "人员效率评分"), ("现场管理", "现场管理评分"),
    ("计划交付", "计划交付评分"), ("数字化", "数字化评分"),
]

# ===== 飞书API =====
def get_token():
    import urllib.request
    data = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode()
    req = urllib.request.Request(f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
        data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
        if result.get("code") != 0:
            raise Exception(f"Token失败: {result.get('msg','')}")
        return result["tenant_access_token"]

def feishu_api(method, path, body=None):
    import urllib.request
    token = get_token()
    url = f"{FEISHU_API_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  ⚠️ API failed: {e}")
        return {"code": -1}

# ===== 飞书消息通知 =====
def send_feishu_message(open_id, title, content):
    """发送飞书消息给指定用户"""
    msg = {
        "receive_id": open_id,
        "msg_type": "text",
        "content": json.dumps({"text": content})
    }
    result = feishu_api("POST", "/im/v1/messages?receive_id_type=open_id", msg)
    if result.get("code") == 0:
        print(f"   📨 消息已发送给 {open_id[:10]}...")
    else:
        print(f"   ⚠️ 消息发送失败: {result.get('msg','')}")
    return result

def send_report_card(open_id, company, total, rating, grade, report_url):
    """发送报告卡片（富文本消息）"""
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    msg = {
        "receive_id": open_id,
        "msg_type": "interactive",
        "content": json.dumps({
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"📋 免费诊断报告 - {company}"},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**企业名称：** {company}"}},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**综合评分：** {total:.2f} / 5.00  **·**  **评级：** {rating}"}},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**商机等级：** {grade}"}},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**生成时间：** {now}"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"📎 完整报告已保存到仓库：\n{report_url}"}},
                {"tag": "note", "text": {"tag": "plain_text", "content": "思派工业 · 精益智能工厂诊断系统 · 评分师 自动生成"}}
            ]
        })
    }
    result = feishu_api("POST", "/im/v1/messages?receive_id_type=open_id", msg)
    if result.get("code") == 0:
        print(f"   🃏 报告卡片已发送给 {open_id[:10]}...")
    else:
        print(f"   ⚠️ 卡片发送失败: {result.get('msg','')}")
    return result

# ===== 评分核心 =====
def extract_text(fields, name):
    raw = fields.get(name, "")
    if isinstance(raw, str): return raw
    if isinstance(raw, list):
        return "".join(item.get("text","") for item in raw if isinstance(item,dict))
    if isinstance(raw, dict):
        vals = raw.get("value",[])
        return str(vals[0]) if vals else ""
    return str(raw)

def get_score(fields, sf):
    f = fields.get(sf, {})
    if isinstance(f, dict):
        vals = f.get("value", [])
        return float(vals[0]) if vals else 0.0
    return float(f) if isinstance(f, (int,float)) else 0.0

def compute(dim_scores):
    return round(sum(dim_scores.get(d,0)*w for d,w in DIMENSION_WEIGHTS.items()), 3)

def get_rating(score):
    for t, g, d in [(4.5,"A级 卓越","精益管理成熟"),
                     (3.5,"B级 良好","有精益基础"),
                     (2.5,"C级 注意","精益基础薄弱"),
                     (0,"D级 风险","管理水平亟待提升")]:
        if score >= t: return g, d
    return "D级 风险", ""

def get_sales_grade(fields):
    loss = extract_text(fields, "Q24. 以上问题每年大概造成多少损失？（单选题）")
    for kw, g, m in [("50万以下","C级商机",25),("50–200万","B级商机",125),
                     ("200–500万","A级商机",350),("500–1000万","S级商机",750),
                     ("1000万以上","S级商机",1500)]:
        if kw in loss: return g, m, loss
    return "待评估", 0, loss

# ===== HTML报告生成 =====
def gen_html(company, contact, dim_scores, total, rating, desc, loss_label, sales_grade):
    now_str = datetime.now().strftime("%Y-%m-%d")
    sorted_dims = sorted(dim_scores.items(), key=lambda x: x[1])
    worst = sorted_dims[:3]
    best = sorted_dims[-1:] if sorted_dims else []

    def sc(s):
        return "#16a34a" if s>=4 else "#ca8a04" if s>=3 else "#f97316" if s>=2 else "#dc2626"

    bars = ""
    for n,s in sorted_dims:
        p = int(s/5*100); c = sc(s)
        bars += f'\n<div style="display:flex;align-items:center;padding:8px 0;border-bottom:1px solid #eee"><div style="width:100px;font-size:13px;color:#555">{n}</div><div style="flex:1;height:16px;background:#f0f0f0;border-radius:8px;margin:0 12px;overflow:hidden"><div style="height:100%;width:{p}%;background:{c};border-radius:8px"></div></div><div style="width:36px;font-size:15px;font-weight:700;color:{c};text-align:right">{s:.1f}</div></div>'

    top3 = "".join(f"<li>{i+1}. {n}（{s:.1f}分）</li>" for i,(n,s) in enumerate(worst))
    best_html = f'<div class="fb" style="background:#f0fdf4;border:1px solid #86efac;color:#166534">✅ 优势维度：{best[0][0]}（{best[0][1]:.1f}分）</div>' if best else ""

    loss_mid = {"50万以下":25,"50":125,"200":350,"500":750}[next((k for k in ["500","200","50"] if k in str(loss_label)),"200")]

    return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>精益智能工厂免费诊断报告 - {company}</title><meta name="viewport" content="width=device-width,initial-scale=1"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,sans-serif;background:#f5f7fa;color:#1a2332;padding:20px}}
.c{{max-width:760px;margin:0 auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.06)}}
.h{{background:linear-gradient(135deg,#1a365d,#2d5a8e);padding:32px 36px 28px;color:#fff}}
.h .l{{display:inline-block;background:rgba(255,255,255,.12);padding:3px 12px;border-radius:14px;font-size:11px;margin-bottom:10px}}
.h h1{{font-size:22px;font-weight:700}}
.h .cname{{font-size:15px;opacity:.85;margin-top:8px}}
.h .m{{font-size:11px;opacity:.6;margin-top:4px}}
.b{{padding:28px 36px}}
.s{{display:flex;gap:20px;padding:20px 24px;background:#f8fafc;border-radius:12px;margin-bottom:24px}}
.s .big{{font-size:34px;font-weight:700;color:#1a365d}}
.s .info{{flex:1}}
.s .info .g{{font-size:17px;font-weight:700}}
.s .info .g .tag{{display:inline-block;margin-left:8px;padding:2px 10px;font-size:11px;border-radius:10px;background:#fee2e2;color:#b91c1c;font-weight:600}}
.s .info .d{{font-size:12px;color:#666;margin-top:4px;line-height:1.5}}
.st{{font-size:14px;font-weight:700;color:#1a365d;margin:18px 0 10px;padding-bottom:6px;border-bottom:2px solid #eef2f6}}
.lg{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:18px}}
.li{{text-align:center;padding:14px;background:#f8fafc;border-radius:10px;border:1px solid #eef2f6}}
.li .lbl{{font-size:10px;color:#888;margin-bottom:2px}}
.li .val{{font-size:20px;font-weight:700}}
.fb{{padding:14px 18px;border-radius:10px;margin-bottom:14px;font-size:12px;line-height:1.6}}
.ca{{text-align:center;padding:20px;background:linear-gradient(135deg,#1a365d,#2d5a8e);border-radius:12px;color:#fff;margin-top:18px}}
.ca h3{{font-size:14px;font-weight:600}}
.ca p{{font-size:11px;opacity:.8;margin-top:4px}}
.ft{{text-align:center;padding:14px;font-size:10px;color:#aaa;border-top:1px solid #eef2f6}}
.disc{{font-size:9px;color:#999;padding:10px 14px;background:#fafafa;border-radius:8px;margin-top:12px}}
</style></head><body><div class="c">
<div class="h"><div class="l">📋 免费诊断报告</div><h1>精益智能工厂初步诊断分析报告</h1><div class="cname">🏢 {company}</div><div class="m">📅 {now_str}</div></div>
<div class="b">
<div class="s"><div><div class="big">{total:.2f}</div><div style="font-size:11px;color:#888">/ 5.00</div></div><div class="info"><div class="g">综合评分 · {rating.split(" ")[0] if " " in rating else rating}<span class="tag">{rating}</span></div><div class="d">{desc}</div></div></div>
<div class="st">📊 八维诊断评分</div>{bars}
<div class="st">💰 损失与商机评估</div>
<div class="lg"><div class="li"><div class="lbl">年度损失</div><div class="val" style="color:#b91c1c;font-size:15px">{loss_label}</div></div><div class="li"><div class="lbl">商机等级</div><div class="val" style="color:#ca8a04;font-size:17px">{sales_grade}</div></div><div class="li"><div class="lbl">改善潜力</div><div class="val" style="color:#16a34a;font-size:15px">约{loss_mid}万</div></div></div>
<div class="st">🔍 Top3改善方向</div>
<div class="fb" style="background:#fef2f2;border:1px solid #fecaca;color:#991b1b"><ol style="padding-left:16px">{top3}</ol></div>
{best_html}
<div class="ca"><h3>📞 想深入了解改善方案？</h3><p>以上为初步诊断。如需详细改善路线图，请联系：<br><strong>思派工业技术（深圳）有限公司 · 联系人：{contact}</strong></p></div>
<div class="disc">📌 本报告由精益智能工厂诊断系统自动生成，仅供参考。</div>
</div><div class="ft">思派工业 · 精益智能工厂领航员 · 评分师 自动生成</div>
</div></body></html>'''

# ===== 主流程 =====
def main():
    print(f"🤖 评分师云端版启动")
    missing = [v for v in ["FEISHU_APP_ID","FEISHU_APP_SECRET"] if not os.environ.get(v)]
    if missing:
        print(f"❌ 缺少环境变量: {missing}")
        sys.exit(1)

    # 获取所有记录
    print("🔍 获取飞书数据...")
    records = []
    pt = None
    while True:
        r = feishu_api("GET", f"/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{TABLE_ID}/records?page_size=50" + (f"&page_token={pt}" if pt else ""))
        items = r.get("data",{}).get("items",[])
        records.extend(items)
        if not r.get("data",{}).get("has_more"): break
        pt = r.get("data",{}).get("page_token")
    print(f"   共 {len(records)} 条记录")

    # 处理待处理记录
    processed = 0
    for rec in records:
        fields = rec.get("fields",{})
        progress = str(fields.get("跟进进度","") or "")
        # Skip only if already processed
        if progress == "已生成报告": continue

        rid = rec["record_id"]
        dim_scores = {d: get_score(fields, sf) for d, sf in DIM_SCORE_FIELDS}
        total = compute(dim_scores)
        rating, desc = get_rating(total)
        grade, loss_mid, loss_label = get_sales_grade(fields)
        company = extract_text(fields, "Q1. 企业名称（填空题，必填）")
        contact = extract_text(fields, "Q29.联系人和联系方式（手机号/微信，必填）")

        print(f"   处理: {company} | {total:.2f} → {rating} | {loss_label} → {grade}")

        # 更新飞书状态
        feishu_api("PUT", f"/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{TABLE_ID}/records/{rid}", {"fields":{"跟进进度":"已生成报告"}})

        # 生成报告（文件名用英文+数字，避免中文链接问题）
        html = gen_html(company, contact, dim_scores, total, rating, desc, loss_label, grade)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_name = "".join(c for c in company if c.isascii() and c.isalnum() or c in " _-")[:15] or "report"
        safe_name = safe_name.strip().replace(" ", "_") or f"report_{rid[:6]}"
        rpath = f"{REPORT_DIR}/diagnosis_{safe_name}_{ts}.html"
        with open(rpath, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"   📄 报告: {rpath}")
        
        # 发送通知给创始人（卡片形式）
        founder_open_id = os.environ.get("FOUNDER_OPEN_ID", "ou_654b4ab922a747e21af74eaa4884a914")
        repo_url = os.environ.get("REPO_URL", "https://github.com/Kevinhanmin/scorer-reports")
        # 优先用Pages地址，如果没有则用GitHub文件链接（两者都可访问）
        pages_url = os.environ.get("PAGES_URL", "")
        if pages_url:
            report_url = f"{pages_url}/reports/{os.path.basename(rpath)}"
        else:
            # Default: use GitHub Pages (repo is public, Pages is free)
            report_url = f"https://kevinhanmin.github.io/scorer-reports/reports/{os.path.basename(rpath)}"
        
        
        try:
            send_report_card(founder_open_id, company, total, rating, grade, report_url)
        except Exception as e:
            print(f"   ⚠️ 卡片发送失败: {e}")
        
        processed += 1

    print(f"\n✅ 完成！处理了 {processed} 条新记录")
    return processed

if __name__ == "__main__":
    main()
