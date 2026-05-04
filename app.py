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
    page_title="學校座標查詢系統 v3.2",
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
    replacements = {'臺北縣': '新北市', '桃園縣': '桃園市'}
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
        '國小': ['國民小學', '小學'], '國民小學': ['國小', '小學'],
        '國中': ['國民中學', '中學'], '國民中學': ['國中'],
        '高中': ['高級中學', '高級中等學校'], '高級中學': ['高中'],
        '高工': ['高級工業職業學校'], '高商': ['高級商業職業學校'],
    }
    for short_form, long_forms in type_map.items():
        if short_form in name:
            for lf in long_forms:
                variants.append(name.replace(short_form, lf))
    
    return list(dict.fromkeys(variants))

def haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    return 6371 * 2 * asin(sqrt(a))

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
    rows = []
    for name, info in sorted(db.items()):
        rows.append({
            '學校名稱': name, '緯度': info.get('lat'), '經度': info.get('lon'),
            '來源': info.get('source', ''), '查詢詞': info.get('query', ''),
            '更新時間': info.get('updated', '')
        })
    return df_to_excel_bytes(pd.DataFrame(rows))

def create_coord_sample_excel():
    data = {
        '學校名稱': ['臺北市大安國小', '新北市板橋國小', '桃園市中壢國小'],
        '緯度': [25.0263, 25.0145, 24.9575],
        '經度': [121.5437, 121.4590, 121.2253],
    }
    return pd.DataFrame(data)

