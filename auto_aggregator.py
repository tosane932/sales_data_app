import os
import openpyxl

# --- 設定・定数 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
folder_path = os.path.join(BASE_DIR, "過去売上高")

def get_filtered_sales_data(target_year=None, target_month=None):
    """
    指定した年(target_year)や月(target_month)で絞り込んで集計する
    引数がNoneなら全期間を集計
    """
    total_sales = {}
    
    if not os.path.exists(folder_path):
        print(f"DEBUG: フォルダが見つかりません -> {folder_path}")
        return total_sales

    files = os.listdir(folder_path)
    print(f"DEBUG: フォルダ内の全ファイル -> {files}")
    
    for file_name in files:
        if not file_name.startswith("売上高") or not file_name.endswith(".xlsx"):
            continue
            
        try:
            # 「売上高202609.xlsx」から「202609」だけを確実に抜き出す（文字ズレ対策）
            date_str = file_name.replace("売上高", "").replace(".xlsx", "")
            year = int(date_str[:4])
            month = int(date_str[4:6])
        except Exception as e:
            print(f"DEBUG: ファイル名解析エラー -> {file_name} ({e})")
            continue

        # フィルタリング判定
        if target_year and year != target_year:
            continue
        if target_month and month != target_month:
            continue

        print(f"DEBUG: 🔵集計開始 -> {file_name}")

        # 集計処理
        file_full_path = os.path.join(folder_path, file_name)
        wb = openpyxl.load_workbook(file_full_path, data_only=True)
        ws = wb.active
        
        # 3列1セットの構造でループ
        for col_idx in range(3, ws.max_column + 1, 3):
            prod_name = ws.cell(row=1, column=col_idx).value
            
            if prod_name:
                for row_idx in range(3, ws.max_row + 1):
                    # 総合計行でストップ
                    cell_val = ws.cell(row=row_idx, column=1).value
                    if str(cell_val) == "総合計":
                        break
                    
                    qty = ws.cell(row=row_idx, column=col_idx + 1).value
                    
                    # 【鉄壁の防禦】空白やNoneを弾き、無理やり整数(int)にして足す
                    if qty is not None and str(qty).strip() != "":
                        try:
                            total_sales[prod_name] = total_sales.get(prod_name, 0) + int(float(qty))
                        except ValueError:
                            pass # 「休」などの文字が入っていた場合は無視する
        
    print(f"DEBUG: 🟢最終集計結果 -> {total_sales}")
    return total_sales