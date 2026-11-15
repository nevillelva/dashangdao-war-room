import fs from "node:fs";
import os from "node:os";
import path from "node:path";
// 把 warroom_monitor_worker.js(ES module 語法)複製成暫存 .mjs 再載入，不需要 package.json
const _tmp = path.join(os.tmpdir(), `worker_${process.pid}.mjs`);
fs.copyFileSync(new URL("./warroom_monitor_worker.js", import.meta.url), _tmp);
const worker = (await import(_tmp)).default;
const RealDate = Date;
let FAKE = new RealDate("2026-10-05T01:20:00Z").getTime();
globalThis.Date = class extends RealDate {
  constructor(...a) { if (a.length === 0) super(FAKE); else super(...a); }
  static now() { return FAKE; }
};
let logged = new Set();       // 已有紀錄的 stage 名稱(system_run_log 的名稱)
let dispatched = [];
let dispatchLog = {};
let tgs = [];
let LAST_RUN_AGO = 60000;     // 最後一次 system_run_log 距離現在多久（毫秒），預設 1 分鐘前
let CONFIG = {};              // system_config 的假資料 {key: value}
globalThis.fetch = async (url, opts = {}) => {
  url = String(url);
  const J = (o, st = 200) => new Response(JSON.stringify(o), { status: st, headers: { "content-type": "application/json" } });
  if (url.includes("api.github.com")) { const b = JSON.parse(opts.body); dispatched.push(b.inputs.stage); return new Response(null, { status: 204 }); }
  if (url.includes("api.telegram.org")) { try { tgs.push(JSON.parse(opts.body).text); } catch (e) {} return J({ ok: true }); }
  if (url.includes("streamlit.app")) return J({ ok: true });
  if (url.includes("cloudflare_dispatch_log")) {
    // 模擬真實 PostgREST（Prefer: return=representation → 回傳陣列；PATCH 只更新「早於 cutoff」的列）
    if (opts.method === "POST") {   // 真實行為：stage 為主鍵，衝突時 ignore-duplicates → 回傳空陣列
      const b = JSON.parse(opts.body);
      if (dispatchLog[b.stage] !== undefined) return J([], 201);
      dispatchLog[b.stage] = FAKE; return J([{ stage: b.stage }], 201);
    }
    const st = decodeURIComponent(url.match(/stage=eq\.([^&]+)/)[1]);
    if (opts.method === "PATCH") {
      const cutoff = new RealDate(decodeURIComponent(url.match(/dispatched_at=lt\.([^&]+)/)[1])).getTime();
      if (dispatchLog[st] !== undefined && dispatchLog[st] < cutoff) { dispatchLog[st] = FAKE; return J([{ stage: st }]); }
      return J([]);
    }
    return J(dispatchLog[st] !== undefined ? [{ dispatched_at: new RealDate(dispatchLog[st]).toISOString() }] : []);
  }
  if (url.includes("system_config")) {
    const m = url.match(/config_key=eq\.([^&]+)/); const k = decodeURIComponent(m[1]);
    return J(CONFIG[k] !== undefined ? [{ config_value: CONFIG[k] }] : []);
  }
  if (url.includes("system_run_log")) {
    if (url.includes("select=stage,created_at")) return J([{ stage: "x", created_at: new RealDate(FAKE - LAST_RUN_AGO).toISOString() }]);
    const m = url.match(/stage=eq\.([^&]+)/); const st = decodeURIComponent(m[1]);
    if (url.includes("order=created_at.desc")) return J([]);
    return J(logged.has(st) ? [{ created_at: new RealDate(FAKE).toISOString() }] : []);
  }
  return J([]);
};
const env = { SUPABASE_URL: "https://x.supabase.co", SUPABASE_KEY: "k", GITHUB_TOKEN: "t", TELEGRAM_BOT_TOKEN: "b", TELEGRAM_CHAT_ID: "c" };
async function at(iso, loggedStages = []) {
  FAKE = new RealDate(iso).getTime(); logged = new Set(loggedStages); dispatched = []; dispatchLog = {}; CONFIG = {};
  await worker.fetch(new Request("https://w/"), env, {});
  return dispatched.filter(s => s !== "broker_flows");
}
async function atKeep(iso, loggedStages = []) {   // 不重設冷卻紀錄（測試同一天連續兩個時點）
  FAKE = new RealDate(iso).getTime(); logged = new Set(loggedStages); dispatched = [];
  await worker.fetch(new Request("https://w/"), env, {});
  return dispatched.filter(s => s !== "broker_flows");
}
async function atAll(iso, loggedStages = []) {   // 不過濾 broker_flows（V9 起它是固定時點的 SCHEDULE 項目）
  FAKE = new RealDate(iso).getTime(); logged = new Set(loggedStages); dispatched = []; dispatchLog = {}; CONFIG = {};
  await worker.fetch(new Request("https://w/"), env, {});
  return dispatched;
}
function eq(name, got, want) {
  const ok = JSON.stringify([...got].sort()) === JSON.stringify([...want].sort());
  console.log(ok ? "✅" : "❌", name, ok ? "" : `got=${JSON.stringify(got)} want=${JSON.stringify(want)}`);
  if (!ok) process.exitCode = 1;
}
// 1) 週一 09:20 台北，什麼都沒跑 → 補發 gate/pool/route2/morning_exit/kbar(ex 09:13+2)；execute(10:02)、time_stop(10:09) 還沒到
eq("09:20 全沒跑", await at("2026-10-05T01:20:00Z"), ["gate","build_intraday_pool","route2_confirm_scan","morning_exit","intraday_kbar"]);
// 2) kbar 的紀錄名稱是 intraday_gate：已有該紀錄就不補發 kbar（V6 以前的 bug）
eq("09:20 intraday_gate 已有紀錄", await at("2026-10-05T01:20:00Z", ["intraday_gate","gate","build_intraday_pool","route2_confirm_scan","morning_exit"]), []);
// 3) 14:30 台北：盤中類已過截止，不再補發任何盤中類（key_usage 恰在截止邊界，time_stop 已到邊界）
const late = await at("2026-10-05T06:31:00Z");
const marketStages = ["gate","build_intraday_pool","route2_confirm_scan","morning_exit","intraday_kbar","intraday_execute","time_stop_check","key_usage_monitor","tail_entry","intraday_force_exit","overnight_flip_exit_monitor","overnight_flip_premarket_monitor","overnight_flip_scan"];
eq("14:31 盤中類不補發", late.filter(s => marketStages.includes(s)), []);
// 4) 休市日 10/9(週五) 09:20 台北：盤中類不補發
eq("休市日 09:20", (await at("2026-10-09T01:20:00Z")).filter(s => marketStages.includes(s)), []);
// 5) V9：tail_entry 改 13:17 才派發；13:05 不發（舊版 13:00 派發再睡 20 分鐘），13:18 沒紀錄→補發；13:40 已過截止不補發
const earlyLogged = ["gate","build_intraday_pool","route2_confirm_scan","morning_exit","intraday_gate","intraday_execute","time_stop_check","key_usage_monitor"];
eq("V9 13:05 tail_entry 不提早派發", (await at("2026-10-05T05:05:00Z", earlyLogged)).filter(s=>s==="tail_entry"), []);
eq("V9 13:18 tail_entry 補發", (await at("2026-10-05T05:18:00Z", earlyLogged)).filter(s=>s==="tail_entry"), ["tail_entry"]);
eq("V9 13:18 tail_entry 已有紀錄", (await at("2026-10-05T05:18:00Z", [...earlyLogged, "tail_entry"])).filter(s=>s==="tail_entry"), []);
eq("13:40 tail_entry 過截止", (await at("2026-10-05T05:40:00Z", earlyLogged)).filter(s=>s==="tail_entry"), []);
// V9.1：強制出場 13:25 排定、grace 1 → 13:26 沒紀錄就補發；13:25:30 還不發
eq("V9.1 13:25 強制出場 尚在寬限內不發", (await at("2026-10-05T05:25:00Z", earlyLogged)).filter(s=>s==="intraday_force_exit"), []);
eq("V9.1 13:26 強制出場 補發", (await at("2026-10-05T05:26:00Z", earlyLogged)).filter(s=>s==="intraday_force_exit"), ["intraday_force_exit"]);
// 6) V9：收盤後輕量階段合併 bundle_evening(19:45)/bundle_late(21:40)；17:58 不再單獨派 snapshot
eq("V9 17:58 不單獨派 snapshot", (await at("2026-10-05T09:58:00Z")).filter(s=>["portfolio_value_snapshot","disposal_watch","nightly_analysis_report","industry_rotation_scan"].includes(s)), []);
eq("V9 19:50 bundle_evening 補發", (await at("2026-10-05T11:50:00Z")).filter(s=>s==="bundle_evening"), ["bundle_evening"]);
eq("V9 19:50 bundle_evening 已有紀錄", (await at("2026-10-05T11:50:00Z", ["bundle_evening"])).filter(s=>s==="bundle_evening"), []);
eq("V9 21:46 bundle_late 補發", (await at("2026-10-05T13:46:00Z")).filter(s=>s==="bundle_late"), ["bundle_late"]);
eq("V9 22:04 signal 補發 / bt_nightly 尚未到", (await at("2026-10-05T14:04:00Z")).filter(s=>["signal","bt_nightly"].includes(s)), ["signal"]);
eq("V9 23:10 bt_nightly 保險補發", (await at("2026-10-05T15:10:00Z", ["signal","overnight_scan","smart_money_scan"])).filter(s=>s==="bt_nightly"), ["bt_nightly"]);
eq("V9 隔日沖/mops 回補已停用", (await at("2026-10-05T01:20:00Z")).filter(s=>s.startsWith("overnight_flip_") || s.startsWith("mops_balance") || s.startsWith("mops_income")), []);
// 7) 週六不補發週一到週五的 stage
eq("週六", (await at("2026-10-03T01:20:00Z")).filter(s => marketStages.includes(s)), []);

