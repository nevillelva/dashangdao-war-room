/**
 * 戰情室 R98 獨立監控 Worker（V10.2 — 早盤情報 05:30／08:00 派發＋Telegram 分段＋休市日讀 Supabase）
 * ────────────────────────────────────────────────────
 * V1：偵測「系統整體沉默太久」並發 Telegram 警報。
 * V2：Worker 自己維護一份跟 system_scheduler.yml 對應的排程表，逐條檢查
 *     漏跑就主動 workflow_dispatch 補跑，繞過 GitHub 自己 cron 排程器
 *     （2026/08/26起有隨機丟棄排程的已知bug）。
 *
 * 【V3 修正，2026-09-12 診斷後補】總指揮官的隔日沖策略(R98續132~138)三個
 * 時效性排程，因為晚於本 Worker(R98續101)建立，一直沒被加進下面的
 * SCHEDULE 看門清單——導致它們完全不受這個「繞過 GitHub cron bug」的
 * 保護，只能落到另一支用240分鐘緩衝的 GitHub Actions 健康監控手上，
 * 每次都在盤後幾小時才空補跑，等於整套隔日沖進場/盤前/出場監控從未真正
 * 在有效時間執行過。經查 system_run_log 證實：這三個 stage 全部歷史紀錄
 * 都是補跑時間、無任何一次準時。本版把它們補進 SCHEDULE，grace 給得很緊
 * （2分鐘），讓 Worker 當它們真正的主要觸發器：
 *   - premarket_monitor 08:29(00:29 UTC)：盤前試撮監控，訂閱到09:00，
 *     08:59:45起偵測崩塌，早幾分鐘開始訂閱無妨，grace 2 綽綽有餘。
 *   - exit_monitor 09:00(01:00 UTC)：出場監控到09:15，必須貼近09:00開始。
 *   - scan 13:13(05:13 UTC)：進場篩選，13:25下單截止，只有12分鐘視窗，
 *     grace 最緊，並保留原生 13:13+13:16 雙 cron 當額外備援（三個保險）。
 * 三個 stage 內部都有「已跑過就跳過」的去重機制(scan查normal紀錄、
 * exit/premarket用claim認領)，就算原生cron跟Worker都觸發也不會重複執行。
 *
 * 部署後務必確認：Cloudflare Dashboard → Triggers → Cron Trigger 頻率是
 * 每1分鐘。另需 secret：GITHUB_TOKEN（fine-grained PAT，對本repo Actions R/W）。
 *
 * 【V10.2，2026-10-07】
 *   1) 早盤情報：台北 05:30（UTC 週日~週四 21:30）派發 premarket_brief（證交所重大訊息檔約 05:24 才更新；stage 內會等到 05:48），
 *      06:10 前才有效（排程端時窗 05:15~06:10，使用者要求 06:00 前收到）；台北 08:00（UTC 週一~週五 00:00）派發 premarket_supplement（時窗到 09:00）。
 *      兩者都是「交易日」才發（market:true）。05:55 還沒有紀錄 → 發「早盤情報延遲」警示一次。
 *   2) sendTelegram 超過 3800 字自動分段（單則上限 4096，超過整則被拒收）；429 依 retry_after 重試一次。
 *   3) 休市日：內建 2026 清單之外，另讀 system_config.tw_market_closed_dates（排程端每天用證交所 API 發布 2026~2027），2027 起不必再改程式。
 *
 * 【V10，2026-10-05】兩項調整：
 *   1) 券商分點：取消「隔天 08:10」時點，改成「當晚」兩個時點——台北 19:20（DJ 資料 19:14 左右更新）與 20:40（備援），
 *      兩者都在 22:00 選股階段之前完成；已跑過(有紀錄)就不補發。
 *   2) 三關：新增 intraday_snap（Shioaji 當日 1分K，不輪詢）。
 *      【V10.1，2026-10-06 使用者指正：要「9:30 與 10:00 各抓一次」】台北 09:26 派發（登入後等到 09:30:30 才查，含到 09:30 結束的最後一根）、
 *      09:56 派發（等到 10:00:30 才查，含到 10:00 結束的最後一根）。K 棒以截止時間截斷，Worker 晚幾分鐘派發結果也一樣。
 *      intraday_mode(system_config)：shadow(預設，輪詢仍是正式來源、快照只比對)→連 2 天相符自動切 fast→輪詢(09:11)略過
 *      （Worker 讀 intraday_mode，fast 時不再派發輪詢；poll 時不派發快照）。兩個快照時點各自獨立冷卻(cooldownKey)。
 *
 * 【V9.1，2026-10-05】V9 實測：尾盤進場 13:18:43 派發、145 秒跑完；強制出場 13:28:43 派發→13:29 才完成(貼近 13:30 收盤)，grace 3→1。
 *
 * 【V9，2026-10-05 Actions 用量控制(≤2000 分鐘/月)】
 *   另外：tail_entry 改 13:17 派發(原 13:00 派發再睡 20 分鐘)；收盤後輕量階段合併為 bundle_evening/bundle_late；
 *   停用已完成的 mops 回補與從未產生部位的隔日沖三階段；新增 bt_nightly 保險時點；原生 cron 全部移除。
 *   原本「broker_flows 窗口型檢查」每 25 分鐘就 dispatch 一次(14:00~隔天 08:40，一天約 35 次)，
 *   而 stage_broker_flows 在「今天都抓完了」時不寫紀錄 → 看門狗永遠覺得沒跑 → 整晚空轉重發。
 *   券商分點改走免費 DJ 公開頁後，單次約 7 分鐘就抓完，所以改成「固定時點」：台北 19:10、21:10、隔天 08:10
 *   （與 broker_flows_scheduler.yml 的 19:07/21:07/08:07 原生 cron 互為備援），已跑過(任何紀錄)就不補發。
 *   stage_broker_flows 也改成「無事可做」時同樣寫一筆 already_complete 紀錄。
 *
 * 【V7修正，2026-10-05 排程稽核(system_run_log 14 個交易日)後】
 *   1) intraday_kbar 在 system_run_log 裡實際寫的 stage 名稱是 intraday_gate，
 *      原本拿 intraday_kbar 去查永遠查不到→每天被重發約 22 次、整天到收盤後都還在發。
 *      新增 logStage 欄位指定「查紀錄用的名稱」。
 *   2) 沒有截止時間：補發可以拖到隔天凌晨。新增 deadline(排定後幾分鐘內才補發)，
 *      超過就不補(排程端另有時窗守門，晚到的補跑也會被略過並留 skipped_late 紀錄)。
 *   3) 沒有休市日判斷：新增 MARKET_CLOSED(與 system_scheduler.py 的 _TW_MARKET_CLOSED_2026 同步)，
 *      休市日不補發盤中類 stage。
 *   4) 盤中類 stage 的 grace 縮到 2~3 分鐘(GitHub 原生 cron 實測延遲 3~7 小時，Worker 才是主要觸發器)。
 *   5) 補進 portfolio_value_snapshot / industry_rotation_scan(原本完全沒有看門)。
 *
 * 【V6修正，2026-09-16 總指揮官反映三關大量unknown，查production資料後
 * 發現的真因】intraday_kbar查證發現過去7個交易日有5天(71%)排定09:24卻
 * 延遲到09:35才真正開始收集K棒(GitHub Actions排程佇列延遲的常態模式)，
 * 導致「第一根bar」與「09:30/09:35錨點bar」重合，觸發上輪修的誠實None
 * 防呆(避免假的0.0%漲跌幅)，讓gate2大量顯示「缺個股與龍頭資料」。已把
 * system_scheduler.yml的觸發點從09:24/09:29提前到09:13/09:18吸收這個
 * 延遲，這裡的看門狗排程表同步更新，避免看門狗用舊時間點判斷「有沒有
 * 漏跑」而失準。
 */

