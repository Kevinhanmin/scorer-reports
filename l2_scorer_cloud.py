#!/usr/bin/env python3
"""
L2评分师 · 精益智造轻量诊断 (¥6,800-9,800)
==============================================
基于架构师L2方案V6（8大维度 × 45 KPI · 精益浪费映射 · 价值流效率指数）
自动从飞书多维表格读取L2问卷数据 → 计算评分 → 生成HTML诊断报告

| 方案A：直接读取飞书已有的8个维度评分字段，加权算总分+生成报告
|
|| v2 (2026-05-25): 新增付费状态过滤 —— 只处理「已付费」或「¥1已测试」记录
|| v3 (2026-05-26): 空付费状态自动填充「待付费」—— 新问卷提交后用户立即能在L2表中看到状态
"""

import os, sys, json, time, math, re
from datetime import datetime
from pathlib import Path

# ===== 环境变量 =====
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
BITABLE_APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN_L2", "JZg8bq0A3aYVU3snPxZcMKmQnid")
TABLE_ID = os.environ.get("TABLE_ID_L2", "tbl0W8eou1chYGzi")
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

REPORT_DIR = "reports"
os.makedirs(REPORT_DIR, exist_ok=True)

# ===== 8大维度权重 =====
DIMENSION_WEIGHTS = {
    "生产效率": 0.20, "质量控制": 0.15, "设备管理": 0.15,
    "库存物流": 0.10, "人员效率": 0.10, "现场管理": 0.10,
    "计划交付": 0.10, "数字化": 0.10,
}

# ===== 付费状态过滤 =====
PAYMENT_STATUS_FIELD = "付费状态"
ALLOWED_PAYMENT_STATUSES = {"¥1已测试", "已付费"}

# 新记录默认付费状态（用于填充空值）
DEFAULT_PAYMENT_STATUS = "待付费"

# ===== 飞书字段映射（维度名 → 飞书评分字段名）=====
# 修复：飞书L1表格中字段无 L2_ 前缀
DIM_SCORE_FIELDS = [
    ("生产效率", "▶ 生产效率SC"), ("质量控制", "▶ 质量控制QC"),
    ("设备管理", "▶ 设备管理EM"), ("库存物流", "▶ 库存物流IV"),
    ("人员效率", "▶ 人员效率HR"), ("现场管理", "▶ 现场管理SM"),
    ("计划交付", "▶ 计划交付SC"), ("数字化", "▶ 数字化水平DG"),
]