// 8) 【V10】broker_flows：只排「當晚」19:20(主)/20:40(備援)，取消隔天 08:10；22:00 之後(21:50 截止)不補發
const bf = async (iso, lg = []) => (await atAll(iso, lg)).filter(s => s === "broker_flows");
eq("V10 週一 19:24 沒紀錄 → 補發", await bf("2026-10-05T11:24:00Z"), ["broker_flows"]);
eq("V10 週一 19:24 已有紀錄 → 不補發", await bf("2026-10-05T11:24:00Z", ["broker_flows"]), []);
eq("V10 週一 19:21 還在寬限內 → 不發", await bf("2026-10-05T11:21:00Z"), []);
eq("V10 週一 14:00 不在時點 → 不發", await bf("2026-10-05T06:00:00Z"), []);
eq("V10 週一 16:30 不在時點 → 不發（舊版會每 25 分鐘發一次）", await bf("2026-10-05T08:30:00Z"), []);
eq("V10 週一 20:45 沒紀錄 → 補發(主/備援時點只發一次)", await bf("2026-10-05T12:45:00Z"), ["broker_flows"]);
eq("V10 週一 21:45 備援時點仍可補發", await bf("2026-10-05T13:45:00Z"), ["broker_flows"]);
eq("V10 週一 22:00 已過 21:50 截止 → 不補發（要在 22:00 選股前完成）", await bf("2026-10-05T14:00:00Z"), []);
eq("V10 週二 08:14 不再有隔天 08:10 時點 → 不發", await bf("2026-10-06T00:14:00Z"), []);
eq("V10 週六 08:14 → 不發", await bf("2026-10-03T00:14:00Z"), []);
eq("V10 週日 19:24 → 不發", await bf("2026-10-04T11:24:00Z"), []);

