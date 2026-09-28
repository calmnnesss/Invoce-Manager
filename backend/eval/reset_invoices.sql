-- 清空全部发票数据，重新从前端导入。
-- v6 不做兼容处理：旧记录是两路仲裁的产物，字段来源与置信度信息缺失，
-- 留着只会让前端要分两套渲染逻辑。
--
-- 顺序按外键依赖：先删子表再删主表。
BEGIN;

DELETE FROM parsing_diffs;
DELETE FROM ocr_results;
DELETE FROM llm_results;
DELETE FROM invoices;

-- 重置自增序列，新导入的发票从 1 开始编号
ALTER SEQUENCE invoices_id_seq RESTART WITH 1;
ALTER SEQUENCE parsing_diffs_id_seq RESTART WITH 1;
ALTER SEQUENCE ocr_results_id_seq RESTART WITH 1;
ALTER SEQUENCE llm_results_id_seq RESTART WITH 1;

COMMIT;

SELECT 'invoices' AS t, count(*) FROM invoices
UNION ALL SELECT 'parsing_diffs', count(*) FROM parsing_diffs
UNION ALL SELECT 'ocr_results', count(*) FROM ocr_results
UNION ALL SELECT 'llm_results', count(*) FROM llm_results;
