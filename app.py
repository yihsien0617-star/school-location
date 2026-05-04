import streamlit as st
import pandas as pd
import json
import os
import re
import time
import urllib.parse
import requests
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
# 常數
# ==========================================
DB_FILE = "school_coordinates_db.json"
# 台灣經緯度範圍（過濾用）
TW_LAT_MIN, TW_LAT_MAX = 21.5, 26.5
TW_LON_MIN, TW_LON_MAX = 119.0, 123.0

# ==========================================
# 初始化
# ==========================================
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_db(db):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # Streamlit Cloud 可能無法寫入

if "school_db" not in st.session_state:
    st.session_state.school_db = load_db()
if "processed_df" not in st.session_state:
    st.session_state.processed_df = None
if "processing_log" not in st.session_state:
    st.session_state.processing_log = []

# ==========================================
# 台灣座標驗證
# ==========================================
def is_in_taiwan(lat, lon):
    """判斷座標是否在台灣範圍內"""
    if lat is None or lon is None:
        return False
    return (TW_LAT_MIN <= lat <= TW_LAT_MAX and
            TW_LON_MIN <= lon <= TW_LON_MAX)

# ==========================================
# 學校名稱正規化
# ==========================================
def normalize_school_name(name):
    """正規化學校名稱"""
    if not name or not isinstance(name, str):
        return ""
    name = name.strip()
    name = name.replace("　", "").replace(" ", "")
    # 統一台/臺
    name = name.replace("台", "臺")
    return name

def generate_name_variants(school_name):
    """
    產生學校名稱的多種變體，用於搜尋
    """
    variants = []
    name = school_name.strip()
    variants.append(name)

    # 台 <-> 臺
    if "台" in name:
        variants.append(name.replace("台", "臺"))
    if "臺" in name:
        variants.append(name.replace("臺", "台"))

    # 移除/新增行政前綴
    admin_prefixes = [
        "國立", "私立", "市立", "縣立",
        "臺北市立", "臺北市私立",
        "新北市立", "新北市私立",
        "桃園市立", "桃園市私立",
        "臺中市立", "臺中市私立",
        "臺南市立", "臺南市私立",
        "高雄市立", "高雄市私立",
        "基隆市立", "新竹市立", "新竹縣立",
        "苗栗縣立", "彰化縣立", "南投縣立",
        "雲林縣立", "嘉義市立", "嘉義縣立",
        "屏東縣立", "宜蘭縣立", "花蓮縣立",
        "臺東縣立", "澎湖縣立", "金門縣立",
        "連江縣立",
    ]

    base = name
    for p in sorted(admin_prefixes, key=len, reverse=True):
        if name.startswith(p):
            base = name[len(p):]
            break

    variants.append(base)

    # 簡稱對應：高級中學 <-> 高中
    for v in list(variants):
        if "高級中學" in v:
            variants.append(v.replace("高級中學", "高中"))
        if "高級中等學校" in v:
            variants.append(v.replace("高級中等學校", "高中"))
            variants.append(v.replace("高級中等學校", "高級中學"))
        if "高中" in v and "高級" not in v:
            variants.append(v.replace("高中", "高級中學"))

    # 去重但保持順序
    seen = set()
    unique = []
    for v in variants:
        if v not in seen and v:
            seen.add(v)
            unique.append(v)
    return unique

# ==========================================
# 模糊比對（查資料庫）
# ==========================================
def fuzzy_match_school(school_name, db):
    """用多種變體去比對資料庫"""
    variants = generate_name_variants(school_name)
    # 再加上 台/臺 互換的所有組合
    expanded = []
    for v in variants:
        expanded.append(v)
        expanded.append(v.replace("台", "臺"))
        expanded.append(v.replace("臺", "台"))
    expanded = list(dict.fromkeys(expanded))  # 去重保序

    for variant in expanded:
        if variant in db:
            return variant

    # 最後嘗試子字串比對（學校核心名稱包含在資料庫 key 中）
    core = school_name
    for p in ["國立", "私立", "市立", "縣立"]:
        if core.startswith(p):
            core = core[len(p):]
            break
    core = core.replace("台", "臺")

    if len(core) >= 4:
        for db_name in db:
            if core in db_name or db_name.endswith(core):
                return db_name

    return None

