# 資料庫容量保留方案（DB_RETENTION）

草擬日期：2026-11-15。對應工具：`db_retention.py`、`db_maintenance.py`、`db_archive.py`、`.github/workflows/db_retention.yml`、`supabase_migration_retention_plan.sql`、`supabase_migration_retention_drop_rpc.sql`。

> 2026-11-15 更新：使用者已核准刪除，但要求資料不遺失。本檔第 8 節為唯讀 SQL 實測數據與預估釋放量；第 9 節為「刪前先匯出」的新流程。第 5、7 節中「刪除前不會匯出」的敘述為修改前的舊行為，已由第 9 節取代。

## 1. 現況

- 資料庫總量約 407MB，Supabase 免費方案上限 500MB，超過即唯讀（使用者提供的背景數據，本次未重新查詢）。
- 最大表：`inst_holding` 80MB（約 15.9 萬列）、`broker_flows` 73MB（約 30.9 萬列）、`etf_price_daily` 33MB、`official_open_data` 31MB（約 5.9 萬列）、`twse_market_snapshot` 26MB（約 10.7 萬列）、`big_holder_weekly` 19MB、`mops_financial_snapshot` 15MB、`market_signal_snapshot` 14MB。
- 既有機制：`db_maintenance.py` 的 `RETENTION` 已定義多張表的保留天數，檔頭註解指出隨 bundle_late 每日執行；`od_retention.py` 負責 `official_open_data` 的分級保留與封存。新工具的保留天數與之一致（測試會比對）。

## 2. 日期欄位（倉庫內查證）

| 表 | 日期欄位 | 證據 |
|---|---|---|
| inst_holding | `date` | `dashangdao.py:3466`，upsert 鍵 `date,symbol` |
| broker_flows | `log_date` | `dashangdao.py:1271-1275` |
| etf_price_daily | `trade_date` | `supabase_migration_etf_price_daily.sql:5` |
| official_open_data | `data_date`（text） | `supabase_migration_official_open_data.sql:5` |
| twse_market_snapshot | `trade_date` | `backfill_twse_snapshot.py:72` |
| big_holder_weekly | `week_date` | `dashangdao_helpers.py:3979` |
| market_signal_snapshot | `trade_date` | `system_scheduler.py:8668-8669` |
| mops_financial_snapshot | **UNKNOWN**（鍵為 symbol/year_roc/season） | `dashangdao.py:3399` |
| telegram_inbox / _images | `created_at` | 兩份 migration SQL |
| backup_* 四張、manual_feed_batches | **UNKNOWN**（倉庫內無參照） | grep 無結果 |

UNKNOWN 表一律排除在執行清單外。

## 3. 三個方案

**方案 A：縮短保留期。** 把 `broker_flows`、`inst_holding` 等表的保留天數再縮短，由既有排程每日清理。
- 優點：不需新服務，程式改動小，容量下降可預測。
- 缺點：歷史資料永久消失，會影響回測與趨勢（例如 `big_holder_weekly`、`etf_price_daily`）。

**方案 B：冷資料匯出到 Supabase Storage。** 超過熱資料期限的列先匯出成 gzip JSONL，上傳私有 bucket，驗證 sha256 與列數後再從資料庫刪除。
- 使用者背景提到 Storage 免費 1GB（本次未查證，以官方現行方案為準）。
- 專案已有先例：`od_retention.py` 就是「封存到私有桶、回讀比對 sha256 與列數、全部一致才刪」。
- 缺點：查詢歷史資料需要另外讀 Storage；要維護封存目錄。

**方案 C：拆分專案。** 把冷資料或歷史表搬到另一個 Supabase 專案，主專案只留熱資料。
- 優點：容量隔離，兩邊各有 500MB。
- 缺點：兩個專案的費用與帳號、連線設定、資料同步都要管理；跨專案查詢不方便。

## 4. 建議順序