const GITHUB_OWNER = "nevillelva";
const GITHUB_REPO = "dashangdao-war-room";
const GITHUB_WORKFLOW_FILE = "system_scheduler.yml";
const GITHUB_REF = "main";

// 【R98續R6(槓桿2)保溫】Streamlit app 公開網址。Worker 盤中時段順便 GET 它，
// 讓 Streamlit Cloud 容器不睡 → 省掉冷啟動 20~40 秒的容器喚醒(總指揮官反映
// 隔夜冷啟動登入要1分鐘的最大一塊)。公開網址、非機密，直接寫死。
const STREAMLIT_APP_URL = "https://dashangdao-war-room-n9soppujuzqzhute5j9uzz.streamlit.app/";

// 【排程表】完全對應 system_scheduler.yml 裡的 cron 設定（皆為UTC時間）。
// days: cron的day-of-week欄位，0=週日...6=週六（跟JS的Date.getUTCDay()一致）
// grace: 寬限分鐘數——超過「排定時間+grace」還查無執行紀錄，才視為漏跑
// 【V7】欄位：stage=要 dispatch 的名稱；logStage=查 system_run_log 用的名稱(預設同 stage)；
// grace=排定後幾分鐘還沒紀錄才補發；deadline=排定後幾分鐘之後就不再補發(預設 180)；
// market=true 代表盤中類，休市日不補發。
const DEFAULT_DEADLINE = 180;
const SCHEDULE = [
  { stage: "gate",                       h: 0,  m: 55, days: [1,2,3,4,5], grace: 3,  deadline: 270, market: true },
  { stage: "build_intraday_pool",        h: 1,  m: 5,  days: [1,2,3,4,5], grace: 3,  deadline: 240, market: true },
  { stage: "route2_confirm_scan",        h: 1,  m: 10, days: [1,2,3,4,5], grace: 3,  deadline: 240, market: true },
  { stage: "morning_exit",               h: 1,  m: 15, days: [1,2,3,4,5], grace: 3,  deadline: 75,  market: true },
  // 【V6調整】09:24→09:13(01:13 UTC)；【V8】再提前到09:11(01:11 UTC)：輪詢要在 09:20 前開始，
  // 09:25 那根K棒才有前一棒可相減出成交量→第一關量比才算得出來(10/5稽核：9/15起每天都是 NULL)。
  // 【V7】三關K棒階段在 system_run_log 的名稱是 intraday_gate。
  { stage: "intraday_kbar", logStage: "intraday_gate", h: 1, m: 11, days: [1,2,3,4,5], grace: 2, deadline: 45, market: true,
    skipIfConfig: { key: "intraday_mode", equals: "fast" } },   // V10：快照模式啟用時不再派發輪詢
  // 【V10.1】三關快照：09:26 派發(登入後等到 09:30:30 才查「9:30」)、09:56 派發(等到 10:00:30 才查「10:00」)；各自冷卻，互不擋對方。
  // 資料以 complete_before 截斷(09:30／10:00)，所以派發晚幾分鐘結果不變；pass1 截止 09:46(仍屬 pass1：stage 以 09:50 分界)。
  { stage: "intraday_snap", h: 1, m: 26, days: [1,2,3,4,5], grace: 0, deadline: 20, market: true,
    cooldownKey: "intraday_snap_1", cooldown: 9, skipIfConfig: { key: "intraday_mode", equals: "poll" } },
  { stage: "intraday_snap", h: 1, m: 56, days: [1,2,3,4,5], grace: 0, deadline: 12, market: true,
    cooldownKey: "intraday_snap_2", cooldown: 9, skipIfConfig: { key: "intraday_mode", equals: "poll" } },
  { stage: "intraday_execute",           h: 2,  m: 2,  days: [1,2,3,4,5], grace: 3,  deadline: 210, market: true },
  { stage: "time_stop_check",            h: 2,  m: 9,  days: [1,2,3,4,5], grace: 3,  deadline: 260, market: true },
  { stage: "key_usage_monitor",          h: 2,  m: 30, days: [1,2,3,4,5], grace: 20, deadline: 240, market: true },
  // 【V9】尾盤進場：原本 13:00 觸發後程式自己睡到 13:20 → 每天白白計費約 20 分鐘。Worker 每分鐘都在，
  // 改 13:17 才派發（stage 內仍會等到 13:20，只剩 ≤3 分鐘）。
  // 【V10.2】早盤情報：台北 05:30 = UTC 21:30（前一個 UTC 日）→ days 用 UTC 星期日~四[0,1,2,3,4]；台北 08:00 = UTC 00:00 → 週一~五。
  { stage: "premarket_brief",            h: 21, m: 30, days: [0,1,2,3,4], grace: 1, deadline: 38, market: true, cooldown: 90 },
  { stage: "premarket_supplement",       h: 0,  m: 0,  days: [1,2,3,4,5], grace: 1, deadline: 55, market: true, cooldown: 90 },
  { stage: "tail_entry",                 h: 5,  m: 17, days: [1,2,3,4,5], grace: 1,  deadline: 12,  market: true },
  { stage: "intraday_force_exit",        h: 5,  m: 25, days: [1,2,3,4,5], grace: 1,  deadline: 60,  market: true },   // V9.1：實測 13:25+3 分才派發→13:29 才跑完，貼近收盤 13:30，grace 縮到 1
  { stage: "big_holder",                 h: 2,  m: 0,  days: [6],         grace: 60 },
  // 【V9】收盤後輕量階段合併成兩個 job（每個 job 至少計 1 分鐘，原本 9 個 job）：
  //   bundle_evening 台北 19:45：disposal_watch、portfolio_value_snapshot、nightly_analysis_report、
  //                              industry_rotation_scan、compute_industry_leaders、etf_dividend_sync
  //   bundle_late    台北 21:40：health、cleanup_test_residue、data_health_check
  { stage: "bundle_evening",             h: 11, m: 45, days: [1,2,3,4,5], grace: 5,  deadline: 400 },
  { stage: "bundle_late",                h: 13, m: 40, days: [1,2,3,4,5], grace: 5,  deadline: 300 },
  { stage: "financial_health_scan",      h: 13, m: 45, days: [2,5],       grace: 30 },
  { stage: "mops_financial_scan",        h: 13, m: 50, days: [2,5],       grace: 30 },
  // 【V9】原生 cron 全部移除（實測延遲 3~7 小時、與 Worker 重複觸發白耗分鐘），這幾個夜間階段 grace 縮到 3 分鐘。
  { stage: "signal",                     h: 14, m: 0,  days: [1,2,3,4,5], grace: 3 },
  { stage: "overnight_scan",             h: 14, m: 15, days: [1,2,3,4,5], grace: 3 },
  { stage: "smart_money_scan",           h: 14, m: 30, days: [1,2,3,4,5], grace: 3 },
  // 【V9】回測規則夜間作業：stage_signal 結尾會順帶執行；這裡是獨立保險（signal 失敗/晚到時才會補派）。
  { stage: "bt_nightly",                 h: 14, m: 50, days: [1,2,3,4,5], grace: 15, deadline: 600 },
  { stage: "filter_backtest",            h: 19, m: 0,  days: [0],         grace: 60 },
  { stage: "overnight_flip_dealer_stats",h: 19, m: 10, days: [0],         grace: 60 },
  { stage: "data_source_health_report",  h: 19, m: 20, days: [0],         grace: 60 },
  // 【V10】券商分點(DJ 免費來源)：只排「當晚」兩個時點——台北 19:20(主) / 20:40(備援)，皆在 22:00 選股前完成；
  // 取消 V9 的「隔天 08:10」。紀錄名稱 broker_flows。
  { stage: "broker_flows",                h: 11, m: 20, days: [1,2,3,4,5], grace: 2, deadline: 80 },
  { stage: "broker_flows",                h: 12, m: 40, days: [1,2,3,4,5], grace: 3, deadline: 70 },
  // 【V9】已停用（依 2026-10-05 稽核 6.2 建議，省 Actions 分鐘；階段程式碼保留，可手動 dispatch）：
  //   mops_balance_sheet_backfill / mops_income_statement_backfill：回補已完成(還剩 0 檔)
  //   overnight_flip_premarket_monitor / overnight_flip_exit_monitor / overnight_flip_scan：隔日沖從未產生過部位
];

