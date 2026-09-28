-- 一次性回填 invoices.invoice_code。
--
-- v6 融合层产出了发票代码并写进 parsing_diffs，但 invoice_service
-- ._update_invoice_from_fields 漏写了 invoices.invoice_code（已修复），
-- 所以修复前识别的发票这一列都是空的。重新识别过的发票不需要这一步。
--
-- 只回填列为空、且长度不超过列宽 20 的记录。
--
--   docker exec -i invoice_db psql -U postgres -d invoice_db < backend/eval/backfill_invoice_code.sql
UPDATE invoices AS i
SET invoice_code = d.final_value
FROM parsing_diffs AS d
WHERE d.invoice_id = i.id
  AND d.field_name = 'invoice_code'
  AND i.invoice_code IS NULL
  AND d.final_value IS NOT NULL
  AND length(d.final_value) <= 20;