1. 先做 `db_retention.py --today 2026-11-15`（乾跑），確認執行清單與截止日。
2. 使用者決定各表保留天數（尤其 `etf_price_daily`、`big_holder_weekly`、`mops_financial_snapshot` 的歷史需求）。
3. 在 Supabase SQL 編輯器執行 A 區估算查詢，取得列數後填入 `--stats` 檔，再跑乾跑看釋放量。
4. 對要清的表執行 `--export`，保存 `manifest.json` 與匯出檔到倉庫之外。
5. 使用者確認後，設定 `RETENTION_CONFIRM=I_HAVE_EXPORTED` 並執行 `--execute`。
6. 清完後確認 `pg_database_size` 與各表列數，再決定是否做 VACUUM（見第 5 節）。
7. 備份表與已移除功能的表，依 `supabase_migration_retention_plan.sql` C、D 區逐一確認後才 DROP。

## 5. 風險與回復

- **刪除不可逆。** 因此工具設計為：預設乾跑；`--execute` 需要環境變數確認值，且需要 `--export` 產生的 manifest，匯出檔的 sha256 與列數都要對得上，刪除前還要再對帳一次。任一步不一致就中止該表。
- **匯出以分頁讀取。** 若匯出期間有排程寫入舊日期的列，可能造成對帳不符，工具會中止而不刪除。建議在沒有同步排程執行時進行。
- **磁碟空間不會立刻回收。** PostgreSQL 刪除列後，空間通常留在表內供重用；要還給系統需要 VACUUM，而 VACUUM FULL 會鎖表，需另外評估。本工具與計畫檔都不執行 VACUUM FULL。
- **回復方式。** 若刪除後需要資料，從匯出檔讀回：`gzip -dc <檔名>.jsonl.gz` 後逐列 insert，並以 manifest 的列數與 sha256 驗證。匯出檔請保存在倉庫之外，並妥善管理（內含股市資料，不含金鑰）。
- **不得事項。** 不得在工具或文件中寫入金鑰；不得未經確認執行 DROP；不得對無日期欄位的表用日期裁切。

## 6. 需要使用者決定的事項

1. 各表保留天數：`etf_price_daily`、`big_holder_weekly`、`market_signal_snapshot`（現為 60 天）、`twse_market_snapshot`（400 天）是否要調整。
2. 採用方案 A、B、C 或組合。
3. 四張備份表與三張已移除功能的表是否 DROP。
4. `mops_financial_snapshot` 與 `manual_feed_batches` 的日期欄位要如何定義（若要裁切）。

## 7. 既有 db_maintenance 自動刪除（現行行為）

本節只描述現行程式的行為，不改變任何刪除邏輯（2026-11-15 收斂說明）。

### 7.1 自動刪除的範圍

- 來源：`db_maintenance.py` 的 `RETENTION` 常數，由 `system_scheduler.py` 的 `stage_db_maintenance` 呼叫，隨 `bundle_late` 跑（依 `system_scheduler.py` 註解為每個交易日一次）。
- 行為：對每張表，刪除日期欄位早於「今天（台北時間）減保留天數」的列；保留天數下限為 `MIN_KEEP_DAYS = 7`。
- 逐日刪除，每次執行每表最多處理 `MAX_DAYS_PER_TABLE = 40` 天的積壓。
- （舊行為，已被第 9 節取代）刪除前不會匯出。新行為：`ARCHIVE_TABLES` 內的表刪前先匯出到 Storage 並校驗，失敗不刪。
- 環境變數 `DB_MAINT_DRY=1` 只回報、不刪除。
- 失敗只記錄，不中斷其他表；結果寫入 `system_run_log`（stage 名 `db_maintenance`）。

### 7.2 現行保留天數（`db_maintenance.RETENTION`）