# ==========================================
# 引擎 1: Nominatim (OpenStreetMap)
# ==========================================
def search_nominatim(school_name):
    """用 Nominatim 搜尋"""
    try:
        geolocator = Nominatim(
            user_agent="school_geocoder_tw_v2",
            timeout=10
        )
        variants = generate_name_variants(school_name)
        search_queries = []
        for v in variants:
            search_queries.append(f"{v}, Taiwan")
            search_queries.append(f"台灣 {v}")
            search_queries.append(v)

        for query in search_queries:
            try:
                location = geolocator.geocode(
                    query,
                    exactly_one=True,
                    timeout=10
                )
                if location and is_in_taiwan(location.latitude, location.longitude):
                    return location.latitude, location.longitude, "Nominatim"
                time.sleep(0.3)
            except Exception:
                time.sleep(0.5)
                continue
    except Exception:
        pass
    return None, None, None

# ==========================================
# 引擎 2: Nominatim structured query
# ==========================================
def search_nominatim_structured(school_name):
    """用 Nominatim 結構化查詢"""
    try:
        geolocator = Nominatim(
            user_agent="school_geocoder_tw_struct_v2",
            timeout=10
        )

        # 嘗試從學校名稱取出城市
        city_patterns = [
            (r"^(臺北市|台北市)", "臺北市"),
            (r"^(新北市)", "新北市"),
            (r"^(桃園市)", "桃園市"),
            (r"^(臺中市|台中市)", "臺中市"),
            (r"^(臺南市|台南市)", "臺南市"),
            (r"^(高雄市)", "高雄市"),
            (r"^(基隆市)", "基隆市"),
            (r"^(新竹市)", "新竹市"),
            (r"^(新竹縣)", "新竹縣"),
            (r"^(苗栗縣)", "苗栗縣"),
            (r"^(彰化縣)", "彰化縣"),
            (r"^(南投縣)", "南投縣"),
            (r"^(雲林縣)", "雲林縣"),
            (r"^(嘉義市)", "嘉義市"),
            (r"^(嘉義縣)", "嘉義縣"),
            (r"^(屏東縣)", "屏東縣"),
            (r"^(宜蘭縣)", "宜蘭縣"),
            (r"^(花蓮縣)", "花蓮縣"),
            (r"^(臺東縣|台東縣)", "臺東縣"),
            (r"^(澎湖縣)", "澎湖縣"),
            (r"^(金門縣)", "金門縣"),
            (r"^(連江縣)", "連江縣"),
        ]

        city = None
        for pattern, city_name in city_patterns:
            if re.search(pattern, school_name.replace("台", "臺")):
                city = city_name
                break

        if city:
            location = geolocator.geocode(
                query=school_name,
                exactly_one=True,
                timeout=10,
                country_codes="tw"
            )
            if location and is_in_taiwan(location.latitude, location.longitude):
                return location.latitude, location.longitude, "Nominatim(結構化)"
            time.sleep(0.3)

        # 不帶城市直接查
        location = geolocator.geocode(
            query=school_name,
            exactly_one=True,
            timeout=10,
            country_codes="tw"
        )
        if location and is_in_taiwan(location.latitude, location.longitude):
            return location.latitude, location.longitude, "Nominatim(country_code)"
        time.sleep(0.3)

    except Exception:
        pass
    return None, None, None

