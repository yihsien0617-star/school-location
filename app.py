import pandas as pd
from geopy.geocoders import Nominatim
import time
import tkinter as tk
from tkinter import filedialog, messagebox
import os
import json

# ==========================================
# 步驟 1：初始化本地學校經緯度資料庫
# ==========================================
# 資料庫檔案路徑（放在程式同一個資料夾下）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "school_coordinates_db.json")

def load_local_db(db_path):
    """載入本地經緯度資料庫（JSON 格式）"""
    if os.path.exists(db_path):
        with open(db_path, "r", encoding="utf-8") as f:
            db = json.load(f)
        print(f"✅ 已載入本地資料庫，目前收錄 {len(db)} 所學校")
        return db
    else:
        print("📝 本地資料庫不存在，將自動建立新的資料庫")
        return {}

def save_local_db(db, db_path):
    """儲存本地經緯度資料庫"""
    with open(db_path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    print(f"💾 資料庫已更新，目前共收錄 {len(db)} 所學校")

# 載入本地資料庫
school_db = load_local_db(DB_FILE)

# ==========================================
# 步驟 2：詢問是否要匯入額外的經緯度資料
# ==========================================
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)

import_more = messagebox.askyesno(
    "匯入經緯度資料",
    "是否要先匯入您手邊已有的學校經緯度資料？\n\n"
    "（支援 CSV 或 Excel，需包含「學校名稱」「緯度」「經度」三個欄位）\n\n"
    "如果不需要，請按「否」直接進行轉換。"
)

if import_more:
    print("請選擇您手邊的學校經緯度資料檔...")
    coord_file = filedialog.askopenfilename(
        title="請選擇學校經緯度資料檔",
        filetypes=[("CSV 檔案", "*.csv"), ("Excel 檔案", "*.xlsx")]
    )

    if coord_file:
        # 讀取匯入檔案
        if coord_file.endswith('.csv'):
            df_import = pd.read_csv(coord_file)
        else:
            df_import = pd.read_excel(coord_file)

        print(f"\n📂 讀取到 {len(df_import)} 筆資料，欄位如下：")
        print(f"   {list(df_import.columns)}\n")

        # --- 自動偵測欄位名稱 ---
        # 學校名稱欄位的候選名稱
        school_col_candidates = ["學校名稱", "畢業學校", "學校", "school", "School", "校名"]
        lat_col_candidates = ["緯度", "學校緯度", "lat", "Lat", "latitude", "Latitude"]
        lon_col_candidates = ["經度", "學校經度", "lng", "lon", "Lon", "Lng", "longitude", "Longitude"]

        def find_column(df, candidates):
            """從候選名稱中找出對應的欄位"""
            for c in candidates:
                if c in df.columns:
                    return c
            return None

        school_col = find_column(df_import, school_col_candidates)
        lat_col = find_column(df_import, lat_col_candidates)
        lon_col = find_column(df_import, lon_col_candidates)

        # 如果自動偵測失敗，讓使用者手動輸入
        if not school_col or not lat_col or not lon_col:
            print("⚠️ 無法自動識別欄位，請手動指定：")
            print(f"   可用欄位：{list(df_import.columns)}")
            if not school_col:
                school_col = input("   請輸入「學校名稱」對應的欄位名稱：").strip()
            if not lat_col:
                lat_col = input("   請輸入「緯度」對應的欄位名稱：").strip()
            if not lon_col:
                lon_col = input("   請輸入「經度」對應的欄位名稱：").strip()

        # 匯入到資料庫
        imported_count = 0
        updated_count = 0
        for _, row in df_import.iterrows():
            name = str(row[school_col]).strip()
            lat = row[lat_col]
            lon = row[lon_col]

            # 跳過空值
            if pd.isna(lat) or pd.isna(lon) or name == "" or name == "nan":
                continue

            if name in school_db:
                # 已存在 → 更新（以新匯入的為準）
                school_db[name] = {"lat": float(lat), "lon": float(lon), "source": "手動匯入"}
                updated_count += 1
            else:
                school_db[name] = {"lat": float(lat), "lon": float(lon), "source": "手動匯入"}
                imported_count += 1

        print(f"✅ 匯入完成！新增 {imported_count} 所，更新 {updated_count} 所")
        save_local_db(school_db, DB_FILE)
    else:
        print("⏭️ 未選擇檔案，跳過匯入步驟")

