#!/usr/bin/env python3
"""报告索引生成器 — 生成 reports/reports_index.json 供手机号查询页面使用"""

import json, os, re, sys, urllib.request

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "cli_a9778f2583f81bd4")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
BITABLE_APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN", "VU3hbjRyuabLhAseoK3ckzOzndg")
TABLE_ID = os.environ.get("TABLE_ID", "tblofr6TCloHk5Zb")
PAGES_BASE = "https://kevinhanmin.github.io/scorer-reports"
REPORT_DIR = "reports"
INDEX_FILE = f"{REPORT_DIR}/reports_index.json"

def get_token():
    d = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode()
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=d, headers={"Content-Type": "application/json"}))
        return json.loads(r.read())["tenant_access_token"]
    except: return None

def fetch_records(token):
    all_records = []
    pt = ""
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{TABLE_ID}/records?page_size=50"
        if pt: url += f"&page_token={pt}"
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})).read())
            all_records.extend(r.get("data",{}).get("items",[]))
            pt = r.get("data",{}).get("page_token","")
            if not pt: break
        except: break
    return all_records

def fetch_github_reports():
    try:
        r = json.loads(urllib.request.urlopen(f"https://api.github.com/repos/Kevinhanmin/scorer-reports/contents/{REPORT_DIR}").read())
        return [f["name"] for f in r if f["name"].endswith(".html") and f["name"] != "reports_index.json"]
    except: return []

def generate():
    print("📊 生成诊断报告索引...")
    os.makedirs(REPORT_DIR, exist_ok=True)
    token = get_token()
    if not token: return False
    records = fetch_records(token)
    print(f"  📥 {len(records)} records from Feishu")
    report_files = fetch_github_reports()
    print(f"  📁 {len(report_files)} report files")
    
    index = []
    for rec in records:
        f = rec.get("fields", {})
        company = f.get("Q1. 企业名称（填空题，必填）", "")
        contact = f.get("Q28.联系人和联系方式（手机号/微信，必填）", "") or f.get("Q29.联系人和联系方式（手机号/微信，必填）", "")
        progress = f.get("跟进进度", "")
        rid = rec.get("record_id", "")
        if progress != "已生成报告" or not company: continue
        
        phones = list(set(re.findall(r"1[3-9]\d{9}", str(contact))))
        safe_company = re.sub(r'[^\w]', '', company)[:15]
        
        matching = sorted([rf for rf in report_files if safe_company.lower() in rf.lower() or company[:4].lower() in rf.lower()], reverse=True)
        
        entry = {"company": company, "phones": phones, "record_id": rid}
        if matching:
            entry["report_url"] = f"{PAGES_BASE}/{REPORT_DIR}/{matching[0]}"
            dm = re.search(r'(\d{8})', matching[0])
            entry["report_date"] = f"{dm.group(1)[:4]}-{dm.group(1)[4:6]}-{dm.group(1)[6:8]}" if dm else ""
        else:
            entry["report_url"] = ""
            entry["report_date"] = ""
        index.append(entry)
        print(f"  {'✅' if matching else '⚠️'} {company[:20]:20s} {'→ '+matching[0] if matching else ''}")
    
    index.sort(key=lambda x: x.get("company",""))
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(f"\n  ✅ {INDEX_FILE} ({len(index)} entries)")
    return True

if __name__ == "__main__":
    sys.exit(0 if generate() else 1)