// 【V7】證交所休市日（平日國定假日/補假/春節結算休市），台北日期；與 system_scheduler.py
// 的 _TW_MARKET_CLOSED_2026 保持同步。盤中類 stage 在這些日期不補發。
const MARKET_CLOSED = new Set([
  "2026-01-01", "2026-02-12", "2026-02-13", "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18",
  "2026-02-19", "2026-02-20", "2026-02-27", "2026-02-28", "2026-04-03", "2026-04-04", "2026-04-05",
  "2026-04-06", "2026-05-01", "2026-06-19", "2026-09-25", "2026-09-28", "2026-10-09", "2026-10-10",
  "2026-10-25", "2026-10-26", "2026-12-25",
]);
function taipeiDateStr(now) {
  return new Date(now.getTime() + 8 * 3600 * 1000).toISOString().slice(0, 10);
}
// 【V10.2】休市日 = 內建 2026 清單 ∪ system_config.tw_market_closed_dates（排程端 stage_db_maintenance 每天發布，涵蓋 2026~2027）。
// 讀取失敗一律退回內建清單（不影響既有行為）。每次 Worker 執行只查一次。
let _closedExtra = null;   // 每次 runWatchdog 開頭清掉（每次執行只查一次）
async function loadClosedExtra(env) {
  if (_closedExtra) return _closedExtra;
  let set = new Set();
  try {
    const url = `${env.SUPABASE_URL}/rest/v1/system_config?select=config_value&config_key=eq.tw_market_closed_dates&limit=1`;
    const resp = await fetch(url, { headers: { apikey: env.SUPABASE_KEY, Authorization: `Bearer ${env.SUPABASE_KEY}` } });
    if (resp.ok) {
      const rows = await resp.json();
      if (rows.length) {
        const v = JSON.parse(String(rows[0].config_value || "{}"));
        if (Array.isArray(v.dates)) set = new Set(v.dates.filter(d => /^\d{4}-\d{2}-\d{2}$/.test(d)));
      }
    }
  } catch (e) { /* 退回內建清單 */ }
  _closedExtra = set;
  return set;
}
async function isMarketClosed(env, now) {
  const d = taipeiDateStr(now);
  if (MARKET_CLOSED.has(d)) return true;
  return (await loadClosedExtra(env)).has(d);
}

