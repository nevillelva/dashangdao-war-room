#!/usr/bin/env bash
# 雲端 Claude Code 工作階段啟動腳本（由 .claude/settings.json 的 SessionStart hook 呼叫）
# - 冪等：已安裝就跳過，可重複執行。
# - 安靜：所有輸出寫入日誌檔，stdout 不輸出任何內容，避免佔用模型上下文。
# - 容錯：任何步驟失敗都不中斷工作階段，只記錄到日誌。

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${HOME}/.cache/dashangdao-bootstrap"
LOG_FILE="${LOG_DIR}/bootstrap.log"
CODEGRAPH_PKG="@colbymchenry/codegraph@1.6.2"
SUPERPOWERS_MARKETPLACE="obra/superpowers-marketplace"
SUPERPOWERS_PLUGIN="superpowers@superpowers-marketplace"

mkdir -p "${LOG_DIR}" 2>/dev/null
exec >>"${LOG_FILE}" 2>&1
echo "=== bootstrap $(date -u +%FT%TZ) repo=${REPO_DIR} ==="

log() { echo "[$(date -u +%T)] $*"; }

# 1. CodeGraph CLI（全域安裝，版本固定）
if command -v codegraph >/dev/null 2>&1; then
  log "codegraph 已安裝：$(codegraph --version 2>/dev/null)"
else
  log "安裝 ${CODEGRAPH_PKG}"
  npm install -g "${CODEGRAPH_PKG}" || log "WARN: codegraph 安裝失敗"
fi

# 2. 關閉 CodeGraph 遙測（重複執行無害）
if command -v codegraph >/dev/null 2>&1; then
  codegraph telemetry off || log "WARN: 關閉遙測失敗"
  # 3. 寫入 Claude Code 全域設定（MCP 與 hook）；-y 為非互動模式
  codegraph install -t claude -l global -y || log "WARN: codegraph install 失敗"
fi

# 4. 專案索引：只在 .codegraph/ 不存在時建立（索引不進 git）
if command -v codegraph >/dev/null 2>&1; then
  if [ ! -d "${REPO_DIR}/.codegraph" ]; then
    log "建立專案索引 codegraph init"
    (cd "${REPO_DIR}" && codegraph init) || log "WARN: codegraph init 失敗"
  else
    log "專案索引已存在，略過"
  fi
fi

# 5. 本機忽略 .codegraph/（寫在 .git/info/exclude，不改動專案 .gitignore，避免停止 hook 誤判未追蹤檔）
EXCLUDE_FILE="${REPO_DIR}/.git/info/exclude"
if [ -d "${REPO_DIR}/.git" ] && ! grep -qx '.codegraph/' "${EXCLUDE_FILE}" 2>/dev/null; then
  echo '.codegraph/' >>"${EXCLUDE_FILE}" && log "已加入 .git/info/exclude"
fi

# 6. Superpowers 外掛（marketplace 已存在時會報錯，忽略即可）
if command -v claude >/dev/null 2>&1; then
  claude plugin marketplace add "${SUPERPOWERS_MARKETPLACE}" >/dev/null 2>&1 || true
  if claude plugin list 2>/dev/null | grep -q "${SUPERPOWERS_PLUGIN}"; then
    log "superpowers 已安裝，略過"
  else
    claude plugin install "${SUPERPOWERS_PLUGIN}" || log "WARN: superpowers 安裝失敗"
  fi
else
  log "WARN: 找不到 claude CLI，略過外掛安裝"
fi

log "bootstrap 完成"
exit 0
