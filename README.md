# 🗺️ 學校經緯度查詢與地圖視覺化系統

上傳申請生資料 → 自動轉換畢業學校為經緯度 → 在台灣地圖上視覺化呈現

## 🚀 線上使用

[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://your-app-name.streamlit.app)

## 功能特色

- 📚 **本地資料庫**：預建台灣 100+ 所學校座標，查詢過的自動累積
- 🔗 **模糊比對**：自動處理「台/臺」互換、前綴差異
- 🌐 **線上查詢**：查不到的自動上 Nominatim 搜尋
- 🗺️ **互動地圖**：即時在台灣地圖上顯示學生分布
- 📊 **統計分析**：自動產出各校人數、地區分布統計
- ✏️ **手動編輯**：可直接在網頁上新增/修改學校座標

## 本地安裝

```bash
git clone https://github.com/your-username/school-geocoder.git
cd school-geocoder
pip install -r requirements.txt
streamlit run app.py
```

## 上傳資料格式

CSV 或 Excel，至少要有一個「畢業學校」欄位：

| 學號 | 姓名 | 畢業學校 | ... |
|------|------|---------|-----|
| 001  | 王小明 | 臺北市立建國高級中學 | ... |

## 部署到 Streamlit Cloud

1. 將程式碼推上 GitHub
2. 前往 [share.streamlit.io](https://share.streamlit.io)
3. 連結 GitHub repo，指定 `app.py` 為主程式
4. 部署完成！