# ==========================================
# 引擎 3: Photon (Komoot 的免費 geocoder)
# ==========================================
def search_photon(school_name):
    """用 Photon API (komoot) 搜尋"""
    try:
        variants = generate_name_variants(school_name)
        for v in variants[:5]:  # 只試前5個變體
            url = "https://photon.komoot.io/api/"
            params = {
                "q": f"{v} Taiwan",
                "limit": 5,
                "lat": 23.7,   # 台灣中心
                "lon": 120.9,
                "lang": "zh"
            }
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                features = data.get("features", [])
                for feat in features:
                    coords = feat.get("geometry", {}).get("coordinates", [])
                    if len(coords) == 2:
                        lon, lat = coords
                        if is_in_taiwan(lat, lon):
                            return lat, lon, "Photon"
            time.sleep(0.3)
    except Exception:
        pass
    return None, None, None

# ==========================================
# 引擎 4: OpenStreetMap Nominatim Search API (直接 HTTP)
# ==========================================
def search_osm_api(school_name):
    """直接用 OSM Nominatim HTTP API"""
    try:
        variants = generate_name_variants(school_name)
        headers = {"User-Agent": "SchoolGeocoderTW/2.0"}

        for v in variants[:5]:
            url = "https://nominatim.openstreetmap.org/search"
            params = {
                "q": f"{v}",
                "format": "json",
                "limit": 5,
                "countrycodes": "tw",
                "accept-language": "zh-TW"
            }
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                results = resp.json()
                for r in results:
                    lat = float(r["lat"])
                    lon = float(r["lon"])
                    if is_in_taiwan(lat, lon):
                        return lat, lon, "OSM API"
            time.sleep(1.0)  # Nominatim 要求 1秒間隔
    except Exception:
        pass
    return None, None, None

# ==========================================
# 引擎 5: 政府資料開放平臺 / TGOS 概念
#   實際用「學校名 + 地址」策略搜尋
# ==========================================
def search_with_address_hint(school_name):
    """
    嘗試「學校名+高中+地址」等組合搜尋
    有些學校用地址比較容易找到
    """
    try:
        geolocator = Nominatim(
            user_agent="school_geocoder_addr_v2",
            timeout=10
        )

        # 從學校名稱猜測所在城市，構建地址式查詢
        name_clean = school_name.replace("國立", "").replace("私立", "")
        name_clean = name_clean.replace("市立", "").replace("縣立", "")

        # 取核心名稱（例如「建國高級中學」→「建國高中」）
        core = name_clean
        core = core.replace("高級中等學校", "高中")
        core = core.replace("高級中學", "高中")

        search_queries = [
            f"{core} school Taiwan",
            f"{school_name} 學校",
        ]

        for query in search_queries:
            location = geolocator.geocode(query, timeout=10)
            if location and is_in_taiwan(location.latitude, location.longitude):
                return location.latitude, location.longitude, "地址推測"
            time.sleep(0.5)

    except Exception:
        pass
    return None, None, None

# ==========================================
# 主搜尋引擎：依序嘗試所有引擎
# ==========================================
def search_all_engines(school_name, progress_callback=None):
    """
    依序嘗試所有搜尋引擎
    回傳 (lat, lon, source) 或 (None, None, None)
    """
    engines = [
        ("Nominatim 基本搜尋", search_nominatim),
        ("Nominatim 結構化搜尋", search_nominatim_structured),
        ("Photon (Komoot)", search_photon),
        ("OSM API 直接搜尋", search_osm_api),
        ("地址推測搜尋", search_with_address_hint),
    ]

    for engine_name, engine_func in engines:
        if progress_callback:
            progress_callback(f"  → 嘗試 {engine_name}...")
        try:
            lat, lon, source = engine_func(school_name)
            if lat is not None and lon is not None:
                return lat, lon, source
        except Exception as e:
            if progress_callback:
                progress_callback(f"  → {engine_name} 發生錯誤: {str(e)[:50]}")
            continue

    return None, None, None

