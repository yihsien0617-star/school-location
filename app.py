import streamlit as st
import pandas as pd
import json
import os
import time
import re
import requests
from datetime import datetime
from math import radians, cos, sin, asin, sqrt
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from io import BytesIO

# ============================================================
# 系統設定
# ============================================================
st.set_page_config(
    page_title="學校座標查詢系統 v3.1",
    page_icon="🏫",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_FILE = "school_coordinates_db.json"
BACKUP_DIR = "backups"
LOG_FILE = "search_log.json"
API_DELAY = 0.15
MAX_WORKERS = 4

# ============================================================
# 工具函數
# ============================================================
def load_database():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_database(db):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if os.path.exists(DB_FILE):
        backup_name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            import shutil
            shutil.copy2(DB_FILE, os.path.join(BACKUP_DIR, backup_name))
        except:
            pass
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def load_search_log():
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return []
    return []

def save_search_log(log):
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(log[-1000:], f, ensure_ascii=False, indent=2)

def normalize_school_name(name):
    if not isinstance(name, str):
        return ""
    name = name.strip()
    name = re.sub(r'\s+', '', name)
    replacements = {
        '臺北縣': '新北市',
        '桃園縣': '桃園市',
    }
    for old, new in replacements.items():
        if old in name:
            name = name.replace(old, new)
    name = name.replace('台', '臺')
    return name

def generate_search_variants(name):
    variants = [name]
    if '臺' in name:
        variants.append(name.replace('臺', '台'))
    if '台' in name:
        variants.append(name.replace('台', '臺'))
    
    prefixes = [
        '臺北市', '台北市', '新北市', '桃園市', '臺中市', '台中市',
        '臺南市', '台南市', '高雄市', '基隆市', '新竹市', '新竹縣',
        '苗栗縣', '彰化縣', '南投縣', '雲林縣', '嘉義市', '嘉義縣',
        '屏東縣', '宜蘭縣', '花蓮縣', '臺東縣', '台東縣', '澎湖縣',
        '金門縣', '連江縣'
    ]
    for prefix in prefixes:
        if name.startswith(prefix):
            short = name[len(prefix):]
            if short and len(short) >= 2:
                variants.append(short)
            break
    
    has_prefix = any(name.startswith(p) for p in prefixes)
    if not has_prefix and len(name) >= 2:
        for city in ['臺北市', '新北市', '桃園市', '臺中市', '臺南市', '高雄市']:
            variants.append(city + name)
    
    type_map = {
        '國小': ['國民小學', '小學'],
        '國民小學': ['國小', '小學'],
        '國中': ['國民中學', '中學'],
        '國民中學': ['國中'],
        '高中': ['高級中學', '高級中等學校'],
        '高級中學': ['高中'],
        '高工': ['高級工業職業學校'],
        '高商': ['高級商業職業學校'],
    }
    for short_form, long_forms in type_map.items():
        if short_form in name:
            for lf in long_forms:
                variants.append(name.replace(short_form, lf))
    
    return list(dict.fromkeys(variants))

def haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    return 6371 * c

# ============================================================
# Excel 工具
# ============================================================
def df_to_excel_bytes(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='資料')
    output.seek(0)
    return output.getvalue()

def db_to_excel_bytes(db):
    """座標資料庫 → Excel"""
    rows = []
    for name, info in sorted(db.items()):
        rows.append({
            '學校名稱': name,
            '緯度': info.get('lat'),
            '經度': info.get('lon'),
            '來源': info.get('source', ''),
            '查詢詞': info.get('query', ''),
            '更新時間': info.get('updated', '')
        })
    df = pd.DataFrame(rows)
    return df_to_excel_bytes(df)

def create_coord_sample_excel():
    """建立座標匯入範例 Excel"""
    data = {
        '學校名稱': ['臺北市大安國小', '新北市板橋國小', '桃園市中壢國小', '臺中市大同國小', '高雄市前鎮國小'],
        '緯度': [25.0263, 25.0145, 24.9575, 24.1436, 22.5947],
        '經度': [121.5437, 121.4590, 121.2253, 120.6811, 120.3300],
    }
    return pd.DataFrame(data)

def excel_to_db(df, col_name, col_lat, col_lon):
    """Excel DataFrame → 資料庫格式 dict"""
    imported = {}
    errors = []
    success = 0
    
    for idx, row in df.iterrows():
        name = str(row[col_name]).strip() if pd.notna(row[col_name]) else ''
        if not name:
            continue
        
        try:
            lat = float(row[col_lat])
            lon = float(row[col_lon])
        except (ValueError, TypeError):
            errors.append(f"第 {idx+2} 行：{name} 座標格式錯誤")
            continue
        
        if not (21.0 < lat < 26.0 and 119.0 < lon < 123.0):
            errors.append(f"第 {idx+2} 行：{name} 座標超出台灣範圍 ({lat}, {lon})")
            continue
        
        normalized = normalize_school_name(name)
        imported[normalized] = {
            'lat': lat,
            'lon': lon,
            'source': 'excel_import',
            'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        success += 1
    
    return imported, success, errors

def detect_columns(df):
    """自動偵測欄位"""
    result = {'id': None, 'name': None, 'school': None}
    
    id_keywords = ['學號', '座號', '編號', 'id', 'ID', '序號']
    for col in df.columns:
        for kw in id_keywords:
            if kw in str(col):
                result['id'] = col
                break
        if result['id']:
            break
    
    name_keywords = ['姓名', '名字', '學生', 'name']
    for col in df.columns:
        for kw in name_keywords:
            if kw in str(col) and '學校' not in str(col):
                result['name'] = col
                break
        if result['name']:
            break
    
    school_keywords = ['學校', '校名', '畢業', '國小', '國中', '就讀', 'school']
    for col in df.columns:
        for kw in school_keywords:
            if kw in str(col):
                result['school'] = col
                break
        if result['school']:
            break
    
    return result

def detect_coord_columns(df):
    """自動偵測座標欄位"""
    result = {'school': None, 'lat': None, 'lon': None}
    
    for col in df.columns:
        col_str = str(col).strip().lower()
        if any(kw in col_str for kw in ['學校', '校名', 'school', '名稱']):
            result['school'] = col
        elif any(kw in col_str for kw in ['緯度', 'lat', '北緯', 'latitude']):
            result['lat'] = col
        elif any(kw in col_str for kw in ['經度', 'lon', 'lng', '東經', 'longitude']):
            result['lon'] = col
    
    return result

def create_student_sample_excel():
    data = {
        '學號': ['S001', 'S002', 'S003', 'S004', 'S005'],
        '姓名': ['王小明', '李小華', '張小美', '陳小強', '林小玲'],
        '畢業國小': ['臺北市大安國小', '新北市板橋國小', '桃園市中壢國小', '臺中市西區大同國小', '高雄市前鎮國小']
    }
    return pd.DataFrame(data)

# ============================================================
# 地理編碼引擎
# ============================================================
class GeocodingEngine:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SchoolGeocoder/3.1 (Educational Research)'
        })
        self.lock = threading.Lock()
        self.search_log = load_search_log()
    
    def _log_search(self, name, result, engine, elapsed):
        entry = {
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'name': name,
            'success': result is not None,
            'engine': engine,
            'elapsed': round(elapsed, 3)
        }
        if result:
            entry['lat'] = result[0]
            entry['lon'] = result[1]
        with self.lock:
            self.search_log.append(entry)
    
    def search_nominatim(self, query):
        try:
            url = "https://nominatim.openstreetmap.org/search"
            params = {'q': query, 'format': 'json', 'limit': 3, 'countrycodes': 'tw', 'addressdetails': 1}
            resp = self.session.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    for item in data:
                        if item.get('class') == 'amenity' and item.get('type') == 'school':
                            return (float(item['lat']), float(item['lon']))
                    return (float(data[0]['lat']), float(data[0]['lon']))
        except:
            pass
        return None
    
    def search_photon(self, query):
        try:
            url = "https://photon.komoot.io/api/"
            params = {'q': query + ' Taiwan', 'limit': 3, 'lang': 'zh'}
            resp = self.session.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                features = data.get('features', [])
                if features:
                    for f in features:
                        if f.get('properties', {}).get('osm_value') == 'school':
                            coords = f['geometry']['coordinates']
                            return (coords[1], coords[0])
                    coords = features[0]['geometry']['coordinates']
                    return (coords[1], coords[0])
        except:
            pass
        return None
    
    def search_osm_structured(self, query):
        try:
            url = "https://nominatim.openstreetmap.org/search"
            params = {'q': query, 'format': 'json', 'limit': 5, 'countrycodes': 'tw'}
            resp = self.session.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for item in data:
                    lat, lon = float(item['lat']), float(item['lon'])
                    if 21.5 < lat < 25.5 and 119.5 < lon < 122.5:
                        return (lat, lon)
        except:
            pass
        return None
    
    def geocode_school(self, school_name, db):
        start_time = time.time()
        normalized = normalize_school_name(school_name)
        
        if normalized in db:
            entry = db[normalized]
            elapsed = time.time() - start_time
            self._log_search(school_name, (entry['lat'], entry['lon']), 'database', elapsed)
            return {'name': school_name, 'normalized': normalized,
                    'lat': entry['lat'], 'lon': entry['lon'], 'source': 'database', 'success': True}
        
        variants = generate_search_variants(normalized)
        for v in variants:
            v_norm = normalize_school_name(v)
            if v_norm in db:
                entry = db[v_norm]
                elapsed = time.time() - start_time
                self._log_search(school_name, (entry['lat'], entry['lon']), 'database_variant', elapsed)
                return {'name': school_name, 'normalized': normalized,
                        'lat': entry['lat'], 'lon': entry['lon'], 'source': 'database (variant)', 'success': True}
        
        engines = [
            ('Nominatim', self.search_nominatim),
            ('Photon', self.search_photon),
            ('OSM', self.search_osm_structured),
        ]
        
        for engine_name, engine_func in engines:
            for variant in variants[:4]:
                try:
                    result = engine_func(variant)
                    if result:
                        lat, lon = result
                        if 21.5 < lat < 25.5 and 119.5 < lon < 122.5:
                            db[normalized] = {
                                'lat': lat, 'lon': lon, 'source': engine_name,
                                'query': variant, 'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            }
                            elapsed = time.time() - start_time
                            self._log_search(school_name, (lat, lon), engine_name, elapsed)
                            return {'name': school_name, 'normalized': normalized,
                                    'lat': lat, 'lon': lon, 'source': engine_name, 'success': True}
                    time.sleep(API_DELAY)
                except:
                    time.sleep(API_DELAY)
        
        elapsed = time.time() - start_time
        self._log_search(school_name, None, 'all_failed', elapsed)
        return {'name': school_name, 'normalized': normalized,
                'lat': None, 'lon': None, 'source': 'not_found', 'success': False}

# ============================================================
# 主介面
# ============================================================
def main():
    # 側邊欄
    with st.sidebar:
        st.title("🏫 學校座標查詢系統")
        st.caption("v3.1 Excel 匯入匯出版")
        st.divider()
        
        db = load_database()
        st.metric("📦 資料庫學校數", f"{len(db)} 所")
        
        if db:
            sources = {}
            for v in db.values():
                src = v.get('source', 'unknown')
                sources[src] = sources.get(src, 0) + 1
            with st.expander("資料來源分布"):
                for src, cnt in sorted(sources.items(), key=lambda x: -x[1]):
                    st.write(f"- {src}: {cnt}")
        
        st.divider()
        
        # ============================
        # 座標資料庫 Excel 匯出
        # ============================
        st.markdown("### 📥 匯出座標資料庫")
        if db:
            st.download_button(
                "📥 下載座標資料庫 (Excel)",
                data=db_to_excel_bytes(db),
                file_name=f"座標資料庫_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
            st.caption(f"共 {len(db)} 筆座標資料")
        else:
            st.info("資料庫為空")
        
        st.divider()
        
        # ============================
        # 座標資料庫 Excel 匯入
        # ============================
        st.markdown("### 📤 匯入座標資料庫")
        
        # 範例下載
        sample_coord_df = create_coord_sample_excel()
        st.download_button(
            "📋 下載匯入範例 (Excel)",
            data=df_to_excel_bytes(sample_coord_df),
            file_name="座標匯入範例.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
        
        uploaded_coord = st.file_uploader(
            "上傳座標 Excel",
            type=['xlsx', 'xls'],
            key='coord_import',
            help="需包含：學校名稱、緯度、經度 三個欄位"
        )
        
        if uploaded_coord:
            try:
                if uploaded_coord.name.endswith('.xls'):
                    df_coord = pd.read_excel(uploaded_coord, engine='xlrd')
                else:
                    df_coord = pd.read_excel(uploaded_coord, engine='openpyxl')
                
                st.success(f"讀取到 {len(df_coord)} 筆，{len(df_coord.columns)} 欄")
                
                # 自動偵測欄位
                detected = detect_coord_columns(df_coord)
                cols = list(df_coord.columns)
                
                coord_school_col = st.selectbox(
                    "學校名稱欄位",
                    cols,
                    index=cols.index(detected['school']) if detected['school'] in cols else 0,
                    key='coord_school_col'
                )
                coord_lat_col = st.selectbox(
                    "緯度欄位",
                    cols,
                    index=cols.index(detected['lat']) if detected['lat'] in cols else min(1, len(cols)-1),
                    key='coord_lat_col'
                )
                coord_lon_col = st.selectbox(
                    "經度欄位",
                    cols,
                    index=cols.index(detected['lon']) if detected['lon'] in cols else min(2, len(cols)-1),
                    key='coord_lon_col'
                )
                
                import_mode = st.radio(
                    "匯入模式",
                    ["合併（保留既有，新增/覆蓋匯入的）", "僅新增（不覆蓋已有）"],
                    key='import_mode'
                )
                
                if st.button("✅ 確認匯入", type="primary", use_container_width=True):
                    imported, success_cnt, errors = excel_to_db(
                        df_coord, coord_school_col, coord_lat_col, coord_lon_col
                    )
                    
                    if import_mode.startswith("僅新增"):
                        new_count = 0
                        for k, v in imported.items():
                            if k not in db:
                                db[k] = v
                                new_count += 1
                        save_database(db)
                        st.success(f"✅ 新增 {new_count} 筆（跳過已有 {success_cnt - new_count} 筆）")
                    else:
                        db.update(imported)
                        save_database(db)
                        st.success(f"✅ 匯入成功！合併 {success_cnt} 筆，資料庫共 {len(db)} 筆")
                    
                    if errors:
                        with st.expander(f"⚠️ {len(errors)} 筆錯誤"):
                            for e in errors:
                                st.write(e)
                    
                    st.rerun()
            
            except Exception as e:
                st.error(f"讀取失敗：{e}")
        
        st.divider()
        
        # JSON 備份（進階）
        with st.expander("🔧 JSON 備份（進階）"):
            if db:
                db_json = json.dumps(db, ensure_ascii=False, indent=2)
                st.download_button(
                    "JSON 匯出",
                    data=db_json,
                    file_name=f"school_db_{datetime.now().strftime('%Y%m%d')}.json",
                    mime="application/json",
                    use_container_width=True
                )
            
            uploaded_json = st.file_uploader("JSON 匯入", type=['json'], key='json_import')
            if uploaded_json:
                try:
                    imported = json.loads(uploaded_json.read().decode('utf-8'))
                    if isinstance(imported, dict):
                        db.update(imported)
                        save_database(db)
                        st.success(f"✅ JSON 匯入成功！共 {len(db)} 筆")
                        st.rerun()
                except Exception as e:
                    st.error(f"JSON 匯入失敗：{e}")
    
    # ============================
    # 主頁面標籤
    # ============================
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📤 上傳學生資料",
        "🗺️ 地圖",
        "📊 統計分析",
        "✏️ 手動編輯",
        "🔧 進階工具"
    ])
    
    # ========================================
    # Tab 1: 上傳學生資料
    # ========================================
    with tab1:
        st.header("📤 上傳學生資料")
        
        col_s1, col_s2 = st.columns([1, 3])
        with col_s1:
            sample_df = create_student_sample_excel()
            st.download_button(
                "📋 下載範例 Excel",
                data=df_to_excel_bytes(sample_df),
                file_name="範例_學生資料.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        with col_s2:
            st.info("💡 上傳包含學校名稱的 Excel，系統自動查詢座標並匯出結果")
        
        uploaded_file = st.file_uploader(
            "上傳學生資料",
            type=['xlsx', 'xls'],
            help="支援 .xlsx 和 .xls 格式"
        )
        
        if uploaded_file:
            try:
                if uploaded_file.name.endswith('.xls'):
                    df_raw = pd.read_excel(uploaded_file, engine='xlrd')
                else:
                    df_raw = pd.read_excel(uploaded_file, engine='openpyxl')
                
                st.success(f"✅ 讀取成功：{len(df_raw)} 筆，{len(df_raw.columns)} 欄")
                
                with st.expander("📋 資料預覽（前 10 筆）", expanded=True):
                    st.dataframe(df_raw.head(10), use_container_width=True)
                
                # 欄位對應
                st.subheader("🔗 欄位對應")
                detected = detect_columns(df_raw)
                
                col_m1, col_m2, col_m3 = st.columns(3)
                columns_list = ['（不使用）'] + list(df_raw.columns)
                
                with col_m1:
                    id_default = columns_list.index(detected['id']) if detected['id'] in columns_list else 0
                    id_col = st.selectbox("學號/座號", columns_list, index=id_default)
                with col_m2:
                    name_default = columns_list.index(detected['name']) if detected['name'] in columns_list else 0
                    name_col = st.selectbox("姓名", columns_list, index=name_default)
                with col_m3:
                    school_default = columns_list.index(detected['school']) if detected['school'] in columns_list else 0
                    school_col = st.selectbox("⭐ 學校名稱（必選）", columns_list, index=school_default)
                
                if school_col == '（不使用）':
                    st.warning("⚠️ 請選擇「學校名稱」欄位")
                else:
                    result_data = []
                    for idx, row in df_raw.iterrows():
                        entry = {'原始索引': idx}
                        if id_col != '（不使用）':
                            entry['學號'] = row[id_col]
                        if name_col != '（不使用）':
                            entry['姓名'] = row[name_col]
                        entry['學校名稱'] = str(row[school_col]).strip() if pd.notna(row[school_col]) else ''
                        result_data.append(entry)
                    
                    df_work = pd.DataFrame(result_data)
                    df_work = df_work[df_work['學校名稱'].str.len() > 0]
                    st.write(f"📊 有效資料：**{len(df_work)}** 筆")
                    
                    col_b1, col_b2 = st.columns([1, 3])
                    with col_b1:
                        start_search = st.button("🚀 開始查詢座標", type="primary", use_container_width=True)
                    with col_b2:
                        workers = st.slider("並行數", 1, 6, MAX_WORKERS)
                    
                    if start_search:
                        db = load_database()
                        engine = GeocodingEngine()
                        unique_schools = df_work['學校名稱'].unique().tolist()
                        st.write(f"🏫 不重複學校：**{len(unique_schools)}** 所")
                        
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        
                        school_results = {}
                        success_count = 0
                        fail_count = 0
                        start_time = time.time()
                        to_search = []
                        
                        for school in unique_schools:
                            normalized = normalize_school_name(school)
                            found = False
                            if normalized in db:
                                school_results[school] = {
                                    'lat': db[normalized]['lat'],
                                    'lon': db[normalized]['lon'],
                                    'source': 'database'
                                }
                                success_count += 1
                                found = True
                            if not found:
                                for v in generate_search_variants(normalized):
                                    v_norm = normalize_school_name(v)
                                    if v_norm in db:
                                        school_results[school] = {
                                            'lat': db[v_norm]['lat'],
                                            'lon': db[v_norm]['lon'],
                                            'source': 'database (variant)'
                                        }
                                        success_count += 1
                                        found = True
                                        break
                            if not found:
                                to_search.append(school)
                        
                        status_text.write(f"📦 資料庫命中：{success_count} | 需查詢：{len(to_search)}")
                        
                        if to_search:
                            completed = 0
                            with ThreadPoolExecutor(max_workers=workers) as executor:
                                futures = {executor.submit(engine.geocode_school, s, db): s for s in to_search}
                                for future in as_completed(futures):
                                    result = future.result()
                                    completed += 1
                                    if result['success']:
                                        school_results[result['name']] = {
                                            'lat': result['lat'], 'lon': result['lon'], 'source': result['source']
                                        }
                                        success_count += 1
                                    else:
                                        fail_count += 1
                                    
                                    progress_bar.progress(min((success_count + fail_count) / len(unique_schools), 1.0))
                                    elapsed = time.time() - start_time
                                    status_text.write(
                                        f"⏱️ {elapsed:.1f}s | ✅ {success_count} | ❌ {fail_count} | 🔄 {completed}/{len(to_search)}"
                                    )
                        
                        save_database(db)
                        save_search_log(engine.search_log)
                        progress_bar.progress(1.0)
                        total_time = time.time() - start_time
                        status_text.write(f"🎉 完成！{total_time:.1f}秒 | ✅ {success_count} | ❌ {fail_count}")
                        
                        lat_list, lon_list, source_list = [], [], []
                        for _, row in df_work.iterrows():
                            school = row['學校名稱']
                            if school in school_results:
                                lat_list.append(school_results[school]['lat'])
                                lon_list.append(school_results[school]['lon'])
                                source_list.append(school_results[school]['source'])
                            else:
                                lat_list.append(None)
                                lon_list.append(None)
                                source_list.append('not_found')
                        
                        df_work['緯度'] = lat_list
                        df_work['經度'] = lon_list
                        df_work['來源'] = source_list
                        
                        st.session_state['result_df'] = df_work
                        st.session_state['school_results'] = school_results
                        
                        st.subheader("📋 查詢結果")
                        df_success = df_work[df_work['緯度'].notna()]
                        df_fail = df_work[df_work['緯度'].isna()]
                        
                        tab_s, tab_f = st.tabs([f"✅ 成功 ({len(df_success)})", f"❌ 未找到 ({len(df_fail)})"])
                        with tab_s:
                            if len(df_success) > 0:
                                st.dataframe(df_success, use_container_width=True)
                        with tab_f:
                            if len(df_fail) > 0:
                                st.dataframe(df_fail, use_container_width=True)
                                st.info("💡 可在「手動編輯」分頁手動輸入座標")
                        
                        st.subheader("📥 下載結果")
                        col_d1, col_d2 = st.columns(2)
                        with col_d1:
                            st.download_button(
                                "📥 完整結果 (Excel)",
                                data=df_to_excel_bytes(df_work),
                                file_name=f"座標結果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True
                            )
                        with col_d2:
                            if len(df_fail) > 0:
                                st.download_button(
                                    "📥 未找到清單 (Excel)",
                                    data=df_to_excel_bytes(df_fail),
                                    file_name=f"未找到_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                    use_container_width=True
                                )
            except Exception as e:
                st.error(f"❌ 讀取失敗：{e}")
                st.info("請確認是有效的 Excel 格式（.xlsx 或 .xls）")
    
    # ========================================
    # Tab 2: 地圖
    # ========================================
    with tab2:
        st.header("🗺️ 學校分布地圖")
        
        if 'result_df' not in st.session_state:
            st.info("📤 請先在「上傳學生資料」查詢座標")
        else:
            df_map = st.session_state['result_df']
            df_valid = df_map[df_map['緯度'].notna() & df_map['經度'].notna()].copy()
            
            if len(df_valid) == 0:
                st.warning("沒有可顯示的座標")
            else:
                try:
                    import folium
                    from folium.plugins import MarkerCluster, HeatMap
                    from streamlit_folium import st_folium
                    
                    map_type = st.radio("地圖模式", ["標記地圖", "聚合地圖", "熱力圖"], horizontal=True)
                    
                    center_lat = df_valid['緯度'].mean()
                    center_lon = df_valid['經度'].mean()
                    m = folium.Map(location=[center_lat, center_lon], zoom_start=8)
                    
                    if map_type == "標記地圖":
                        for _, row in df_valid.iterrows():
                            popup = f"🏫 {row['學校名稱']}"
                            if '姓名' in row and pd.notna(row.get('姓名')):
                                popup += f"<br>👤 {row['姓名']}"
                            folium.CircleMarker(
                                location=[row['緯度'], row['經度']], radius=6,
                                popup=folium.Popup(popup, max_width=200),
                                tooltip=row['學校名稱'],
                                color='#3388ff', fill=True, fillColor='#3388ff', fillOpacity=0.7
                            ).add_to(m)
                    elif map_type == "聚合地圖":
                        cluster = MarkerCluster().add_to(m)
                        for _, row in df_valid.iterrows():
                            popup = f"🏫 {row['學校名稱']}"
                            if '姓名' in row and pd.notna(row.get('姓名')):
                                popup += f"<br>👤 {row['姓名']}"
                            folium.Marker(
                                location=[row['緯度'], row['經度']],
                                popup=folium.Popup(popup, max_width=200),
                                tooltip=row['學校名稱']
                            ).add_to(cluster)
                    else:
                        HeatMap(df_valid[['緯度', '經度']].values.tolist(), radius=15).add_to(m)
                    
                    st_folium(m, width=None, height=600)
                    st.caption(f"📍 顯示 {len(df_valid)} 個地點")
                except ImportError:
                    st.error("需安裝：`pip install folium streamlit-folium`")
    
    # ========================================
    # Tab 3: 統計分析
    # ========================================
    with tab3:
        st.header("📊 統計分析")
        
        if 'result_df' not in st.session_state:
            st.info("📤 請先查詢座標")
        else:
            df_stats = st.session_state['result_df']
            df_valid = df_stats[df_stats['緯度'].notna()].copy()
            
            if len(df_valid) == 0:
                st.warning("沒有可分析的資料")
            else:
                c1, c2, c3, c4 = st.columns(4)
                with c1: st.metric("總筆數", len(df_stats))
                with c2: st.metric("成功查詢", len(df_valid))
                with c3: st.metric("不重複學校", df_valid['學校名稱'].nunique())
                with c4:
                    rate = len(df_valid) / len(df_stats) * 100 if len(df_stats) > 0 else 0
                    st.metric("成功率", f"{rate:.1f}%")
                
                st.divider()
                st.subheader("🏫 各學校學生數")
                school_counts = df_valid['學校名稱'].value_counts()
                st.bar_chart(school_counts.head(20))
                
                with st.expander(f"完整列表（{len(school_counts)} 所）"):
                    st.dataframe(
                        pd.DataFrame({'學校': school_counts.index, '學生數': school_counts.values}),
                        use_container_width=True
                    )
                
                st.divider()
                st.subheader("📍 區域分布")
                
                def get_region(name):
                    regions = {
                        '臺北': '臺北市', '台北': '臺北市', '新北': '新北市', '桃園': '桃園市',
                        '臺中': '臺中市', '台中': '臺中市', '臺南': '臺南市', '台南': '臺南市',
                        '高雄': '高雄市', '基隆': '基隆市', '新竹': '新竹', '苗栗': '苗栗縣',
                        '彰化': '彰化縣', '南投': '南投縣', '雲林': '雲林縣', '嘉義': '嘉義',
                        '屏東': '屏東縣', '宜蘭': '宜蘭縣', '花蓮': '花蓮縣',
                        '臺東': '臺東縣', '台東': '臺東縣', '澎湖': '澎湖縣',
                        '金門': '金門縣', '連江': '連江縣',
                    }
                    for key, region in regions.items():
                        if key in str(name):
                            return region
                    return '其他'
                
                df_valid['區域'] = df_valid['學校名稱'].apply(get_region)
                st.bar_chart(df_valid['區域'].value_counts())
                
                st.divider()
                st.subheader("📏 距離分析")
                ref_lat = st.number_input("參考點緯度", value=24.9936, format="%.4f")
                ref_lon = st.number_input("參考點經度", value=121.3010, format="%.4f")
                
                if st.button("計算距離"):
                    distances = [haversine(ref_lon, ref_lat, row['經度'], row['緯度']) for _, row in df_valid.iterrows()]
                    df_valid['距離(km)'] = [round(d, 2) for d in distances]
                    st.write(f"平均：**{sum(distances)/len(distances):.2f}** km | "
                            f"最近：**{min(distances):.2f}** km | 最遠：**{max(distances):.2f}** km")
                    
                    bins = [0, 5, 10, 20, 50, 100, float('inf')]
                    labels = ['0-5km', '5-10km', '10-20km', '20-50km', '50-100km', '100km+']
                    df_valid['距離區間'] = pd.cut(distances, bins=bins, labels=labels)
                    st.bar_chart(df_valid['距離區間'].value_counts().sort_index())
    
    # ========================================
    # Tab 4: 手動編輯
    # ========================================
    with tab4:
        st.header("✏️ 手動編輯座標")
        
        db = load_database()
        
        st.subheader("➕ 新增/修改")
        ce1, ce2, ce3 = st.columns([2, 1, 1])
        with ce1: edit_name = st.text_input("學校名稱", placeholder="例：臺北市大安國小")
        with ce2: edit_lat = st.number_input("緯度", value=25.0330, format="%.6f", key='ed_lat')
        with ce3: edit_lon = st.number_input("經度", value=121.5654, format="%.6f", key='ed_lon')
        
        if st.button("💾 儲存", type="primary"):
            if edit_name:
                normalized = normalize_school_name(edit_name)
                db[normalized] = {
                    'lat': edit_lat, 'lon': edit_lon, 'source': 'manual',
                    'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                save_database(db)
                st.success(f"✅ 已儲存：{edit_name}")
                st.rerun()
        
        st.divider()
        
        if 'result_df' in st.session_state:
            df_fail = st.session_state['result_df'][st.session_state['result_df']['緯度'].isna()]
            if len(df_fail) > 0:
                st.subheader(f"❌ 未找到的學校（{len(df_fail)} 筆）")
                for school in df_fail['學校名稱'].unique():
                    with st.expander(f"🏫 {school}"):
                        cf1, cf2 = st.columns(2)
                        with cf1: fix_lat = st.number_input("緯度", value=25.0, format="%.6f", key=f"fl_{school}")
                        with cf2: fix_lon = st.number_input("經度", value=121.5, format="%.6f", key=f"fo_{school}")
                        st.markdown(f"🔍 [Google Maps 搜尋](https://www.google.com/maps/search/{school})")
                        if st.button(f"儲存 {school}", key=f"sv_{school}"):
                            db[normalize_school_name(school)] = {
                                'lat': fix_lat, 'lon': fix_lon, 'source': 'manual_fix',
                                'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            }
                            save_database(db)
                            st.success(f"✅ 已儲存 {school}")
        
        st.divider()
        st.subheader("📖 瀏覽資料庫")
        if db:
            search_term = st.text_input("🔍 搜尋", placeholder="輸入關鍵字...")
            filtered = {k: v for k, v in db.items() if not search_term or search_term in k}
            if filtered:
                st.dataframe(
                    pd.DataFrame([
                        {'學校': k, '緯度': v['lat'], '經度': v['lon'],
                         '來源': v.get('source', ''), '更新': v.get('updated', '')}
                        for k, v in sorted(filtered.items())
                    ]),
                    use_container_width=True
                )
                st.caption(f"顯示 {len(filtered)} / {len(db)} 筆")
            
            with st.expander("🗑️ 刪除"):
                del_name = st.selectbox("選擇學校", sorted(db.keys()))
                if st.button("刪除"):
                    del db[del_name]
                    save_database(db)
                    st.success(f"已刪除：{del_name}")
                    st.rerun()
    
    # ========================================
    # Tab 5: 進階工具
    # ========================================
    with tab5:
        st.header("🔧 進階工具")
        
        st.subheader("📝 批次貼上查詢")
        batch_text = st.text_area("學校名稱（每行一個）", height=150,
                                   placeholder="臺北市大安國小\n新北市板橋國小")
        
        if st.button("🔍 批次查詢") and batch_text.strip():
            schools = [s.strip() for s in batch_text.strip().split('\n') if s.strip()]
            db = load_database()
            engine = GeocodingEngine()
            progress = st.progress(0)
            results = []
            for i, school in enumerate(schools):
                results.append(engine.geocode_school(school, db))
                progress.progress((i + 1) / len(schools))
            save_database(db)
            save_search_log(engine.search_log)
            
            df_batch = pd.DataFrame([
                {'學校': r['name'], '緯度': r['lat'], '經度': r['lon'],
                 '來源': r['source'], '狀態': '✅' if r['success'] else '❌'}
                for r in results
            ])
            st.dataframe(df_batch, use_container_width=True)
            st.download_button(
                "📥 下載結果 (Excel)",
                data=df_to_excel_bytes(df_batch),
                file_name=f"批次結果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        
        st.divider()
        st.subheader("📜 搜尋記錄")
        search_log = load_search_log()
        if search_log:
            st.write(f"共 {len(search_log)} 筆")
            st.dataframe(pd.DataFrame(search_log[-50:][::-1]), use_container_width=True)
            success_c = sum(1 for l in search_log if l.get('success'))
            st.write(f"成功率：{success_c}/{len(search_log)} ({success_c/len(search_log)*100:.1f}%)")
        else:
            st.info("尚無記錄")
        
        st.divider()
        st.subheader("🏥 資料庫健康檢查")
        if st.button("執行檢查"):
            db = load_database()
            issues = []
            for name, info in db.items():
                lat, lon = info.get('lat'), info.get('lon')
                if lat is None or lon is None:
                    issues.append(f"❌ {name}: 缺座標")
                elif not (21.5 < lat < 25.5):
                    issues.append(f"⚠️ {name}: 緯度 {lat} 超出範圍")
                elif not (119.5 < lon < 122.5):
                    issues.append(f"⚠️ {name}: 經度 {lon} 超出範圍")
            if issues:
                st.warning(f"發現 {len(issues)} 個問題：")
                for issue in issues:
                    st.write(issue)
            else:
                st.success(f"✅ 健康！共 {len(db)} 筆均正常")

if __name__ == '__main__':
    main()