// 9) 【V10.1】三關快照 intraday_snap：09:26 派發(查 9:30)／09:56 派發(查 10:00) 兩個時點各自獨立冷卻；intraday_mode 控制輪詢/快照是否派發
const priorLogged = ["gate","build_intraday_pool","route2_confirm_scan","morning_exit","intraday_gate"];
eq("V10.1 09:25 還沒到 → 不發 snap", (await at("2026-10-05T01:25:00Z", priorLogged)).filter(s=>s==="intraday_snap"), []);
eq("V10.1 09:27 沒紀錄 → 發 snap", (await at("2026-10-05T01:27:00Z", priorLogged)).filter(s=>s==="intraday_snap"), ["intraday_snap"]);
eq("V10.1 09:27 已有 intraday_snap 紀錄 → 不發", (await at("2026-10-05T01:27:00Z", [...priorLogged, "intraday_snap"])).filter(s=>s==="intraday_snap"), []);
eq("V10.1 09:47 過截止(20 分) → 不補發 pass1", (await at("2026-10-05T01:47:00Z", priorLogged)).filter(s=>s==="intraday_snap"), []);
// 同一天：09:27 派發 pass1 後，09:57 的 pass2 不被 pass1 的冷卻擋住
await at("2026-10-05T01:27:00Z", priorLogged);
eq("V10.1 09:28 pass1 冷卻中 → 不重複派發", (await atKeep("2026-10-05T01:28:00Z", priorLogged)).filter(s=>s==="intraday_snap"), []);
eq("V10.1 09:57 pass2 獨立冷卻 → 派發", (await atKeep("2026-10-05T01:57:00Z", priorLogged)).filter(s=>s==="intraday_snap"), ["intraday_snap"]);
eq("V10.1 10:09 pass2 過截止(12 分) → 不補發", (await at("2026-10-05T02:09:00Z", priorLogged)).filter(s=>s==="intraday_snap"), []);
eq("V10.1 休市日 09:27 不派 snap", (await at("2026-10-09T01:27:00Z", priorLogged)).filter(s=>s==="intraday_snap"), []);
// intraday_mode
async function atCfg(iso, cfg, lg = []) {
  FAKE = new RealDate(iso).getTime(); logged = new Set(lg); dispatched = []; dispatchLog = {}; CONFIG = cfg;
  await worker.fetch(new Request("https://w/"), env, {});
  return dispatched;
}
eq("V10 fast 模式：09:20 不再派發輪詢 intraday_kbar", (await atCfg("2026-10-05T01:20:00Z", { intraday_mode: "fast" })).filter(s=>s==="intraday_kbar"), []);
eq("V10 shadow 模式：09:20 仍派發輪詢", (await atCfg("2026-10-05T01:20:00Z", { intraday_mode: "shadow" })).filter(s=>s==="intraday_kbar"), ["intraday_kbar"]);
eq("V10 沒設定：預設仍派發輪詢", (await atCfg("2026-10-05T01:20:00Z", {})).filter(s=>s==="intraday_kbar"), ["intraday_kbar"]);
eq("V10.1 poll 模式：不派發快照", (await atCfg("2026-10-05T01:27:00Z", { intraday_mode: "poll" }, priorLogged)).filter(s=>s==="intraday_snap"), []);
eq("V10.1 fast 模式：派發快照", (await atCfg("2026-10-05T01:27:00Z", { intraday_mode: "fast" }, priorLogged)).filter(s=>s==="intraday_snap"), ["intraday_snap"]);

