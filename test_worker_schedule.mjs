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
function eq(name, got, want) {
  const ok = JSON.stringify([...got].sort()) === JSON.stringify([...want].sort());
  console.log(ok ? "✅" : "❌", name, ok ? "" : `got=${JSON.stringify(got)} want=${JSON.stringify(want)}`);
  if (!ok) process.exitCode = 1;
}
// 1) 週一 09:20 台北，什麼都沒跑 → 補發 gate/pool/route2/morning_exit/kbar(ex 09:13+2)；execute(10:02)、time_stop(10:09) 還沒到
eq("09:20 全沒跑", await at("2026-10-05T01:20:00Z"), ["gate","build_intraday_pool","route2_confirm_scan","morning_exit","intraday_kbar","overnight_flip_exit_monitor"]);
// 2) kbar 的紀錄名稱是 intraday_gate：已有該紀錄就不補發 kbar（V6 以前的 bug）
eq("09:20 intraday_gate 已有紀錄", await at("2026-10-05T01:20:00Z", ["intraday_gate","gate","build_intraday_pool","route2_confirm_scan","morning_exit","overnight_flip_exit_monitor"]), []);
// 3) 14:30 台北：盤中類已過截止，不再補發任何盤中類（key_usage 恰在截止邊界，time_stop 已到邊界）
const late = await at("2026-10-05T06:31:00Z");
const marketStages = ["gate","build_intraday_pool","route2_confirm_scan","morning_exit","intraday_kbar","intraday_execute","time_stop_check","key_usage_monitor","tail_entry","intraday_force_exit","overnight_flip_exit_monitor","overnight_flip_premarket_monitor","overnight_flip_scan"];
eq("14:31 盤中類不補發", late.filter(s => marketStages.includes(s)), []);
// 4) 休市日 10/9(週五) 09:20 台北：盤中類不補發
eq("休市日 09:20", (await at("2026-10-09T01:20:00Z")).filter(s => marketStages.includes(s)), []);
// 5) 13:05 台北 tail_entry 沒跑 → 補發；13:40 台北已過截止 → 不補發
eq("13:05 tail_entry", (await at("2026-10-05T05:05:00Z", ["gate","build_intraday_pool","route2_confirm_scan","morning_exit","intraday_gate","intraday_execute","time_stop_check","key_usage_monitor","overnight_flip_exit_monitor"])).filter(s=>s==="tail_entry"), ["tail_entry"]);
eq("13:40 tail_entry 過截止", (await at("2026-10-05T05:40:00Z")).filter(s=>s==="tail_entry"), []);
// 6) portfolio_value_snapshot 17:35 排定，17:58 台北沒跑 → 補發
eq("17:58 snapshot", (await at("2026-10-05T09:58:00Z")).filter(s=>s==="portfolio_value_snapshot"), ["portfolio_value_snapshot"]);
// 7) 週六不補發週一到週五的 stage
eq("週六", (await at("2026-10-03T01:20:00Z")).filter(s => marketStages.includes(s)), []);