// broker_flows特殊處理：不是單一時間點，而是「收盤後14:00到隔天08:40
// (台灣)，每20分鐘一批」的高頻窗口型排程，改用「窗口內+距上次執行是否
// 超過25分鐘」的邏輯判斷，而不是逐一比對19個時段。
// 窗口換算成UTC（跨夜）：06:00～23:59 以及 00:00～00:40（隔天）
// （V9：isBrokerFlowsWindow 已移除，見 SCHEDULE 的 broker_flows 固定時點）

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runWatchdog(env));
  },
  async fetch(request, env, ctx) {
    const result = await runWatchdog(env);
    return new Response(JSON.stringify(result, null, 2), {
      headers: { "content-type": "application/json; charset=utf-8" },
    });
  },
};

async function runWatchdog(env) {
  _closedExtra = null;
  const now = new Date();
  const summary = { ok: true, checked_at: now.toISOString(), dispatched: [], skipped_cooldown: [], silence_alert: null };

  // ── 1. 原本V1的「系統整體沉默太久」總體檢查（保留，當最後一層安全網）──
  const silence = await checkOverallSilence(env, now);
  summary.silence_alert = silence;

  // ── 2. 逐項排程檢查，漏跑就主動dispatch補跑 ──
  for (const item of SCHEDULE) {
    const scheduledToday = scheduledTimeToday(now, item.h, item.m);
    const dayMatches = item.days.includes(now.getUTCDay());
    if (!dayMatches) continue;
    if (now < addMinutes(scheduledToday, item.grace)) continue; // 還沒超過寬限期
    // 【V7】超過截止時間就不補發（排程端也有時窗守門，晚到的補跑會被略過）
    if (now > addMinutes(scheduledToday, item.deadline ?? DEFAULT_DEADLINE)) continue;
    // 【V7】休市日不補發盤中類 stage
    if (item.market && (await isMarketClosed(env, now))) continue;

    // 【V10】依 system_config 的設定略過（例如快照模式啟用時不再派發輪詢）
    if (item.skipIfConfig) {
      const cv = await getConfigValue(env, item.skipIfConfig.key);
      if (cv === item.skipIfConfig.equals) continue;
    }

    const hasRun = await hasStageRunSince(env, item.logStage || item.stage, scheduledToday);
    if (hasRun) continue; // 正常跑過了，不用管

    const canDispatch = await checkAndSetCooldown(env, item.cooldownKey || item.stage, item.cooldown ?? 60);
    if (!canDispatch) {
      summary.skipped_cooldown.push(item.stage);
      continue;
    }

    const dispatchResult = await dispatchWorkflow(env, item.stage);
    summary.dispatched.push({ stage: item.stage, scheduled: scheduledToday.toISOString(), dispatch_ok: dispatchResult.ok, status: dispatchResult.status });
  }

  // ── 2b. 【V10.2】早盤情報延遲警示：台北 05:55~06:40 還沒有 premarket_brief 的紀錄（含「略過」紀錄）→ 提醒一次 ──
  try {
    const pmSched = scheduledTimeToday(now, 21, 30);
    if ([0,1,2,3,4].includes(now.getUTCDay()) && now >= addMinutes(pmSched, 25) && now <= addMinutes(pmSched, 70)
        && !(await isMarketClosed(env, now))) {
      const done = await hasStageRunSince(env, "premarket_brief", pmSched);
      if (!done && (await checkAndSetCooldown(env, "premarket_late_alert", 180))) {
        await sendTelegram(env, "⚠️ 早盤情報延遲：台北 05:55 了還沒有產生紀錄（證交所重大訊息檔可能還沒更新，或排程沒跑起來）。"
          + "Worker 已嘗試派發；稍後可到網站「📰 早盤情報」查看，或在 GitHub Actions 手動執行 premarket_brief。");
        summary.premarket_late_alert = true;
      }
    }
  } catch (e) {
    summary.premarket_late_alert_error = String(e);
  }

  // ── 3. （V9 已移除）broker_flows 窗口型輪詢：改為上面 SCHEDULE 的固定時點（V10：當晚 19:20 / 20:40）──

  // ── 4. 如果這次真的補跑了任何東西，額外發一則Telegram通知（跟純警報分開，讓你知道「有出手」而不是靜默） ──
  if (summary.dispatched.length > 0) {
    const lines = summary.dispatched.map(d => `・${d.stage}${d.dispatch_ok ? "" : "（呼叫失敗,HTTP " + d.status + "）"}`);
    await sendTelegram(
      env,
      `🛠️ [獨立監控-Cloudflare] 偵測到 ${summary.dispatched.length} 個排程漏跑，已主動補打GitHub Actions觸發：\n` +
        lines.join("\n")
    );
  }

  // ── 5. 保溫：低頻造訪 Streamlit app，避免容器12小時無流量被休眠 ──
  try {
    const _canWarm = await checkAndSetCooldown(env, "keepwarm_ping", 600);
    if (_canWarm && STREAMLIT_APP_URL) {
      const _wr = await fetch(STREAMLIT_APP_URL, {
        method: "GET",
        headers: { "User-Agent": "warroom-monitor-keepwarm" },
      });
      summary.keepwarm = { pinged: true, status: _wr.status };
    } else {
      summary.keepwarm = { pinged: false, reason: _canWarm ? "no_url" : "cooldown_active" };
    }
  } catch (e) {
    // 保溫失敗絕不影響看門狗本業，只記錄
    summary.keepwarm = { pinged: false, error: String(e) };
  }

  return summary;
}