// 10) 【V10.2】早盤情報：台北 05:30(UTC 前一日 21:30，週日~四)派發 premarket_brief；08:00(UTC 00:00 週一~五)派發 premarket_supplement；休市日不發
const pmOnly = (a) => a.filter(s => s.startsWith("premarket_"));
eq("V10.2 週四 05:31 沒紀錄 → 派發早盤情報", pmOnly(await at("2026-10-07T21:31:00Z")), ["premarket_brief"]);
eq("V10.2 週四 05:31 已有紀錄 → 不發", pmOnly(await at("2026-10-07T21:31:00Z", ["premarket_brief"])), []);
eq("V10.2 05:29 還沒到 → 不發", pmOnly(await at("2026-10-07T21:29:00Z")), []);
eq("V10.2 06:15 已過截止(38 分) → 不補發", pmOnly(await at("2026-10-07T22:15:00Z")), []);
eq("V10.2 國慶補假(週五 10/9) 05:31 → 不發", pmOnly(await at("2026-10-08T21:31:00Z")), []);
eq("V10.2 週一 05:31(UTC 週日) → 發", pmOnly(await at("2026-10-11T21:31:00Z")), ["premarket_brief"]);
eq("V10.2 週六 05:31(UTC 週五) → 不發", pmOnly(await at("2026-10-09T21:31:00Z")), []);
eq("V10.2 週四 08:01 → 派發補充", pmOnly(await at("2026-10-08T00:01:00Z")), ["premarket_supplement"]);
eq("V10.2 週四 08:01 補充已有紀錄 → 不發", pmOnly(await at("2026-10-08T00:01:00Z", ["premarket_supplement"])), []);
eq("V10.2 休市日 08:01 → 不發", pmOnly(await at("2026-10-09T00:01:00Z")), []);
eq("V10.2 09:05 已過截止 → 不補發", pmOnly(await at("2026-10-08T01:05:00Z")), []);
// 休市日改讀 Supabase：2027-02-08(週一)假設為休市
eq("V10.2 沒有 Supabase 休市日 → 照派", pmOnly(await atCfg("2027-02-07T21:31:00Z", {})), ["premarket_brief"]);
eq("V10.2 Supabase 休市日 → 不派", pmOnly(await atCfg("2027-02-07T21:31:00Z", { tw_market_closed_dates: JSON.stringify({ asof: "2026-12-01", dates: ["2027-02-08"] }) })), []);
eq("V10.2 Supabase 休市日格式壞掉 → 退回內建清單", pmOnly(await atCfg("2027-02-07T21:31:00Z", { tw_market_closed_dates: "{{壞" })), ["premarket_brief"]);
// 延遲警示：05:56 沒有紀錄 → 發一次 Telegram
tgs = [];
await at("2026-10-07T21:56:00Z");
eq("V10.2 05:56 沒紀錄 → 發延遲警示 1 次", [tgs.filter(t => t.includes("早盤情報延遲")).length], [1]);
tgs = [];
await at("2026-10-07T21:56:00Z", ["premarket_brief"]);
eq("V10.2 05:56 已有紀錄 → 不警示", [tgs.filter(t => t.includes("早盤情報延遲")).length], [0]);
tgs = [];
await at("2026-10-08T21:56:00Z");
eq("V10.2 休市日 05:56 → 不警示", [tgs.filter(t => t.includes("早盤情報延遲")).length], [0]);

