"""HTML 跳脫工具（不依賴 streamlit）。

用途：把「來自外部或資料庫」的文字放進 st.markdown(..., unsafe_allow_html=True)
之前先跳脫，避免標題、摘要、備註等內容夾帶 <script>、事件屬性或版面破壞標籤。
純數字與程式寫死的標籤不需要經過 esc()。
"""
import html


def esc(x):
    """回傳跳脫後的字串；None 視為空字串。"""
    return html.escape(str(x) if x is not None else "", quote=True)
