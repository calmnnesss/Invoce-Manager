#!/usr/bin/env bash
# 单张图调用 qwen3.5-ocr，控制台直出请求与完整原始响应。
#
#   bash eval/ocr_call.sh b20
#   bash eval/ocr_call.sh b20 --rotate --n 2048
#   bash eval/ocr_call.sh b20 --task advanced_recognition
#   bash eval/ocr_call.sh b20 --task none --prompt "只输出价税合计的小写金额"
#
# schema 在 eval/ocr_schema.json，直接改那个文件即可，不用动代码。
set -e
cd "$(dirname "$0")/.."

NAME="${1:?用法: bash eval/ocr_call.sh <图片名，如 b20> [ocr_call.py 的其他参数]}"
shift || true

# 取图（已存在就不重复下载）
mkdir -p /tmp/ocr_imgs
if [ ! -s "/tmp/ocr_imgs/$NAME.jpg" ]; then
  id=$(python3 - "$NAME" <<'PY'
import json, sys
m = json.load(open('eval/file_ids.json', encoding='utf-8'))
print(m[sys.argv[1] + '.jpg'][0])
PY
)
  curl -s -o "/tmp/ocr_imgs/$NAME.jpg" "http://localhost:18080/api/invoices/$id/file"
  echo "已下载 $NAME.jpg (invoice id=$id)"
fi

docker cp "/tmp/ocr_imgs/$NAME.jpg" "invoice_backend:/tmp/$NAME.jpg" > /dev/null
docker cp eval/ocr_call.py    invoice_backend:/tmp/ocr_call.py    > /dev/null
docker cp eval/ocr_schema.json invoice_backend:/tmp/ocr_schema.json > /dev/null

EXTRA=""
if [ -n "$DASHSCOPE_BASE_URL" ]; then
  EXTRA="--base-url $DASHSCOPE_BASE_URL"
fi

docker exec invoice_backend python /tmp/ocr_call.py \
    --image "/tmp/$NAME.jpg" $EXTRA "$@"