| 表 | 日期欄位 | 保留天數 | 與 db_retention 執行清單重疊 |
|---|---|---|---|
| overnight_scan_results | scan_date | 14 | 否 |
| data_source_health_log | log_date | 30 | 否 |
| perf_log | created_at | 30 | 否 |
| warcard_cache | trade_date | 14 | 否 |
| market_signal_snapshot | trade_date | 60 | **是** |
| factor_snapshot | trade_date | 90 | 否 |
| intraday_5min_bars | trade_date | 45 | 否 |
| intraday_gate_results | trade_date | 180 | 否 |
| route2_watchlist | trade_date | 120 | 否 |
| broker_flows | log_date | 90 | **是** |
| broker_style_daily | log_date | 60 | 否 |
| inst_holding | date | 180 | **是** |
| twse_market_snapshot | trade_date | 400 | **是** |
| system_run_log | run_date | 365 | 否 |
| ui_selftest_reports | created_at | 45 | 否 |
| news_items | published_at | 60 | 否 |
| mops_events | ev_date | 180 | 否 |
| premarket_brief | brief_date | 180 | 否 |
| premarket_outcome | brief_date | 365 | 否 |

`official_open_data` 不在此表內，由 `od_retention.py` 另行分級保留與封存（見第 2 節）。

### 7.3 與 db_retention.py 的關係

- `db_retention.py` 是人工執行的工具：預設乾跑，`--execute` 需要 `--export` 產生的 manifest 與確認環境變數 `RETENTION_CONFIRM=I_HAVE_EXPORTED`，刪除前會匯出並對帳。
- 兩者的保留天數一致（`test_db_retention.py` 會比對）。`db_retention.py` 的 `TABLES` 中有 4 張表同時在 `db_maintenance.RETENTION` 內：`inst_holding`、`broker_flows`、`twse_market_snapshot`、`market_signal_snapshot`。
- **（已解決，見第 9 節）這 4 張表曾由 `db_maintenance` 每日自動刪除且沒有匯出步驟；現在刪前一律先匯出並校驗。** `db_retention.py` 的「先匯出再刪」保護不會套用到這 4 張表的自動刪除。若要對這 4 張表做可回復的刪除，應先停用或調整 `db_maintenance` 對它們的設定，這需要使用者決定，本次未變更。
- `db_retention.py` 的 `TABLES` 其餘表（如 `etf_price_daily`、`big_holder_weekly`）不在 `db_maintenance` 內，目前沒有任何自動刪除。

### 7.4 保留天數的目前值與變更方式

- 目前值以 `db_maintenance.py` 的 `RETENTION` 為準；變更時需同步修改 `db_retention.py` 的 `TABLES`（若該表兩邊都有），並執行 `test_db_retention.py` 與 `test_db_maintenance.py`。
- 任何表的保留天數都不得低於 7 天（兩邊的下限皆為 `MIN_KEEP_DAYS = 7`）。

## 8. 唯讀實測（2026-11-15，僅 SELECT；未對正式資料庫做任何 DELETE/DROP/ALTER）

### 8.1 各表大小與列數（含索引的 `pg_total_relation_size`）

| 表 | 大小 | 列數（精確） | 備註 |
|---|---|---|---|
| inst_holding | 79.7MB | 163,920 | 日期 2026-05-18 ~ 11-13 |
| broker_flows | 72.8MB | 312,754 | 日期 2026-08-19 ~ 11-13 |
| etf_price_daily | 33.4MB | 335,799 | 無保留期（待決定） |
| official_open_data | 30.6MB | 61,945 | heap 20MB、索引 10MB |
| twse_market_snapshot | 25.6MB | 108,388 | 2025-10-09 ~ 2026-11-13 |
| big_holder_weekly | 18.6MB | 105,557 | 無保留期（待決定） |
| mops_financial_snapshot | 15.1MB | 55,374 | 需保留 10 季 |
| market_signal_snapshot | 13.8MB | 47,051 | 2026-09-14 ~ 11-13 |
| broker_style_daily | 13.5MB | 8,331 | heap 13.5MB／僅 8,331 列，疑似表膨脹（bloat） |
| news_items | 11.1MB | 14,830 | |
| factor_snapshot | 10.4MB | 62,094 | |
| ui_selftest_reports | 9.6MB | 602 | heap 僅 0.6MB，大小幾乎都在 TOAST（報告內文） |
| backup_holiday_derived_20261014 | 4.2MB | 9,685 | |
| backup_instant_exit_artifacts_20261005 | 0.5MB | 596 | |
| backup_holiday_twse_snapshot_20261005 | 0.5MB | 3,264 | |
| backup_holiday_intraday_bars_20261005 | 0.07MB | 333 | |
| telegram_inbox / telegram_inbox_images / manual_feed_batches | 0.06 / 0.05 / 0.02MB | 皆 0 | manual_feed_batches 有 `created_at`（原 UNKNOWN，已查明） |

