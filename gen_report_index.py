#!/usr/bin/env python3
"""报告索引生成器 — 生成 reports/reports_index.json 供手机号查询页面使用"""

import json, os, re, sys, urllib.request

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "cli_a9778f2583f81bd4")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
PAGES_BASE = "https://sptechsz.com"
REPORT_DIR = "reports"
INDEX_FILE = f"{REPORT_DIR}/reports_index.json"

# L1问卷表
L1_BITABLE_APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN", "VU3hbjRyuabLhAseoK3ckzOzndg")
L1_TABLE_ID = os.environ.get("TABLE_ID", "tblofr6TCloHk5Zb")

# L2深度诊断表
L2_BITABLE_APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN_L2", "JZg8bq0A3aYVU3snPxZcMKmQnid")
L2_TABLE_ID = os.environ.get("TABLE_ID_L2", "tbl0W8eou1chYGzi")


def get_token():
    d = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode()
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=d, headers={"Content-Type": "application/json"}))
        return json.loads(r.read())["tenant_access_token"]
    except: return None


def fetch_records(token, app_token, table_id):
    all_records = []
    pt = ""
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=50"
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


def match_report(report_files, prefix, keywords):
    """Find the best matching report file for given prefix and keywords."""
    candidates = [rf for rf in report_files if rf.startswith(prefix)]
    matching = sorted([rf for rf in candidates if any(k.lower() in rf.lower() for k in keywords)], reverse=True)
    return matching[0] if matching else (candidates[0] if candidates else "")


def extract_phones(text):
    return list(set(re.findall(r"1[3-9]\d{9}", str(text))))


def index_l1_records(token, records, report_files):
    """Index L1 questionnaire records."""
    index = []
    for rec in records:
        f = rec.get("fields", {})
        company = f.get("Q1. 企业名称（填空题，必填）", "")
        contact = f.get("Q28.联系人和联系方式（手机号/微信，必填）", "") or f.get("Q29.联系人和联系方式（手机号/微信，必填）", "")
        progress = f.get("跟进进度", "")
        rid = rec.get("record_id", "")
        if progress != "已生成报告" or not company: continue

        phones = extract_phones(contact)
        safe_company = re.sub(r'[^\w]', '', company)[:15]

        matched = match_report(report_files, "diagnosis_", [safe_company, company[:4], rid[:6]])
        entry = {"company": company, "phones": phones, "record_id": rid, "type": "l1"}
        if matched:
            entry["report_url"] = f"{PAGES_BASE}/{REPORT_DIR}/{matched}"
            dm = re.search(r'(\d{8})', matched)
            entry["report_date"] = f"{dm.group(1)[:4]}-{dm.group(1)[4:6]}-{dm.group(1)[6:8]}" if dm else ""
        else:
            entry["report_url"] = ""
            entry["report_date"] = ""
        index.append(entry)
        print(f"  {'✅' if matched else '⚠️'} [L1] {company[:20]:20s} {'→ '+matched if matched else ''}")
    return index


def index_l2_records(token, records, report_files):
    """Index L2 depth diagnosis records."""
    index = []
    for rec in records:
        f = rec.get("fields", {})
        company = ""
        raw = f.get("企业名称", "")
        if isinstance(raw, str): company = raw
        elif isinstance(raw, list): company = "".join(item.get("text","") for item in raw if isinstance(item,dict))
        elif isinstance(raw, dict):
            vals = raw.get("value",[])
            company = str(vals[0]) if vals else ""

        if not company: continue

        # L2 records that have a composite score are considered valid
        total_raw = f.get("▶ 综合得分")
        if not total_raw: continue

        rating_raw = ""
        r = f.get("▶ 评级判定", "")
        if isinstance(r, str): rating_raw = r
        elif isinstance(r, list): rating_raw = "".join(item.get("text","") for item in r if isinstance(item,dict))

        rid = rec.get("record_id", "")
        phones = []  # L2表可能没有手机号
        safe_company = re.sub(r'[^\w]', '', company)[:15]

        matched = match_report(report_files, "l2_", [safe_company, company[:4], rid[:6]])
        entry = {
            "company": company, "phones": phones, "record_id": rid,
            "type": "l2", "rating": rating_raw
        }
        if matched:
            entry["report_url"] = f"{PAGES_BASE}/{REPORT_DIR}/{matched}"
            dm = re.search(r'(\d{8})', matched)
            entry["report_date"] = f"{dm.group(1)[:4]}-{dm.group(1)[4:6]}-{dm.group(1)[6:8]}" if dm else ""
        else:
            entry["report_url"] = ""
            entry["report_date"] = ""
        index.append(entry)
        print(f"  {'✅' if matched else '⚠️'} [L2] {company[:20]:20s} {'→ '+matched if matched else ''}")
    return index


def generate():
    print("📊 生成诊断报告索引 (L1 + L2)...")
    os.makedirs(REPORT_DIR, exist_ok=True)
    token = get_token()
    if not token: return False

    report_files = fetch_github_reports()
    print(f"  📁 {len(report_files)} report files on GitHub")

    # L1索引
    l1_records = fetch_records(token, L1_BITABLE_APP_TOKEN, L1_TABLE_ID)
    print(f"  📥 {len(l1_records)} L1 records from Feishu")
    l1_index = index_l1_records(token, l1_records, report_files)

    # L2索引
    l2_records = fetch_records(token, L2_BITABLE_APP_TOKEN, L2_TABLE_ID)
    print(f"  📥 {len(l2_records)} L2 records from Feishu")
    l2_index = index_l2_records(token, l2_records, report_files)

    # 合并索引（L1在前，L2在后）
    index = l1_index + l2_index
    index.sort(key=lambda x: (x.get("type","l1"), x.get("company","")))

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(f"\n  ✅ {INDEX_FILE} (L1: {len(l1_index)} + L2: {len(l2_index)} = {len(index)} entries)")
    return True


if __name__ == "__main__":
    sys.exit(0 if generate() else 1)
