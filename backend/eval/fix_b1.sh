#!/usr/bin/env bash
# 应用 fusion.py 的购买方 candidates 修复，重启并重跑已导入的发票。
set -e
cd /home/lucas/code/Invoice-Manager/backend

cp /mnt/c/temp/step1/fusion.py app/services/fusion.py
python3 -c "import ast,io;ast.parse(io.open('app/services/fusion.py',encoding='utf-8').read());print('AST OK')"

cd /home/lucas/code/Invoice-Manager
docker compose restart backend > /dev/null 2>&1
sleep 18
curl -s -o /dev/null -w 'health=%{http_code}\n' http://localhost:18080/api/health

IDS=$(docker exec -i invoice_db psql -U postgres -d invoice_db -At \
      -c 'select id from invoices order by id')
for i in $IDS; do
  curl -s -X POST "http://localhost:18080/api/invoices/$i/process" \
       -o /dev/null -w "invoice $i HTTP=%{http_code}\n"
done
