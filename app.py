import streamlit as st
import pandas as pd
import json
import os
import time
from geopy.geocoders import Nominatim
import folium
from streamlit_folium import st_folium

# ==========================================
# 頁面設定
# ==========================================
st.set_page_config(
    page_title="學校經緯度查詢與地圖視覺化",
    page_icon="🗺️",
    layout="wide"
)

st.title("🗺️ 學校經緯度查詢與地圖視覺化系統")
st.markdown("上傳申請生資料，自動將畢業學校轉為經緯度並在地圖上呈現")

# ==========================================
# 初始化 Session State（資料庫存在記憶體中）
# ==========================================
DB_FILE = "school_coordinates_db.json"

def load_db():
    """載入本地資料庫"""
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_db(db):
    """儲存資料庫"""
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

if "school_db" not in st.session_state:
    st.session_state.school_db = load_db()

if "processed_df" not in st.session_state:
    st.session_state.processed_df = None

if "processing_log" not in st.session_state:
    st.session_state.processing_log = []

# ==========================================
# 側邊欄：資料庫管理
# ==========================================
with st.sidebar:
    st.header("📚 學校資料庫管理")
    st.metric("目前收錄學校數", f"{len(st.session_state.school_db)} 所")

    st.divider()

    # --- 匯入經緯度資料 ---
    st.subheader("📥 匯入經緯度資料")
    st.caption("支援 CSV/Excel，需包含學校名稱、緯度、經度欄位")

    uploaded_db = st.file_uploader(
        "選擇經緯度資料檔",
        type=["csv", "xlsx"],
        key="db_upload"
    )

    if uploaded_db:
        if uploaded_db.name.endswith('.csv'):
            df_import = pd.read_csv(uploaded_db)
        else:
            df_import = pd.read_excel(uploaded_db)

        st.write(f"讀取到 {len(df_import)} 筆，欄位：")
        st.write(list(df_import.columns))

        # 欄位選擇
        col1, col2, col3 = st.columns(3)
        with col1:
            school_col = st.selectbox("學校名稱欄位", df_import.columns, key="imp_school")
        with col2:
            lat_col = st.selectbox("緯度欄位", df_import.columns, key="imp_lat")
        with col3:
            lon_col = st.selectbox("經度欄位", df_import.columns, key="imp_lon")

        if st.button("🔄 匯入到資料庫", type="primary"):
            count = 0
            for _, row in df_import.iterrows():
                name = str(row[school_col]).strip()
                lat = row[lat_col]
                lon = row[lon_col]
                if pd.notna(lat) and pd.notna(lon) and name and name != "nan":
                    st.session_state.school_db[name] = {
                        "lat": float(lat),
                        "lon": float(lon),
                        "source": "手動匯入"
                    }
                    count += 1
            save_db(st.session_state.school_db)
            st.success(f"✅ 成功匯入 {count} 所學校！")
            st.rerun()

    st.divider()

    # --- 匯出資料庫 ---
    st.subheader("📤 匯出資料庫")
    if st.session_state.school_db:
        db_export = pd.DataFrame([
            {"學校名稱": k, "緯度": v["lat"], "經度": v["lon"], "來源": v.get("source", "")}
            for k, v in st.session_state.school_db.items()
        ])
        st.download_button(
            label="⬇️ 下載完整資料庫 (CSV)",
            data=db_export.to_csv(index=False).encode("utf-8-sig"),
            file_name="school_coordinates_db_export.csv",
            mime="text/csv"
        )

    st.divider()

    # --- 搜尋資料庫 ---
    st.subheader("🔎 搜尋資料庫")
    search_term = st.text_input("輸入學校名稱關鍵字")
    if search_term:
        results = {
            k: v for k, v in st.session_state.school_db.items()
            if search_term in k
        }
        if results:
            for name, info in results.items():
                st.write(f"📍 **{name}** ({info['lat']:.4f}, {info['lon']:.4f})")
        else:
            st.write("找不到符合的學校")

# ==========================================
# 模糊比對函式
# ==========================================
def fuzzy_match_school(school_name, db):
    """嘗試用多種變體去比對資料庫"""
    variants = set()
    variants.add(school_name)
    variants.add(school_name.replace("台", "臺"))
    variants.add(school_name.replace("臺", "台"))

    prefixes = ["國立", "私立", "市立", "縣立"]
    expanded = set()
    for v in variants:
        expanded.add(v)
        for p in prefixes:
            if v.startswith(p):
                expanded.add(v[len(p):])
            else:
                expanded.add(p + v)

    for variant in expanded:
        if variant in db:
            return variant
    return None

