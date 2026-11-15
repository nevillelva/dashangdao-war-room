# 資料庫容量保留方案（DB_RETENTION）

草擬日期：2026-11-15。對應工具：`db_retention.py`、`test_db_retention.py`、`supabase_migration_retention_plan.sql`。

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