# ==========================================
# 側邊欄：資料庫管理
# ==========================================
with st.sidebar:
    st.header("📚 學校資料庫管理")
    st.metric("目前收錄學校數", f"{len(st.session_state.school_db)} 所")
    st.divider()

    st.subheader("📥 匯入經緯度資料")
    st.caption("CSV/Excel，需含學校名稱、緯度、經度欄位")
    uploaded_db = st.file_uploader("選擇經緯度資料檔", type=["csv", "xlsx"], key="db_upload")

    if uploaded_db:
        if uploaded_db.name.endswith('.csv'):
            df_import = pd.read_csv(uploaded_db)
        else:
            df_import = pd.read_excel(uploaded_db)

        st.write(f"讀取到 {len(df_import)} 筆")
        c1, c2, c3 = st.columns(3)
        with c1:
            school_col_imp = st.selectbox("學校名稱", df_import.columns, key="ic1")
        with c2:
            lat_col_imp = st.selectbox("緯度", df_import.columns, key="ic2")
        with c3:
            lon_col_imp = st.selectbox("經度", df_import.columns, key="ic3")

        if st.button("🔄 匯入", type="primary"):
            count = 0
            for _, row in df_import.iterrows():
                name = str(row[school_col_imp]).strip()
                lat = row[lat_col_imp]
                lon = row[lon_col_imp]
                if pd.notna(lat) and pd.notna(lon) and name and name != "nan":
                    st.session_state.school_db[name] = {
                        "lat": float(lat), "lon": float(lon), "source": "手動匯入"
                    }
                    count += 1
            save_db(st.session_state.school_db)
            st.success(f"✅ 匯入 {count} 所！")
            st.rerun()

    st.divider()
    st.subheader("📤 匯出資料庫")
    if st.session_state.school_db:
        db_exp = pd.DataFrame([
            {"學校名稱": k, "緯度": v["lat"], "經度": v["lon"], "來源": v.get("source", "")}
            for k, v in st.session_state.school_db.items()
        ])
        st.download_button(
            "⬇️ 下載資料庫 CSV",
            data=db_exp.to_csv(index=False).encode("utf-8-sig"),
            file_name="school_db_export.csv",
            mime="text/csv"
        )

    st.divider()
    st.subheader("🔎 搜尋資料庫")
    search_term = st.text_input("關鍵字搜尋")
    if search_term:
        results = {k: v for k, v in st.session_state.school_db.items() if search_term in k}
        if results:
            for name, info in list(results.items())[:20]:
                st.write(f"📍 **{name}** ({info['lat']:.4f}, {info['lon']:.4f})")
        else:
            st.write("找不到")

