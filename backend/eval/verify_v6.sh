#!/usr/bin/env bash
# 验证 v6 生产链路：看日志、融合明细、发票主表。
set -e
cd /home/lucas/code/Invoice-Manager

ID="${1:-86}"

echo "=== 后端日志 ==="
docker logs invoice_backend --since 5m 2>&1 \
  | grep -E 'Qwen OCR|Fusion \[|in parallel|processed successfully|Error|Traceback' \
  | tail -10

echo
echo "=== 融合明细 parsing_diffs (invoice=$ID) ==="
cat > /tmp/q1.sql <<'SQL'
select field_name, coalesce(source,'-') as src,
       coalesce(final_value,'(null)') as final, resolved
from parsing_diffs where invoice_id = :iid order by id;
SQL
docker exec -i invoice_db psql -U postgres -d invoice_db -v iid="$ID" -f - < /tmp/q1.sql

echo
echo "=== 发票主表 ==="
cat > /tmp/q2.sql <<'SQL'
select status, buyer_name, seller_name, seller_tax_id,
       amount, tax_amount, total_with_tax
from invoices where id = :iid;
SQL
docker exec -i invoice_db psql -U postgres -d invoice_db -v iid="$ID" -f - < /tmp/q2.sql
