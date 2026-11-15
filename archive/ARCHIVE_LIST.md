# archive/ 清單：舊版 Cloudflare Worker 腳本（2026-11-15 移入）

移入原因：根目錄 Worker 檔過多，但只有 `warroom_monitor_worker.js` 是線上現行版（system_scheduler.py 的 deploy 步驟讀它）。
其餘 16 份為歷代版本，已無 workflow 或文件引用；只有 system_scheduler.py 的 deploy 步驟用它們做「線上是否為已知上一版」的 sha256 比對，
該讀取路徑已改為先讀 archive/、找不到再退回根目錄。檔案以 git mv 搬移，歷史可追溯（`git log --follow archive/<檔名>`）。

| 檔名 | sha256（前16碼） | 行數 |
|---|---|---|
| warroom_monitor_worker.v10.js | b78fc3dafc617802 | 353 |
| warroom_monitor_worker.v10_1.js | 056ff5fab9822ad5 | 356 |
| warroom_monitor_worker.v10_2.js | 6099e26d592a3b6f | 533 |
| warroom_monitor_worker.v10_3.js | 14bdc4332adb8cb0 | 537 |
| warroom_monitor_worker.v10_4.js | 958e90200bc09fd5 | 545 |
| warroom_monitor_worker.v10_5.js | 97b917f9d87bcde9 | 653 |
| warroom_monitor_worker.v10_6.js | 25ccf5b7aaea79d7 | 654 |
| warroom_monitor_worker.v10_6b.js | e4867499906a0068 | 490 |
| warroom_monitor_worker.v10_7.js | da019081e0bb0d57 | 495 |
| warroom_monitor_worker.v10_8.js | b342c792357c44c9 | 497 |
| warroom_monitor_worker.v10_9.js | 32001b5765a52604 | 499 |
| warroom_monitor_worker.v6.js | 6ce5fee99f4e11b3 | 293 |
| warroom_monitor_worker.v7.js | 5e2fde51c057dfbf | 326 |
| warroom_monitor_worker.v8.js | f2c69f6ea4e6b990 | 328 |
| warroom_monitor_worker.v9.js | 22b89914de264841 | 319 |
| warroom_monitor_worker.v9_1.js | f9f92af6a023d392 | 321 |

根目錄保留：`warroom_monitor_worker.js`（線上現行版，未移動）。