# ==========================================
# 主功能區 Tabs
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
        "請上傳 CSV 或 Excel 檔案",
        type=["csv", "xlsx"],
        key="main_upload"
    )

    if uploaded_file:
        if uploaded_file.name.endswith('.csv'):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)

        st.success(f"✅ 讀取 **{len(df)}** 筆資料，欄位：{list(df.columns)}")

        school_column = st.selectbox(
            "請選擇「畢業學校」欄位",
            df.columns,
            index=list(df.columns).index("畢業學校") if "畢業學校" in df.columns else 0
        )

        with st.expander("👀 預覽前 10 筆"):
            st.dataframe(df.head(10))

        # ===== 開始轉換 =====
        if st.button("🚀 開始轉換經緯度（含自動上網搜尋）", type="primary"):

            unique_schools = df[school_column].dropna().unique()
            total = len(unique_schools)
            st.write(f"共 **{total}** 所不重複學校，開始處理...")

            found = {}
            not_found = []
            log = []

            progress_bar = st.progress(0)
            status_text = st.empty()
            detail_container = st.empty()

            for i, school in enumerate(unique_schools):
                school = str(school).strip()
                pct = (i + 1) / total
                progress_bar.progress(pct)
                status_text.text(f"({i+1}/{total}) 處理: {school}")

                # ---------- Step 1: 資料庫直接查 ----------
                if school in st.session_state.school_db:
                    info = st.session_state.school_db[school]
                    found[school] = (info["lat"], info["lon"])
                    log.append(f"✅ [資料庫] {school}")
                    continue

                # 正規化後再查一次
                norm = normalize_school_name(school)
                if norm in st.session_state.school_db:
                    info = st.session_state.school_db[norm]
                    st.session_state.school_db[school] = {
                        "lat": info["lat"], "lon": info["lon"],
                        "source": f"正規化→{norm}"
                    }
                    found[school] = (info["lat"], info["lon"])
                    log.append(f"✅ [正規化] {school} → {norm}")
                    continue

                # ---------- Step 2: 模糊比對 ----------
                match = fuzzy_match_school(school, st.session_state.school_db)
                if match:
                    info = st.session_state.school_db[match]
                    st.session_state.school_db[school] = {
                        "lat": info["lat"], "lon": info["lon"],
                        "source": f"模糊比對→{match}"
                    }
                    found[school] = (info["lat"], info["lon"])
                    log.append(f"🔗 [模糊比對] {school} → {match}")
                    continue

                # ---------- Step 3: 多引擎線上搜尋 ----------
                log.append(f"🔍 [線上搜尋] {school}...")
                detail_container.info(f"🌐 正在上網搜尋: **{school}**（使用多個搜尋引擎）")

                def log_progress(msg):
                    log.append(f"   {msg}")

                lat, lon, source = search_all_engines(school, progress_callback=log_progress)

                if lat is not None and lon is not None:
                    st.session_state.school_db[school] = {
                        "lat": lat, "lon": lon,
                        "source": f"線上查詢({source})"
                    }
                    found[school] = (lat, lon)
                    log.append(f"✅ [線上成功] {school} → ({lat:.4f}, {lon:.4f}) via {source}")
                else:
                    not_found.append(school)
                    log.append(f"❌ [全部失敗] {school}")

                time.sleep(0.3)

            # ===== 處理完成 =====
            save_db(st.session_state.school_db)
            progress_bar.progress(1.0)
            detail_container.empty()
            status_text.text("✅ 全部處理完成！")

            # 對應回原始資料
            def get_lat(s):
                s = str(s).strip()
                if s in st.session_state.school_db:
                    return st.session_state.school_db[s]["lat"]
                m = fuzzy_match_school(s, st.session_state.school_db)
                if m:
                    return st.session_state.school_db[m]["lat"]
                return None

            def get_lon(s):
                s = str(s).strip()
                if s in st.session_state.school_db:
                    return st.session_state.school_db[s]["lon"]
                m = fuzzy_match_school(s, st.session_state.school_db)
                if m:
                    return st.session_state.school_db[m]["lon"]
                return None

            df["學校緯度"] = df[school_column].map(get_lat)
            df["學校經度"] = df[school_column].map(get_lon)

            st.session_state.processed_df = df
            st.session_state.processing_log = log

            # 結果統計
            success = total - len(not_found)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("總學校數", total)
            c2.metric("✅ 成功", success)
            c3.metric("❌ 失敗", len(not_found))
            c4.metric("成功率", f"{success/total*100:.1f}%")

            # 搜尋紀錄
            with st.expander("📋 完整搜尋紀錄", expanded=False):
                for l in log:
                    if l.startswith("✅"):
                        st.markdown(f"<span style='color:green'>{l}</span>", unsafe_allow_html=True)
                    elif l.startswith("❌"):
                        st.markdown(f"<span style='color:red'>{l}</span>", unsafe_allow_html=True)
                    elif l.startswith("🔗"):
                        st.markdown(f"<span style='color:blue'>{l}</span>", unsafe_allow_html=True)
                    elif l.startswith("🔍"):
                        st.markdown(f"<span style='color:orange'>{l}</span>", unsafe_allow_html=True)
                    else:
                        st.text(l)

            # 找不到的學校
            if not_found:
                st.warning(f"⚠️ 以下 {len(not_found)} 所學校所有引擎都搜尋不到：")
                nf_df = pd.DataFrame({
                    "學校名稱": not_found,
                    "資料筆數": [len(df[df[school_column] == s]) for s in not_found]
                })
                st.dataframe(nf_df, hide_index=True)

                missing_csv = pd.DataFrame({
                    "學校名稱": not_found, "緯度": "", "經度": ""
                })
                st.download_button(
                    "⬇️ 下載待補清單 CSV（填完後可從左側匯入）",
                    data=missing_csv.to_csv(index=False).encode("utf-8-sig"),
                    file_name="待補經緯度學校.csv",
                    mime="text/csv"
                )

    # 下載結果
    if st.session_state.processed_df is not None:
        st.divider()
        st.subheader("📥 下載轉換結果")
        result_csv = st.session_state.processed_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "⬇️ 下載含經緯度的完整資料 (CSV)",
            data=result_csv,
            file_name="申請生資料_含經緯度.csv",
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
            st.warning("沒有座標可顯示")
        else:
            col_ctrl, col_map = st.columns([1, 3])

            with col_ctrl:
                map_style = st.selectbox(
                    "底圖", ["OpenStreetMap", "CartoDB positron", "CartoDB dark_matter"]
                )
                marker_size = st.slider("標記大小", 3, 15, 6)
                show_heatmap = st.checkbox("依學生人數縮放", value=True)

            with col_map:
                school_col = "畢業學校" if "畢業學校" in df_map.columns else df_map.columns[0]
                school_counts = (
                    df_map.groupby([school_col, "學校緯度", "學校經度"])
                    .size().reset_index(name="學生人數")
                )

                m = folium.Map(location=[23.7, 120.9], zoom_start=8, tiles=map_style)

                for _, row in school_counts.iterrows():
                    if show_heatmap:
                        r = max(marker_size, row["學生人數"] * 2)
                        folium.CircleMarker(
                            location=[row["學校緯度"], row["學校經度"]],
                            radius=r,
                            popup=f"{row[school_col]}<br>學生: {row['學生人數']}",
                            tooltip=f"{row[school_col]} ({row['學生人數']}人)",
                            color="crimson", fill=True,
                            fill_color="crimson", fill_opacity=0.5
                        ).add_to(m)
                    else:
                        folium.Marker(
                            location=[row["學校緯度"], row["學校經度"]],
                            popup=f"{row[school_col]}<br>學生: {row['學生人數']}",
                            tooltip=f"{row[school_col]} ({row['學生人數']}人)"
                        ).add_to(m)

                st_folium(m, width=800, height=600)

            st.subheader("📊 各校學生人數")
            st.dataframe(
                school_counts.sort_values("學生人數", ascending=False),
                use_container_width=True, hide_index=True
            )
    else:
        st.info("👈 請先在「上傳與轉換」完成轉換")