# ==========================================
# 步驟 3：選擇申請生資料檔
# ==========================================
print("\n" + "=" * 50)
print("請選擇您要轉換的申請生資料檔...")

file_path = filedialog.askopenfilename(
    title="請選擇申請生資料檔",
    filetypes=[("CSV 檔案", "*.csv"), ("Excel 檔案", "*.xlsx")]
)

if not file_path:
    print("❌ 您沒有選擇任何檔案，程式結束。")
    exit()

print(f"✅ 成功載入檔案：{os.path.basename(file_path)}\n")

# 讀取資料
if file_path.endswith('.csv'):
    df = pd.read_csv(file_path)
else:
    df = pd.read_excel(file_path)

# ==========================================
# 步驟 4：比對本地資料庫 + 模糊比對
# ==========================================
unique_schools = df['畢業學校'].dropna().unique()
print(f"總共 {len(df)} 筆資料，{len(unique_schools)} 所不重複學校\n")

# 分類：哪些查得到、哪些查不到
found_in_db = []
need_online_search = []

# --- 模糊比對函式 ---
def fuzzy_match_school(school_name, db):
    """
    嘗試用多種變體去比對資料庫
    例如：「國立臺灣大學」vs「臺灣大學」vs「台灣大學」
    """
    # 台/臺 互換
    variants = set()
    variants.add(school_name)
    variants.add(school_name.replace("台", "臺"))
    variants.add(school_name.replace("臺", "台"))

    # 嘗試加上/去掉前綴
    prefixes = ["國立", "私立", "市立", "縣立"]
    expanded = set()
    for v in variants:
        expanded.add(v)
        for p in prefixes:
            if v.startswith(p):
                expanded.add(v[len(p):])  # 去掉前綴
            else:
                expanded.add(p + v)  # 加上前綴
    
    # 在資料庫中搜尋
    for variant in expanded:
        if variant in db:
            return variant  # 回傳匹配到的 key
    
    return None

for school in unique_schools:
    # 直接比對
    if school in school_db:
        found_in_db.append(school)
    else:
        # 模糊比對
        match_key = fuzzy_match_school(school, school_db)
        if match_key:
            # 把模糊比對的結果也加進資料庫（用原始名稱當 key）
            school_db[school] = school_db[match_key].copy()
            school_db[school]["source"] = f"模糊比對→{match_key}"
            found_in_db.append(school)
            print(f"🔗 模糊比對成功: {school} → {match_key}")
        else:
            need_online_search.append(school)

print(f"\n📊 比對結果：")
print(f"   ✅ 本地資料庫已有：{len(found_in_db)} 所")
print(f"   🔍 需要上網查詢：{len(need_online_search)} 所\n")

# ==========================================
# 步驟 5：線上查詢（只查資料庫裡沒有的）
# ==========================================
not_found_schools = []

