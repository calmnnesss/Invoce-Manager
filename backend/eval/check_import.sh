#!/usr/bin/env bash
# 检查前端导入后的 v6 数据完整性。
set -e
cd /home/lucas/code/Invoice-Manager

echo "=== 发票总览 ==="
cat > /tmp/v1.sql <<'SQL'
select id, file_name, status, verdict, pipeline_version,
       jsonb_array_length(coalesce(degraded::jsonb,'[]'::jsonb)) as n_degraded,
       jsonb_array_length(coalesce(nulled::jsonb,'[]'::jsonb))   as n_nulled
from invoices order by id;
SQL
docker exec -i invoice_db psql -U postgres -d invoice_db -f - < /tmp/v1.sql

echo
echo "=== 来源 / 置信分布（全部发票）==="
cat > /tmp/v2.sql <<'SQL'
select coalesce(source,'(null)') as source,
       coalesce(confidence,'(null)') as confidence,
       count(*) as n
from parsing_diffs group by 1,2 order by 1,2;
SQL
docker exec -i invoice_db psql -U postgres -d invoice_db -f - < /tmp/v2.sql

echo
echo "=== 第一张的逐字段明细 ==="
cat > /tmp/v3.sql <<'SQL'
select field_name,
       coalesce(source,'-') as src,
       coalesce(confidence,'-') as conf,
       coalesce(left(ocr_value,22),'-') as ocr,
       coalesce(left(llm_value,22),'-') as vlm,
       coalesce(left(final_value,24),'(null)') as final
from parsing_diffs
where invoice_id = (select min(id) from invoices)
order by id;
SQL
docker exec -i invoice_db psql -U postgres -d invoice_db -f - < /tmp/v3.sql

echo
echo "=== checks 命中情况 ==="
cat > /tmp/v4.sql <<'SQL'
select i.id,
       c->>'rule_id' as rule,
       c->>'status'  as status,
       left(coalesce(c->>'detail',''), 60) as detail
from invoices i,
     lateral jsonb_array_elements(coalesce(i.checks::jsonb,'[]'::jsonb)) c
where c->>'status' = 'fail'
order by i.id;
SQL
docker exec -i invoice_db psql -U postgres -d invoice_db -f - < /tmp/v4.sql
