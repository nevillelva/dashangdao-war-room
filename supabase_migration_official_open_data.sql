-- 2026-10-16 證交所／櫃買 OpenAPI 逐檔公開資料彙整表（質押、轉讓申報、停資停券、當沖等）
create table if not exists public.official_open_data (
  id bigserial primary key,
  dataset text not null,
  data_date text not null,
  symbol text not null,
  payload jsonb not null default '{}'::jsonb,
  source_url text,
  created_at timestamptz not null default now(),
  unique (dataset, data_date, symbol)
);
create index if not exists official_open_data_sym_idx on public.official_open_data (symbol, dataset, data_date desc);
alter table public.official_open_data enable row level security;
