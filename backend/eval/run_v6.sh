#!/usr/bin/env bash
# v6 融合管线端到端测试：跑三条线路，然后融合出报告。
#
#   bash eval/run_v6.sh                  # 全跑（约 102 次 API 调用，10~15 分钟）
#   SKIP_VLM=1 bash eval/run_v6.sh       # VLM 已跑过，跳过
#   SKIP_OCR=1 bash eval/run_v6.sh       # OCR 已跑过，跳过
#   SKIP_VLM=1 SKIP_OCR=1 SKIP_QR=1 bash eval/run_v6.sh   # 只重放融合（零调用）
#
# 三条线路各自的产物落在不同地方，互不覆盖：
#   VLM  -> logs/llm/*.log          （prompt v3.1 + 四主体清单）
#   OCR  -> eval/ocr_probe_out/     （11 字段 schema, rotate=off, N=8192）
#   QR   -> eval/qr_result.json
# 融合层只读这三处，不重新调用任何模型 —— 改融合规则可以随时重放。
set -e
cd "$(dirname "$0")/.."

echo "=============================================================="
echo " v6 管线   $(date '+%F %T')"
echo "=============================================================="

if [ -z "$SKIP_VLM" ]; then
  echo
  echo ">>> [1/4] VLM 线路 qwen-vl-max (prompt v3.1)  51 次调用"
  python3 eval/run_batch.py --expect-version v3.1
  python3 eval/score_logs.py --prompt-version v3.1 --entities 2026-09-08-no-E05 \
      --label v3_1_no_e05 --no-save > /dev/null
else
  echo; echo ">>> [1/4] VLM 线路  已跳过"
fi

if [ -z "$SKIP_OCR" ]; then
  echo
  echo ">>> [2/4] OCR 线路 qwen3.5-ocr (rotate=off)  51 次调用"
  ROTATE=off PIXELS=8192 TAG=v6_ocr bash eval/run_ocr_full.sh
else
  echo; echo ">>> [2/4] OCR 线路  已跳过"
fi

if [ -z "$SKIP_QR" ]; then
  echo
  echo ">>> [3/4] 二维码线路  本地解码，零成本"
  docker cp eval/qr_decode.py invoice_backend:/tmp/qr_decode.py > /dev/null
  docker exec invoice_backend python /tmp/qr_decode.py \
      --image-dir /tmp/ocr_imgs --out /tmp/qr_result.json | tail -3
  docker cp invoice_backend:/tmp/qr_result.json eval/qr_result.json > /dev/null
else
  echo; echo ">>> [3/4] 二维码线路  已跳过"
fi

echo
echo ">>> [4/4] 融合 + 报告"
python3 eval/simulate_fusion.py --csv

echo
echo "=============================================================="
echo " 产物"
echo "=============================================================="
ls -la eval/reports/v6_* 2>/dev/null
