#!/usr/bin/env python3
"""
报告查询页自动更新脚本
========================
从飞书获取所有L1/L2记录 → 解析联系人手机号 → 更新 report_query.html

运行方式：
  python update_report_query.py
  
环境变量：
  FEISHU_APP_ID, FEISHU_APP_SECRET (通过 ~/.scorer_env 或 GitHub Secrets)
  BITABLE_APP_TOKEN, TABLE_ID (L1表)
  L2_BITABLE_APP_TOKEN, L2_TABLE_ID (可选，L2表)
"""
import os, sys, json, re
from datetime import datetime

# ===== 配置 =====
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
BITABLE_APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN", "VU3hbjRyuabLhAseoK3ckzOzndg")
TABLE_ID = os.environ.get("TABLE_ID", "tblofr6TCloHk5Zb")
L2_BITABLE_APP_TOKEN = os.environ.get("L2_BITABLE_APP_TOKEN", "JZg8bq0A3aYVU3snPxZcMKmQnid")
L2_TABLE_ID = os.environ.get("L2_TABLE_ID", "tbl0W8eou1chYGzi")
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

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

def extract_text(fields, name):
    raw = fields.get(name, "")
    if isinstance(raw, str): return raw
    if isinstance(raw, list):
        return "".join(item.get("text","") for item in raw if isinstance(item,dict))
    if isinstance(raw, dict):
        vals = raw.get("value",[])
        return str(vals[0]) if vals else ""
    return str(raw)

def extract_phone(contact_text):
    """从联系人文本中提取手机号"""
    if not contact_text:
        return None
    # 查找11位手机号
    phones = re.findall(r'1[3-9]\d{9}', contact_text)
    if phones:
        return phones[0]
    # 查找带连字符的号码
    phones = re.findall(r'1[3-9]\d[-\s]?\d{4}[-\s]?\d{4}', contact_text)
    if phones:
        return re.sub(r'[-\s]', '', phones[0])
    return None

def extract_company(fields):
    """尝试从不同字段名提取企业名称"""
    for name in ["Q1. 企业名称（填空题，必填）", "企业名称", "公司名称"]:
        val = extract_text(fields, name)
        if val:
            return val
    return "未知企业"

def get_records(app_token, table_id):
    """获取飞书表中所有记录"""
    records = []
    pt = None
    page = 0
    while True:
        r = feishu_api("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=50" + (f"&page_token={pt}" if pt else ""))
        items = r.get("data",{}).get("items",[])
        records.extend(items)
        page += 1
        if not r.get("data",{}).get("has_more"):
            break
        pt = r.get("data",{}).get("page_token")
        if page > 20:  # 安全限制
            break
    return records

