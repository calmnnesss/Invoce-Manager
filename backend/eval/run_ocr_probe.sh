#!/usr/bin/env bash
# Phase 0：qwen3.5-ocr key_information_extraction 独立探针。
#
# 完全独立于现有 LLM 线：不改 app/ 代码，日志落在容器 /tmp/ocr_out，
# 结束后拷回 backend/eval/ocr_probe_out/。
#
#   bash eval/run_ocr_probe.sh                                   # 默认档位
#   PIXELS=2048,4096,8192,16384 bash eval/run_ocr_probe.sh       # 分辨率扫描
#   bash eval/run_ocr_probe.sh s1 on                             # 换 schema / 开转正
#
# 环境变量：
#   PIXELS              max_pixels 档位，单位 32*32*N，逗号分隔（默认 8192）
#   DASHSCOPE_BASE_URL  自定义端点（北京业务空间形如
#                       https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1）
set -e
cd "$(dirname "$0")/.."

SCHEMA="${1:-s0}"
ROTATE="${2:-off}"
# 默认样本：6 张竖图（占数据集 86%，且都是已知的销售方名称/税号失败样本）
# + b3 横图对照。之前只跑横图是取样偏差，这里修正。
FILES="${3:-b3,b2,b10,b25,b36,b34,b20}"
PIXELS="${PIXELS:-8192}"

EXTRA=""
if [ -n "$DASHSCOPE_BASE_URL" ]; then
  EXTRA="--base-url $DASHSCOPE_BASE_URL"
fi

echo "== 1. 下载样本图 =="
mkdir -p /tmp/ocr_imgs
for n in $(echo "$FILES" | tr ',' ' '); do
  id=$(python3 - "$n" <<'PY'
import json, sys
m = json.load(open('eval/file_ids.json', encoding='utf-8'))
print(m[sys.argv[1] + '.jpg'][0])
PY
)
  if [ ! -s "/tmp/ocr_imgs/$n.jpg" ]; then
    curl -s -o "/tmp/ocr_imgs/$n.jpg" "http://localhost:18080/api/invoices/$id/file"
  fi
  printf '  %-6s id=%-4s %s bytes\n' "$n" "$id" "$(stat -c%s "/tmp/ocr_imgs/$n.jpg")"
done

echo "== 2. 拷进容器 =="
docker exec invoice_backend rm -rf /tmp/ocr_imgs
docker cp /tmp/ocr_imgs invoice_backend:/tmp/ocr_imgs > /dev/null
docker cp eval/ocr_probe.py invoice_backend:/tmp/ocr_probe.py > /dev/null
docker cp eval/ground_truth.json invoice_backend:/tmp/ground_truth.json > /dev/null

echo "== 3. 调用 qwen3.5-ocr =="
docker exec invoice_backend python /tmp/ocr_probe.py \
    --schema "$SCHEMA" --rotate "$ROTATE" --files "$FILES" \
    --pixels-levels "$PIXELS" $EXTRA

echo "== 4. 取回结果 =="
mkdir -p eval/ocr_probe_out
docker cp invoice_backend:/tmp/ocr_out/. eval/ocr_probe_out/ > /dev/null
ls eval/ocr_probe_out | wc -l