### 8.2 依目前保留期，今天實際可清的舊列（截止日以 2026-11-15 計）

| 表 | 保留 | 截止日 | 舊列數 | 預估釋放（依列數比例） |
|---|---|---|---|---|
| inst_holding | 180 天 | 2026-05-19 | 978 | 約 0.5MB |
| broker_flows | 90 天 | 2026-08-17 | 0 | 0 |
| twse_market_snapshot | 400 天 | 2025-10-11 | 120 | 約 0.03MB |
| market_signal_snapshot | 60 天 | 2026-09-16 | 2,144 | 約 0.7MB |
| broker_style_daily | 60 天 | 2026-09-16 | 456 | 約 0.8MB（含膨脹的 heap，列數比例僅為粗估） |
| factor_snapshot、news_items | 90／60 天 | — | 0 | 0 |
| telegram_inbox 等 3 表 | 30 天 | 2026-10-16 | 0 | 0 |

**重要結論：現行保留期下，熱表資料幾乎都還在保留期內，單靠「清舊列」只能釋放約 2MB。** `db_maintenance` 每天都在清，積壓已很少。

### 8.3 預估可釋放容量（誠實版）

| 項目 | 預估 | 備註 |
|---|---|---|
| 4 張備份表 DROP | 約 5.3MB（5,537,792 位元組） | DROP 會立即還空間；需先套用 DROP RPC migration（`supabase_migration_retention_drop_rpc.sql`） |
| 熱表依現行保留期清舊列 | 約 2MB | DELETE 後空間留在表內供重用，不一定降低資料庫總大小，需 VACUUM（FULL 會鎖表，需另評估） |
| official_open_data 瘦身（僅報告） | 上限約 3.9MB | `source_url` 去重約 3.4MB、`payload.market` 常數鍵約 0.5MB；尚未動工，需先確認讀取端 |
| **合計（現行保留期）** | **約 7~11MB（約 407MB 的 2%）** | |

若使用者願意「縮短保留期」（資料已先匯出到 Storage，縮短不再等於遺失），額外可釋放（依列數比例粗估，另以唯讀 SQL 計得舊列數）：

| 假設 | 多出舊列數 | 預估釋放 |
|---|---|---|
| broker_flows 90 → 60 天（截止 2026-09-16） | 79,323 | 約 19MB |
| broker_flows 90 → 45 天（截止 2026-10-01） | 138,698 | 約 34MB |
| inst_holding 180 → 90 天（截止 2026-08-17） | 82,866 | 約 42MB |
| inst_holding 180 → 60 天（截止 2026-09-16） | 112,802 | 約 57MB |
| twse_market_snapshot 400 → 180 天（截止 2026-05-19） | 17,160 | 約 4MB |

這些是「使用者決定」項目，本次沒有修改任何保留天數。

## 9. 刪前先匯出（2026-11-15 新流程）

### 9.1 共用匯出模組 `db_archive.py`

- 沿用 `bt_cache_store.py`：傳輸層直接用 `SupabaseStorage`（私有 bucket，預設 `db-archive`，可用 `DB_ARCHIVE_BUCKET` 覆寫）、單片上限 45MB、先傳分片最後才寫 `manifest.json`。
- 格式：Parquet（zstd，需 pyarrow）；沒有 pyarrow 或欄位混型別時退回 `jsonl.gz`。每片的格式記在 manifest。
- 校驗：匯出後把 manifest 與全部分片下載回來，檢查 bytes、sha256、解碼後列數，總列數必須等於伺服器 `count=exact`。任何不符丟 `ArchiveError`。
- 讀取順序：日期欄位 + 決勝欄位（PK，於 2026-11-15 唯讀查得）固定排序，避免分頁漏列／重複；讀出列數與伺服器計數不符就放棄。
- 物件路徑：`<表>/<起始日>_<截止日>_<UTC時間>/part-NNN.parquet` 與 `manifest.json`，不覆蓋舊批次。

