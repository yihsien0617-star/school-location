import streamlit as st
import pandas as pd
import json
import os
import time
import math
import re
from io import BytesIO

# === 必須是第一個 Streamlit 指令 ===
st.set_page_config(
    page_title="學校座標查詢系統",
    page_icon="🏫",
    layout="wide"
)

# === 安全載入 requests ===
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# === 安全載入 geopy ===
try:
    from geopy.geocoders import Nominatim
    from geopy.exc import GeocoderTimedOut, GeocoderServiceError
    HAS_GEOPY = True
except ImportError:
    HAS_GEOPY = False

# === 安全載入 folium ===
try:
    import folium
    from folium.plugins import HeatMap, MarkerCluster
    from streamlit_folium import st_folium
    HAS_FOLIUM = True
except ImportError:
    HAS_FOLIUM = False

# ============================
# 資料庫管理
# ============================
DB_FILE = "school_coordinates_db.json"

@st.cache_data
def load_database_from_file():
    """從檔案載入資料庫"""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def get_database():
    """取得資料庫（優先用 session_state）"""
    if 'school_db' not in st.session_state:
        st.session_state.school_db = load_database_from_file()
    return st.session_state.school_db

def save_database(db):
    """儲存資料庫"""
    st.session_state.school_db = db
    try:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except:
        pass

# ============================
# 學校名稱正規化
# ============================
def normalize_name(name):
    """基本正規化"""
    if not name or not isinstance(name, str):
        return ""
    name = name.strip()
    name = name.replace("臺", "台")
    name = re.sub(r'\s+', '', name)
    return name

def generate_variants(name):
    """產生學校名稱的各種變體"""
    variants = set()
    normalized = normalize_name(name)
    if not normalized:
        return variants
    variants.add(normalized)
    
    # 台/臺 互換
    if "台" in normalized:
        variants.add(normalized.replace("台", "臺"))
    if "臺" in normalized:
        variants.add(normalized.replace("臺", "台"))
    
    # 移除常見前綴
    for prefix in ["國立", "私立", "市立", "縣立"]:
        for v in list(variants):
            if v.startswith(prefix):
                variants.add(v[len(prefix):])
            else:
                variants.add(prefix + v)
    
    # 簡稱/全稱互換
    replacements = [
        ("高級中學", "高中"),
        ("高級商工職業學校", "高商"),
        ("高級工業職業學校", "高工"),
        ("高級商業職業學校", "高商"),
        ("高級家事商業職業學校", "家商"),
        ("高級農工職業學校", "農工"),
        ("國民中學", "國中"),
        ("國民小學", "國小"),
    ]
    for full, short in replacements:
        for v in list(variants):
            if full in v:
                variants.add(v.replace(full, short))
            if short in v and full not in v:
                variants.add(v.replace(short, full))
    
    return variants

# ============================
# 座標驗證（台灣範圍）
# ============================
def is_valid_taiwan_coordinate(lat, lon):
    """檢查座標是否在台灣範圍內"""
    try:
        lat, lon = float(lat), float(lon)
        return 21.5 <= lat <= 26.5 and 119.0 <= lon <= 123.0
    except:
        return False

# ============================
# 搜尋引擎（加入超時保護）
# ============================
SEARCH_TIMEOUT = 5  # 每次搜尋最多等 5 秒

def search_nominatim_basic(name):
    """Nominatim 基本搜尋"""
    if not HAS_GEOPY:
        return None, None, None
    try:
        geolocator = Nominatim(user_agent="school_geocoder_tw_v2", timeout=SEARCH_TIMEOUT)
        location = geolocator.geocode(name, exactly_one=True, country_codes="tw")
        if location and is_valid_taiwan_coordinate(location.latitude, location.longitude):
            return location.latitude, location.longitude, "Nominatim基本搜尋"
    except:
        pass
    return None, None, None

