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
let CONFIG = {};              // system_config 的假資料 {key: value}
globalThis.fetch = async (url, opts = {}) => {
  url = String(url);
  const J = (o, st = 200) => new Response(JSON.stringify(o), { status: st, headers: { "content-type": "application/json" } });
  if (url.includes("api.github.com")) { const b = JSON.parse(opts.body); dispatched.push(b.inputs.stage); return new Response(null, { status: 204 }); }
  if (url.includes("api.telegram.org") || url.includes("streamlit.app")) return J({ ok: true });
  if (url.includes("cloudflare_dispatch_log")) {
    if (opts.method === "POST") { const b = JSON.parse(opts.body); dispatchLog[b.stage] = FAKE; return J({}, 201); }
    const m = url.match(/stage=eq\.([^&]+)/); const st = decodeURIComponent(m[1]);
    return J(dispatchLog[st] ? [{ dispatched_at: new RealDate(dispatchLog[st]).toISOString() }] : []);
  }
  if (url.includes("system_config")) {
    const m = url.match(/config_key=eq\.([^&]+)/); const k = decodeURIComponent(m[1]);
    return J(CONFIG[k] !== undefined ? [{ config_value: CONFIG[k] }] : []);
  }
  if (url.includes("system_run_log")) {
    if (url.includes("select=stage,created_at")) return J([{ stage: "x", created_at: new RealDate(FAKE - 60000).toISOString() }]);
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