# ===== KPI定义（用于详细分析）=====
KPI_DEFS = {
    "PE1": ("L2_PE1_OEE_区间", "L2_PE1_OEE_精确值", {"<65%": 1, "65-75%": 3, "75-85%": 4, ">85%": 5}, 1/6, "生产效率", "等待浪费"),
    "PE2": ("L2_PE2_计划达成率_区间", "L2_PE2_计划达成率_精确值", {"<80%": 1, "80-90%": 3, "90-95%": 4, ">95%": 5}, 1/6, "生产效率", "过量生产"),
    "PE3": ("L2_PE3_设备利用率_区间", "L2_PE3_设备利用率_精确值", {"<60%": 1, "60-75%": 3, "75-85%": 4, ">85%": 5}, 1/6, "生产效率", "等待浪费"),
    "PE4": ("L2_PE4_换型时长_区间", "L2_PE4_换型时长_精确值", {">60min": 1, "30-60min": 3, "15-30min": 4, "<15min": 5}, 1/6, "生产效率", "等待浪费"),
    "PE5": ("L2_PE5_人均产值_区间", "L2_PE5_人均产值_精确值", {"<5万": 1, "5-10万": 3, "10-20万": 4, ">20万": 5}, 1/6, "生产效率", "动作浪费"),
    "PE6": ("L2_PE6_产线平衡率_区间", "L2_PE6_产线平衡率_精确值", {"<60%": 1, "60-70%": 3, "70-80%": 4, ">80%": 5}, 1/6, "生产效率", "等待浪费"),
    "QC1": ("L2_QC1_FPY_区间", "L2_QC1_FPY_精确值", {"<90%": 1, "90-95%": 3, "95-98%": 4, ">98%": 5}, 1/6, "质量控制", "不良品浪费"),
    "QC2": ("L2_QC2_不良率_区间", "L2_QC2_不良率_精确值", {">5%": 1, "2-5%": 3, "1-2%": 4, "<1%": 5}, 1/6, "质量控制", "不良品浪费"),
    "QC3": ("L2_QC3_客诉批次_区间", "L2_QC3_客诉批次_精确值", {">5次": 1, "3-5次": 3, "1-2次": 4, "0次": 5}, 1/6, "质量控制", "不良品浪费"),
    "QC4": ("L2_QC4_返工率_区间", "L2_QC4_返工率_精确值", {">10%": 1, "5-10%": 3, "2-5%": 4, "<2%": 5}, 1/6, "质量控制", "不良品浪费"),
    "QC5": ("L2_QC5_IQC覆盖率_区间", "L2_QC5_IQC覆盖率_精确值", {"<50%": 1, "50-80%": 3, "80-95%": 4, ">95%": 5}, 1/6, "质量控制", "不良品浪费"),
    "QC6": ("L2_QC6_防错覆盖率_区间", "L2_QC6_防错覆盖率_精确值", {"未开展": 1, "试点中": 3, "部分推行": 4, "全面推行": 5}, 1/6, "质量控制", "不良品浪费"),
    "EM1": ("L2_EM1_MTBF_区间", "L2_EM1_MTBF_精确值", {"<100h": 1, "100-300h": 3, "300-500h": 4, ">500h": 5}, 1/5, "设备管理", "等待浪费"),
    "EM2": ("L2_EM2_MTTR_区间", "L2_EM2_MTTR_精确值", {">4h": 1, "2-4h": 3, "1-2h": 4, "<1h": 5}, 1/5, "设备管理", "等待浪费"),
    "EM3": ("L2_EM3_设备完好率_区间", "L2_EM3_设备完好率_精确值", {"<80%": 1, "80-90%": 3, "90-95%": 4, ">95%": 5}, 1/5, "设备管理", "等待浪费"),
    "EM4": ("L2_EM4_TPM覆盖率_区间", "L2_EM4_TPM覆盖率_精确值", {"未开展": 1, "试点中": 3, "部分推行": 4, "全面推行": 5}, 1/5, "设备管理", "等待浪费"),
    "EM5": ("L2_EM5_备件管理_区间", "L2_EM5_备件管理_精确值", {"无管理": 1, "手工台账": 3, "安全库存": 4, "系统管理": 5}, 1/5, "设备管理", "库存浪费"),
    "IV1": ("L2_IV1_原材料周转_区间", "L2_IV1_原材料周转_精确值", {">60天": 1, "30-60天": 3, "15-30天": 4, "<15天": 5}, 1/5, "库存物流", "库存浪费"),
    "IV2": ("L2_IV2_WIP天数_区间", "L2_IV2_WIP天数_精确值", {">20天": 1, "10-20天": 3, "5-10天": 4, "<5天": 5}, 1/5, "库存物流", "库存浪费"),
    "IV3": ("L2_IV3_成品周转_区间", "L2_IV3_成品周转_精确值", {">30天": 1, "15-30天": 3, "7-15天": 4, "<7天": 5}, 1/5, "库存物流", "库存浪费"),
    "IV4": ("L2_IV4_配送准时率_区间", "L2_IV4_配送准时率_精确值", {"<70%": 1, "70-85%": 3, "85-95%": 4, ">95%": 5}, 1/5, "库存物流", "搬运浪费"),
    "IV5": ("L2_IV5_拉动方式_区间", "L2_IV5_拉动方式_精确值", {"批量推动": 1, "依计划配送": 3, "看板拉动": 4, "连续流": 5}, 1/5, "库存物流", "过量生产"),
    "HR1": ("L2_HR1_直间比_区间", "L2_HR1_直间比_精确值", {">5:1": 1, "3:1-5:1": 3, "2:1-3:1": 4, "<2:1": 5}, 1/5, "人员效率", "动作浪费"),
    "HR2": ("L2_HR2_标准作业覆盖率_区间", "L2_HR2_标准作业覆盖率_精确值", {"<30%": 1, "30-60%": 3, "60-85%": 4, ">85%": 5}, 1/5, "人员效率", "动作浪费"),
    "HR3": ("L2_HR3_多能工比例_区间", "L2_HR3_多能工比例_精确值", {"<10%": 1, "10-25%": 3, "25-50%": 4, ">50%": 5}, 1/5, "人员效率", "等待浪费"),
    "HR4": ("L2_HR4_离职率_区间", "L2_HR4_离职率_精确值", {">10%": 1, "5-10%": 3, "3-5%": 4, "<3%": 5}, 1/5, "人员效率", ""),
    "HR5": ("L2_HR5_改善提案参与率_区间", "L2_HR5_改善提案参与率_精确值", {"无机制": 1, "<10%": 2, "10-30%": 4, ">30%": 5}, 1/5, "人员效率", ""),
    "SM1": ("L2_SM1_5S水平_区间", "L2_SM1_5S水平_精确值", {"整理前": 1, "整理/整顿": 3, "清扫/清洁": 4, "素养阶段": 5}, 1/5, "现场管理", "动作浪费"),
    "SM2": ("L2_SM2_目视化覆盖率_区间", "L2_SM2_目视化覆盖率_精确值", {"<20%": 1, "20-50%": 3, "50-80%": 4, ">80%": 5}, 1/5, "现场管理", ""),
    "SM3": ("L2_SM3_响应时间_区间", "L2_SM3_响应时间_精确值", {">30min": 1, "15-30min": 3, "5-15min": 4, "<5min": 5}, 1/5, "现场管理", "等待浪费"),
    "SM4": ("L2_SM4_安全事件_区间", "L2_SM4_安全事件_精确值", {">5次": 1, "3-5次": 3, "1-2次": 4, "0次": 5}, 1/5, "现场管理", ""),
    "SM5": ("L2_SM5_巡检制度_区间", "L2_SM5_巡检制度_精确值", {"无": 1, "定期巡检": 3, "标准化检查清单": 5}, 1/5, "现场管理", ""),
    "SC1": ("L2_SC1_OTD_区间", "L2_SC1_OTD_精确值", {"<70%": 1, "70-85%": 3, "85-95%": 4, ">95%": 5}, 1/5, "计划交付", ""),
    "SC2": ("L2_SC2_排程方式_区间", "L2_SC2_排程方式_精确值", {"手工": 1, "Excel": 3, "ERP": 4, "MES自动排程": 5}, 1/5, "计划交付", "过量生产"),
    "SC3": ("L2_SC3_紧急插单_区间", "L2_SC3_紧急插单_精确值", {">30%": 1, "15-30%": 3, "5-15%": 4, "<5%": 5}, 1/5, "计划交付", "过量生产"),
    "SC4": ("L2_SC4_产能负荷率_区间", "L2_SC4_产能负荷率_精确值", {">110%": 1, "95-110%": 3, "80-95%": 4, "<80%": 5}, 1/5, "计划交付", ""),
    "SC5": ("L2_SC5_瓶颈管理_区间", "L2_SC5_瓶颈管理_精确值", {"未识别": 1, "已知但不监控": 3, "定期监控": 4, "动态管理": 5}, 1/5, "计划交付", "等待浪费"),
    "DG1": ("L2_DG1_ERP模块_区间", "L2_DG1_ERP模块_精确值", {"无": 1, "1-3个": 3, "4-6个": 4, ">6个": 5}, 1/5, "数字化", ""),
    "DG2": ("L2_DG2_MES覆盖_区间", "L2_DG2_MES覆盖_精确值", {"未使用": 1, "<30%": 2, "30-70%": 4, ">70%": 5}, 1/5, "数字化", ""),
    "DG3": ("L2_DG3_设备联网率_区间", "L2_DG3_设备联网率_精确值", {"<10%": 1, "10-30%": 3, "30-60%": 4, ">60%": 5}, 1/5, "数字化", ""),
    "DG4": ("L2_DG4_数据可视化_区间", "L2_DG4_数据可视化_精确值", {"<10%": 1, "10-30%": 3, "30-60%": 4, ">60%": 5}, 1/5, "数字化", ""),
    "DG5": ("L2_DG5_决策支持_区间", "L2_DG5_决策支持_精确值", {"无": 1, "手工报表": 3, "BI看板": 4, "智能预警": 5}, 1/5, "数字化", ""),
}

