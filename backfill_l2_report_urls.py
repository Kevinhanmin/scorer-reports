#!/usr/bin/env python3
"""
Smart backfill: match L2 report files to Feishu records by record_id prefix.
"""
import os, sys, json
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from l2_scorer_cloud import get_token, feishu_api, BITABLE_APP_TOKEN, TABLE_ID, FEISHU_API_BASE

PAGES_URL = os.environ.get("PAGES_URL", "https://sptechsz.com")
REPORT_DIR = "reports"

def main():
    print("=" * 50)
    print("🔙 回填L2报告URL到飞书（智能匹配）")
    print("=" * 50)
    
    # 1. Get all L2 records from Feishu
    print("\n📡 获取飞书L2表所有记录...")
    records = []
    pt = None
    while True:
        r = feishu_api("GET", f"/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{TABLE_ID}/records?page_size=50" + (f"&page_token={pt}" if pt else ""))
        items = r.get("data",{}).get("items",[])
        records.extend(items)
        if not r.get("data",{}).get("has_more"): break
        pt = r.get("data",{}).get("page_token")
    print(f"   共 {len(records)} 条记录")
    
    # 2. Find all l2 report files, group by prefix
    report_dir = Path(REPORT_DIR)
    l2_files = sorted(report_dir.glob("l2_rec*.html"))
    
    # Build map: prefix -> latest file
    file_by_prefix = {}
    for f in l2_files:
        name = f.name
        parts = name.split("_")
        if len(parts) >= 2:
            prefix = parts[1]  # e.g. "rec27qzw" from "l2_rec27qzw_20260522_123548.html"
            if prefix not in file_by_prefix or name > file_by_prefix[prefix].name:
                file_by_prefix[prefix] = f
    
    print(f"📁 L2报告文件前缀: {list(file_by_prefix.keys())}")
    
    # 3. Match by prefix
    matched = 0
    total_records = 0
    for rec in records:
        rid = rec["record_id"]  # e.g. "rec27qzwynurH7"
        fields = rec.get("fields", {})
        company = fields.get("企业名称", "") or ""
        total = fields.get("▶ 综合得分", "")
        
        # Try to match prefix: the rec ID in filename is first 9-10 chars of full ID
        # e.g. "rec27qzwynurH7" prefix "rec27qzw" (9 chars since 'rec' + 6 chars)
        matched_file = None
        for prefix, f in file_by_prefix.items():
            if rid.startswith(prefix):
                matched_file = f
                break
        
        if matched_file:
            report_url = f"{PAGES_URL}/reports/{matched_file.name}"
            print(f"\n[{matched+1}] {rid} ({company or '无企业名'})")
            print(f"   匹配文件: {matched_file.name}")
            print(f"   URL: {report_url}")
            
            try:
                resp = feishu_api("PUT", 
                    f"/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{TABLE_ID}/records/{rid}",
                    {"fields": {"L2_报告URL": report_url}})
                if resp.get("code") == 0:
                    print(f"   ✅ 写入成功")
                    matched += 1
                else:
                    print(f"   ⚠️ 写入失败: {resp.get('msg','')}")
            except Exception as e:
                print(f"   ❌ 异常: {e}")
        else:
            print(f"\n  {rid} ({company or '无企业名'}) — 无匹配报告文件")
        
        total_records += 1
    
    print(f"\n✅ 完成！成功回填 {matched}/{total_records} 条")

if __name__ == "__main__":
    main()