# ==========================================
# Tab 3: 統計分析
# ==========================================
with tab3:
    st.header("📊 統計分析")

    if st.session_state.processed_df is not None:
        df_s = st.session_state.processed_df
        school_col = "畢業學校" if "畢業學校" in df_s.columns else df_s.columns[0]

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("🏫 申請人數 TOP 20")
            top = df_s[school_col].value_counts().head(20)
            st.bar_chart(top)

        with c2:
            st.subheader("🗺️ 地區分布")
            def region(lat, lon):
                if pd.isna(lat) or pd.isna(lon): return "未知"
                if lat >= 24.8: return "北部"
                elif lat >= 24.0: return "中部"
                elif lat >= 23.0: return "南部"
                elif lon >= 121.0: return "東部"
                else: return "離島/其他"
            df_s["地區"] = df_s.apply(lambda r: region(r.get("學校緯度"), r.get("學校經度")), axis=1)
            st.bar_chart(df_s["地區"].value_counts())

        st.subheader("📋 完整統計表")
        full = df_s.groupby(school_col).agg(
            人數=(school_col, "count"),
            緯度=("學校緯度", "first"),
            經度=("學校經度", "first")
        ).sort_values("人數", ascending=False).reset_index()
        full.columns = ["學校", "人數", "緯度", "經度"]
        st.dataframe(full, use_container_width=True, hide_index=True)
    else:
        st.info("👈 請先完成轉換")

