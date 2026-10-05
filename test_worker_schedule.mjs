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
  FAKE = new RealDate(iso).getTime(); logged = new Set(loggedStages); dispatched = []; dispatchLog = {};
  await worker.fetch(new Request("https://w/"), env, {});
  return dispatched.filter(s => s !== "broker_flows");
}
async function atAll(iso, loggedStages = []) {   // 不過濾 broker_flows（V9 起它是固定時點的 SCHEDULE 項目）
  FAKE = new RealDate(iso).getTime(); logged = new Set(loggedStages); dispatched = []; dispatchLog = {};
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

// 8) 【V9】broker_flows 固定時點：19:10 / 21:10 / 隔天 08:10(台北)；已有紀錄就不補發；非時點不發
const bf = async (iso, lg = []) => (await atAll(iso, lg)).filter(s => s === "broker_flows");
eq("V9 週一 19:14 沒紀錄 → 補發", await bf("2026-10-05T11:14:00Z"), ["broker_flows"]);
eq("V9 週一 19:14 已有紀錄 → 不補發", await bf("2026-10-05T11:14:00Z", ["broker_flows"]), []);
eq("V9 週一 14:00(UTC 06:00) 不在時點 → 不發", await bf("2026-10-05T06:00:00Z"), []);
eq("V9 週一 16:30 不在時點 → 不發（舊版會每 25 分鐘發一次）", await bf("2026-10-05T08:30:00Z"), []);
eq("V9 週一 22:00 21:10 時點已過 deadline 前、沒紀錄 → 補發一次", await bf("2026-10-05T14:00:00Z"), ["broker_flows"]);
eq("V9 週一 22:00 已有紀錄 → 不補發", await bf("2026-10-05T14:00:00Z", ["broker_flows"]), []);
eq("V9 週二 08:14(UTC 00:14) 沒紀錄 → 補發（前一晚資料）", await bf("2026-10-06T00:14:00Z"), ["broker_flows"]);
eq("V9 週六 08:14(UTC 00:14) → 補發（週五資料）", await bf("2026-10-03T00:14:00Z"), ["broker_flows"]);
eq("V9 週日 08:14(UTC 00:14) → 不發", await bf("2026-10-04T00:14:00Z"), []);
eq("V9 週一 08:14(UTC 00:14, 週日資料不存在) → 不發", await bf("2026-10-05T00:14:00Z"), []);
