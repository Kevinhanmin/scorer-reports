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
    """发送报告卡片（带点击按钮的交互卡片）"""
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
                {"tag": "hr"},
                {"tag": "action", "actions": [
                    {
                        "tag": "button",
                        "type": "primary",
                        "text": {"tag": "plain_text", "content": "📄 查看完整诊断报告"},
                        "multi_url": {
                            "url": report_url,
                            "android_url": report_url,
                            "ios_url": report_url,
                            "pc_url": report_url
                        }
                    }
                ]},
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


# ===== HTML报告生成（v2.0 大升级）=====
def gen_html(company, contact, dim_scores, total, rating, desc, loss_label, sales_grade):
    now_str = datetime.now().strftime("%Y-%m-%d")
    sorted_dims = sorted(dim_scores.items(), key=lambda x: x[1])
    worst = sorted_dims[:3]
    best = sorted_dims[-1:] if sorted_dims else []

    def sc(s):
        if s >= 4.5: return "#16a34a", "优秀"
        if s >= 3.5: return "#65a30d", "良好"
        if s >= 2.5: return "#ca8a04", "关注"
        if s >= 1.5: return "#f97316", "薄弱"
        return "#dc2626", "风险"

    # 进度条
    bars = ""
    for n, s in sorted_dims:
        p = int(s/5*100); c, lbl = sc(s)
        bars += f'''
        <div class="dim-row">
            <div class="dim-label">{n} <span class="dim-tag {lbl}">{lbl}</span></div>
            <div class="dim-bar-wrap"><div class="dim-bar" style="width:{p}%;background:{c}"></div></div>
            <div class="dim-score" style="color:{c}">{s:.1f}</div>
        </div>'''

    # Top3 列表
    top3_items = ""
    dim_descriptions = {
        "生产效率": "关注设备综合效率(OEE)、产线平衡、瓶颈工序",
        "质量控制": "关注不良率、返工率、防错体系有效性",
        "库存物流": "关注库存周转天数、在制品堆积、物料配送效率",
        "设备管理": "关注故障率、TPM体系、快速换模水平",
        "人员效率": "关注人员利用率、标准作业覆盖率、技能依赖度",
        "现场管理": "关注5S水平、目视化管理、标准化程度",
        "计划交付": "关注交付及时率、计划稳定性、排产合理性",
        "数字化": "关注信息系统覆盖度、数据可视化、自动化水平",
    }
    for i, (n, s) in enumerate(worst):
        detail = dim_descriptions.get(n, "")
        bar_w = int(s/5*100)
        top3_items += f'''
        <div class="improve-item">
            <div class="improve-num">0{i+1}</div>
            <div class="improve-info">
                <div class="improve-title">{n}（{s:.1f}分）</div>
                <div class="improve-desc">{detail}</div>
                <div class="improve-bar"><div class="improve-bar-fill" style="width:{bar_w}%"></div></div>
            </div>
        </div>'''

    best_html = ""
    if best:
        n, s = best[0]; c, lbl = sc(s)
        best_html = f'<div class="strength-box"><span class="strength-icon">⭐</span><span class="strength-label">优势维度</span> <strong>{n}</strong>（{s:.1f}分）— 保持此优势并转化为核心竞争力</div>'

    # 损失估算
    try:
        loss_mid = {"50万以下": 25, "50": 125, "200": 350, "500": 750}[next((k for k in ["500", "200", "50"] if k in str(loss_label)), "200")]
    except:
        loss_mid = 100

    # 建议内容
    suggestions = {
        (4.5, 5.0): "整体运营状况优秀，建议在现有基础上推进数字化升级，建立行业标杆示范效应。",
        (3.5, 4.5): "具备良好的管理基础，建议针对薄弱维度进行专项改善，可考虑L2轻量诊断做深度评估。",
        (2.5, 3.5): "存在明显的改善空间和管理浪费，建议尽快启动系统性诊断，制定90天改善计划。",
        (0, 2.5): "管理水平亟待提升，存在较大经营风险，强烈建议立即启动全面诊断和改善项目。",
    }
    suggestion = ""
    for (lo, hi), text in sorted(suggestions.items(), reverse=True):
        if lo <= total < hi:
            suggestion = text
            break

    # 评级标签
    grade_color = {"A": "#16a34a", "B": "#65a30d", "C": "#ca8a04", "D": "#dc2626"}
    grade_key = rating[0] if rating else "C"
    gc = grade_color.get(grade_key, "#ca8a04")

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>精益智能工厂免费诊断报告 - {company}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:#f0f2f5;color:#1d1d1f;padding:20px;line-height:1.6}}
.c{{max-width:800px;margin:0 auto;background:#fff;border-radius:20px;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,.08)}}
/* 头部 */
.h{{background:linear-gradient(135deg,#0f172a,#1e3a5f,#2563eb);padding:40px 44px 32px;color:#fff;position:relative;overflow:hidden}}
.h::before{{content:'';position:absolute;top:-60px;right:-60px;width:200px;height:200px;border-radius:50%;background:rgba(255,255,255,.04)}}
.h::after{{content:'';position:absolute;bottom:-40px;left:-40px;width:150px;height:150px;border-radius:50%;background:rgba(255,255,255,.03)}}
.h .l{{display:inline-block;background:rgba(255,255,255,.12);padding:4px 14px;border-radius:20px;font-size:12px;letter-spacing:.5px;backdrop-filter:blur(4px);margin-bottom:14px;border:1px solid rgba(255,255,255,.08)}}
.h h1{{font-size:26px;font-weight:700;letter-spacing:-.5px}}
.h .cname{{font-size:16px;opacity:.9;margin-top:10px}}
.h .meta{{display:flex;gap:24px;margin-top:12px;font-size:12px;opacity:.65}}
/* 内容区 */
.b{{padding:32px 40px 28px}}
/* 综合评分模块 */
.score-card{{display:flex;gap:28px;padding:24px 28px;background:linear-gradient(135deg,#f8fafc,#f1f5f9);border-radius:16px;margin-bottom:28px;border:1px solid #e2e8f0}}
.score-big{{text-align:center;min-width:100px}}
.score-big .num{{font-size:48px;font-weight:800;color:#0f172a;line-height:1}}
.score-big .denom{{font-size:14px;color:#94a3b8;margin-top:2px}}
.score-big .grade-badge{{display:inline-block;margin-top:8px;padding:3px 14px;border-radius:20px;font-size:13px;font-weight:600;color:#fff;background:{gc}}}
.score-info{{flex:1}}
.score-info .g{{font-size:18px;font-weight:700;color:#0f172a}}
.score-info .g .tag{{display:inline-block;margin-left:8px;padding:2px 12px;font-size:11px;border-radius:12px;background:{gc}20;color:{gc};font-weight:600}}
.score-info .d{{font-size:13px;color:#64748b;margin-top:6px;line-height:1.6}}
/* 维度评分 */
.section-title{{font-size:15px;font-weight:700;color:#0f172a;margin:24px 0 12px;padding-bottom:8px;border-bottom:2px solid #eef2f6;display:flex;align-items:center;gap:8px}}
.dim-row{{display:flex;align-items:center;padding:7px 0;gap:12px}}
.dim-label{{width:100px;font-size:13px;color:#334155;display:flex;align-items:center;gap:6px;flex-shrink:0}}
.dim-tag{{font-size:9px;padding:1px 8px;border-radius:10px;font-weight:500}}
.dim-tag.优秀{{background:#dcfce7;color:#16a34a}}
.dim-tag.良好{{background:#ecfccb;color:#65a30d}}
.dim-tag.关注{{background:#fef9c3;color:#ca8a04}}
.dim-tag.薄弱{{background:#ffedd5;color:#f97316}}
.dim-tag.风险{{background:#fef2f2;color:#dc2626}}
.dim-bar-wrap{{flex:1;height:10px;background:#f1f5f9;border-radius:5px;overflow:hidden}}
.dim-bar{{height:100%;border-radius:5px;transition:width 1s ease}}
.dim-score{{width:36px;font-size:14px;font-weight:700;text-align:right;flex-shrink:0}}
/* 三栏数据 */
.stat-grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:24px}}
.stat-item{{text-align:center;padding:18px 12px;background:#f8fafc;border-radius:12px;border:1px solid #e2e8f0}}
.stat-item .stat-lbl{{font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px}}
.stat-item .stat-val{{font-size:22px;font-weight:800}}
/* Top3改善 */
.improve-list{{display:flex;flex-direction:column;gap:12px;margin-bottom:24px}}
.improve-item{{display:flex;gap:14px;padding:16px 18px;background:#fef2f2;border:1px solid #fecaca;border-radius:12px}}
.improve-num{{font-size:12px;font-weight:800;color:#dc2626;width:28px;text-align:center;padding-top:2px}}
.improve-info{{flex:1}}
.improve-title{{font-size:14px;font-weight:700;color:#991b1b}}
.improve-desc{{font-size:11px;color:#b91c1c;margin-top:2px;opacity:.8}}
.improve-bar{{margin-top:6px;height:4px;background:#fecaca;border-radius:2px;overflow:hidden}}
.improve-bar-fill{{height:100%;background:#dc2626;border-radius:2px}}
.strength-box{{padding:14px 18px;background:#f0fdf4;border:1px solid #86efac;border-radius:12px;font-size:12px;color:#166534;margin-bottom:24px;line-height:1.6}}
/* 建议框 */
.suggestion-box{{padding:18px 22px;background:linear-gradient(135deg,#eff6ff,#dbeafe);border:1px solid #bfdbfe;border-radius:12px;margin-bottom:24px}}
.suggestion-box .sug-title{{font-size:13px;font-weight:700;color:#1e40af;margin-bottom:4px}}
.suggestion-box .sug-text{{font-size:12px;color:#1e40af;line-height:1.7}}
/* 行动号召 */
.cta{{text-align:center;padding:28px 24px;background:linear-gradient(135deg,#0f172a,#1e3a5f);border-radius:16px;color:#fff;margin-top:20px}}
.cta h3{{font-size:17px;font-weight:700;letter-spacing:-.3px}}
.cta p{{font-size:13px;opacity:.85;margin:8px 0 16px;line-height:1.6}}
.cta .cta-btn{{display:inline-block;padding:12px 32px;background:#2563eb;color:#fff;border-radius:30px;font-size:14px;font-weight:600;text-decoration:none;transition:all .2s;border:1px solid rgba(255,255,255,.15)}}
.cta .cta-btn:hover{{background:#1d4ed8;transform:translateY(-1px)}}
.cta .cta-info{{font-size:11px;opacity:.6;margin-top:12px}}
/* 底部 */
.footer{{text-align:center;padding:18px;font-size:10px;color:#94a3b8;border-top:1px solid #e2e8f0}}
.footer .brand{{font-size:11px;font-weight:600;color:#64748b;margin-bottom:2px}}
.disclaimer{{font-size:9px;color:#94a3b8;padding:12px 18px;background:#f8fafc;border-radius:8px;margin-top:12px;line-height:1.5}}
/* 响应式 */
@media(max-width:600px){{.h{{padding:28px 20px 24px}}.b{{padding:20px 16px}}.score-card{{flex-direction:column;gap:12px;padding:16px}}.score-big{{min-width:unset}}.dim-label{{width:80px;font-size:12px}}.stat-grid{{gap:8px}}.stat-item{{padding:12px 6px}}}}
</style>
</head>
<body>
<div class="c">
    <div class="h">
        <div class="l">🔍 免费诊断报告</div>
        <h1>精益智能工厂 · 初步诊断分析报告</h1>
        <div class="cname">🏢 {company}</div>
        <div class="meta"><span>📅 报告日期：{now_str}</span><span>📋 报告编号：DIAG-{datetime.now().strftime("%Y%m%d%H%M")}</span></div>
    </div>
    <div class="b">
        <!-- 综合评分 -->
        <div class="score-card">
            <div class="score-big">
                <div class="num">{total:.2f}</div>
                <div class="denom">/ 5.00</div>
                <div class="grade-badge">{rating}</div>
            </div>
            <div class="score-info">
                <div class="g">综合评分 · 企业健康度评估<span class="tag">{rating}</span></div>
                <div class="d">{desc}</div>
            </div>
        </div>

        <!-- 八维诊断评分 -->
        <div class="section-title">📊 八维诊断评分体系</div>
        {bars}

        <!-- 损失与商机 -->
        <div class="section-title">💰 经济损失与改善机会评估</div>
        <div class="stat-grid">
            <div class="stat-item">
                <div class="stat-lbl">📉 年度预计损失</div>
                <div class="stat-val" style="color:#dc2626">{loss_label}</div>
            </div>
            <div class="stat-item">
                <div class="stat-lbl">🎯 商机等级</div>
                <div class="stat-val" style="color:#ca8a04">{sales_grade}</div>
            </div>
            <div class="stat-item">
                <div class="stat-lbl">💡 年改善潜力</div>
                <div class="stat-val" style="color:#16a34a">约{loss_mid}万</div>
            </div>
        </div>

        <!-- 优势 -->
        {best_html}

        <!-- 紧急改善方向 -->
        <div class="section-title">🔴 优先改善方向（Top 3）</div>
        <div class="improve-list">{top3_items}</div>

        <!-- 专家建议 -->
        <div class="suggestion-box">
            <div class="sug-title">🎯 专家诊断建议</div>
            <div class="sug-text">{suggestion}</div>
        </div>

        <!-- 行动号召 -->
        <div class="cta">
            <h3>📞 想获取详细改善方案？</h3>
            <p>以上为初步免费诊断结果。如需获取针对您工厂的详细改善路线图<br>以及量化的投入产出分析，请联系我们安排深度诊断。</p>
            <a class="cta-btn" href="https://kevinhanmin.github.io/scorer-reports/" target="_blank">📋 预约专家深度诊断 →</a>
            <div class="cta-info">联系人：{contact} · 思派工业技术（深圳）有限公司</div>
        </div>

        <div class="disclaimer">📌 免责声明：本报告由精益智能工厂诊断系统基于问卷数据自动生成，旨在提供初步参考。报告中的评分、损失估算、改善建议等均为基于有限信息的初步判断，不代表最终诊断结论。如需准确的工厂诊断报告，请联系思派工业技术安排现场深度诊断。</div>
    </div>
    <div class="footer">
        <div class="brand">思派工业技术（深圳）有限公司 · 精益智能工厂领航员</div>
        <div>© 2026 思派工业技术 · 由评分师自动生成</div>
    </div>
</div>
</body>
</html>'''


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
        # 生成报告URL（暂不发送，等git commit后再发）
        pages_url = os.environ.get("PAGES_URL", "")
        if pages_url:
            report_url = f"{pages_url}/reports/{os.path.basename(rpath)}"
        else:
            report_url = f"https://kevinhanmin.github.io/scorer-reports/reports/{os.path.basename(rpath)}"
        
        # 保存待发送通知到pending文件（避免git commit前就发通知）
        founder_open_id = os.environ.get("FOUNDER_OPEN_ID", "ou_654b4ab922a747e21af74eaa4884a914")
        pending_file = "pending_notifications.json"
        notif = {
            "open_id": founder_open_id,
            "company": company,
            "total": total,
            "rating": rating,
            "grade": grade,
            "report_url": report_url,
            "timestamp": datetime.now().isoformat()
        }
        pending = []
        if os.path.exists(pending_file):
            try:
                with open(pending_file, "r") as pf:
                    pending = json.load(pf)
            except:
                pass
        pending.append(notif)
        with open(pending_file, "w") as pf:
            json.dump(pending, pf, ensure_ascii=False)
        print(f"   📝 通知已暂存，待git commit后发送")
        
        processed += 1

    print(f"\n✅ 完成！处理了 {processed} 条新记录")
    return processed

def send_pending_notifications():
    """读取pending_notifications.json并发送所有待发送的飞书卡片"""
    pending_file = "pending_notifications.json"
    if not os.path.exists(pending_file):
        print("📭 没有待发送的通知")
        return 0
    
    with open(pending_file, "r") as pf:
        pending = json.load(pf)
    
    if not pending:
        print("📭 没有待发送的通知")
        return 0
    
    print(f"📨 发送 {len(pending)} 条待处理通知...")
    sent = 0
    for n in pending:
        try:
            send_report_card(
                n["open_id"], n["company"],
                n["total"], n["rating"], n["grade"], n["report_url"]
            )
            sent += 1
            time.sleep(1)  # 避免频率限制
        except Exception as e:
            print(f"   ⚠️ 通知发送失败: {e}")
    
    # 发送完成后删除pending文件
    os.remove(pending_file)
    print(f"✅ 已发送 {sent} 条通知，临时文件已清理")
    return sent

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--notify":
        send_pending_notifications()
    else:
        main()