# ==========================================
# 線上查詢函式
# ==========================================
def search_online(school_name, geolocator):
    """多策略線上查詢"""
    search_queries = [
        f"台灣 {school_name}",
        f"Taiwan {school_name}",
        school_name,
    ]

    if "台" in school_name:
        search_queries.append(f"台灣 {school_name.replace('台', '臺')}")
    if "臺" in school_name:
        search_queries.append(f"台灣 {school_name.replace('臺', '台')}")
    if "高中" in school_name:
        search_queries.append(f"台灣 {school_name}高級中學")

    for query in search_queries:
        try:
            location = geolocator.geocode(query, timeout=10)
            if location:
                if (21.5 <= location.latitude <= 26.5 and
                    119.0 <= location.longitude <= 123.0):
                    return location.latitude, location.longitude
            time.sleep(0.5)
        except Exception:
            time.sleep(1)

    return None, None

# ==========================================
# 主要功能區
# ==========================================
tab1, tab2, tab3, tab4 = st.tabs([
    "📁 上傳與轉換", "🗺️ 地圖視覺化", "📊 統計分析", "✏️ 手動編輯"
])

# ==========================================
# Tab 1: 上傳與轉換
# ==========================================
with tab1:
    st.header("📁 上傳申請生資料")

    uploaded_file = st.file_uploader(
        "請上傳申請生資料檔（CSV 或 Excel）",
        type=["csv", "xlsx"],
        key="main_upload"
    )

    if uploaded_file:
        # 讀取資料
        if uploaded_file.name.endswith('.csv'):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)

        st.write(f"✅ 成功讀取 **{len(df)}** 筆資料")
        st.write(f"📋 欄位：{list(df.columns)}")

        # 讓使用者選擇學校名稱欄位
        school_column = st.selectbox(
            "請選擇「畢業學校」對應的欄位",
            df.columns,
            index=list(df.columns).index("畢業學校") if "畢業學校" in df.columns else 0
        )

        # 預覽資料
        with st.expander("👀 預覽前 10 筆資料"):
            st.dataframe(df.head(10))

        # 開始轉換按鈕
        if st.button("🚀 開始轉換經緯度", type="primary"):
            unique_schools = df[school_column].dropna().unique()
            st.write(f"共 **{len(unique_schools)}** 所不重複學校")

            # 分類
            found = {}
            not_found = []
            log = []

            progress_bar = st.progress(0)
            status_text = st.empty()
            log_container = st.container()

            for i, school in enumerate(unique_schools):
                progress = (i + 1) / len(unique_schools)
                progress_bar.progress(progress)
                status_text.text(f"處理中... ({i+1}/{len(unique_schools)}) {school}")

                # Step 1: 直接查資料庫
                if school in st.session_state.school_db:
                    info = st.session_state.school_db[school]
                    found[school] = (info["lat"], info["lon"])
                    log.append(f"✅ {school} → 資料庫命中")
                    continue

                # Step 2: 模糊比對
                match = fuzzy_match_school(school, st.session_state.school_db)
                if match:
                    info = st.session_state.school_db[match]
                    st.session_state.school_db[school] = {
                        "lat": info["lat"],
                        "lon": info["lon"],
                        "source": f"模糊比對→{match}"
                    }
                    found[school] = (info["lat"], info["lon"])
                    log.append(f"🔗 {school} → 模糊比對 → {match}")
                    continue

                # Step 3: 線上查詢
                log.append(f"🔍 {school} → 上網查詢中...")
                geolocator = Nominatim(user_agent="school_geocoder_streamlit")
                lat, lon = search_online(school, geolocator)

                if lat and lon:
                    st.session_state.school_db[school] = {
                        "lat": lat,
                        "lon": lon,
                        "source": "線上查詢(Nominatim)"
                    }
                    found[school] = (lat, lon)
                    log.append(f"✅ {school} → 線上查詢成功 ({lat:.4f}, {lon:.4f})")
                else:
                    not_found.append(school)
                    log.append(f"❌ {school} → 找不到")

                time.sleep(1)  # API 速率限制

            # 儲存更新後的資料庫
            save_db(st.session_state.school_db)

            # 對應回原始資料
            def get_lat(s):
                if s in st.session_state.school_db:
                    return st.session_state.school_db[s]["lat"]
                return None

            def get_lon(s):
                if s in st.session_state.school_db:
                    return st.session_state.school_db[s]["lon"]
                return None

            df["學校緯度"] = df[school_column].map(get_lat)
            df["學校經度"] = df[school_column].map(get_lon)

            st.session_state.processed_df = df
            st.session_state.processing_log = log

            # 顯示結果
            progress_bar.progress(1.0)
            status_text.text("✅ 轉換完成！")

            total = len(unique_schools)
            success = total - len(not_found)

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("總學校數", total)
            col2.metric("成功轉換", success)
            col3.metric("無法轉換", len(not_found))
            col4.metric("成功率", f"{success/total*100:.1f}%")

            # 顯示 log
            with st.expander("📋 查詢紀錄", expanded=False):
                for l in log:
                    st.text(l)

            # 顯示找不到的學校
            if not_found:
                st.warning(f"⚠️ 以下 {len(not_found)} 所學校無法自動找到座標：")
                not_found_df = pd.DataFrame({
                    "學校名稱": not_found,
                    "資料筆數": [len(df[df[school_column] == s]) for s in not_found]
                })
                st.dataframe(not_found_df)

                # 提供待補清單下載
                missing_csv = pd.DataFrame({
                    "學校名稱": not_found,
                    "緯度": "",
                    "經度": ""
                })
                st.download_button(
                    "⬇️ 下載待補經緯度清單",
                    data=missing_csv.to_csv(index=False).encode("utf-8-sig"),
                    file_name="待補經緯度學校.csv",
                    mime="text/csv"
                )

    # 下載轉換結果
    if st.session_state.processed_df is not None:
        st.divider()
        st.subheader("📥 下載轉換結果")
        result_csv = st.session_state.processed_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="⬇️ 下載含經緯度的完整資料 (CSV)",
            data=result_csv,
            file_name="申請生資料_已轉換經緯度.csv",
            mime="text/csv",
            type="primary"
        )
        with st.expander("👀 預覽轉換結果"):
            st.dataframe(st.session_state.processed_df.head(20))