### 9.2 `db_maintenance.py`（每日自動）

- `ARCHIVE_TABLES`：inst_holding、broker_flows、twse_market_snapshot、market_signal_snapshot，另含 factor_snapshot、intraday_5min_bars、intraday_gate_results、route2_watchlist、broker_style_daily、news_items、mops_events、premarket_brief、premarket_outcome。刻意不含可再生的日誌與快取（perf_log、system_run_log、data_source_health_log、ui_selftest_reports、warcard_cache、overnight_scan_results）。
- 逐日刪除前：①匯出該日列並校驗 ②刪除前再核對「待刪列數 == 已匯出列數」③才刪。匯出器不可用（缺 Storage 設定）、匯出失敗、校驗失敗、列數不符，任一情況都**不刪該表**，其他表照常。
- 失敗的表會出現在清理報告，且報告層級升為 warn，觸發 Telegram 推播。
- `DB_MAINT_DRY=1` 仍為只回報（不匯出、不刪）。

### 9.3 `db_retention.py`（人工核准後執行）

- 預設乾跑：不連線，印出每表計畫與 SQL 字串，並附 official_open_data 瘦身報告。
- 涵蓋：4 張熱表；`backup_*`（整表匯出後 DROP，表名日期距今須 ≥ 30 天，目前 4 張都符合）；`telegram_inbox`、`telegram_inbox_images`、`manual_feed_batches`（保留 30 天）；`official_open_data` 只出乾跑報告（刪舊仍由 `od_retention.py` 負責）。
- `--execute`：必須設 `RETENTION_CONFIRM=I_HAVE_EXPORTED` 且 Storage 設定齊全；逐表「匯出到 Storage → 校驗 → 刪前對帳 → 刪除/DROP」，單表失敗不動該表並回傳非 0。不再需要本機 manifest（GitHub 執行環境不保留檔案）；`--export` 保留為選用的本機 gzip 副本。
- DROP 只經 RPC `retention_drop_backup_table`（只接受 `backup_..._YYYYMMDD` 表名、無 CASCADE、僅 service_role）。**此 RPC 尚未套用到正式資料庫**，需使用者核准後在 SQL 編輯器執行 `supabase_migration_retention_drop_rpc.sql`；未套用時備份表只會匯出、不會被刪，並提示原因。
- GitHub：`.github/workflows/db_retention.yml`（僅手動）。`execute` 預設 false；要真刪需 `execute=true` 且 `confirm=I_HAVE_EXPORTED`。流程永遠先乾跑再執行。

### 9.4 核准後的執行順序

1. 使用者在 SQL 編輯器套用 `supabase_migration_retention_drop_rpc.sql`（只為備份表 DROP；不需要的話可略過，備份表就只匯出）。
2. 確認 GitHub Secrets 有 `SUPABASE_URL`、`SUPABASE_KEY`（建議另設 `SUPABASE_STORAGE_KEY`）。
3. 手動執行 `db_retention.yml`，`execute=false` 看乾跑；再以 `execute=true`、`confirm=I_HAVE_EXPORTED` 執行，可用 `tables` 先試單一小表。
4. 到 Storage `db-archive` bucket 確認出現各批次 `manifest.json`；到 `system_run_log`／Actions 日誌確認各表「完成」。
5. 清理後以唯讀 SQL 重量大小；若空間未降，再評估 VACUUM。
6. Storage 免費額度（使用者背景為 1GB，未查證）：Parquet 壓縮後 4 張熱表舊列約數 MB，備份表約 5MB 壓縮後更小；額度足夠，但縮短保留期後的大量匯出要先估容量。

### 9.5 pyarrow 相依

`system_scheduler.yml` 的 pip 安裝行與新的 `db_retention.yml` 已加入 `pyarrow`；沒有 pyarrow 時自動退回 `jsonl.gz`，匯出與校驗仍有效，只是格式不同。`requirements.txt`（Streamlit 網頁用）未變更。