// 11) 【V10.2】Telegram 分段：從原始碼取出 splitMessage 驗證（單則 ≤3800、不丟字、順序不變）
const src = fs.readFileSync(new URL("./warroom_monitor_worker.js", import.meta.url), "utf8");
const m = src.match(/function splitMessage[\s\S]*?\n}\n/);
const splitMessage = new Function(m[0] + "; return splitMessage;")();
const long = Array.from({ length: 400 }, (_, i) => `第${i}行 ` + "字".repeat(30)).join("\n");
const parts = splitMessage(long);
eq("V10.2 長訊息被切成多則且每則 ≤4096", [parts.length > 1, parts.every(p => p.length <= 4096)], [true, true]);
eq("V10.2 分段不丟字不亂序", [parts.map(p => p.replace(/^\(\d+\/\d+\)\n/, "")).join("\n") === long], [true]);
eq("V10.2 短訊息原樣", splitMessage("短"), ["短"]);
eq("V10.2 單行超長硬切", [splitMessage("a".repeat(9000)).every(p => p.length <= 4096)], [true]);

// 12) 【2026-10-13】沉默警報：只有「到期未跑」才報，夜間/週末長空檔不報；報過一次後冷卻內不重報
const silenceCount = () => tgs.filter(t => t.includes("排程系統異常沉默")).length;
LAST_RUN_AGO = 3 * 3600 * 1000;
tgs = []; await at("2026-10-05T12:00:00Z", ["bundle_evening", "broker_flows"]);  // 週一 20:00 台北，晚間階段皆已跑過，無到期排程，最後執行 3 小時前
eq("沉默：夜間無到期排程 → 不報警", [silenceCount()], [0]);
tgs = []; await at("2026-10-11T12:00:00Z", ["bundle_evening", "broker_flows"]);  // 週日 20:00 台北（週末無排程到期）
eq("沉默：週末長空檔 → 不報警", [silenceCount()], [0]);
tgs = []; await at("2026-10-05T01:20:00Z");           // 週一 09:20 台北，gate 等多項到期未跑 + 最後執行 3 小時前
eq("沉默：有到期未跑 → 報一次", [silenceCount()], [1]);
tgs = []; await atKeep("2026-10-05T01:30:00Z");       // 10 分鐘後仍沉默，冷卻 120 分鐘內不重報
eq("沉默：冷卻內不重報", [silenceCount()], [0]);
LAST_RUN_AGO = 60000;