# ==========================================
# Tab 2: 地圖視覺化
# ==========================================
with tab2:
    st.header("🗺️ 學校分布地圖")

    if st.session_state.processed_df is not None:
        df_map = st.session_state.processed_df.dropna(subset=["學校緯度", "學校經度"])

        if len(df_map) == 0:
            st.warning("沒有可顯示的座標資料")
        else:
            # 地圖設定
            col1, col2 = st.columns([1, 3])

            with col1:
                map_style = st.selectbox(
                    "地圖底圖",
                    ["OpenStreetMap", "CartoDB positron", "CartoDB dark_matter"]
                )

                color_column = st.selectbox(
                    "依據哪個欄位上色",
                    ["不分色"] + [c for c in df_map.columns if df_map[c].dtype == "object"],
                    index=0
                )

                marker_size = st.slider("標記大小", 3, 15, 6)
                show_heatmap = st.checkbox("顯示熱力效果（依學生人數）", value=True)

            with col2:
                # 計算每個學校的學生數
                school_col = [c for c in df_map.columns if c in ["畢業學校"]][0] if "畢業學校" in df_map.columns else df_map.columns[0]
                school_counts = df_map.groupby([school_col, "學校緯度", "學校經度"]).size().reset_index(name="學生人數")

                # 建立地圖（以台灣為中心）
                m = folium.Map(
                    location=[23.7, 120.9],
                    zoom_start=8,
                    tiles=map_style
                )

                # 定義顏色
                colors = [
                    "red", "blue", "green", "purple", "orange",
                    "darkred", "darkblue", "darkgreen", "cadetblue", "pink",
                    "lightred", "lightblue", "lightgreen", "gray", "black"
                ]

                if show_heatmap:
                    # 用 CircleMarker，大小依學生人數縮放
                    for _, row in school_counts.iterrows():
                        radius = max(marker_size, row["學生人數"] * 2)

                        folium.CircleMarker(
                            location=[row["學校緯度"], row["學校經度"]],
                            radius=radius,
                            popup=f"{row[school_col]}<br>學生人數: {row['學生人數']}",
                            tooltip=f"{row[school_col]} ({row['學生人數']}人)",
                            color="crimson",
                            fill=True,
                            fill_color="crimson",
                            fill_opacity=0.5
                        ).add_to(m)
                else:
                    # 用普通 Marker
                    for _, row in school_counts.iterrows():
                        folium.Marker(
                            location=[row["學校緯度"], row["學校經度"]],
                            popup=f"{row[school_col]}<br>學生人數: {row['學生人數']}",
                            tooltip=f"{row[school_col]} ({row['學生人數']}人)"
                        ).add_to(m)

                st_folium(m, width=800, height=600)

            # 地圖下方統計表
            st.subheader("📊 各校學生人數統計")
            school_stats = school_counts.sort_values("學生人數", ascending=False)
            st.dataframe(
                school_stats,
                use_container_width=True,
                hide_index=True
            )

    else:
        st.info("👈 請先在「上傳與轉換」頁籤上傳資料並完成轉換")