def search_nominatim_structured(name):
    """Nominatim 結構化搜尋"""
    if not HAS_GEOPY:
        return None, None, None
    try:
        geolocator = Nominatim(user_agent="school_geocoder_tw_v3", timeout=SEARCH_TIMEOUT)
        location = geolocator.geocode(
            query={"q": name, "countrycodes": "tw"},
            exactly_one=True
        )
        if location and is_valid_taiwan_coordinate(location.latitude, location.longitude):
            return location.latitude, location.longitude, "Nominatim結構化搜尋"
    except:
        pass
    return None, None, None

def search_photon(name):
    """Photon (Komoot) 搜尋"""
    if not HAS_REQUESTS:
        return None, None, None
    try:
        url = "https://photon.komoot.io/api/"
        params = {
            "q": name,
            "limit": 1,
            "lat": 23.5,
            "lon": 121.0,
            "lang": "zh"
        }
        resp = requests.get(url, params=params, timeout=SEARCH_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("features"):
                coords = data["features"][0]["geometry"]["coordinates"]
                lon, lat = coords[0], coords[1]
                if is_valid_taiwan_coordinate(lat, lon):
                    return lat, lon, "Photon搜尋"
    except:
        pass
    return None, None, None

def search_osm_api(name):
    """OSM Nominatim HTTP API"""
    if not HAS_REQUESTS:
        return None, None, None
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": name,
            "format": "json",
            "limit": 1,
            "countrycodes": "tw",
            "bounded": 1,
            "viewbox": "119.0,26.5,123.0,21.5"
        }
        headers = {"User-Agent": "SchoolGeocoderTW/2.0"}
        resp = requests.get(url, params=params, headers=headers, timeout=SEARCH_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
                if is_valid_taiwan_coordinate(lat, lon):
                    return lat, lon, "OSM API搜尋"
    except:
        pass
    return None, None, None

# ============================
# 核心：查詢單一學校座標（帶分批機制）
# ============================
def lookup_school(name, db, use_online=True):
    """
    查詢學校座標
    回傳: (lat, lon, source, matched_name)
    """
    if not name or not isinstance(name, str):
        return None, None, "無效名稱", None
    
    normalized = normalize_name(name)
    
    # 第一步：完全匹配資料庫
    if normalized in db:
        entry = db[normalized]
        return entry["lat"], entry["lon"], "資料庫完全匹配", normalized
    
    # 第二步：變體匹配資料庫
    variants = generate_variants(name)
    for variant in variants:
        if variant in db:
            entry = db[variant]
            return entry["lat"], entry["lon"], f"資料庫變體匹配({variant})", variant
    
    # 第三步：資料庫模糊搜尋
    for db_name in db:
        if normalized in db_name or db_name in normalized:
            entry = db[db_name]
            return entry["lat"], entry["lon"], f"資料庫模糊匹配({db_name})", db_name
    
    # 第四步：線上搜尋（如果啟用）
    if not use_online:
        return None, None, "僅離線模式-未找到", None
    
    search_queries = list(variants)[:5]  # 最多嘗試 5 個變體
    
    engines = [
        ("Nominatim", search_nominatim_basic),
        ("Photon", search_photon),
        ("OSM API", search_osm_api),
    ]
    
    for query in search_queries:
        for engine_name, engine_func in engines:
            try:
                lat, lon, source = engine_func(query)
                if lat is not None and lon is not None:
                    # 成功！存入資料庫
                    db[normalized] = {
                        "lat": lat, "lon": lon,
                        "source": source,
                        "original_query": name
                    }
                    save_database(db)
                    return lat, lon, source, normalized
            except:
                pass
            time.sleep(0.3)  # 縮短延遲，避免超時
    
    return None, None, "所有搜尋引擎均未找到", None

# ============================
# 🔑 關鍵修正：分批處理 + 不超時
# ============================
def process_batch(df, school_col, db, use_online, batch_size=5):
    """
    分批處理學校座標查詢
    每批處理完就更新 session_state，避免超時
    """
    # 初始化處理狀態
    if 'processing_state' not in st.session_state:
        st.session_state.processing_state = {
            'results': {},
            'current_index': 0,
            'is_complete': False
        }
    
    state = st.session_state.processing_state
    schools = df[school_col].tolist()
    total = len(schools)
    
    if state['is_complete']:
        return state['results']
    
    # 計算本批次範圍
    start_idx = state['current_index']
    end_idx = min(start_idx + batch_size, total)
    
    # 進度顯示
    progress_bar = st.progress(start_idx / total if total > 0 else 0)
    status_text = st.empty()
    log_area = st.empty()
    
    logs = []
    
    for i in range(start_idx, end_idx):
        school_name = str(schools[i]).strip()
        status_text.text(f"處理中: {school_name} ({i+1}/{total})")
        progress_bar.progress((i + 1) / total)
        
        lat, lon, source, matched = lookup_school(school_name, db, use_online)
        
        state['results'][i] = {
            'lat': lat, 'lon': lon,
            'source': source, 'matched': matched
        }
        
        status_icon = "✅" if lat else "❌"
        logs.append(f"{status_icon} {school_name} → {source}")
        log_area.text_area("處理記錄", "\n".join(logs[-10:]), height=150)
    
    # 更新索引
    state['current_index'] = end_idx
    
    if end_idx >= total:
        state['is_complete'] = True
        status_text.text(f"✅ 全部完成！共 {total} 筆")
        progress_bar.progress(1.0)
    else:
        remaining = total - end_idx
        status_text.text(f"⏳ 已處理 {end_idx}/{total}，剩餘 {remaining} 筆。點擊「繼續處理」按鈕...")
    
    st.session_state.processing_state = state
    return state['results'] if state['is_complete'] else None

# ============================
# 地區分類
# ============================
def classify_region(lat, lon):
    """根據座標分類地區"""
    try:
        lat, lon = float(lat), float(lon)
    except:
        return "未知"
    
    if lat >= 24.4:
        if lon < 121.3:
            return "北部"
        else:
            return "東部"
    elif lat >= 23.0:
        if lon < 120.8:
            return "中部"
        else:
            return "東部"
    elif lat >= 22.0:
        return "南部"
    else:
        if lon < 120.0:
            return "離島"
        return "南部"

# ============================
# 主介面
# ============================
st.title("🏫 學校座標查詢系統")

# 載入資料庫
db = get_database()

# ============================
# 側邊欄
# ============================
with st.sidebar:
    st.header("📊 資料庫管理")
    st.metric("資料庫學校數", len(db))
    
    st.divider()
    
    # 匯入資料庫
    st.subheader("📥 匯入座標資料庫")
    uploaded_db = st.file_uploader("上傳 JSON 資料庫", type=["json"], key="db_upload")
    if uploaded_db:
        try:
            new_db = json.load(uploaded_db)
            db.update(new_db)
            save_database(db)
            st.success(f"匯入成功！新增/更新 {len(new_db)} 筆")
            st.rerun()
        except Exception as e:
            st.error(f"匯入失敗: {e}")
    
    # 匯入 CSV 座標
    st.subheader("📥 從 CSV 匯入座標")
    uploaded_csv_db = st.file_uploader("上傳含座標的 CSV", type=["csv"], key="csv_db_upload")
    if uploaded_csv_db:
        try:
            csv_db = pd.read_csv(uploaded_csv_db)
            st.write("欄位:", list(csv_db.columns))
            col_name = st.selectbox("學校名稱欄位", csv_db.columns, key="csv_db_name")
            col_lat = st.selectbox("緯度欄位", csv_db.columns, key="csv_db_lat")
            col_lon = st.selectbox("經度欄位", csv_db.columns, key="csv_db_lon")
            if st.button("匯入座標", key="btn_import_csv"):
                count = 0
                for _, row in csv_db.iterrows():
                    name = normalize_name(str(row[col_name]))
                    try:
                        lat, lon = float(row[col_lat]), float(row[col_lon])
                        if is_valid_taiwan_coordinate(lat, lon) and name:
                            db[name] = {"lat": lat, "lon": lon, "source": "CSV匯入"}
                            count += 1
                    except:
                        pass
                save_database(db)
                st.success(f"匯入 {count} 筆座標")
                st.rerun()
        except Exception as e:
            st.error(f"讀取失敗: {e}")
    
    st.divider()
    
    # 搜尋資料庫
    st.subheader("🔍 搜尋資料庫")
    search_term = st.text_input("輸入學校名稱關鍵字")
    if search_term:
        results = {k: v for k, v in db.items() if search_term in k}
        if results:
            for name, info in list(results.items())[:10]:
                st.write(f"📍 {name}: ({info['lat']:.4f}, {info['lon']:.4f})")
        else:
            st.write("未找到相關學校")
    
    st.divider()
    
    # 匯出資料庫
    st.subheader("📤 匯出資料庫")
    if db:
        db_json = json.dumps(db, ensure_ascii=False, indent=2)
        st.download_button(
            "💾 下載資料庫 JSON",
            db_json,
            "school_coordinates_db.json",
            "application/json"
        )

# ============================
# 主要分頁
# ============================
tab1, tab2, tab3, tab4 = st.tabs([
    "📤 上傳轉檔", "🗺️ 地圖視覺化", "📊 統計分析", "✏️ 手動編輯"
])

# ============================
# Tab 1: 上傳轉檔
# ============================
with tab1:
    st.header("📤 上傳學校資料並轉換座標")
    
    uploaded_file = st.file_uploader(
        "上傳 CSV 或 Excel 檔案",
        type=["csv", "xlsx", "xls"],
        key="main_upload"
    )
    
    if uploaded_file:
        # 讀取檔案
        try:
            if uploaded_file.name.endswith('.csv'):
                df = pd.read_csv(uploaded_file)
            else:
                df = pd.read_excel(uploaded_file)
            
            st.success(f"成功讀取 {len(df)} 筆資料")
            st.dataframe(df.head(), use_container_width=True)
            
            # 選擇欄位
            col1, col2 = st.columns(2)
            with col1:
                school_col = st.selectbox("🏫 學校名稱欄位", df.columns)
            with col2:
                student_col = st.selectbox(
                    "👥 學生數欄位（選填）",
                    ["無"] + list(df.columns)
                )
            
            # 搜尋選項
            st.subheader("⚙️ 搜尋設定")
            use_online = st.checkbox("啟用線上搜尋（較慢但更完整）", value=True)
            
            batch_size = st.slider(
                "每批處理筆數（數字越小越不容易超時）",
                min_value=1, max_value=20, value=5
            )
            
            # === 關鍵：分批處理按鈕 ===
            col_btn1, col_btn2, col_btn3 = st.columns(3)
            
            with col_btn1:
                start_btn = st.button("🚀 開始處理", type="primary")
            with col_btn2:
                continue_btn = st.button("⏩ 繼續處理")
            with col_btn3:
                reset_btn = st.button("🔄 重新開始")
            
            if reset_btn:
                if 'processing_state' in st.session_state:
                    del st.session_state.processing_state
                if 'result_df' in st.session_state:
                    del st.session_state.result_df
                st.rerun()
            
            if start_btn:
                # 重設處理狀態
                st.session_state.processing_state = {
                    'results': {},
                    'current_index': 0,
                    'is_complete': False
                }
            
            # 執行處理
            if start_btn or continue_btn:
                if 'processing_state' not in st.session_state:
                    st.session_state.processing_state = {
                        'results': {},
                        'current_index': 0,
                        'is_complete': False
                    }
                
                results = process_batch(df, school_col, db, use_online, batch_size)
                
                if results is not None:
                    # 處理完成，組裝結果
                    lats, lons, sources = [], [], []
                    for i in range(len(df)):
                        r = results.get(i, {})
                        lats.append(r.get('lat'))
                        lons.append(r.get('lon'))
                        sources.append(r.get('source', '未處理'))
                    
                    result_df = df.copy()
                    result_df['緯度'] = lats
                    result_df['經度'] = lons
                    result_df['座標來源'] = sources
                    
                    if student_col != "無":
                        result_df['學生數'] = pd.to_numeric(df[student_col], errors='coerce').fillna(0).astype(int)
                    
                    st.session_state.result_df = result_df
                    st.session_state.school_col = school_col
                    st.session_state.student_col = student_col
                else:
                    # 尚未完成
                    state = st.session_state.processing_state
                    st.warning(f"已處理 {state['current_index']}/{len(df)} 筆，請點擊「⏩ 繼續處理」")
            
            # 顯示結果
            if 'result_df' in st.session_state:
                result_df = st.session_state.result_df
                
                st.divider()
                st.subheader("📋 處理結果")
                
                # 統計
                found = result_df['緯度'].notna().sum()
                total = len(result_df)
                missing = total - found
                
                col_s1, col_s2, col_s3, col_s4 = st.columns(4)
                col_s1.metric("總筆數", total)
                col_s2.metric("成功", found)
                col_s3.metric("失敗", missing)
                col_s4.metric("成功率", f"{found/total*100:.1f}%" if total > 0 else "0%")
                
                st.dataframe(result_df, use_container_width=True)
                
                # 下載結果
                col_d1, col_d2 = st.columns(2)
                
                with col_d1:
                    csv_data = result_df.to_csv(index=False).encode('utf-8-sig')
                    st.download_button(
                        "📥 下載完整結果 CSV",
                        csv_data,
                        "school_coordinates_result.csv",
                        "text/csv"
                    )
                
                with col_d2:
                    missing_df = result_df[result_df['緯度'].isna()]
                    if len(missing_df) > 0:
                        missing_csv = missing_df.to_csv(index=False).encode('utf-8-sig')
                        st.download_button(
                            f"📥 下載未找到清單 ({len(missing_df)} 筆)",
                            missing_csv,
                            "missing_schools.csv",
                            "text/csv"
                        )
        
        except Exception as e:
            st.error(f"檔案處理錯誤: {e}")
            st.exception(e)

# ============================
# Tab 2: 地圖視覺化
# ============================
with tab2:
    st.header("🗺️ 地圖視覺化")
    
    if 'result_df' not in st.session_state:
        st.info("請先在「上傳轉檔」分頁處理資料")
    elif not HAS_FOLIUM:
        st.error("folium 未安裝，無法顯示地圖")
    else:
        result_df = st.session_state.result_df
        map_df = result_df.dropna(subset=['緯度', '經度']).copy()
        
        if len(map_df) == 0:
            st.warning("沒有有效座標的資料可顯示")
        else:
            map_type = st.radio("地圖類型", ["標記地圖", "熱力圖"], horizontal=True)
            
            center_lat = map_df['緯度'].mean()
            center_lon = map_df['經度'].mean()
            
            m = folium.Map(location=[center_lat, center_lon], zoom_start=8)
            
            school_col = st.session_state.get('school_col', '學校')
            student_col = st.session_state.get('student_col', '無')
            
            if map_type == "標記地圖":
                for _, row in map_df.iterrows():
                    school_name = str(row.get(school_col, ''))
                    lat, lon = row['緯度'], row['經度']
                    
                    popup_text = f"<b>{school_name}</b><br>座標: ({lat:.4f}, {lon:.4f})"
                    
                    radius = 6
                    if student_col != "無" and '學生數' in row:
                        try:
                            students = int(row['學生數'])
                            popup_text += f"<br>學生數: {students}"
                            radius = max(4, min(20, students / 100))
                        except:
                            pass
                    
                    folium.CircleMarker(
                        location=[lat, lon],
                        radius=radius,
                        popup=folium.Popup(popup_text, max_width=300),
                        color='blue',
                        fill=True,
                        fillColor='blue',
                        fillOpacity=0.6
                    ).add_to(m)
            
            else:  # 熱力圖
                heat_data = [[row['緯度'], row['經度']] for _, row in map_df.iterrows()]
                if student_col != "無" and '學生數' in map_df.columns:
                    heat_data = [
                        [row['緯度'], row['經度'], float(row.get('學生數', 1))]
                        for _, row in map_df.iterrows()
                    ]
                HeatMap(heat_data).add_to(m)
            
            st_folium(m, width=None, height=600)
            
            st.caption(f"地圖顯示 {len(map_df)} 所學校")

# ============================
# Tab 3: 統計分析
# ============================
with tab3:
    st.header("📊 統計分析")
    
    if 'result_df' not in st.session_state:
        st.info("請先在「上傳轉檔」分頁處理資料")
    else:
        result_df = st.session_state.result_df
        valid_df = result_df.dropna(subset=['緯度', '經度']).copy()
        
        if len(valid_df) == 0:
            st.warning("沒有有效資料進行分析")
        else:
            # 地區分類
            valid_df['地區'] = valid_df.apply(
                lambda row: classify_region(row['緯度'], row['經度']), axis=1
            )
            
            st.subheader("🗺️ 地區分布")
            region_counts = valid_df['地區'].value_counts()
            
            col_r1, col_r2 = st.columns(2)
            with col_r1:
                st.dataframe(
                    region_counts.reset_index().rename(
                        columns={'index': '地區', '地區': '地區', 'count': '學校數'}
                    ),
                    use_container_width=True
                )
            with col_r2:
                st.bar_chart(region_counts)
            
            # 座標來源統計
            st.subheader("📡 座標來源統計")
            source_counts = result_df['座標來源'].value_counts()
            st.bar_chart(source_counts)
            
            # 學生數排名
            student_col = st.session_state.get('student_col', '無')
            school_col = st.session_state.get('school_col', '學校')
            
            if student_col != "無" and '學生數' in valid_df.columns:
                st.subheader("🏆 學生數排名 Top 20")
                top20 = valid_df.nlargest(20, '學生數')[[school_col, '學生數', '地區']]
                st.dataframe(top20, use_container_width=True)

# ============================
# Tab 4: 手動編輯
# ============================
with tab4:
    st.header("✏️ 手動編輯與即時查詢")
    
    # 即時查詢
    st.subheader("🔍 即時查詢單一學校")
    query_name = st.text_input("輸入學校名稱", placeholder="例: 台北市立建國高級中學")
    
    if st.button("🔍 搜尋", key="btn_single_search"):
        if query_name:
            with st.spinner(f"搜尋中: {query_name}..."):
                lat, lon, source, matched = lookup_school(query_name, db, use_online=True)
            
            if lat is not None:
                st.success(f"✅ 找到！ {query_name}")
                st.write(f"- 緯度: {lat:.6f}")
                st.write(f"- 經度: {lon:.6f}")
                st.write(f"- 來源: {source}")
                if matched:
                    st.write(f"- 匹配名稱: {matched}")
                
                if HAS_FOLIUM:
                    m = folium.Map(location=[lat, lon], zoom_start=15)
                    folium.Marker(
                        [lat, lon],
                        popup=query_name,
                        icon=folium.Icon(color='red', icon='info-sign')
                    ).add_to(m)
                    st_folium(m, width=None, height=400)
            else:
                st.error(f"❌ 未找到: {query_name} ({source})")
    
    st.divider()
    
    # 手動新增/修改
    st.subheader("➕ 手動新增座標")
    col_e1, col_e2, col_e3 = st.columns(3)
    
    with col_e1:
        manual_name = st.text_input("學校名稱", key="manual_name")
    with col_e2:
        manual_lat = st.number_input("緯度", min_value=21.0, max_value=27.0, value=25.0, format="%.6f")
    with col_e3:
        manual_lon = st.number_input("經度", min_value=119.0, max_value=123.0, value=121.5, format="%.6f")
    
    if st.button("💾 儲存座標", key="btn_save_manual"):
        if manual_name:
            normalized = normalize_name(manual_name)
            db[normalized] = {
                "lat": manual_lat,
                "lon": manual_lon,
                "source": "手動輸入"
            }
            save_database(db)
            st.success(f"已儲存: {normalized} ({manual_lat:.6f}, {manual_lon:.6f})")
        else:
            st.warning("請輸入學校名稱")
    
    st.divider()
    
    # 手動刪除
    st.subheader("🗑️ 刪除資料庫項目")
    if db:
        delete_name = st.selectbox("選擇要刪除的學校", sorted(db.keys()))
        if st.button("🗑️ 刪除", key="btn_delete"):
            if delete_name in db:
                del db[delete_name]
                save_database(db)
                st.success(f"已刪除: {delete_name}")
                st.rerun()

# ============================
# 頁尾
# ============================
st.divider()
st.caption("🏫 學校座標查詢系統 v2.0 | 分批處理版本 | 支援離線/線上混合查詢")
