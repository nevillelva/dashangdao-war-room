-- 【2026-10-13 第 9-1 項】Telegram 收件匣：Worker webhook 只負責「驗證＋原文入庫」，解析與寫入仍走管理員頁的預覽確認流程。
-- RLS 開啟且不設 policy：只有 service role（Worker 與 App 後端）可讀寫，匿名金鑰無法存取。
create table if not exists public.telegram_inbox (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  update_id bigint not null unique,      -- Telegram 重送同一則時靠這個去重
  chat_id text not null,
  message_id bigint,
  text text not null,
  status text not null default 'new' check (status in ('new','parsed','rejected','done')),
  note text
);
alter table public.telegram_inbox enable row level security;
create index if not exists telegram_inbox_status_idx on public.telegram_inbox (status, created_at);