# ==========================================
# Tab 3: 統計分析
# ==========================================
with tab3:
    st.header("📊 統計分析")

    if st.session_state.processed_df is not None:
        df_stats = st.session_state.processed_df

        # 基本統計
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("🏫 各校申請人數 TOP 20")
            school_col = "畢業學校" if "畢業學校" in df_stats.columns else df_stats.columns[0]
            top_schools = df_stats[school_col].value_counts().head(20)
            st.bar_chart(top_schools)

        with col2:
            st.subheader("🗺️ 地區分布")
            # 根據經緯度大致分類地區
            def classify_region(lat, lon):
                if pd.isna(lat) or pd.isna(lon):
                    return "未知"
                if lat >= 24.8:
                    return "北部"
                elif lat >= 24.0:
                    return "中部"
                elif lat >= 23.0:
                    return "南部"
                elif lon >= 121.0:
                    return "東部"
                else:
                    return "離島/其他"

            df_stats["地區"] = df_stats.apply(
                lambda row: classify_region(row.get("學校緯度"), row.get("學校經度")),
                axis=1
            )
            region_counts = df_stats["地區"].value_counts()
            st.bar_chart(region_counts)

        # 詳細表格
        st.subheader("📋 完整學校統計表")
        full_stats = df_stats.groupby(school_col).agg(
            人數=(school_col, "count"),
            緯度=("學校緯度", "first"),
            經度=("學校經度", "first")
        ).sort_values("人數", ascending=False).reset_index()
        full_stats.columns = ["學校名稱", "申請人數", "緯度", "經度"]
        st.dataframe(full_stats, use_container_width=True, hide_index=True)

    else:
        st.info("👈 請先在「上傳與轉換」頁籤上傳資料並完成轉換")

# ==========================================
# Tab 4: 手動編輯資料庫
# ==========================================
with tab4:
    st.header("✏️ 手動編輯學校座標")

    st.subheader("➕ 新增 / 修改學校座標")

    col1, col2, col3 = st.columns(3)
    with col1:
        new_school = st.text_input("學校名稱", placeholder="例：臺北市立建國高級中學")
    with col2:
        new_lat = st.number_input("緯度", value=23.5, min_value=21.0, max_value=27.0, format="%.6f")
    with col3:
        new_lon = st.number_input("經度", value=120.5, min_value=118.0, max_value=124.0, format="%.6f")

    if st.button("💾 儲存到資料庫"):
        if new_school.strip():
            st.session_state.school_db[new_school.strip()] = {
                "lat": new_lat,
                "lon": new_lon,
                "source": "手動新增"
            }
            save_db(st.session_state.school_db)
            st.success(f"✅ 已儲存：{new_school} ({new_lat:.4f}, {new_lon:.4f})")
            st.rerun()
        else:
            st.error("請輸入學校名稱")

    st.divider()

    # 批次編輯
    st.subheader("📝 批次編輯資料庫")
    st.caption("直接在表格中編輯，修改完按下方的「儲存修改」按鈕")

    if st.session_state.school_db:
        db_df = pd.DataFrame([
            {"學校名稱": k, "緯度": v["lat"], "經度": v["lon"], "來源": v.get("source", "")}
            for k, v in st.session_state.school_db.items()
        ]).sort_values("學校名稱").reset_index(drop=True)

        edited_df = st.data_editor(
            db_df,
            use_container_width=True,
            num_rows="dynamic",  # 允許新增/刪除列
            key="db_editor"
        )

        if st.button("💾 儲存所有修改", type="primary"):
            new_db = {}
            for _, row in edited_df.iterrows():
                name = str(row["學校名稱"]).strip()
                if name and name != "nan":
                    new_db[name] = {
                        "lat": float(row["緯度"]),
                        "lon": float(row["經度"]),
                        "source": str(row.get("來源", "手動編輯"))
                    }
            st.session_state.school_db = new_db
            save_db(new_db)
            st.success(f"✅ 資料庫已更新！共 {len(new_db)} 所學校")
            st.rerun()
    else:
        st.info("資料庫目前是空的，請先匯入資料或手動新增")

# ==========================================
# 頁尾
# ==========================================
st.divider()
st.caption(
    "💡 提示：本地資料庫會自動累積，每次查詢過的學校都會被記住。"
    "您也可以在左側欄匯入/匯出資料庫，或在「手動編輯」分頁中修改座標。"
)