async function checkOverallSilence(env, now) {
  const url = `${env.SUPABASE_URL}/rest/v1/system_run_log?select=stage,created_at&order=created_at.desc&limit=1`;
  const resp = await fetch(url, {
    headers: { apikey: env.SUPABASE_KEY, Authorization: `Bearer ${env.SUPABASE_KEY}` },
  });
  if (!resp.ok) {
    await sendTelegram(env, `🔴 [獨立監控-Cloudflare] 查詢Supabase失敗(HTTP ${resp.status})，無法確認排程系統現況，請立即人工檢查。`);
    return { alerted: true, reason: "supabase_query_failed" };
  }
  const rows = await resp.json();
  if (!rows.length) return { alerted: false, reason: "no_rows" };

  const lastRun = new Date(rows[0].created_at);
  const minutesSince = (now - lastRun) / 60000;
  const THRESHOLD_MINUTES = 40;
  if (minutesSince > THRESHOLD_MINUTES) {
    await sendTelegram(
      env,
      `🔴 [獨立監控-Cloudflare] 偵測到排程系統異常沉默！\n最近一次成功執行的stage：${rows[0].stage}\n距離現在已經 ${minutesSince.toFixed(0)} 分鐘沒有任何排程執行過（門檻${THRESHOLD_MINUTES}分鐘）\n主動補跑機制也在同步運作中，若持續沉默代表連補跑都可能受阻，請人工檢查。`
    );
    return { alerted: true, minutesSince, lastStage: rows[0].stage };
  }
  return { alerted: false, minutesSince, lastStage: rows[0].stage };
}

