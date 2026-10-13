// 【2026-10-13】Worker 圖片收件測試：假 Telegram＋假 Supabase，驗證收圖、驗證、失敗處理、重送去重。
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
const _tmp = path.join(os.tmpdir(), `worker_photo_${process.pid}.mjs`);
fs.copyFileSync(new URL("./warroom_monitor_worker.js", import.meta.url), _tmp);
const worker = (await import(_tmp)).default;

let fails = 0;
const check = (name, cond) => { console.log((cond ? "PASS: " : "FAIL: ") + name); if (!cond) fails++; };

const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3, 4, 5, 6, 7, 8]);
const JPG = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 9, 9, 9, 9]);
const PDF = new Uint8Array([0x25, 0x50, 0x44, 0x46, 1, 2, 3]);

let S;   // 每個案例重設的狀態
const reset = (o = {}) => {
  S = { sent: [], inboxPosts: [], imagePosts: [], patches: [], files: {}, getFileFail: false, inboxDup: false,
        inboxFail: false, imageFail: false, nextId: 41, fileInfoSize: null, ...o };
};
globalThis.fetch = async (url, opts = {}) => {
  url = String(url);
  const J = (o, st = 200) => new Response(JSON.stringify(o), { status: st, headers: { "content-type": "application/json" } });
  if (url.includes("/sendMessage")) { S.sent.push(JSON.parse(opts.body).text); return J({ ok: true }); }
  if (url.includes("/getFile")) {
    if (S.getFileFail) return J({ ok: false }, 400);
    const id = new URL(url).searchParams.get("file_id");
    if (!(id in S.files)) return J({ ok: false }, 400);
    return J({ ok: true, result: { file_path: `photos/${id}.bin`, ...(S.fileInfoSize ? { file_size: S.fileInfoSize } : {}) } });
  }
  if (url.includes("/file/bot")) {
    const id = url.split("/photos/")[1].replace(".bin", "");
    return new Response(S.files[id], { status: 200 });
  }
  if (url.includes("/rest/v1/telegram_inbox_images")) {
    S.imagePosts.push(JSON.parse(opts.body)[0]);
    return S.imageFail ? new Response("err", { status: 500 }) : new Response(null, { status: 201 });
  }
  if (url.includes("/rest/v1/telegram_inbox")) {
    if (opts.method === "PATCH") { S.patches.push({ url, body: JSON.parse(opts.body) }); return new Response(null, { status: 204 }); }
    if (S.inboxFail) return new Response("err", { status: 500 });
    const row = JSON.parse(opts.body)[0];
    S.inboxPosts.push(row);
    return S.inboxDup ? J([], 201) : J([{ id: S.nextId }], 201);
  }
  return J({});
};

const ENV = { TG_WEBHOOK_SECRET: "sec", TELEGRAM_BOT_TOKEN: "TOKEN", TELEGRAM_CHAT_ID: "100", SUPABASE_URL: "https://x.supabase.co", SUPABASE_KEY: "k" };
const hook = async (message, { secret = "sec", env = ENV, update_id = 1 } = {}) => {
  const req = new Request("https://w.example/tg-webhook", { method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": secret, "content-type": "application/json" },
    body: JSON.stringify({ update_id, message }) });
  return worker.fetch(req, env, {});
};
const base = { chat: { id: 100 }, message_id: 7 };

// 1. 多種尺寸：挑 ≤1.5MB 的最大一張，存圖與 caption
reset({ files: { small: PNG, big: PNG } });
let r = await hook({ ...base, caption: "今天的籌碼", photo: [
  { file_id: "small", width: 90, height: 60, file_size: 1000 },
  { file_id: "big", width: 1280, height: 800, file_size: 120000 },
  { file_id: "huge", width: 4000, height: 3000, file_size: 9000000 }] });
check("收到圖片：回 200", r.status === 200);
check("挑到 ≤1.5MB 的最大尺寸", S.inboxPosts[0]?.file_id === "big");
check("入庫列為 photo、text 空、附 caption、狀態 new", S.inboxPosts[0]?.kind === "photo" && S.inboxPosts[0]?.text === "" && S.inboxPosts[0]?.caption === "今天的籌碼" && S.inboxPosts[0]?.status === "new");
check("圖片位元組以 base64 存入並記錄格式與大小", S.imagePosts[0]?.inbox_id === 41 && S.imagePosts[0]?.mime === "image/png"
  && S.imagePosts[0]?.size_bytes === PNG.length && Buffer.from(S.imagePosts[0].data_b64, "base64").equals(Buffer.from(PNG)));
check("回覆使用者並指向收件匣", S.sent.length === 1 && S.sent[0].includes("#41") && S.sent[0].includes("OCR"));

// 2. 用檔頭判斷格式（Telegram 照片 MIME 固定標 jpeg，實際內容為 JPEG）
reset({ files: { a: JPG } });
await hook({ ...base, photo: [{ file_id: "a", width: 800, height: 600, file_size: 500 }] });
check("JPEG 以檔頭判斷為 image/jpeg", S.imagePosts[0]?.mime === "image/jpeg");