def build_report_data():
    """构建完整的报告查询数据（L1 + L2）"""
    print("📡 获取飞书L1记录...")
    records = get_records(BITABLE_APP_TOKEN, TABLE_ID)
    print(f"   共 {len(records)} 条记录")
    
    reports = []
    for rec in records:
        fields = rec.get("fields", {})
        rid = rec["record_id"]
        
        # 提取企业名称
        company = extract_company(fields)
        
        # 提取手机号
        contact_raw = ""
        for cn in ["Q28.联系人和联系方式（手机号/微信，必填）", "Q29.联系人和联系方式（手机号/微信，必填）", "联系人"]:
            val = extract_text(fields, cn)
            if val:
                contact_raw = val
                break
        
        phone = extract_phone(contact_raw)
        
        # L1报告URL
        l1_report_url = ""
        l1_rating = ""
        l1_date = ""
        
        # 从L1表字段获取评分信息
        total_score = 0.0
        try:
            total_val = fields.get("总分", {})
            if isinstance(total_val, dict):
                total_score = float(total_val.get("value", [0])[0]) if total_val.get("value") else 0.0
            elif isinstance(total_val, (int, float)):
                total_score = float(total_val)
        except:
            pass
        
        rating = extract_text(fields, "综合评级")
        if not rating:
            if total_score >= 4.5: rating = "A级 卓越"
            elif total_score >= 3.5: rating = "B级 良好"
            elif total_score >= 2.5: rating = "C级 注意"
            elif total_score >= 0: rating = "D级 风险"
        
        # 报告日期
        submit_time = ""
        st = fields.get("提交时间", "")
        if isinstance(st, (int, float)):
            try:
                submit_time = datetime.fromtimestamp(st / 1000).strftime("%Y-%m-%d")
            except:
                submit_time = str(st)
        elif isinstance(st, str) and st:
            submit_time = st[:10] if len(st) >= 10 else st
        
        # 读取报告URL（L1和L2）
        report_type = "l1"
        report_url = ""
        l1_url = extract_text(fields, "L1_报告URL")
        l2_url = extract_text(fields, "L2_报告URL")
        
        if l2_url:
            report_type = "l2"
            report_url = l2_url
            l2_rating = extract_text(fields, "L2_综合评级")
            if l2_rating:
                rating = l2_rating
        elif l1_url:
            report_url = l1_url
        
        # 只看有手机号或企业名称的记录
        if not phone and not company:
            continue
        
        report_entry = {
            "company": company,
            "phones": [phone] if phone else [],
            "record_id": rid,
            "type": report_type,
            "rating": rating or "待评级",
            "report_url": report_url,
            "report_date": submit_time or datetime.now().strftime("%Y-%m-%d")
        }
        reports.append(report_entry)
    
    # ===== L2表（独立深度诊断表）=====
    print("\n📡 获取飞书L2深度诊断表记录...")
    try:
        l2_records = get_records(L2_BITABLE_APP_TOKEN, L2_TABLE_ID)
        print(f"   共 {len(l2_records)} 条记录")
    except Exception as e:
        print(f"   ⚠️ 获取L2表失败: {e}")
        l2_records = []
    
    for rec in l2_records:
        fields = rec.get("fields", {})
        rid = rec["record_id"]
        
        # 提取企业名称
        company = extract_text(fields, "企业名称") or "未知企业"
        
        # 提取手机号
        phone = None
        phone_text = extract_text(fields, "联系方式") or ""
        phone = extract_phone(phone_text)
        
        # L2报告URL
        l2_url = extract_text(fields, "L2_报告URL")
        
        # 综合得分、评级
        total_score = 0.0
        try:
            total_raw = fields.get("▶ 综合得分", 0)
            total_score = float(total_raw)
        except:
            pass
        
        rating_raw = extract_text(fields, "▶ 评级判定") or ""
        if not rating_raw:
            if total_score >= 4.5: rating_raw = "A级 卓越"
            elif total_score >= 3.5: rating_raw = "B级 良好"
            elif total_score >= 2.5: rating_raw = "C级 注意"
            elif total_score >= 0: rating_raw = "D级 风险"
        
        # 只保留有URL的记录（有实际报告）
        if not l2_url:
            continue
        
        report_entry = {
            "company": company,
            "phones": [phone] if phone else [],
            "record_id": rid,
            "type": "l2",
            "rating": rating_raw or "待评级",
            "report_url": l2_url,
            "report_date": datetime.now().strftime("%Y-%m-%d")
        }
        reports.append(report_entry)
    
    # 去重（按company+phone去重，保留最新）
    seen = set()
    unique_reports = []
    for r in sorted(reports, key=lambda x: x["report_date"], reverse=True):
        key = (r["company"], tuple(r["phones"]))
        if key not in seen:
            seen.add(key)
            unique_reports.append(r)
    
    print(f"\n📊 共提取 {len(unique_reports)} 条有效报告数据（L1+L2合计）")
    return unique_reports


def update_report_query_html(reports, html_path="reports/report_query.html"):
    """更新 report_query.html 中的报告数据和手机号映射"""
    print(f"📝 更新 {html_path}...")
    
    # 构建PHONE_MAP
    phone_map = {}
    for r in reports:
        for p in r["phones"]:
            if p:
                # 存储脱敏的phone hash -> record_id映射
                hashed = hash_phone(p)
                if hashed not in phone_map:
                    phone_map[hashed] = []
                phone_map[hashed].append(r["record_id"])
    
    # 读取现有HTML
    if not os.path.exists(html_path):
        print(f"❌ {html_path} 不存在")
        return False
    
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    
    # 替换REPORTS数组
    reports_json = json.dumps(reports, ensure_ascii=False, indent=2)
    
    # 找到 REPORTS 和 PHONE_MAP 的占位符并替换
    import re as re_mod
    
    # 替换 REPORTS 数组
    pattern = r'const REPORTS = \[.*?\];'
    replacement = f'const REPORTS = {reports_json};'
    new_html = re_mod.sub(pattern, replacement, html, count=1, flags=re_mod.DOTALL)
    
    # 替换 PHONE_MAP
    phone_map_json = json.dumps(phone_map, ensure_ascii=False, indent=2)
    pattern2 = r'const PHONE_MAP = \{.*?\};'
    replacement2 = f'const PHONE_MAP = {phone_map_json};'
    new_html = re_mod.sub(pattern2, replacement2, new_html, count=1, flags=re_mod.DOTALL)
    
    if new_html == html:
        print("  ⚠️ 替换失败，HTML内容未变化")
        return False
    
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(new_html)
    
    print("  ✅ 更新完成")
    return True


def hash_phone(phone):
    """与前端匹配的脱敏手机号hash函数"""
    import base64
    return 'p_' + base64.b64encode(phone.encode()).decode().replace('=', '')[:16]


if __name__ == "__main__":
    # 检查凭证
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        # 尝试从 .scorer_env 读取
        env_path = os.path.expanduser("~/.scorer_env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if '=' in line and not line.startswith('#'):
                        k, v = line.split('=', 1)
                        os.environ[k.strip()] = v.strip()
            FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
            FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
    
    missing = [v for v in ["FEISHU_APP_ID", "FEISHU_APP_SECRET"] if not os.environ.get(v)]
    if missing:
        print(f"❌ 缺少环境变量: {missing}")
        print("   请设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
        sys.exit(1)
    
    print("=" * 50)
    print("🔍 报告查询数据更新器")
    print("=" * 50)
    
    reports = build_report_data()
    update_report_query_html(reports)
    
    print("\n✅ 完成！")
