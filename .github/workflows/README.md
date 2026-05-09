# scorer-reports

精益智能工厂免费诊断报告自动生成系统

## 目录结构
- `scorer_cloud.py` — 评分师云端版脚本
- `.github/workflows/scorer.yml` — GitHub Actions自动触发配置
- `reports/` — 自动生成的诊断报告（HTML）

## 环境变量（GitHub Secrets需配置）
| Secret | 说明 | 值 |
|--------|------|-----|
| FEISHU_APP_ID | 飞书应用App ID | cli_a9778f2583f81bd4 |
| FEISHU_APP_SECRET | 飞书应用App Secret | (你的密钥) |
| BITABLE_APP_TOKEN | 多维表格Token | VU3hbjRyuabLhAseoK3ckzOzndg |
| TABLE_ID | 数据表ID | tblofr6TCloHk5Zb |