def excel_to_db(df, col_name, col_lat, col_lon):
    imported, errors, success = {}, [], 0
    for idx, row in df.iterrows():
        name = str(row[col_name]).strip() if pd.notna(row[col_name]) else ''
        if not name:
            continue
        try:
            lat, lon = float(row[col_lat]), float(row[col_lon])
        except (ValueError, TypeError):
            errors.append(f"第 {idx+2} 行：{name} 座標格式錯誤")
            continue
        if not (21.0 < lat < 26.0 and 119.0 < lon < 123.0):
            errors.append(f"第 {idx+2} 行：{name} 座標超出台灣範圍 ({lat}, {lon})")
            continue
        imported[normalize_school_name(name)] = {
            'lat': lat, 'lon': lon, 'source': 'excel_import',
            'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        success += 1
    return imported, success, errors

def detect_columns(df):
    result = {'id': None, 'name': None, 'school': None}
    for col in df.columns:
        col_s = str(col)
        if not result['id'] and any(k in col_s for k in ['學號', '座號', '編號', 'id', 'ID', '序號']):
            result['id'] = col
        if not result['name'] and any(k in col_s for k in ['姓名', '名字', '學生', 'name']) and '學校' not in col_s:
            result['name'] = col
        if not result['school'] and any(k in col_s for k in ['學校', '校名', '畢業', '國小', '國中', '就讀', 'school']):
            result['school'] = col
    return result

def detect_coord_columns(df):
    result = {'school': None, 'lat': None, 'lon': None}
    for col in df.columns:
        col_s = str(col).strip().lower()
        if any(k in col_s for k in ['學校', '校名', 'school', '名稱']):
            result['school'] = col
        elif any(k in col_s for k in ['緯度', 'lat', '北緯', 'latitude']):
            result['lat'] = col
        elif any(k in col_s for k in ['經度', 'lon', 'lng', '東經', 'longitude']):
            result['lon'] = col
    return result

# ============================================================
# 地理編碼引擎
# ============================================================
class GeocodingEngine:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'SchoolGeocoder/3.2 (Educational Research)'})
        self.lock = threading.Lock()
        self.search_log = load_search_log()
    
    def _log(self, name, result, engine, elapsed):
        entry = {
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'name': name, 'success': result is not None,
            'engine': engine, 'elapsed': round(elapsed, 3)
        }
        if result:
            entry['lat'], entry['lon'] = result
        with self.lock:
            self.search_log.append(entry)
    
    def search_nominatim(self, query):
        try:
            resp = self.session.get(
                "https://nominatim.openstreetmap.org/search",
                params={'q': query, 'format': 'json', 'limit': 3, 'countrycodes': 'tw', 'addressdetails': 1},
                timeout=10
            )
            if resp.status_code == 200 and resp.json():
                data = resp.json()
                for item in data:
                    if item.get('class') == 'amenity' and item.get('type') == 'school':
                        return (float(item['lat']), float(item['lon']))
                return (float(data[0]['lat']), float(data[0]['lon']))
        except:
            pass
        return None
    
    def search_photon(self, query):
        try:
            resp = self.session.get(
                "https://photon.komoot.io/api/",
                params={'q': query + ' Taiwan', 'limit': 3, 'lang': 'zh'},
                timeout=10
            )
            if resp.status_code == 200:
                features = resp.json().get('features', [])
                if features:
                    for f in features:
                        if f.get('properties', {}).get('osm_value') == 'school':
                            c = f['geometry']['coordinates']
                            return (c[1], c[0])
                    c = features[0]['geometry']['coordinates']
                    return (c[1], c[0])
        except:
            pass
        return None
    
    def search_osm(self, query):
        try:
            resp = self.session.get(
                "https://nominatim.openstreetmap.org/search",
                params={'q': query, 'format': 'json', 'limit': 5, 'countrycodes': 'tw'},
                timeout=10
            )
            if resp.status_code == 200:
                for item in resp.json():
                    lat, lon = float(item['lat']), float(item['lon'])
                    if 21.5 < lat < 25.5 and 119.5 < lon < 122.5:
                        return (lat, lon)
        except:
            pass
        return None
    
    def geocode(self, school_name, db):
        t0 = time.time()
        normalized = normalize_school_name(school_name)
        
        # 資料庫查找
        if normalized in db:
            e = db[normalized]
            self._log(school_name, (e['lat'], e['lon']), 'database', time.time()-t0)
            return {'name': school_name, 'lat': e['lat'], 'lon': e['lon'], 'source': 'database', 'success': True}
        
        for v in generate_search_variants(normalized):
            vn = normalize_school_name(v)
            if vn in db:
                e = db[vn]
                self._log(school_name, (e['lat'], e['lon']), 'database_variant', time.time()-t0)
                return {'name': school_name, 'lat': e['lat'], 'lon': e['lon'], 'source': 'database', 'success': True}
        
        # 線上查詢
        engines = [('Nominatim', self.search_nominatim), ('Photon', self.search_photon), ('OSM', self.search_osm)]
        variants = generate_search_variants(normalized)[:4]
        
        for eng_name, eng_func in engines:
            for variant in variants:
                try:
                    result = eng_func(variant)
                    if result:
                        lat, lon = result
                        if 21.5 < lat < 25.5 and 119.5 < lon < 122.5:
                            db[normalized] = {
                                'lat': lat, 'lon': lon, 'source': eng_name,
                                'query': variant, 'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            }
                            self._log(school_name, (lat, lon), eng_name, time.time()-t0)
                            return {'name': school_name, 'lat': lat, 'lon': lon, 'source': eng_name, 'success': True}
                    time.sleep(API_DELAY)
                except:
                    time.sleep(API_DELAY)
        
        self._log(school_name, None, 'all_failed', time.time()-t0)
        return {'name': school_name, 'lat': None, 'lon': None, 'source': 'not_found', 'success': False}

# ============================================================
# 主介面
# ============================================================
def main():
    # ====== 側邊欄 ======
    with st.sidebar:
        st.title("🏫 學校座標查詢系統")
        st.caption("v3.2 保留原始格式版")
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
        
        # 座標資料庫 匯出
        st.markdown("### 📥 匯出座標資料庫")
        if db:
            st.download_button(
                "📥 下載座標資料庫 (Excel)",
                data=db_to_excel_bytes(db),
                file_name=f"座標資料庫_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        else:
            st.info("資料庫為空")
        
        st.divider()
        
        # 座標資料庫 匯入
        st.markdown("### 📤 匯入座標資料庫")
        sample_coord_df = create_coord_sample_excel()
        st.download_button(
            "📋 下載匯入範例",
            data=df_to_excel_bytes(sample_coord_df),
            file_name="座標匯入範例.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
        
        uploaded_coord = st.file_uploader("上傳座標 Excel", type=['xlsx', 'xls'], key='coord_import')
        
        if uploaded_coord:
            try:
                eng = 'xlrd' if uploaded_coord.name.endswith('.xls') else 'openpyxl'
                df_coord = pd.read_excel(uploaded_coord, engine=eng)
                st.success(f"讀取 {len(df_coord)} 筆")
                
                detected = detect_coord_columns(df_coord)
                cols = list(df_coord.columns)
                
                coord_school_col = st.selectbox("學校名稱欄位", cols,
                    index=cols.index(detected['school']) if detected['school'] in cols else 0, key='cs_col')
                coord_lat_col = st.selectbox("緯度欄位", cols,
                    index=cols.index(detected['lat']) if detected['lat'] in cols else min(1, len(cols)-1), key='cl_col')
                coord_lon_col = st.selectbox("經度欄位", cols,
                    index=cols.index(detected['lon']) if detected['lon'] in cols else min(2, len(cols)-1), key='co_col')
                
                import_mode = st.radio("匯入模式", ["合併（覆蓋）", "僅新增（不覆蓋）"], key='imp_mode')
                
                if st.button("✅ 確認匯入", type="primary", use_container_width=True):
                    imported, success_cnt, errors = excel_to_db(df_coord, coord_school_col, coord_lat_col, coord_lon_col)
                    if import_mode.startswith("僅新增"):
                        new_c = sum(1 for k in imported if k not in db)
                        for k, v in imported.items():
                            if k not in db:
                                db[k] = v
                        save_database(db)
                        st.success(f"✅ 新增 {new_c} 筆")
                    else:
                        db.update(imported)
                        save_database(db)
                        st.success(f"✅ 合併 {success_cnt} 筆，共 {len(db)} 筆")
                    if errors:
                        with st.expander(f"⚠️ {len(errors)} 筆錯誤"):
                            for e in errors:
                                st.write(e)
                    st.rerun()
            except Exception as e:
                st.error(f"讀取失敗：{e}")
        
        st.divider()
        with st.expander("🔧 JSON 備份（進階）"):
            if db:
                st.download_button("JSON 匯出", data=json.dumps(db, ensure_ascii=False, indent=2),
                    file_name=f"school_db_{datetime.now().strftime('%Y%m%d')}.json", mime="application/json",
                    use_container_width=True)
            up_json = st.file_uploader("JSON 匯入", type=['json'], key='json_imp')
            if up_json:
                try:
                    imp = json.loads(up_json.read().decode('utf-8'))
                    if isinstance(imp, dict):
                        db.update(imp)
                        save_database(db)
                        st.success(f"✅ JSON 匯入 {len(db)} 筆")
                        st.rerun()
                except Exception as e:
                    st.error(f"失敗：{e}")
    
    # ====== 主頁面 ======
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📤 上傳學生資料", "🗺️ 地圖", "📊 統計分析", "✏️ 手動編輯", "🔧 進階工具"
    ])
    
    # ========================================
    # Tab 1: 上傳 → 保留原始格式 + 附加經緯度
    # ========================================
    with tab1:
        st.header("📤 上傳學生資料")
        
        st.info("💡 系統會**完整保留**您上傳的 Excel 所有欄位，只在最後附加「緯度」「經度」兩欄")
        
        uploaded_file = st.file_uploader("上傳學生資料 Excel", type=['xlsx', 'xls'], help="支援 .xlsx / .xls")
        
        if uploaded_file:
            try:
                eng = 'xlrd' if uploaded_file.name.endswith('.xls') else 'openpyxl'
                df_original = pd.read_excel(uploaded_file, engine=eng)
                
                st.success(f"✅ 讀取成功：{len(df_original)} 筆 × {len(df_original.columns)} 欄")
                
                with st.expander("📋 原始資料預覽（前 10 筆）", expanded=True):
                    st.dataframe(df_original.head(10), use_container_width=True)
                
                # 只需選擇「學校名稱」欄位
                st.subheader("🔗 選擇學校名稱欄位")
                detected = detect_columns(df_original)
                cols_list = list(df_original.columns)
                
                school_default = cols_list.index(detected['school']) if detected['school'] in cols_list else 0
                school_col = st.selectbox(
                    "⭐ 哪一欄是學校名稱？",
                    cols_list,
                    index=school_default,
                    help="系統會根據這一欄查詢經緯度"
                )
                
                # 預覽該欄
                sample_schools = df_original[school_col].dropna().unique()[:8]
                st.write("該欄範例值：", " / ".join([str(s) for s in sample_schools]))
                
                # 統計
                valid_count = df_original[school_col].notna().sum()
                unique_count = df_original[school_col].nunique()
                st.write(f"📊 有效 **{valid_count}** 筆，不重複學校 **{unique_count}** 所")
                
                col_b1, col_b2 = st.columns([1, 3])
                with col_b1:
                    start_search = st.button("🚀 開始查詢座標", type="primary", use_container_width=True)
                with col_b2:
                    workers = st.slider("並行查詢數", 1, 6, MAX_WORKERS)
                
                if start_search:
                    db = load_database()
                    engine = GeocodingEngine()
                    
                    # 取得不重複學校
                    unique_schools = [str(s).strip() for s in df_original[school_col].dropna().unique() if str(s).strip()]
                    
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    school_coords = {}  # 學校 → (lat, lon)
                    success_count = 0
                    fail_count = 0
                    t0 = time.time()
                    
                    # 先查資料庫
                    to_search = []
                    for school in unique_schools:
                        normalized = normalize_school_name(school)
                        found = False
                        
                        if normalized in db:
                            school_coords[school] = (db[normalized]['lat'], db[normalized]['lon'])
                            success_count += 1
                            found = True
                        
                        if not found:
                            for v in generate_search_variants(normalized):
                                vn = normalize_school_name(v)
                                if vn in db:
                                    school_coords[school] = (db[vn]['lat'], db[vn]['lon'])
                                    success_count += 1
                                    found = True
                                    break
                        
                        if not found:
                            to_search.append(school)
                    
                    status_text.write(f"📦 資料庫命中 {success_count} 所 | 需線上查詢 {len(to_search)} 所")
                    
                    # 線上查詢
                    if to_search:
                        completed = 0
                        with ThreadPoolExecutor(max_workers=workers) as executor:
                            futures = {executor.submit(engine.geocode, s, db): s for s in to_search}
                            for future in as_completed(futures):
                                result = future.result()
                                completed += 1
                                if result['success']:
                                    school_coords[result['name']] = (result['lat'], result['lon'])
                                    success_count += 1
                                else:
                                    fail_count += 1
                                
                                total = success_count + fail_count
                                progress_bar.progress(min(total / len(unique_schools), 1.0))
                                elapsed = time.time() - t0
                                status_text.write(
                                    f"⏱️ {elapsed:.1f}s | ✅ {success_count} | ❌ {fail_count} | 🔄 {completed}/{len(to_search)}"
                                )
                    
                    save_database(db)
                    save_search_log(engine.search_log)
                    progress_bar.progress(1.0)
                    total_time = time.time() - t0
                    
                    st.success(f"🎉 完成！{total_time:.1f} 秒 | ✅ {success_count} 所 | ❌ {fail_count} 所")
                    
                    # ============================
                    # 核心：在原始 DataFrame 後面加兩欄
                    # ============================
                    df_result = df_original.copy()
                    
                    lat_list = []
                    lon_list = []
                    for _, row in df_original.iterrows():
                        school = str(row[school_col]).strip() if pd.notna(row[school_col]) else ''
                        if school in school_coords:
                            lat_list.append(school_coords[school][0])
                            lon_list.append(school_coords[school][1])
                        else:
                            lat_list.append(None)
                            lon_list.append(None)
                    
                    df_result['緯度'] = lat_list
                    df_result['經度'] = lon_list
                    
                    # 存到 session
                    st.session_state['result_df'] = df_result
                    st.session_state['school_col'] = school_col
                    st.session_state['school_coords'] = school_coords
                    
                    # 預覽
                    st.subheader("📋 結果預覽")
                    
                    found_mask = df_result['緯度'].notna()
                    
                    tab_ok, tab_fail = st.tabs([
                        f"✅ 已配對 ({found_mask.sum()})",
                        f"❌ 未找到 ({(~found_mask).sum()})"
                    ])
                    
                    with tab_ok:
                        if found_mask.sum() > 0:
                            st.dataframe(df_result[found_mask].head(50), use_container_width=True)
                    with tab_fail:
                        if (~found_mask).sum() > 0:
                            st.dataframe(df_result[~found_mask].head(50), use_container_width=True)
                            st.info("💡 可到「手動編輯」分頁補上座標後重新下載")
                    
                    # ============================
                    # 下載：完全保留原始格式 + 附加經緯度
                    # ============================
                    st.subheader("📥 下載結果")
                    
                    st.markdown(f"""
                    ```
                    原始檔案欄位: {' | '.join(str(c) for c in df_original.columns)}
                    ＋ 附加欄位:  緯度 | 經度
                    總欄數: {len(df_original.columns)} → {len(df_result.columns)}
                    ```
                    """)
                    
                    col_d1, col_d2 = st.columns(2)
                    with col_d1:
                        st.download_button(
                            "📥 下載完整結果 (Excel)",
                            data=df_to_excel_bytes(df_result),
                            file_name=f"學生資料_含座標_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                            type="primary"
                        )
                    with col_d2:
                        df_not_found = df_result[~found_mask]
                        if len(df_not_found) > 0:
                            st.download_button(
                                f"📥 未配對清單 ({len(df_not_found)} 筆)",
                                data=df_to_excel_bytes(df_not_found),
                                file_name=f"未配對_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True
                            )
            
            except Exception as e:
                st.error(f"❌ 讀取失敗：{e}")
                st.info("請確認是有效的 Excel 格式（.xlsx 或 .xls）")
        
        # 如果之前已有結果，顯示下載按鈕
        elif 'result_df' in st.session_state:
            st.divider()
            st.subheader("📥 上次查詢結果仍可下載")
            df_prev = st.session_state['result_df']
            found_mask = df_prev['緯度'].notna()
            st.write(f"共 {len(df_prev)} 筆 | ✅ {found_mask.sum()} | ❌ {(~found_mask).sum()}")
            st.download_button(
                "📥 下載結果 (Excel)",
                data=df_to_excel_bytes(df_prev),
                file_name=f"學生資料_含座標_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
    
    # ========================================
    # Tab 2: 地圖
    # ========================================
    with tab2:
        st.header("🗺️ 學校分布地圖")
        
        if 'result_df' not in st.session_state:
            st.info("📤 請先在「上傳學生資料」查詢座標")
        else:
            df_map = st.session_state['result_df']
            school_col = st.session_state.get('school_col', '')
            df_valid = df_map[df_map['緯度'].notna() & df_map['經度'].notna()].copy()
            
            if len(df_valid) == 0:
                st.warning("沒有可顯示的座標")
            else:
                try:
                    import folium
                    from folium.plugins import MarkerCluster, HeatMap
                    from streamlit_folium import st_folium
                    
                    map_type = st.radio("地圖模式", ["標記地圖", "聚合地圖", "熱力圖"], horizontal=True)
                    
                    center_lat, center_lon = df_valid['緯度'].mean(), df_valid['經度'].mean()
                    m = folium.Map(location=[center_lat, center_lon], zoom_start=8)
                    
                    if map_type == "標記地圖":
                        for _, row in df_valid.iterrows():
                            name = str(row[school_col]) if school_col and pd.notna(row.get(school_col)) else '未知'
                            folium.CircleMarker(
                                location=[row['緯度'], row['經度']], radius=6,
                                popup=folium.Popup(f"🏫 {name}", max_width=200),
                                tooltip=name,
                                color='#3388ff', fill=True, fillColor='#3388ff', fillOpacity=0.7
                            ).add_to(m)
                    elif map_type == "聚合地圖":
                        cluster = MarkerCluster().add_to(m)
                        for _, row in df_valid.iterrows():
                            name = str(row[school_col]) if school_col and pd.notna(row.get(school_col)) else '未知'
                            folium.Marker(
                                location=[row['緯度'], row['經度']],
                                popup=folium.Popup(f"🏫 {name}", max_width=200),
                                tooltip=name
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
            school_col = st.session_state.get('school_col', '')
            df_valid = df_stats[df_stats['緯度'].notna()].copy()
            
            if len(df_valid) == 0:
                st.warning("沒有可分析的資料")
            else:
                c1, c2, c3, c4 = st.columns(4)
                with c1: st.metric("總筆數", len(df_stats))
                with c2: st.metric("已配對", len(df_valid))
                with c3: st.metric("不重複學校", df_valid[school_col].nunique() if school_col else '–')
                with c4:
                    rate = len(df_valid) / len(df_stats) * 100 if len(df_stats) > 0 else 0
                    st.metric("配對率", f"{rate:.1f}%")
                
                if school_col:
                    st.divider()
                    st.subheader("🏫 各學校學生數")
                    school_counts = df_valid[school_col].value_counts()
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
                    
                    df_valid['區域'] = df_valid[school_col].apply(get_region)
                    st.bar_chart(df_valid['區域'].value_counts())
                
                st.divider()
                st.subheader("📏 距離分析")
                ref_lat = st.number_input("參考點緯度", value=24.9936, format="%.4f")
                ref_lon = st.number_input("參考點經度", value=121.3010, format="%.4f")
                
                if st.button("計算距離"):
                    distances = [haversine(ref_lon, ref_lat, row['經度'], row['緯度']) for _, row in df_valid.iterrows()]
                    df_valid['距離(km)'] = [round(d, 2) for d in distances]
                    st.write(f"平均 **{sum(distances)/len(distances):.2f}** km | "
                            f"最近 **{min(distances):.2f}** km | 最遠 **{max(distances):.2f}** km")
                    
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
        
        st.subheader("➕ 新增/修改學校座標")
        ce1, ce2, ce3 = st.columns([2, 1, 1])
        with ce1: edit_name = st.text_input("學校名稱", placeholder="例：臺北市大安國小")
        with ce2: edit_lat = st.number_input("緯度", value=25.0330, format="%.6f", key='ed_lat')
        with ce3: edit_lon = st.number_input("經度", value=121.5654, format="%.6f", key='ed_lon')
        
        if st.button("💾 儲存", type="primary"):
            if edit_name:
                db[normalize_school_name(edit_name)] = {
                    'lat': edit_lat, 'lon': edit_lon, 'source': 'manual',
                    'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                save_database(db)
                st.success(f"✅ 已儲存：{edit_name}")
                st.rerun()
        
        st.divider()
        
        # 顯示未找到的學校（從結果中）
        if 'result_df' in st.session_state:
            df_r = st.session_state['result_df']
            school_col = st.session_state.get('school_col', '')
            if school_col:
                df_fail = df_r[df_r['緯度'].isna()]
                if len(df_fail) > 0:
                    st.subheader(f"❌ 未配對的學校（{df_fail[school_col].nunique()} 所）")
                    for school in df_fail[school_col].unique():
                        if pd.isna(school) or str(school).strip() == '':
                            continue
                        school_str = str(school).strip()
                        with st.expander(f"🏫 {school_str}"):
                            cf1, cf2 = st.columns(2)
                            with cf1: fix_lat = st.number_input("緯度", value=25.0, format="%.6f", key=f"fl_{school_str}")
                            with cf2: fix_lon = st.number_input("經度", value=121.5, format="%.6f", key=f"fo_{school_str}")
                            st.markdown(f"🔍 [Google Maps 搜尋](https://www.google.com/maps/search/{school_str})")
                            if st.button(f"儲存 {school_str}", key=f"sv_{school_str}"):
                                db[normalize_school_name(school_str)] = {
                                    'lat': fix_lat, 'lon': fix_lon, 'source': 'manual_fix',
                                    'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                }
                                save_database(db)
                                st.success(f"✅ 已儲存 {school_str}")
                    
                    st.divider()
                    if st.button("🔄 用更新的資料庫重新配對", type="primary"):
                        db = load_database()
                        school_coords = st.session_state.get('school_coords', {})
                        
                        # 重新配對
                        for school in df_fail[school_col].unique():
                            if pd.isna(school):
                                continue
                            school_str = str(school).strip()
                            normalized = normalize_school_name(school_str)
                            if normalized in db:
                                school_coords[school_str] = (db[normalized]['lat'], db[normalized]['lon'])
                        
                        # 更新結果
                        df_updated = st.session_state['result_df'].copy()
                        lat_list, lon_list = [], []
                        for _, row in df_updated.iterrows():
                            s = str(row[school_col]).strip() if pd.notna(row.get(school_col)) else ''
                            if s in school_coords:
                                lat_list.append(school_coords[s][0])
                                lon_list.append(school_coords[s][1])
                            else:
                                lat_list.append(None)
                                lon_list.append(None)
                        df_updated['緯度'] = lat_list
                        df_updated['經度'] = lon_list
                        
                        st.session_state['result_df'] = df_updated
                        st.session_state['school_coords'] = school_coords
                        
                        new_found = df_updated['緯度'].notna().sum()
                        st.success(f"✅ 重新配對完成！已配對 {new_found}/{len(df_updated)} 筆")
                        st.rerun()
        
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
                if st.button("刪除", key='del_btn'):
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
                results.append(engine.geocode(school, db))
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
