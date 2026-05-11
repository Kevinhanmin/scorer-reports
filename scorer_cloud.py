#!/usr/bin/env python3
"""
评分师 · 云端版 (for GitHub Actions)
====================================
自动从飞书多维表格读取问卷数据 → 计算评分 → 生成HTML诊断报告

环境变量通过GitHub Secrets传入（无需本地.env文件）
"""
import os, sys, json, time, math, re
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
    for t, g, d, guidance in [
        (4.5, "A级 卓越", "精益管理成熟",
         "贵工厂整体管理水平处于行业领先地位，各项指标表现优秀。\n"
         "💡 方向建议：\n"
         "1️⃣ 保持现有管理优势，将成功经验标准化和流程化\n"
         "2️⃣ 推进数字化升级，建立行业标杆示范效应\n"
         "3️⃣ 关注前沿管理技术，持续保持竞争优势\n"
         "4️⃣ 可将您的管理经验转化为行业案例，提升行业影响力"),
        (3.5, "B级 良好", "有精益基础",
         "贵工厂具备良好的管理基础，多数维度表现平稳，但仍存在优化空间。\n"
         "💡 方向建议：\n"
         "1️⃣ 针对评分较低维度（见Top3改善方向）做专项改善\n"
         "2️⃣ 建议安排L2轻量诊断对薄弱环节进行深度评估\n"
         "3️⃣ 建立持续改善机制，避免管理优势下滑\n"
         "4️⃣ 对标行业标杆，制定3-6个月改善路线图"),
        (2.5, "C级 注意", "精益基础薄弱",
         "贵工厂存在明显的改善空间和管理浪费，多个维度需要重点关注。\n"
         "💡 方向建议：\n"
         "1️⃣ 立即启动系统性诊断（建议L2诊断），全面评估改善机会\n"
         "2️⃣ 优先改善Top3薄弱维度，快速见效建立信心\n"
         "3️⃣ 制定90天改善计划，设定可量化的改善目标\n"
         "4️⃣ 建议引入精益管理辅导，避免损失持续扩大"),
        (0, "D级 风险", "管理水平亟待提升",
         "贵工厂在多个维度存在严重问题，经营风险较高，改善紧迫性极强。\n"
         "⚠️ 方向建议：\n"
         "1️⃣ 强烈建议立即启动全面诊断，系统性识别问题根因\n"
         "2️⃣ 优先解决安全、质量等高风险领域问题\n"
         "3️⃣ 制定应急改善方案，快速止血止损\n"
         "4️⃣ 建议联系精益专家做现场诊断，制定整体提升路线图"),
    ]:
        if score >= t:
            # Format with line breaks preserved for HTML
            d_html = d + "<br><br>" + guidance.replace("\n", "<br>")
            return g, d_html, guidance
    return "D级 风险", "", ""