// 13) 【2026-11-15】派發失敗警報：假 fetch 模擬 GitHub 403／500／逾時／例外；同日同階段同原因只發一次；文字不含 token
{
  const baseFetch = globalThis.fetch;
  let mode = "ok";                 // ok | 403 | 500 | timeout | throw
  let ghCalls = 0;
  const SECRET = "ghp_SECRET_TOKEN_VALUE";
  const envF = { ...env, GITHUB_TOKEN: SECRET, TELEGRAM_BOT_TOKEN: "TGBOT_SECRET", TELEGRAM_CHAT_ID: "chat" };
  globalThis.fetch = async (url, opts = {}) => {
    if (String(url).includes("api.github.com")) {
      ghCalls++;
      if (mode === "403") return new Response(JSON.stringify({ message: "Bad credentials " + SECRET }), { status: 403 });
      if (mode === "500") return new Response("boom", { status: 500 });
      if (mode === "throw") throw new TypeError("network down https://api.github.com ?token=" + SECRET);
      if (mode === "timeout") return new Promise((_, rej) => opts.signal.addEventListener("abort", () => rej(Object.assign(new Error("aborted"), { name: "AbortError" }))));
    }
    return baseFetch(url, opts);
  };
  const run = async (iso, resetLog = true) => {
    FAKE = new RealDate(iso).getTime(); logged = new Set(); dispatched = []; tgs = [];
    if (resetLog) dispatchLog = {};
    CONFIG = {};
    return worker.fetch(new Request("https://w/"), envF, {});
  };
  const alerts = () => tgs.filter(t => t.includes("Worker 派發失敗"));
  const noSecret = () => tgs.every(t => !t.includes(SECRET) && !t.includes("TGBOT_SECRET"));
  const T = "2026-10-05T01:20:00Z";  // 週一 09:20 台北，gate 等多項到期

  mode = "403";
  await run(T);
  eq("警報 403：gate 有發警報且含原因 http_403", [alerts().some(t => t.includes("gate") && t.includes("http_403"))], [true]);
  eq("警報 403：每個失敗階段各一則（同階段不重複）", [alerts().length === new Set(alerts().map(t => t.match(/派發失敗：(\S+)/)[1])).size], [true]);
  eq("警報 403：不含 token／bot token", [noSecret()], [true]);
  eq("警報 403：不再另發舊式「補派失敗」彙總", [tgs.some(t => t.includes("補派失敗"))], [false]);
  const n403 = alerts().length;
  // 同日、冷卻 60 分鐘後再試仍 403：同階段同原因不重發（dispatchLog 保留去重列，但讓 60 分鐘冷卻過期）
  for (const k of Object.keys(dispatchLog)) if (!k.startsWith("dispatch_fail:")) dispatchLog[k] -= 2 * 3600 * 1000;
  await run("2026-10-05T02:30:00Z", false);
  eq("警報 403：同日同階段同原因第二次不重發", [alerts().filter(t => t.includes("gate")).length], [0]);

  // 原因改變（403 → 500）視為新警報
  mode = "500";
  for (const k of Object.keys(dispatchLog)) if (!k.startsWith("dispatch_fail:")) dispatchLog[k] -= 2 * 3600 * 1000;
  await run("2026-10-05T03:40:00Z", false);
  eq("警報 500：原因不同 → 再發一次，含 http_500", [alerts().some(t => t.includes("http_500"))], [true]);
  eq("警報 500：不含金鑰", [noSecret()], [true]);

  // 隔天重新計算
  mode = "403";
  for (const k of Object.keys(dispatchLog)) if (!k.startsWith("dispatch_fail:")) dispatchLog[k] -= 24 * 3600 * 1000;
  await run("2026-10-06T01:20:00Z", false);
  eq("警報 隔日：同階段同原因重新警報", [alerts().some(t => t.includes("gate") && t.includes("http_403"))], [true]);

  // 逾時：不卡死、不丟例外、發 timeout 警報（縮短逾時時間以加速測試：直接呼叫匯出外的邏輯不可行，故用真計時 10 秒的替代——改用假計時器）
  mode = "timeout";
  const realST = globalThis.setTimeout;
  globalThis.setTimeout = (fn, ms, ...a) => realST(fn, ms === 10000 ? 5 : ms, ...a);
  await run(T);
  globalThis.setTimeout = realST;
  eq("警報 逾時：發 timeout 警報", [alerts().some(t => t.includes("gate") && t.includes("timeout"))], [true]);
  eq("警報 逾時：不含金鑰", [noSecret()], [true]);

  // 例外（網路錯誤，訊息夾帶 token）：只用錯誤名稱，不外洩訊息
  mode = "throw";
  await run(T);
  eq("警報 例外：發 exception_TypeError 警報", [alerts().some(t => t.includes("exception_TypeError"))], [true]);
  eq("警報 例外：錯誤訊息內的 token 不外洩", [noSecret()], [true]);

  // 缺 GITHUB_TOKEN
  mode = "ok";
  FAKE = new RealDate(T).getTime(); logged = new Set(); dispatched = []; tgs = []; dispatchLog = {}; CONFIG = {};
  await worker.fetch(new Request("https://w/"), { ...envF, GITHUB_TOKEN: undefined }, {});
  eq("警報 no_token：發警報", [alerts().some(t => t.includes("no_token"))], [true]);

  // 派發成功：不發失敗警報
  mode = "ok";
  await run(T);
  eq("警報 成功派發：無失敗警報", [alerts().length], [0]);

  // Telegram 自己失敗不拖垮看門狗
  mode = "403";
  globalThis.fetch = async (url, opts = {}) => {
    if (String(url).includes("api.telegram.org")) throw new Error("tg down");
    if (String(url).includes("api.github.com")) return new Response("", { status: 403 });
    return baseFetch(url, opts);
  };
  FAKE = new RealDate(T).getTime(); logged = new Set(); dispatchLog = {}; CONFIG = {};
  const resp = await worker.fetch(new Request("https://w/"), envF, {});
  eq("警報：Telegram 失敗仍回傳看門狗結果(不丟例外)", [resp.status], [200]);
  globalThis.fetch = baseFetch;
}