async function hasStageRunSince(env, stage, sinceDate) {
  const url = `${env.SUPABASE_URL}/rest/v1/system_run_log?select=created_at&stage=eq.${encodeURIComponent(stage)}&created_at=gte.${sinceDate.toISOString()}&limit=1`;
  const resp = await fetch(url, {
    headers: { apikey: env.SUPABASE_KEY, Authorization: `Bearer ${env.SUPABASE_KEY}` },
  });
  if (!resp.ok) return true; // 查詢失敗時保守處理，不誤觸發補跑
  const rows = await resp.json();
  return rows.length > 0;
}

// 【V10】讀 system_config 的設定值（讀不到/查詢失敗一律回 null＝當作沒設定，不影響既有行為）
async function getConfigValue(env, key) {
  try {
    const url = `${env.SUPABASE_URL}/rest/v1/system_config?select=config_value&config_key=eq.${encodeURIComponent(key)}&limit=1`;
    const resp = await fetch(url, { headers: { apikey: env.SUPABASE_KEY, Authorization: `Bearer ${env.SUPABASE_KEY}` } });
    if (!resp.ok) return null;
    const rows = await resp.json();
    return rows.length ? String(rows[0].config_value || "").trim().toLowerCase() : null;
  } catch (e) {
    return null;
  }
}

