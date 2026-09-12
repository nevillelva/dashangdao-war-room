/**
 * 戰情室 R98 獨立監控 Worker（V3 — 補上三個隔日沖時效性排程）
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
 * 每5分鐘（cron 格式：星號斜線5）。時效性排程若還停在每20分鐘，2分鐘的
 * grace 會等到下一次20分鐘週期才被抓到，scan 的12分鐘視窗會來不及。
 * 另需 secret：GITHUB_TOKEN（fine-grained PAT，對本repo Actions R/W）。
 */

const GITHUB_OWNER = "nevillelva";
const GITHUB_REPO = "dashangdao-war-room";
const GITHUB_WORKFLOW_FILE = "system_scheduler.yml";
const GITHUB_REF = "main";

// 【排程表】完全對應 system_scheduler.yml 裡的 cron 設定（皆為UTC時間）。
// days: cron的day-of-week欄位，0=週日...6=週六（跟JS的Date.getUTCDay()一致）
// grace: 寬限分鐘數——超過「排定時間+grace」還查無執行紀錄，才視為漏跑
const SCHEDULE = [
  { stage: "gate",                       h: 0,  m: 55, days: [1,2,3,4,5], grace: 15 },
  { stage: "build_intraday_pool",        h: 1,  m: 5,  days: [1,2,3,4,5], grace: 15 },
  { stage: "route2_confirm_scan",        h: 1,  m: 10, days: [1,2,3,4,5], grace: 15 },
  { stage: "morning_exit",               h: 1,  m: 15, days: [1,2,3,4,5], grace: 15 },
  { stage: "intraday_kbar",              h: 1,  m: 24, days: [1,2,3,4,5], grace: 10 },
  { stage: "intraday_execute",           h: 2,  m: 2,  days: [1,2,3,4,5], grace: 20 },
  { stage: "time_stop_check",            h: 2,  m: 9,  days: [1,2,3,4,5], grace: 15 },
  { stage: "key_usage_monitor",          h: 2,  m: 30, days: [1,2,3,4,5], grace: 20 },
  { stage: "mops_balance_sheet_backfill",h: 2,  m: 3,  days: [1,2,3,4,5], grace: 60 },
  { stage: "mops_income_statement_backfill", h: 3, m: 9, days: [1,2,3,4,5], grace: 60 },
  { stage: "tail_entry",                 h: 5,  m: 0,  days: [1,2,3,4,5], grace: 20 },
  { stage: "intraday_force_exit",        h: 5,  m: 25, days: [1,2,3,4,5], grace: 20 },
  { stage: "big_holder",                 h: 2,  m: 0,  days: [6],         grace: 60 },
  { stage: "disposal_watch",             h: 9,  m: 30, days: [1,2,3,4,5], grace: 30 },
  { stage: "nightly_analysis_report",    h: 10, m: 9,  days: [1,2,3,4,5], grace: 30 },
  { stage: "health",                     h: 13, m: 30, days: [1,2,3,4,5], grace: 30 },
  { stage: "cleanup_test_residue",       h: 13, m: 35, days: [1,2,3,4,5], grace: 30 },
  { stage: "data_health_check",          h: 13, m: 40, days: [1,2,3,4,5], grace: 30 },
  { stage: "financial_health_scan",      h: 13, m: 45, days: [2,5],       grace: 30 },
  { stage: "mops_financial_scan",        h: 13, m: 50, days: [2,5],       grace: 30 },
  { stage: "signal",                     h: 14, m: 0,  days: [1,2,3,4,5], grace: 30 },
  { stage: "overnight_scan",             h: 14, m: 15, days: [1,2,3,4,5], grace: 30 },
  { stage: "smart_money_scan",           h: 14, m: 30, days: [1,2,3,4,5], grace: 30 },
  { stage: "filter_backtest",            h: 19, m: 0,  days: [0],         grace: 60 },
  { stage: "overnight_flip_dealer_stats",h: 19, m: 10, days: [0],         grace: 60 },
  { stage: "data_source_health_report",  h: 19, m: 20, days: [0],         grace: 60 },
  // 【V3新增】三個隔日沖時效性排程——grace刻意給緊，讓Worker當主要觸發器。
  { stage: "overnight_flip_premarket_monitor", h: 0, m: 29, days: [1,2,3,4,5], grace: 2 },
  { stage: "overnight_flip_exit_monitor",      h: 1, m: 0,  days: [1,2,3,4,5], grace: 2 },
  { stage: "overnight_flip_scan",              h: 5, m: 13, days: [1,2,3,4,5], grace: 2 },
];

// broker_flows特殊處理：不是單一時間點，而是「收盤後14:00到隔天08:40
// (台灣)，每20分鐘一批」的高頻窗口型排程，改用「窗口內+距上次執行是否
// 超過25分鐘」的邏輯判斷，而不是逐一比對19個時段。
// 窗口換算成UTC（跨夜）：06:00～23:59 以及 00:00～00:40（隔天）
function isBrokerFlowsWindow(now) {
  const day = now.getUTCDay(); // 0=Sun...6=Sat
  const hh = now.getUTCHours();
  const mm = now.getUTCMinutes();
  const minutesNow = hh * 60 + mm;
  // 平日 Mon-Fri (1-5)：06:00(360分) ~ 23:59
  const inEveningPart = day >= 1 && day <= 5 && minutesNow >= 360;
  // 隔天清晨 00:00~00:40，原cron是週二到週六(2-6)因為算的是「隔天」
  const inEarlyMorningPart = day >= 2 && day <= 6 && minutesNow <= 40;
  return inEveningPart || inEarlyMorningPart;
}

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

    const hasRun = await hasStageRunSince(env, item.stage, scheduledToday);
    if (hasRun) continue; // 正常跑過了，不用管

    const canDispatch = await checkAndSetCooldown(env, item.stage, 60);
    if (!canDispatch) {
      summary.skipped_cooldown.push(item.stage);
      continue;
    }

    const dispatchResult = await dispatchWorkflow(env, item.stage);
    summary.dispatched.push({ stage: item.stage, scheduled: scheduledToday.toISOString(), dispatch_ok: dispatchResult.ok, status: dispatchResult.status });
  }

  // ── 3. broker_flows 窗口型檢查 ──
  if (isBrokerFlowsWindow(now)) {
    const lastRun = await lastStageRunAt(env, "broker_flows");
    const minutesSinceLast = lastRun ? (now - lastRun) / 60000 : Infinity;
    if (minutesSinceLast > 25) {
      const canDispatch = await checkAndSetCooldown(env, "broker_flows", 15);
      if (canDispatch) {
        const dispatchResult = await dispatchWorkflow(env, "broker_flows");
        summary.dispatched.push({ stage: "broker_flows", minutes_since_last: minutesSinceLast, dispatch_ok: dispatchResult.ok, status: dispatchResult.status });
      } else {
        summary.skipped_cooldown.push("broker_flows");
      }
    }
  }

  // ── 4. 如果這次真的補跑了任何東西，額外發一則Telegram通知（跟純警報分開，讓你知道「有出手」而不是靜默） ──
  if (summary.dispatched.length > 0) {
    const lines = summary.dispatched.map(d => `・${d.stage}${d.dispatch_ok ? "" : "（呼叫失敗,HTTP " + d.status + "）"}`);
    await sendTelegram(
      env,
      `🛠️ [獨立監控-Cloudflare] 偵測到 ${summary.dispatched.length} 個排程漏跑，已主動補打GitHub Actions觸發：\n` +
        lines.join("\n")
    );
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

async function sendTelegram(env, text) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return;
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text }),
  });
}
