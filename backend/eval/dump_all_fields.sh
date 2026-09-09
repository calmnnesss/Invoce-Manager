#!/usr/bin/env bash
# 为各个 prompt 版本导出「逐字段原始值 vs GT」对照，落到 eval/reports/。
# 不做归因，只出原始事实，供人工分析。
#
#   bash eval/dump_all_fields.sh
#
# 日志分布：
#   v1 / v2        logs/llm/archive/2026-09-07_v1-v2
#   v3.1（五主体）  logs/llm/archive/2026-09-08_v3.1
#   v3.1（四主体）  logs/llm （当前目录）
set -e
cd "$(dirname "$0")/.."

mkdir -p eval/reports

A7=logs/llm/archive/2026-09-07_v1-v2
A8=logs/llm/archive/2026-09-08_v3.1

echo "[1/4] v1"
python3 eval/match_fields.py --log-dir "$A7" --prompt-version v1 \
    --csv eval/reports/v1_fields.csv > eval/reports/v1_fields.txt 2>&1

echo "[2/4] v2"
python3 eval/match_fields.py --log-dir "$A7" --prompt-version v2 \
    --csv eval/reports/v2_fields.csv > eval/reports/v2_fields.txt 2>&1

echo "[3/4] v3.1 五主体（含 E05）"
python3 eval/match_fields.py --log-dir "$A8" --prompt-version v3.1 \
    --csv eval/reports/v3.1_5ent_fields.csv > eval/reports/v3.1_5ent_fields.txt 2>&1

echo "[4/4] v3.1 四主体（无 E05）"
python3 eval/match_fields.py --log-dir logs/llm --prompt-version v3.1 \
    --entities 2026-09-08-no-E05 \
    --csv eval/reports/v3.1_4ent_fields.csv > eval/reports/v3.1_4ent_fields.txt 2>&1

echo
echo "=== 各版本汇总 ==="
for n in v1 v2 v3.1_5ent v3.1_4ent; do
  printf '%-12s ' "$n"
  grep -h '共列出' "eval/reports/${n}_fields.txt" || echo '(无)'
done

echo
ls -la eval/reports/