async function lastStageRunAt(env, stage) {
  const url = `${env.SUPABASE_URL}/rest/v1/system_run_log?select=created_at&stage=eq.${encodeURIComponent(stage)}&order=created_at.desc&limit=1`;
  const resp = await fetch(url, {
    headers: { apikey: env.SUPABASE_KEY, Authorization: `Bearer ${env.SUPABASE_KEY}` },
  });
  if (!resp.ok) return null;
  const rows = await resp.json();
  return rows.length ? new Date(rows[0].created_at) : null;
}

// 節流：若同一stage在cooldownMinutes內已經補打過，回傳false（不要再打）；
// 否則寫入/更新紀錄並回傳true（可以打）
async function checkAndSetCooldown(env, stage, cooldownMinutes) {
  const checkUrl = `${env.SUPABASE_URL}/rest/v1/cloudflare_dispatch_log?select=dispatched_at&stage=eq.${encodeURIComponent(stage)}&limit=1`;
  const checkResp = await fetch(checkUrl, {
    headers: { apikey: env.SUPABASE_KEY, Authorization: `Bearer ${env.SUPABASE_KEY}` },
  });
  if (checkResp.ok) {
    const rows = await checkResp.json();
    if (rows.length) {
      const last = new Date(rows[0].dispatched_at);
      const minutesSince = (new Date() - last) / 60000;
      if (minutesSince < cooldownMinutes) return false;
    }
  }
  const upsertUrl = `${env.SUPABASE_URL}/rest/v1/cloudflare_dispatch_log`;
  await fetch(upsertUrl, {
    method: "POST",
    headers: {
      apikey: env.SUPABASE_KEY,
      Authorization: `Bearer ${env.SUPABASE_KEY}`,
      "content-type": "application/json",
      Prefer: "resolution=merge-duplicates",
    },
    body: JSON.stringify({ stage, dispatched_at: new Date().toISOString(), reason: "watchdog_auto_dispatch" }),
  });
  return true;
}