# ===== 精益浪费映射 =====
WASTE_DIMENSIONS = {
    "等待浪费": {"生产效率": 0.40, "设备管理": 0.35, "现场管理": 0.25},
    "不良品浪费": {"质量控制": 1.0},
    "库存浪费": {"库存物流": 0.80, "设备管理": 0.20},
    "动作浪费": {"人员效率": 0.55, "现场管理": 0.45},
    "过量生产": {"生产效率": 0.50, "计划交付": 0.50},
    "搬运浪费": {"库存物流": 1.0},
}


# ===== 飞书API核心 =====
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


def extract_text(fields, name):
    raw = fields.get(name, "")
    if isinstance(raw, str): return raw
    if isinstance(raw, list):
        return "".join(item.get("text","") for item in raw if isinstance(item,dict))
    if isinstance(raw, dict):
        vals = raw.get("value",[])
        return str(vals[0]) if vals else ""
    return str(raw)


# ===== 评分引擎 =====
def compute_dim_scores(fields):
    """读取8个维度评分字段，返回 (kpi_results, dim_scores)"""
    dim_scores = {}
    for dim, field_name in DIM_SCORE_FIELDS:
        raw = fields.get(field_name)
        if isinstance(raw, (int, float)):
            dim_scores[dim] = float(raw)
        elif isinstance(raw, dict):
            vals = raw.get("value", [])
            dim_scores[dim] = float(vals[0]) if vals else 0.0
        elif isinstance(raw, str) and raw.replace('.','',1).isdigit():
            dim_scores[dim] = float(raw)
        else:
            dim_scores[dim] = 0.0
    return None, dim_scores


def compute_total(dim_scores):
    return round(sum(dim_scores.get(d, 0) * w for d, w in DIMENSION_WEIGHTS.items()), 3)


def get_rating(score):
    for t, g, d, praise, guidance in [
        (4.5, "A级 卓越", "精益管理成熟",
         "贵工厂整体管理水平处于行业领先地位，各项指标表现优秀，值得充分肯定！",
         "💡 方向建议：\n1️⃣ 保持现有管理优势，将成功经验标准化和流程化\n2️⃣ 推进数字化升级，建立行业标杆示范效应\n3️⃣ 关注前沿管理技术，持续保持竞争优势"),
        (3.5, "B级 良好", "有精益基础",
         "贵工厂具备良好的管理基础，多数维度表现平稳，值得肯定！",
         "💡 方向建议：\n1️⃣ 针对评分较低维度做专项改善\n2️⃣ 建议安排L2深度诊断对薄弱环节进行精准评估\n3️⃣ 建立持续改善机制，避免管理优势下滑"),
        (2.5, "C级 注意", "精益基础薄弱",
         "贵工厂已迈出精益管理的第一步，主动诊断评估本身就是进步。",
         "💡 方向建议：\n1️⃣ 立即启动系统性L2诊断，全面评估改善机会\n2️⃣ 优先改善Top3薄弱维度，快速见效\n3️⃣ 制定90天改善计划，设定可量化目标"),
        (0, "D级 风险", "管理水平亟待提升",
         "正视自身问题、主动寻求改进，这本身就是企业家的远见和担当。",
         "⚠️ 方向建议：\n1️⃣ 强烈建议立即启动全面诊断\n2️⃣ 优先解决安全、质量等高风险领域\n3️⃣ 制定应急改善方案，快速止血止损"),
    ]:
        if score >= t:
            d_html = d + "<br><br>👍 " + praise + "<br><br>" + guidance.replace("\n", "<br>")
            return g, d_html, praise + "\n" + guidance
    return "D级 风险", "", ""


