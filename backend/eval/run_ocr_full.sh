#!/usr/bin/env bash
# 全量跑 qwen3.5-ocr 的 key_information_extraction（51 张），并导出 CSV。
#
#   bash eval/run_ocr_full.sh                 # rotate 开，默认档 N=8192
#   ROTATE=off bash eval/run_ocr_full.sh      # 关转正
#   PIXELS=2048 bash eval/run_ocr_full.sh     # 换分辨率档
#
# schema 用 eval/ocr_schema.json（你改的那份），不用内置版本。
# 结果落在 eval/ocr_probe_out/，CSV 在 eval/reports/。
set -e
cd "$(dirname "$0")/.."

ROTATE="${ROTATE:-on}"
PIXELS="${PIXELS:-8192}"
TAG="${TAG:-full_rot${ROTATE}_n${PIXELS}}"

EXTRA=""
if [ -n "$DASHSCOPE_BASE_URL" ]; then
  EXTRA="--base-url $DASHSCOPE_BASE_URL"
fi

echo "== 1. 准备 51 张图 =="
mkdir -p /tmp/ocr_imgs
FILES=$(python3 - <<'PY'
import json
m = json.load(open('eval/file_ids.json', encoding='utf-8'))
names = sorted([k[:-4] for k in m if k != 'b195.jpg'],
               key=lambda x: int(x.lstrip('b')))
print(','.join(names))
PY
)
python3 - "$FILES" <<'PY'
import json, subprocess, sys, os
m = json.load(open('eval/file_ids.json', encoding='utf-8'))
names = sys.argv[1].split(',')
missing = 0
for n in names:
    p = f'/tmp/ocr_imgs/{n}.jpg'
    if os.path.exists(p) and os.path.getsize(p) > 10000:
        continue
    iid = m[n + '.jpg'][0]
    subprocess.run(['curl', '-s', '-o', p,
                    f'http://localhost:18080/api/invoices/{iid}/file'], check=True)
    missing += 1
print(f'  共 {len(names)} 张，本次新下载 {missing} 张')
PY

echo "== 2. 拷进容器 =="
docker exec invoice_backend rm -rf /tmp/ocr_imgs /tmp/ocr_out
docker cp /tmp/ocr_imgs invoice_backend:/tmp/ocr_imgs > /dev/null
docker cp eval/ocr_probe.py    invoice_backend:/tmp/ocr_probe.py    > /dev/null
docker cp eval/ocr_schema.json invoice_backend:/tmp/ocr_schema.json > /dev/null
docker cp eval/ground_truth.json invoice_backend:/tmp/ground_truth.json > /dev/null

echo "== 3. 调用 qwen3.5-ocr（rotate=$ROTATE, N=$PIXELS）=="
docker exec invoice_backend python /tmp/ocr_probe.py \
    --files "$FILES" --rotate "$ROTATE" --pixels-levels "$PIXELS" \
    --schema-file /tmp/ocr_schema.json $EXTRA

echo "== 4. 取回结果 =="
rm -rf eval/ocr_probe_out
mkdir -p eval/ocr_probe_out
docker cp invoice_backend:/tmp/ocr_out/. eval/ocr_probe_out/ > /dev/null

echo "== 5. 导出 CSV =="
python3 eval/ocr_report.py --n "$PIXELS" \
    --out "eval/reports/ocr_${TAG}.csv" | tail -3