// 3. 檔案（document）：PNG 收、PDF 不收
reset({ files: { d: PNG } });
await hook({ ...base, document: { file_id: "d", mime_type: "image/png", file_size: 800 } });
check("以檔案傳送的 PNG 可收", S.inboxPosts.length === 1 && S.imagePosts[0]?.mime === "image/png");
reset({ files: { d: PDF } });
await hook({ ...base, document: { file_id: "d", mime_type: "application/pdf", file_size: 800 } });
check("PDF 不收、回覆只收文字與截圖", S.inboxPosts.length === 0 && S.sent[0].includes("截圖"));

// 4. 檔頭與宣告不符（偽裝成圖片的檔案）
reset({ files: { f: PDF } });
await hook({ ...base, document: { file_id: "f", mime_type: "image/png", file_size: 800 } });
check("宣告是圖片但檔頭不是 → 不入庫並說明", S.inboxPosts.length === 0 && S.imagePosts.length === 0 && S.sent[0].includes("不是支援的圖片格式"));

// 5. 過大
reset({ files: {} });
await hook({ ...base, photo: [{ file_id: "huge", width: 4000, height: 3000, file_size: 9000000 }] });
check("所有尺寸都超過上限 → 回覆請裁切、不入庫", S.inboxPosts.length === 0 && S.sent[0].includes("超過"));
reset({ files: { x: PNG }, fileInfoSize: 9000000 });
await hook({ ...base, photo: [{ file_id: "x", width: 800, height: 600 }] });
check("getFile 回報檔案過大 → 不下載、不入庫", S.inboxPosts.length === 0 && S.sent[0].includes("超過"));

// 6. 下載失敗：回 200（不讓 Telegram 重送）、提示重傳、不入庫
reset({ getFileFail: true });
r = await hook({ ...base, photo: [{ file_id: "z", width: 800, height: 600, file_size: 500 }] });
check("下載失敗：回 200、提示重傳、不入庫", r.status === 200 && S.inboxPosts.length === 0 && S.sent[0].includes("重傳"));

// 7. Telegram 重送同一則（update_id 已存在）：不存圖、不重複回覆
reset({ files: { a: PNG }, inboxDup: true });
r = await hook({ ...base, photo: [{ file_id: "a", width: 800, height: 600, file_size: 500 }] });
check("重送去重：不存圖、不再回覆", r.status === 200 && S.imagePosts.length === 0 && S.sent.length === 0);

// 8. 圖片存庫失敗：該列標成退回、提示重傳
reset({ files: { a: PNG }, imageFail: true });
await hook({ ...base, photo: [{ file_id: "a", width: 800, height: 600, file_size: 500 }] });
check("圖片儲存失敗：整列標 rejected 並提示重傳", S.patches.length === 1 && S.patches[0].body.status === "rejected" && S.patches[0].url.includes("id=eq.41") && S.sent[0].includes("重傳"));
reset({ files: { a: PNG }, inboxFail: true });
r = await hook({ ...base, photo: [{ file_id: "a", width: 800, height: 600, file_size: 500 }] });
check("入庫失敗：提示重傳且不存圖", r.status === 200 && S.imagePosts.length === 0 && S.sent[0].includes("重傳"));

// 9. 安全條件
reset({ files: { a: PNG } });
r = await hook({ ...base, photo: [{ file_id: "a", width: 800, height: 600, file_size: 500 }] }, { secret: "wrong" });
check("密鑰錯誤 → 403", r.status === 403 && S.inboxPosts.length === 0);
reset({ files: { a: PNG } });
r = await hook({ chat: { id: 999 }, message_id: 1, photo: [{ file_id: "a", width: 800, height: 600, file_size: 500 }] });
check("非白名單聊天室 → 靜默忽略（不回覆、不入庫）", r.status === 200 && S.sent.length === 0 && S.inboxPosts.length === 0);

// 10. 文字流程不受影響
reset();
await hook({ ...base, text: "代號\t日期\n2330\t2026-10-12" });
check("文字訊息照舊入庫（kind 欄位不設，預設 text）", S.inboxPosts.length === 1 && S.inboxPosts[0].text.startsWith("代號") && S.inboxPosts[0].kind === undefined && S.imagePosts.length === 0);
reset();
await hook({ ...base, sticker: { file_id: "s" } });
check("貼圖等其他類型 → 回覆只收文字與截圖", S.inboxPosts.length === 0 && S.sent[0].includes("截圖"));

// 11. 大圖 base64 不爆堆疊（1.4MB）
reset({ files: { big: new Uint8Array(1400000).fill(0xff).map((v, i) => (i < 3 ? [0xff, 0xd8, 0xff][i] : 7)) } });
r = await hook({ ...base, photo: [{ file_id: "big", width: 3000, height: 2000, file_size: 1400000 }] });
check("1.4MB 圖片可完整編碼存入", S.imagePosts[0]?.size_bytes === 1400000 && Buffer.from(S.imagePosts[0].data_b64, "base64").length === 1400000);

if (fails) { console.log(`\n${fails} 項失敗`); process.exit(1); }
console.log("\n全部通過");