# ===== 飞书消息通知 =====
def send_feishu_message(open_id, title, content):
    msg = {
        "receive_id": open_id,
        "msg_type": "text",
        "content": json.dumps({"text": content})
    }
    result = feishu_api("POST", "/im/v1/messages?receive_id_type=open_id", msg)
    if result.get("code") == 0:
        print(f"   📨 消息已发送")
    else:
        print(f"   ⚠️ 消息发送失败: {result.get('msg','')}")
    return result


def send_report_card(open_id, company, total, rating, report_url):
    """发送L2报告卡片通知"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    grade_emoji = {"A": "🏆", "B": "📊", "C": "🔍", "D": "⚠️"}.get(rating[0], "📋")

    msg = {
        "receive_id": open_id,
        "msg_type": "interactive",
        "content": json.dumps({
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"{grade_emoji} L2轻量诊断报告 - {company}"},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**企业名称：** {company}"}},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**综合评分：** {total:.2f} / 5.00  **·**  **评级：** {rating}"}},
                {"tag": "hr"},
                {"tag": "action", "actions": [{
                    "tag": "button",
                    "type": "primary",
                    "text": {"tag": "plain_text", "content": "📄 查看完整L2诊断报告"},
                    "multi_url": {
                        "url": report_url,
                        "android_url": report_url,
                        "ios_url": report_url,
                        "pc_url": report_url
                    }
                }]},
                {"tag": "note", "text": {"tag": "plain_text", "content": f"思派工业 · L2评分师 自动生成 · {now}"}}
            ]
        })
    }
    result = feishu_api("POST", "/im/v1/messages?receive_id_type=open_id", msg)
    if result.get("code") == 0:
        print(f"   🃏 L2报告卡片已发送")
    else:
        print(f"   ⚠️ 卡片发送失败: {result.get('msg','')}")
    return result


# ===== L2 HTML报告生成 =====
def gen_html(company, contact, dim_kpi_results, dim_scores, total, rating, rating_desc, future_data):
    """生成L2轻量诊断HTML报告"""
    now_str = datetime.now().strftime("%Y-%m-%d")
    diag_id = datetime.now().strftime("L2-%Y%m%d%H%M")
    sorted_dims = sorted(dim_scores.items(), key=lambda x: x[1])
    worst = sorted_dims[:3]
    best = sorted_dims[-1:] if sorted_dims else []

    def sc(s):
        if s >= 4.5: return "#16a34a", "优秀"
        if s >= 3.5: return "#65a30d", "良好"
        if s >= 2.5: return "#ca8a04", "关注"
        if s >= 1.5: return "#f97316", "薄弱"
        return "#dc2626", "风险"

    # 维度进度条
    bars = ""
    for n, s in sorted_dims:
        p = int(s/5*100); c, lbl = sc(s)
        bars += f'''
        <div class="dim-row">
            <div class="dim-label">{n} <span class="dim-tag {lbl}">{lbl}</span></div>
            <div class="dim-bar-wrap"><div class="dim-bar" style="width:{p}%;background:{c}"></div></div>
            <div class="dim-score" style="color:{c}">{s:.1f}</div>
        </div>'''

    # Top3 改善建议
    dim_tips = {
        "生产效率": ("设备综合效率(OEE)偏低、产线平衡损失大、瓶颈工序限制产能", "① 测定OEE基线 → ② 消除六大损失 → ③ 建立产线平衡墙"),
        "质量控制": ("不良率偏高、返工成本大、缺乏防错机制", "① 建立不良品统计看板 → ② 导入防错(Poka-Yoke) → ③ 推行首件检验+过程检验"),
        "库存物流": ("库存周转天数长、在制品堆积严重、物料配送效率低", "① ABC分类法优化 → ② 建立拉动式配送(Kanban) → ③ 设置物料超市"),
        "设备管理": ("设备故障率高、缺乏TPM体系、换模时间偏长", "① 建立设备总账+故障记录 → ② 推行自主保全(7步法) → ③ SMED快速换型"),
        "人员效率": ("人员利用率低、标准化作业覆盖不足、技能依赖度高", "① 制定标准作业组合票 → ② 建立岗位技能矩阵 → ③ 推行多能工培训"),
        "现场管理": ("5S水平不高、目视化管理不足、标准化程度不够", "① 5S红牌作战 → ② 设置区域目视化看板 → ③ 建立巡检标准清单"),
        "计划交付": ("交付及时率不达标、计划频繁变动、排产不够科学", "① 建立MPS主生产计划 → ② 推行TOC约束排产 → ③ 设置计划达成率看板"),
        "数字化": ("信息系统覆盖不足、数据未可视化、自动化水平偏低", "① 选择轻量级MES系统 → ② 建立关键指标数字看板 → ③ 试点自动数据采集"),
    }
    top3_items = ""
    for i, (n, s) in enumerate(worst):
        detail, actions = dim_tips.get(n, ("需进一步分析", "建议安排现场深度诊断"))
        bar_w = int(s/5*100)
        top3_items += f'''
        <div class="improve-item">
            <div class="improve-num">0{i+1}</div>
            <div class="improve-info">
                <div class="improve-title">{n}（{s:.1f}分）</div>
                <div class="improve-desc">{detail}</div>
                <div class="improve-bar"><div class="improve-bar-fill" style="width:{bar_w}%"></div></div>
                <div class="improve-action">{actions}</div>
            </div>
        </div>'''

    # 优势维度
    best_html = ""
    if best:
        n, s = best[0]; c, lbl = sc(s)
        best_html = f'<div class="strength-box"><span class="strength-icon">⭐</span><span class="strength-label">优势维度</span> <strong>{n}</strong>（{s:.1f}分）— 保持此优势并转化为核心竞争力</div>'

    # 评级颜色
    grade_color = {"A": "#16a34a", "B": "#65a30d", "C": "#ca8a04", "D": "#dc2626"}
    grade_key = rating[0] if rating else "C"
    gc = grade_color.get(grade_key, "#ca8a04")

    # 智能建议
    suggestions = {
        (4.5, 5.0): "🎯 整体运营状况优秀！建议：① 将优势经验标准化，打造内部标杆；② 推进数字化升级(MES/WMS)；③ 建立行业标杆示范。",
        (3.5, 4.5): "📈 具备良好的管理基础，改善潜力巨大。建议聚焦薄弱维度（见Top 3改善方向），短期内启动专项改善。",
        (2.5, 3.5): "🔍 存在明显的改善空间和管理浪费。建议立即：① 从Top 3维度入手实施90天快速改善；② 启动系统性诊断绘制VSM价值流图；③ 导入精益管理框架。",
        (0, 2.5): "🚨 管理水平亟待提升！强烈建议：① 立即启动全面诊断；② 制定6-12个月精益转型路径图；③ 考虑引入外部专家指导。",
    }
    suggestion = ""
    for (lo, hi), text in sorted(suggestions.items(), reverse=True):
        if lo <= total < hi:
            suggestion = text
            break

    # L2未来方向汇总（适配L2真实字段名）
    future_html = ""
    if future_data:
        items = [
            (label, val) for label, val in [
            ("🏭 智能工厂规划", future_data.get("智能工厂规划", "")),
            ("⚠️ 智能化瓶颈", future_data.get("智能化瓶颈", "")),
            ("🌿 绿色制造认知", future_data.get("绿色制造认知", "")),
            ("📉 碳排需求", future_data.get("碳排需求", "")),
        ]]
        future_html = '<div class="section-title">🔮 未来方向评估</div>'
        for lbl, val in items:
            if val and val != "未填":
                future_html += f'<div style="padding:6px 0;font-size:13px;color:#334155"><strong>{lbl}：</strong>{val}</div>'
        ai_interest = future_data.get("AI兴趣", "")
        if ai_interest:
            future_html += '<div style="margin-top:8px"><strong style="font-size:12px;color:#64748b">AI应用兴趣：</strong> '
            future_html += ai_interest + "</div>"

    # 损失估算 (L2按中等水平估算)
    loss_label = "100-300万元"
    loss_mid = 200

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>L2精益智能工厂轻量诊断报告 - {company}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:#f0f2f5;color:#1d1d1f;padding:20px;line-height:1.6}}
.c{{max-width:800px;margin:0 auto;background:#fff;border-radius:20px;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,.08)}}
.h{{background:linear-gradient(135deg,#1e3a5f,#2563eb,#7c3aed);padding:40px 44px 32px;color:#fff;position:relative;overflow:hidden}}
.h::before{{content:'';position:absolute;top:-60px;right:-60px;width:200px;height:200px;border-radius:50%;background:rgba(255,255,255,.04)}}
.h .l{{display:inline-block;background:rgba(255,255,255,.12);padding:4px 14px;border-radius:20px;font-size:12px;letter-spacing:.5px;backdrop-filter:blur(4px);margin-bottom:14px;border:1px solid rgba(255,255,255,.08)}}
.h h1{{font-size:26px;font-weight:700;letter-spacing:-.5px}}
.h .cname{{font-size:16px;opacity:.9;margin-top:10px}}
.h .meta{{display:flex;gap:24px;margin-top:12px;font-size:12px;opacity:.65}}
.b{{padding:32px 40px 28px}}
.score-card{{display:flex;gap:28px;padding:24px 28px;background:linear-gradient(135deg,#f8fafc,#f1f5f9);border-radius:16px;margin-bottom:28px;border:1px solid #e2e8f0}}
.score-big{{text-align:center;min-width:100px}}
.score-big .num{{font-size:48px;font-weight:800;color:#0f172a;line-height:1}}
.score-big .denom{{font-size:14px;color:#94a3b8;margin-top:2px}}
.score-big .grade-badge{{display:inline-block;margin-top:8px;padding:3px 14px;border-radius:20px;font-size:13px;font-weight:600;color:#fff;background:{gc}}}
.score-info{{flex:1}}
.score-info .g{{font-size:18px;font-weight:700;color:#0f172a}}
.score-info .g .tag{{display:inline-block;margin-left:8px;padding:2px 12px;font-size:11px;border-radius:12px;background:{gc}20;color:{gc};font-weight:600}}
.score-info .d{{font-size:13px;color:#64748b;margin-top:6px;line-height:1.6}}
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
.stat-grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:24px}}
.stat-item{{text-align:center;padding:18px 12px;background:#f8fafc;border-radius:12px;border:1px solid #e2e8f0}}
.stat-item .stat-lbl{{font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px}}
.stat-item .stat-val{{font-size:22px;font-weight:800}}
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
.suggestion-box{{padding:18px 22px;background:linear-gradient(135deg,#eff6ff,#dbeafe);border:1px solid #bfdbfe;border-radius:12px;margin-bottom:24px}}
.suggestion-box .sug-title{{font-size:13px;font-weight:700;color:#1e40af;margin-bottom:4px}}
.suggestion-box .sug-text{{font-size:12px;color:#1e40af;line-height:1.7}}
.potential-box{{background:linear-gradient(135deg,#f0fdf4,#dcfce7);border:2px solid #86efac;border-radius:16px;overflow:hidden;margin-bottom:24px}}
.potential-header{{padding:16px 20px;background:#05966914;font-size:15px;font-weight:700;color:#065f46;border-bottom:1px solid #a7f3d0;display:flex;align-items:center;gap:8px}}
.potential-body{{padding:16px 20px;font-size:13px;color:#065f46;line-height:1.7}}
.cta{{text-align:center;padding:28px 24px;background:linear-gradient(135deg,#1e3a5f,#7c3aed);border-radius:16px;color:#fff;margin-top:20px}}
.cta h3{{font-size:17px;font-weight:700;letter-spacing:-.3px}}
.cta p{{font-size:13px;opacity:.85;margin:8px 0 16px;line-height:1.6}}
.cta .cta-btn{{display:inline-block;padding:12px 32px;background:#2563eb;color:#fff;border-radius:30px;font-size:14px;font-weight:600;text-decoration:none;transition:all .2s;border:1px solid rgba(255,255,255,.15)}}
.footer{{text-align:center;padding:18px;font-size:10px;color:#94a3b8;border-top:1px solid #e2e8f0}}
.footer .brand{{font-size:11px;font-weight:600;color:#64748b;margin-bottom:2px}}
.disclaimer{{font-size:9px;color:#94a3b8;padding:12px 18px;background:#f8fafc;border-radius:8px;margin-top:12px;line-height:1.5}}
</style>
</head>
<body>
<div class="c">
    <div class="h">
        <div class="l">🔬 L2轻量诊断报告</div>
        <h1>精益智能工厂 · 深度诊断分析报告</h1>
        <div class="cname">🏢 {company}</div>
        <div class="meta"><span>📅 报告日期：{now_str}</span><span>📋 报告编号：{diag_id}</span><span>📊 版本：L2-v1.0</span></div>
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
                <div class="g">L2综合诊断评分 · 8维度深度评估<span class="tag">{rating}</span></div>
                <div class="d">{rating_desc}</div>
            </div>
        </div>

        <!-- 八维诊断评分 -->
        <div class="section-title">📊 八维深度诊断评分体系</div>
        {bars}

        <!-- 经济效益分析 -->
        <div class="section-title">💰 经济效益评估</div>
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
                <div class="stat-lbl">📊 诊断覆盖</div>
                <div class="stat-val" style="color:#2563eb">8/8 维度</div>
            </div>
        </div>

        <!-- 改善潜力 -->
        <div class="potential-box">
            <div class="potential-header">
                <span>🎯</span>
                <span>您每年可能浪费约 <strong style="font-size:22px;color:#059669;letter-spacing:1px">{loss_mid}万元</strong></span>
            </div>
            <div class="potential-body">
                <p>基于L2轻量诊断评估，贵工厂存在约 <strong>{loss_mid}万元/年</strong> 的改善空间。通过系统精益改善可实现：</p>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0">
                    <div style="padding:10px 14px;background:#f0fdf4;border-radius:8px;font-size:13px;border:1px solid #a7f3d0">⬇️ 生产成本降低 <strong>15-25%</strong></div>
                    <div style="padding:10px 14px;background:#f0fdf4;border-radius:8px;font-size:13px;border:1px solid #a7f3d0">⬆️ 生产效率提升 <strong>20-35%</strong></div>
                    <div style="padding:10px 14px;background:#f0fdf4;border-radius:8px;font-size:13px;border:1px solid #a7f3d0">📦 库存周转加速 <strong>25-45%</strong></div>
                    <div style="padding:10px 14px;background:#f0fdf4;border-radius:8px;font-size:13px;border:1px solid #a7f3d0">✅ 产品合格率提高 <strong>8-20%</strong></div>
                </div>
                <div style="font-size:12px;color:#059669;margin-top:8px;padding:10px 14px;background:#ecfdf5;border-radius:8px;border:1px solid #a7f3d0">💡 以上为L2轻量诊断估算，实际改善空间需L3现场深度诊断确认。</div>
            </div>
        </div>

        <!-- 优势 -->
        {best_html}

        <!-- 紧急改善方向 -->
        <div class="section-title">🔴 优先改善方向（Top 3）</div>
        <div class="improve-list">{top3_items}</div>

        <!-- 专家建议 -->
        <div class="suggestion-box">
            <div class="sug-title">🎯 L2专家诊断建议</div>
            <div class="sug-text">{suggestion}</div>
        </div>

        <!-- 未来方向 -->
        {future_html}

        <!-- 行动号召 -->
        <div class="cta">
            <h3>📞 需要现场深度诊断（L3）？</h3>
            <p>以上为L2轻量诊断结果。如需获取针对您工厂的详细改善路线图<br>以及量化的投入产出分析，请联系我们安排L3现场深度诊断。</p>
            <a class="cta-btn" href="https://sptechsz.com/" target="_blank">📋 预约L3现场深度诊断 →</a>
            <div style="font-size:11px;opacity:.6;margin-top:12px">联系微信客服 · 思派工业技术（深圳）有限公司</div>
        </div>

        <div class="disclaimer">📌 免责声明：本报告由精益智能工厂L2诊断系统基于问卷数据自动生成，旨在提供深度参考。报告中的评分、损失估算、改善建议等均为基于问卷信息的专业判断，不代表最终诊断结论。如需准确的工厂诊断报告，请联系思派工业技术安排L3现场深度诊断。</div>
    </div>
    <div class="footer">
        <div class="brand">思派工业技术（深圳）有限公司 · 精益智能工厂领航员</div>
        <div>© 2026 思派工业技术 · L2评分师自动生成 · 诊断编号：{diag_id}</div>
    </div>
</div>
</body>
</html>'''


# ===== 主流程 =====
def main():
    print(f"🤖 L2评分师启动 (¥6,800轻量诊断 · 方案A · v2付费过滤)")
    print(f"   📋 仅处理付费状态为「{', '.join(sorted(ALLOWED_PAYMENT_STATUSES))}」的记录")
    missing = [v for v in ["FEISHU_APP_ID","FEISHU_APP_SECRET"] if not os.environ.get(v)]
    if missing:
        print(f"❌ 缺少环境变量: {missing}")
        sys.exit(1)

    # 获取所有记录
    print("🔍 获取飞书L2深度诊断表数据...")
    records = []
    pt = None
    while True:
        r = feishu_api("GET", f"/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{TABLE_ID}/records?page_size=50" + (f"&page_token={pt}" if pt else ""))
        items = r.get("data",{}).get("items",[])
        records.extend(items)
        if not r.get("data",{}).get("has_more"): break
        pt = r.get("data",{}).get("page_token")
    print(f"   共 {len(records)} 条记录（均视为L2深度诊断记录）")

    # L2表所有记录都是深度诊断数据，无「诊断层级」字段
    # 处理有综合得分的记录
    processed = 0
    for rec in records:
        fields = rec.get("fields",{})
        rid = rec["record_id"]
        company = extract_text(fields, "企业名称") or "未知企业"

        # ===== 付费状态过滤（v2）=====
        payment_status = extract_text(fields, PAYMENT_STATUS_FIELD) or ""

        # v3: 空付费状态自动填充「待付费」——新问卷提交后用户能看到状态
        if not payment_status or payment_status.strip() == "":
            print(f"   🏷️ {company}: 付费状态为空 → 写入「{DEFAULT_PAYMENT_STATUS}」")
            feishu_api("PUT", f"/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{TABLE_ID}/records/{rid}",
                {"fields": {PAYMENT_STATUS_FIELD: DEFAULT_PAYMENT_STATUS}})
            payment_status = DEFAULT_PAYMENT_STATUS
            continue  # 新记录标记为待付费，跳过评分（等支付后再处理）

        if payment_status not in ALLOWED_PAYMENT_STATUSES:
            print(f"   ⏭️ {company}: 付费状态为「{payment_status}」，跳过（仅处理{', '.join(sorted(ALLOWED_PAYMENT_STATUSES))}）")
            continue

        # 读取已有公式计算的综合分和评级
        total_raw = fields.get("▶ 综合得分")
        # 如果综合得分为空或0，跳过（尚未填完KPI）
        if not total_raw:
            continue

        try:
            total = float(total_raw)
        except (TypeError, ValueError):
            continue

        rating_raw = extract_text(fields, "▶ 评级判定") or ""
        rating = rating_raw if rating_raw else get_rating(total)[0]
        maturity_raw = extract_text(fields, "▶ 成熟度") or ""

        # 直接从L2表字段读取各维度分
        dim_scores = {}
        for dim, field_name in DIM_SCORE_FIELDS:
            raw = fields.get(field_name)
            if isinstance(raw, (int, float)):
                dim_scores[dim] = float(raw)
            elif isinstance(raw, dict):
                vals = raw.get("value", [])
                dim_scores[dim] = float(vals[0]) if vals else 0.0
            elif isinstance(raw, str) and raw.replace('.','',1).lstrip('-').isdigit():
                dim_scores[dim] = float(raw)
            else:
                dim_scores[dim] = 0.0

        # 用已有的评级描述，如果评级为空则用get_rating推算
        _, rating_desc, _ = get_rating(total)
        if rating:
            # 使用已有的评级
            pass

        # 收集未来方向数据（使用L2表中实际的中文字段名）
        future_data = {}
        # L2表字段：您对智能工厂建设的认知和规划处于哪个阶段 (type 3=选择)
        # L2表字段：您认为当前智能化推进的最大瓶颈是 (type 3=选择)
        # L2表字段：贵公司对绿色制造（碳中和/节能减排）的关注度 (type 3=选择)
        # L2表字段：贵公司是否有碳排放报告或ESG披露需求 (type 3=选择)
        # L2表字段：请勾选您认为AI最可能在生产管理中发挥价值的环节 (type 4=多选)
        smart_factory = extract_text(fields, "您对智能工厂建设的认知和规划处于哪个阶段") or ""
        bottleneck = extract_text(fields, "您认为当前智能化推进的最大瓶颈是") or ""
        green_aware = extract_text(fields, "贵公司对绿色制造（碳中和/节能减排）的关注度") or ""
        carbon_need = extract_text(fields, "贵公司是否有碳排放报告或ESG披露需求") or ""

        # AI兴趣是多选字段(type 4)
        ai_raw = fields.get("请勾选您认为AI最可能在生产管理中发挥价值的环节", [])
        if isinstance(ai_raw, list):
            ai_interests = []
            for item in ai_raw:
                text = item.get("text", "") if isinstance(item, dict) else str(item)
                if text:
                    ai_interests.append(text)
            ai_list = ", ".join(ai_interests)
        else:
            ai_list = ""

        future_data = {
            "智能工厂规划": smart_factory,
            "智能化瓶颈": bottleneck,
            "绿色制造认知": green_aware,
            "碳排需求": carbon_need,
            "AI兴趣": ai_list,
        }

        # L2表没有「联系人」字段，用企业名作为display
        contact = company

        print(f"   处理: {company} | 综合{total:.2f} → {rating} | 成熟度:{maturity_raw} | 付费:{payment_status}")

        # 生成报告
        html = gen_html(company, contact, None, dim_scores, total, rating, rating_desc, future_data)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_name = "".join(c for c in company if c.isascii() and (c.isalnum() or c in " _-_"))[:20] or ""
        safe_name = safe_name.strip().replace(" ", "_") if safe_name else rid[:8]
        rpath = f"{REPORT_DIR}/l2_{safe_name}_{ts}.html"
        with open(rpath, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"   📄 报告: {rpath}")

        # 暂存通知
        pages_url = os.environ.get("PAGES_URL", "")
        if pages_url:
            report_url = f"{pages_url}/reports/{os.path.basename(rpath)}"
        else:
            report_url = f"https://sptechsz.com/reports/{os.path.basename(rpath)}"

        # 写回L2_报告URL到飞书，供报告查询页使用
        if rid:
            try:
                feishu_api("PUT", f"/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{TABLE_ID}/records/{rid}",
                    {"fields": {"L2_报告URL": report_url}})
                print(f"   🔗 L2_报告URL已写入飞书: {report_url}")
            except Exception as e:
                print(f"   ⚠️ 写入L2_报告URL失败: {e}")

        founder_open_id = os.environ.get("FOUNDER_OPEN_ID", "ou_654b4ab922a747e21af74eaa4884a914")
        pending_file = "pending_notifications.json"
        notif = {
            "open_id": founder_open_id, "company": company,
            "total": total, "rating": rating,
            "report_url": report_url, "timestamp": datetime.now().isoformat()
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
        print(f"   📝 通知已暂存")
        processed += 1

    print(f"\n✅ 完成！处理了 {processed} 条L2记录")
    return processed


def send_pending_notifications():
    pending_file = "pending_notifications.json"
    if not os.path.exists(pending_file):
        print("📭 没有待发送的L2通知")
        return 0
    with open(pending_file, "r") as pf:
        pending = json.load(pf)
    if not pending:
        print("📭 没有待发送的L2通知")
        return 0
    print(f"📨 发送 {len(pending)} 条L2待处理通知...")
    sent = 0
    for n in pending:
        try:
            send_report_card(
                n["open_id"], n["company"],
                n["total"], n["rating"], n["report_url"]
            )
            sent += 1
            time.sleep(1)
        except Exception as e:
            print(f"   ⚠️ 通知发送失败: {e}")
    os.remove(pending_file)
    print(f"✅ 已发送 {sent} 条通知")
    return sent


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--notify":
        send_pending_notifications()
    else:
        main()