if need_online_search:
    print(f"開始線上查詢 {len(need_online_search)} 所學校...\n")
    geolocator = Nominatim(user_agent="hwai_university_enrollment_analysis")

    for i, school in enumerate(need_online_search, 1):
        print(f"[{i}/{len(need_online_search)}] 查詢: {school}...", end=" ")

        try:
            # 嘗試多種搜尋策略
            search_queries = [
                f"台灣 {school}",
                f"Taiwan {school}",
                school,
                # 如果是高中，嘗試加上「高級中學」
                f"台灣 {school}高級中學" if "高中" in school else None,
                # 如果名稱中有「台」，也試試「臺」
                f"台灣 {school.replace('台', '臺')}" if "台" in school else None,
                f"台灣 {school.replace('臺', '台')}" if "臺" in school else None,
            ]
            # 移除 None
            search_queries = [q for q in search_queries if q]

            location = None
            for query in search_queries:
                location = geolocator.geocode(query)
                if location:
                    # 檢查是否在台灣範圍內（大致經緯度）
                    if (21.5 <= location.latitude <= 26.5 and
                        119.0 <= location.longitude <= 123.0):
                        break
                    else:
                        location = None  # 不在台灣，繼續嘗試
                time.sleep(0.5)

            if location:
                school_db[school] = {
                    "lat": location.latitude,
                    "lon": location.longitude,
                    "source": "線上查詢(Nominatim)"
                }
                print(f"✅ ({location.latitude:.4f}, {location.longitude:.4f})")
            else:
                not_found_schools.append(school)
                print(f"❌ 找不到")

            time.sleep(1)  # 遵守 API 速率限制

        except Exception as e:
            print(f"⚠️ 錯誤: {e}")
            not_found_schools.append(school)
            time.sleep(2)

    # 查完後儲存更新後的資料庫
    save_local_db(school_db, DB_FILE)
else:
    print("🎉 所有學校都在本地資料庫中，不需要上網查詢！")

# ==========================================
# 步驟 6：將經緯度對應回原始資料
# ==========================================
def get_lat(school):
    if school in school_db:
        return school_db[school]["lat"]
    return None

def get_lon(school):
    if school in school_db:
        return school_db[school]["lon"]
    return None

df['學校緯度'] = df['畢業學校'].map(get_lat)
df['學校經度'] = df['畢業學校'].map(get_lon)

# ==========================================
# 步驟 7：匯出結果
# ==========================================
dir_name = os.path.dirname(file_path)
base_name = os.path.splitext(os.path.basename(file_path))[0]
output_filename = os.path.join(dir_name, f"{base_name}_已轉換經緯度.csv")

df.to_csv(output_filename, index=False, encoding="utf-8-sig")

# ==========================================
# 步驟 8：顯示結果摘要
# ==========================================
total = len(unique_schools)
success = total - len(not_found_schools)
success_rate = (success / total * 100) if total > 0 else 0

print("\n" + "=" * 50)
print("📋 轉換結果摘要")
print("=" * 50)
print(f"   資料總筆數：{len(df)} 筆")
print(f"   不重複學校：{total} 所")
print(f"   成功轉換　：{success} 所 ({success_rate:.1f}%)")
print(f"   無法轉換　：{len(not_found_schools)} 所")
print(f"\n   📁 輸出檔案：{output_filename}")
print(f"   📁 資料庫　：{DB_FILE}")

if not_found_schools:
    print(f"\n⚠️ 以下 {len(not_found_schools)} 所學校無法自動找到座標：")
    print("-" * 40)
    for s in not_found_schools:
        count = len(df[df['畢業學校'] == s])
        print(f"   - {s} （共 {count} 筆資料）")
    print("-" * 40)
    print("💡 您可以：")
    print("   1. 在輸出的 CSV 中手動補上經緯度")
    print("   2. 建立/更新經緯度資料檔，下次匯入即可自動比對")

    # 匯出找不到的學校清單，方便手動補齊
    missing_file = os.path.join(dir_name, f"{base_name}_待補經緯度學校.csv")
    missing_df = pd.DataFrame({
        "學校名稱": not_found_schools,
        "緯度": "",
        "經度": "",
        "備註": "請手動填入經緯度，之後可匯入當資料庫"
    })
    missing_df.to_csv(missing_file, index=False, encoding="utf-8-sig")
    print(f"\n   📄 待補清單已匯出：{missing_file}")

print("\n✅ 程式執行完畢！")