async function dispatchWorkflow(env, stage) {
  if (!env.GITHUB_TOKEN) {
    return { ok: false, status: "no_token" };
  }
  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${GITHUB_WORKFLOW_FILE}/dispatches`;
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "warroom-monitor-cloudflare-worker",
      "content-type": "application/json",
    },
    body: JSON.stringify({ ref: GITHUB_REF, inputs: { stage } }),
  });
  return { ok: resp.status === 204, status: resp.status };
}

function scheduledTimeToday(now, h, m) {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), h, m, 0));
  return d;
}
function addMinutes(date, minutes) {
  return new Date(date.getTime() + minutes * 60000);
}

// 【V10.2】Telegram 單則上限 4096 字，超過會整則被拒收 → 依段落／換行切成 ≤3800 字，多則加 (i/n)；429 依 retry_after 重試一次。
function splitMessage(text, limit = 3800) {
  text = String(text ?? "");
  if (text.length <= limit) return [text];
  const parts = [];
  let cur = "";
  const push = (s) => { if (s) parts.push(s); };
  for (const para of text.split("\n")) {
    let line = para;
    while (line.length > limit) {            // 單行超長：硬切
      if (cur) { push(cur); cur = ""; }
      push(line.slice(0, limit));
      line = line.slice(limit);
    }
    if ((cur ? cur.length + 1 : 0) + line.length > limit) { push(cur); cur = line; }
    else cur = cur ? cur + "\n" + line : line;
  }
  push(cur);
  return parts.length > 1 ? parts.map((p, i) => `(${i + 1}/${parts.length})\n${p}`) : parts;
}

async function sendTelegram(env, text) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return;
  for (const chunk of splitMessage(text)) {
    for (let attempt = 0; attempt < 2; attempt++) {
      const resp = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text: chunk }),
      });
      if (resp.status !== 429) break;
      let wait = 2;
      try { wait = Math.min(((await resp.json()).parameters || {}).retry_after || 2, 10); } catch (e) { /* 預設 2 秒 */ }
      await new Promise(r => setTimeout(r, wait * 1000));
    }
  }
}
