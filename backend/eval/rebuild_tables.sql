-- v6 阶段二：重建发票相关表。
--
-- 项目没有 alembic，表由 main.py 的 Base.metadata.create_all 创建，
-- 而 create_all 不会为已存在的表补列。库已清空、且明确不做兼容，
-- 所以直接 DROP 让应用启动时重建最干净。
--
-- 新增的列：
--   invoices.verdict / checks / degraded / nulled / pipeline_version
--   parsing_diffs.qr_value / confidence / note
DROP TABLE IF EXISTS parsing_diffs CASCADE;
DROP TABLE IF EXISTS ocr_results CASCADE;
DROP TABLE IF EXISTS llm_results CASCADE;
DROP TABLE IF EXISTS invoices CASCADE;

SELECT tablename FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;