# ==========================================
# Tab 4: 手動編輯
# ==========================================
with tab4:
    st.header("✏️ 手動編輯學校座標")

    st.subheader("➕ 新增/修改")
    c1, c2, c3 = st.columns(3)
    with c1:
        new_school = st.text_input("學校名稱", placeholder="例：臺北市立建國高級中學")
    with c2:
        new_lat = st.number_input("緯度", value=23.5, min_value=21.0, max_value=27.0, format="%.6f")
    with c3:
        new_lon = st.number_input("經度", value=120.5, min_value=118.0, max_value=124.0, format="%.6f")

    if st.button("💾 儲存"):
        if new_school.strip():
            st.session_state.school_db[new_school.strip()] = {
                "lat": new_lat, "lon": new_lon, "source": "手動新增"
            }
            save_db(st.session_state.school_db)
            st.success(f"✅ 已儲存：{new_school}")
            st.rerun()

    st.divider()

    # 單一學校即時線上搜尋
    st.subheader("🔍 即時搜尋單一學校")
    st.caption("輸入任何學校名稱，系統會立即使用所有搜尋引擎尋找")
    test_school = st.text_input("輸入學校名稱", key="test_search", placeholder="例：竹東高中")

    if st.button("🌐 立即搜尋"):
        if test_school.strip():
            with st.spinner(f"正在搜尋 {test_school}..."):
                search_log = []
                lat, lon, source = search_all_engines(
                    test_school.strip(),
                    progress_callback=lambda msg: search_log.append(msg)
                )
            if lat and lon:
                st.success(f"✅ 找到！ {test_school} → ({lat:.6f}, {lon:.6f}) via {source}")
                for sl in search_log:
                    st.text(sl)
                if st.button("💾 加入資料庫", key="add_search_result"):
                    st.session_state.school_db[test_school.strip()] = {
                        "lat": lat, "lon": lon, "source": f"即時搜尋({source})"
                    }
                    save_db(st.session_state.school_db)
                    st.success("已加入資料庫！")
                    st.rerun()
            else:
                st.error(f"❌ 所有引擎都找不到 {test_school}")
                for sl in search_log:
                    st.text(sl)

    st.divider()

    st.subheader("📝 批次編輯資料庫")
    if st.session_state.school_db:
        db_df = pd.DataFrame([
            {"學校名稱": k, "緯度": v["lat"], "經度": v["lon"], "來源": v.get("source", "")}
            for k, v in st.session_state.school_db.items()
        ]).sort_values("學校名稱").reset_index(drop=True)

        edited = st.data_editor(db_df, use_container_width=True, num_rows="dynamic", key="db_ed")

        if st.button("💾 儲存所有修改", type="primary"):
            new_db = {}
            for _, row in edited.iterrows():
                name = str(row["學校名稱"]).strip()
                if name and name != "nan":
                    new_db[name] = {
                        "lat": float(row["緯度"]),
                        "lon": float(row["經度"]),
                        "source": str(row.get("來源", "手動編輯"))
                    }
            st.session_state.school_db = new_db
            save_db(new_db)
            st.success(f"✅ 已更新！共 {len(new_db)} 所")
            st.rerun()

# ==========================================
# 頁尾
# ==========================================
st.divider()
st.caption(
    "🔍 搜尋引擎順序：①資料庫直查 → ②正規化比對 → ③模糊比對 → "
    "④Nominatim基本 → ⑤Nominatim結構化 → ⑥Photon(Komoot) → "
    "⑦OSM API直查 → ⑧地址推測｜所有結果自動存入資料庫供下次使用"
)
