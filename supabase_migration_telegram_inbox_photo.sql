-- 【2026-10-13 圖片收件】Telegram 收件匣支援圖片：Worker 只負責「驗證＋下載圖片＋入庫」，
-- 辨識（視覺模型或 OCR，由管理員在頁面自選）與寫入仍走「預覽 → 確認」流程。
-- 圖片位元組存在獨立表 telegram_inbox_images（base64），避免拖慢收件匣清單查詢；
-- 寫入或退回後立即刪除（圖片可能含個人資料，不長期保留）。RLS 開啟、無 policy（只有 service role 可讀寫）。
alter table public.telegram_inbox
  add column if not exists kind text not null default 'text' check (kind in ('text', 'photo')),
  add column if not exists file_id text,
  add column if not exists caption text,
  add column if not exists engine text check (engine is null or engine in ('vision', 'ocr', 'manual'));

create table if not exists public.telegram_inbox_images (
  inbox_id bigint primary key references public.telegram_inbox(id) on delete cascade,
  created_at timestamptz not null default now(),
  mime text not null,
  size_bytes integer not null check (size_bytes > 0),
  data_b64 text not null
);
alter table public.telegram_inbox_images enable row level security;
