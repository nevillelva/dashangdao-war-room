# ==========================================
# 8. 側邊欄控制台 (夜間備份中心)
# ==========================================
st.markdown("<style>#MainMenu {visibility: hidden;} footer {visibility: hidden;}</style>", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰略控制台</h2>", unsafe_allow_html=True)
    
    st.markdown("---")
    st.markdown("<h4 style='color:#00d2ff;'>🌙 夜間備份與法人記憶中心</h4>", unsafe_allow_html=True)
    
    # 總指揮專屬的 21:30 戰略提示
    st.markdown("""
    <div style='background:#1a1c23; padding:10px; border-radius:5px; border-left:3px solid #f1c40f; margin-bottom:15px; font-size:13px; color:#ddd; line-height: 1.6;'>
    <strong>⚠️ 總指揮戰略提醒：</strong><br>
    證交所「融資融券」須等全台券商結算，通常至晚間 21:00 後才會完整釋出。<br>
    👉 <strong>請統一於每晚 21:30 後，點擊下方進行「一鍵打包」與「下載備份」，確保籌碼數據 100% 完整！</strong>
    </div>
    """, unsafe_allow_html=True)
    
    if st.button("📥 1. 抓取今日全市場法人與信用數據", use_container_width=True, type="primary"):
        run_nightly_institutional_batch()
        
    export_payload = {
        "pinned_stocks": st.session_state.pinned_stocks,
        "portfolio": st.session_state.portfolio,
        "inst_history": st.session_state.inst_history
    }
    export_json = json.dumps(export_payload, ensure_ascii=False, indent=4)
    st.download_button(label="💾 2. 下載最新戰情備份 (JSON)", data=export_json, file_name=f"54088_backup_{datetime.now().strftime('%Y%m%d')}.json", mime="application/json", use_container_width=True)

    uploaded_file = st.file_uploader("📤 3. 上傳戰情備份 (還原記憶)", type=['json'])
    if uploaded_file is not None:
        if st.button("⚠️ [確認覆蓋並還原記憶體]", use_container_width=True):
            try:
                imported_data = json.load(uploaded_file)
                st.session_state.pinned_stocks = imported_data.get("pinned_stocks", {})
                st.session_state.portfolio = imported_data.get("portfolio", {})
                if "inst_history" in imported_data:
                    st.session_state.inst_history.update(imported_data["inst_history"])
                save_local_db()
                st.toast("✅ [系統提示] 實體備份資料還原成功！")
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.error(f"檔案解析失敗: {e}")
                
    if st.session_state.inst_history:
        dates = sorted(list(st.session_state.inst_history.keys()), reverse=True)
        st.markdown(f"<div style='font-size:13px; color:#aaa; margin-top:10px;'>目前系統記憶體內含有 <b>{len(dates)}</b> 天法人歷史資料。最新紀錄: {dates[0]}</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div style='font-size:13px; color:#ff4d4d; margin-top:10px;'>⚠️ 系統尚無歷史記憶。盤中單檔運算將自動啟用 API 救援模式。</div>", unsafe_allow_html=True)

    st.markdown("---")
    
    intel_input = st.text_area("🔍 雷達手動匯入 (輸入代碼或名稱)", placeholder="如：2330 聯電 加高...\n也可直接貼上 AI 報告內容")
    if st.button("🚀 [強制解析並匯入雷達]", use_container_width=True):
        if intel_input.strip():
            found_codes = set(re.findall(r'\b\d{4}\b', intel_input))
            for code, name in TW_STOCK_NAMES.items():
                if name in intel_input and len(name) >= 2: found_codes.add(code)
            if found_codes:
                for c in found_codes: st.session_state.pinned_stocks[c] = {}
                save_local_db(); st.rerun()
            else: st.warning("⚠️ 找不到對應的股票代碼或名稱。")

    st.markdown("---")
    scan_scope = st.selectbox("🌐 掃描範圍", ["全市場 1700+ 檔", "電子/半導體/光電"])
    min_volume_filter = st.slider("⚖️ 最低 5 日均量 (張)：", 0, 5000, 500, 100)

    def get_scope_codes(scope):
        if "全市場" in scope: return GLOBAL_MARKET_CODES
        elif "電子" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('23','24','30','31','32','33','34','35','36','49','52','53','54','61','62','64','80','81','82'))]
        return GLOBAL_MARKET_CODES

    def run_command_scan(cmd_name, scope, min_vol):
        results = []
        codes = get_scope_codes(scope)
        bar = st.progress(0)
        status = st.empty()
        invalid_signals = ["[📉 空頭觀望]", "[高檔觀望]", "[⚠️ 拉回整理]", "[💀 觸發停損]", "[🚨 撤退警告]", "[⚠️ 盤整洗盤陷阱]"]
        for i, c in enumerate(codes):
            if i % 3 == 0: status.text(f"雷達鎖定與過濾中... ({i}/{len(codes)})")
            d = calculate_signals(c, get_stock_data(c), is_panic_global=is_panic, twii_gain=global_twii_gain, is_scan=True)
            if d and d['vol_5d'] >= min_vol and not d['is_action_needed']: 
                if d['signal'] not in invalid_signals:
                    if cmd_name == "指令一" and d['is_first_red'] and d['is_vol_breakout'] and ("金叉" in d['kdj_str'] or "金叉" in d['macd_str']): results.append(d)
                    elif cmd_name == "指令二" and (d['price'] > d['cost']) and (d['gain'] < 2.0) and (d['price'] < d['cost'] * 1.1) and (d['vol_ratio'] >= 1.2): results.append(d)
                    elif cmd_name == "指令三" and d['val_score'] >= 60: results.append(d)
                    elif cmd_name == "指令四" and d['t_buy'] > 0 and any("集團" in t['text'] or "熱門" in t.get('title','') for t in d.get('ai_tags_dict', [])): results.append(d) 
                    elif cmd_name == "指令五" and d['f_buy'] > 0 and d['margin_diff'] < 0: results.append(d) 
                    elif cmd_name == "指令六" and any("盾牌" in t['text'] for t in d.get('ai_tags_dict', [])): results.append(d)
                    elif cmd_name == "指令八" and d['is_yesterday_strong']: results.append(d)
                    elif cmd_name == "指令九" and any("糾結" in t.get('title', '') for t in d.get('ai_tags_dict', [])): results.append(d)
                    elif cmd_name == "指令十" and d['vol_ratio'] <= 0.6 and d['margin_diff'] < 0: results.append(d)
                    elif cmd_name == "常規": results.append(d)
            bar.progress(min((i + 1) / len(codes), 1.0))
        bar.empty(); status.empty()
        return results

    st.markdown("<div class='cmd-btn'>", unsafe_allow_html=True)
    if st.button("⚔️ [指令一] 主升段突擊", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令一", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_1"
    if st.button("🐟 [指令二] 魚頭潛伏期", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令二", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_2"
    if st.button("💪 [指令五] 籌碼霸王色", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令五", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_5"
    st.markdown("</div>", unsafe_allow_html=True)
    
    st.markdown("<div class='scan-btn'>", unsafe_allow_html=True)
    if st.button("🔎 [常規掃描] 黃金起漲與魚身", use_container_width=True):
        st.session_state.scan_results = run_command_scan("常規", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "golden"
    st.markdown("</div>", unsafe_allow_html=True)

    if st.button("🚪 [安全登出系統]", use_container_width=True):
        st.session_state.authenticated = False
        if "auth" in st.query_params: del st.query_params["auth"]
        st.rerun()