def get_sales_grade(fields):
    loss = extract_text(fields, "Q24. 以上问题每年大概造成多少损失？（单选题）")
    # 清洗选项字母（如"A. 50万以下"→"50万以下"）
    loss_clean = re.sub(r'^[A-Z][\.\s]*', '', loss) if loss else ""
    for kw, g, m in [("50万以下","C级商机",25),("50–200万","B级商机",125),
                     ("200–500万","A级商机",350),("500–1000万","S级商机",750),
                     ("1000万以上","S级商机",1500)]:
        if kw in loss_clean: return g, m, loss_clean
    return "待评估", 0, loss_clean


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

    # Top3 列表 - 添加具体改进行动建议
    top3_items = ""
    dim_tips = {
        "生产效率": {
            "detail": "设备综合效率(OEE)偏低、产线平衡损失大、瓶颈工序限制产能",
            "actions": "① 测定OEE基线 → ② 消除六大损失(停机/换型/速度/缺陷) → ③ 建立产线平衡墙"
        },
        "质量控制": {
            "detail": "不良率偏高、返工成本大、缺乏防错机制",
            "actions": "① 建立不良品统计看板 → ② 导入防错(Poka-Yoke)装置 → ③ 推行首件检验+过程检验"
        },
        "库存物流": {
            "detail": "库存周转天数长、在制品堆积严重、物料配送效率低",
            "actions": "① ABC分类法优化库存 → ② 建立拉动式配送(Kanban) → ③ 设置物料超市/水蜘蛛配送"
        },
        "设备管理": {
            "detail": "设备故障率高、缺乏TPM体系、换模时间偏长",
            "actions": "① 建立设备总账+故障记录 → ② 推行自主保全(7步法) → ③ SMED快速换型分析"
        },
        "人员效率": {
            "detail": "人员利用率低、标准化作业覆盖不足、技能依赖度高",
            "actions": "① 制定标准作业组合票 → ② 建立岗位技能矩阵 → ③ 推行多能工培训计划"
        },
        "现场管理": {
            "detail": "5S水平不高、目视化管理不足、标准化程度不够",
            "actions": "① 5S红牌作战 → ② 设置区域目视化看板 → ③ 建立现场巡检标准清单"
        },
        "计划交付": {
            "detail": "交付及时率不达标、计划频繁变动、排产不够科学",
            "actions": "① 建立MPS主生产计划 → ② 推行TOC约束排产 → ③ 设置计划达成率看板"
        },
        "数字化": {
            "detail": "信息系统覆盖不足、数据未可视化、自动化水平偏低",
            "actions": "① 选择轻量级MES系统 → ② 建立关键指标数字看板 → ③ 试点自动数据采集"
        },
    }
    for i, (n, s) in enumerate(worst):
        tip = dim_tips.get(n, {"detail": "需进一步分析", "actions": "建议安排现场深度诊断"})
        bar_w = int(s/5*100)
        top3_items += f'''
        <div class="improve-item">
            <div class="improve-num">0{i+1}</div>
            <div class="improve-info">
                <div class="improve-title">{n}（{s:.1f}分）</div>
                <div class="improve-desc">{tip["detail"]}</div>
                <div class="improve-bar"><div class="improve-bar-fill" style="width:{bar_w}%"></div></div>
                <div class="improve-action">{tip["actions"]}</div>
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

    # 智能建议：结合综合评分和最低维度
    suggestions = {
        (4.5, 5.0): "🎯 整体运营状况优秀！贵工厂已具备行业领先水平。建议：① 将优势经验标准化，打造内部标杆；② 推进数字化升级(可考虑MES/WMS)；③ 建立行业标杆示范，扩大品牌影响力。",
        (3.5, 4.5): "📈 具备良好的管理基础，改善潜力巨大。建议聚焦薄弱维度（见Top 3改善方向），短期内启动专项改善，快速见效。考虑L2轻量诊断做深度评估，获取定制化路线图。",
        (2.5, 3.5): "🔍 存在明显的改善空间和管理浪费。建议立即：① 从Top 3维度入手，实施90天快速改善；② 启动系统性诊断，绘制价值流图(VSM)；③ 导入精益管理框架(5S→TPM→标准化)。",
        (0, 2.5): "🚨 管理水平亟待提升，存在较大经营风险！强烈建议：① 立即启动全面诊断，识别关键痛点；② 制定6-12个月精益转型路径图；③ 考虑引入外部专家指导，避免走弯路。",
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
.dim-bar{{height:100%;border-radius:5px;transition:width 1s ease;animation:barGrow 1.2s ease-out}}
@keyframes barGrow{{from{{width:0%}}}}
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
.improve-action{{font-size:11px;color:#991b1b;margin-top:6px;padding:6px 10px;background:#fef2f2;border:1px solid #fecaca;border-radius:6px;line-height:1.5}}
.strength-box{{padding:14px 18px;background:#f0fdf4;border:1px solid #86efac;border-radius:12px;font-size:12px;color:#166534;margin-bottom:24px;line-height:1.6}}
/* 建议框 */
.suggestion-box{{padding:18px 22px;background:linear-gradient(135deg,#eff6ff,#dbeafe);border:1px solid #bfdbfe;border-radius:12px;margin-bottom:24px}}
.suggestion-box .sug-title{{font-size:13px;font-weight:700;color:#1e40af;margin-bottom:4px}}
.suggestion-box .sug-text{{font-size:12px;color:#1e40af;line-height:1.7}}
/* 改善潜力亮点 */
.potential-box{{background:linear-gradient(135deg,#f0fdf4,#dcfce7);border:2px solid #86efac;border-radius:16px;overflow:hidden;margin-bottom:24px}}
.potential-header{{padding:16px 20px;background:#05966914;font-size:15px;font-weight:700;color:#065f46;border-bottom:1px solid #a7f3d0;display:flex;align-items:center;gap:8px}}
.potential-body{{padding:16px 20px;font-size:13px;color:#065f46;line-height:1.7}}
.potential-body p{{margin-bottom:8px}}
.potential-benefits{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0}}
.benefit-item{{padding:10px 14px;background:#fff;border-radius:8px;font-size:13px;border:1px solid #a7f3d0}}
.benefit-icon{{margin-right:6px}}
.potential-cta-text{{font-size:12px;color:#059669;margin-top:8px;padding:10px 14px;background:#ecfdf5;border-radius:8px;border:1px solid #a7f3d0}}
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

        <!-- 经济效益分析 -->
        <div class="section-title">💰 经济效益分析</div>
        <div class="stat-grid">
            <div class="stat-item">
                <div class="stat-lbl">📉 年度预计损失</div>
                <div class="stat-val" style="color:#dc2626">{loss_label}</div>
            </div>
            <div class="stat-item">
                <div class="stat-lbl">⚡ 改善空间</div>
                <div class="stat-val" style="color:#059669">{loss_mid}万元/年</div>
            </div>
            <div class="stat-item">
                <div class="stat-lbl">📊 诊断覆盖率</div>
                <div class="stat-val" style="color:#2563eb">8/8 维度</div>
            </div>
        </div>

        <!-- 改善潜力亮点 -->
        <div class="potential-box">
            <div class="potential-header">
                <span>🎯</span>
                <span>您每年可能浪费约 <strong style="font-size:22px;color:#059669;letter-spacing:1px">{loss_mid}万元</strong></span>
            </div>
            <div class="potential-body">
                <p>根据问卷数据初步评估，贵工厂存在约 <strong>{loss_mid}万元/年</strong> 的改善空间。通过系统精益改善，通常可实现：</p>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0">
                    <div style="padding:10px 14px;background:#f0fdf4;border-radius:8px;font-size:13px;border:1px solid #a7f3d0">⬇️ 生产成本降低 <strong>10-20%</strong></div>
                    <div style="padding:10px 14px;background:#f0fdf4;border-radius:8px;font-size:13px;border:1px solid #a7f3d0">⬆️ 生产效率提升 <strong>15-30%</strong></div>
                    <div style="padding:10px 14px;background:#f0fdf4;border-radius:8px;font-size:13px;border:1px solid #a7f3d0">📦 库存周转加速 <strong>20-40%</strong></div>
                    <div style="padding:10px 14px;background:#f0fdf4;border-radius:8px;font-size:13px;border:1px solid #a7f3d0">✅ 产品合格率提高 <strong>5-15%</strong></div>
                </div>
                <div style="font-size:12px;color:#059669;margin-top:8px;padding:10px 14px;background:#ecfdf5;border-radius:8px;border:1px solid #a7f3d0">💡 以上为初步估算，实际改善空间需现场深度诊断确认。立即预约获取专属方案。</div>
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
        rating, desc, _ = get_rating(total)
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
