-- ============================================================================
-- 【僅為檔案，尚未套用到正式資料庫；使用者核准後才可執行】
-- db_retention.py --execute 用來 DROP 備份表的 RPC。PostgREST 本身無法下 DDL，因此需要這個函式。
-- 安全設計：
--   1. 只接受 backup_ 開頭、結尾為 8 位數日期的表名（與 db_retention.py 的 _BACKUP_RE 相同）。
--   2. 只能刪 public schema，且表必須存在；不使用 CASCADE（有相依物件就失敗，不連帶刪除）。
--   3. 只授權 service_role；anon／authenticated 一律不可呼叫。
--   4. db_retention.py 在呼叫前已完成「匯出到 Storage、sha256 回讀校驗、刪前列數對帳」，函式本身不做這些。
-- ============================================================================
create or replace function public.retention_drop_backup_table(p_table text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_table !~ '^backup_[a-z0-9_]+_[0-9]{8}$' then
    raise exception 'retention_drop_backup_table: 表名不符規則：%', p_table;
  end if;
  if to_regclass(format('public.%I', p_table)) is null then
    raise exception 'retention_drop_backup_table: 表不存在：%', p_table;
  end if;
  execute format('drop table public.%I', p_table);   -- 不加 CASCADE
end;
$$;

revoke all on function public.retention_drop_backup_table(text) from public, anon, authenticated;
grant execute on function public.retention_drop_backup_table(text) to service_role;
