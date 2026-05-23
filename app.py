import os
import datetime
import calendar
from flask import Flask, render_template, request, send_file
import openpyxl
from openpyxl.styles import Font, Side, Border, PatternFill, Alignment
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles.protection import Protection

app = Flask(__name__)

# 🌟 保存先フォルダを「my_sales_app/過去売上高」に完全適応！
PAST_FOLDER = os.path.join(os.path.dirname(__file__), "過去売上高")
if not os.path.exists(PAST_FOLDER):
    os.makedirs(PAST_FOLDER)

def generate_excel(year, month, products_data):
    """
    Webの入力から、完全に保護されたオーダーメイドExcelを生成する関数
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "月間売上入力"
    ws.protection.enabled = True

    # 枠線とデザイン定義
    thin = Side(border_style="thin", color="000000")
    medium = Side(border_style="medium", color="000000")
    border_header_normal = Border(left=thin, right=thin, top=medium, bottom=medium)
    border_header_block_end = Border(left=thin, right=medium, top=medium, bottom=medium)
    border_data_normal = Border(left=thin, right=thin, top=thin, bottom=thin)
    border_data_block_end = Border(left=thin, right=medium, top=thin, bottom=thin)

    fill_product = PatternFill(start_color="E6F2FF", end_color="E6F2FF", fill_type="solid")
    fill_item = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
    fill_total = PatternFill(start_color="FFE6E6", end_color="FFE6E6", fill_type="solid")

    weekday_colors = {
        "日": PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid"),
        "土": PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
    }

    align_center = Alignment(horizontal="center", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    ws["A1"] = "日付"
    ws["B1"] = "曜日"
    ws["A2"] = "固定"
    ws["B2"] = "自動算出"
    for cell_id in ["A1", "B1", "A2", "B2"]:
        ws[cell_id].alignment = align_center

    # 商品列の構築
    current_col = 3
    for prod in products_data:
        c_price = openpyxl.utils.get_column_letter(current_col)
        c_qty = openpyxl.utils.get_column_letter(current_col + 1)
        c_amount = openpyxl.utils.get_column_letter(current_col + 2)
        
        ws[f"{c_price}1"] = prod["name"]
        ws.merge_cells(f"{c_price}1:{c_amount}1")
        ws[f"{c_price}1"].fill = fill_product
        ws[f"{c_price}1"].font = Font(name="Noto Sans CJK SC", size=11, bold=True)
        ws[f"{c_price}1"].alignment = align_center
        
        ws[f"{c_price}2"] = "価格"
        ws[f"{c_qty}2"] = "数量"
        ws[f"{c_amount}2"] = "合計金額"
        
        for col_let in [c_price, c_qty, c_amount]:
            ws[f"{col_let}2"].fill = fill_item
            ws[f"{col_let}2"].alignment = align_center
        
        current_col += 3

    # 月の日数判定と日付行生成
    _, max_days = calendar.monthrange(year, month)
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"]
    
    for day in range(1, max_days + 1):
        row_idx = 2 + day
        date_val = datetime.date(year, month, day)
        
        ws[f"A{row_idx}"] = date_val
        ws[f"A{row_idx}"].number_format = 'm/d'
        ws[f"A{row_idx}"].border = border_data_normal
        ws[f"A{row_idx}"].alignment = align_center
        
        ja_w = weekday_ja[date_val.weekday()]
        ws[f"B{row_idx}"] = ja_w
        ws[f"B{row_idx}"].border = Border(left=thin, right=medium, top=thin, bottom=thin)
        ws[f"B{row_idx}"].alignment = align_center
        if ja_w in weekday_colors:
            ws[f"B{row_idx}"].fill = weekday_colors[ja_w]
        
        p_col = 3
        for prod in products_data:
            c_p = openpyxl.utils.get_column_letter(p_col)
            c_q = openpyxl.utils.get_column_letter(p_col + 1)
            c_a = openpyxl.utils.get_column_letter(p_col + 2)
            
            if row_idx == 3:
                ws[f"{c_p}3"] = prod["price"]
            else:
                ws[f"{c_p}{row_idx}"] = f"=${c_p}$3"
            
            ws[f"{c_a}{row_idx}"] = f"={c_p}{row_idx}*{c_q}{row_idx}"
            p_col += 3

    # 総合計行の構築
    total_row = 2 + max_days + 1
    ws[f"A{total_row}"] = "総合計"
    ws[f"A{total_row}"].font = Font(name="Noto Sans CJK SC", size=10, bold=True)
    ws[f"A{total_row}"].fill = fill_total
    ws[f"A{total_row}"].alignment = align_center
    ws[f"A{total_row}"].border = border_data_normal
    ws[f"B{total_row}"].fill = fill_total
    ws[f"B{total_row}"].border = Border(left=thin, right=medium, top=thin, bottom=thin)

    p_col = 3
    max_col_idx = 2 + (len(products_data) * 3)

    for _ in products_data:
        c_p = openpyxl.utils.get_column_letter(p_col)
        c_q = openpyxl.utils.get_column_letter(p_col + 1)
        c_a = openpyxl.utils.get_column_letter(p_col + 2)
        
        ws[f"{c_p}{total_row}"] = ""
        ws[f"{c_q}{total_row}"] = f"=SUM({c_q}3:{c_q}{total_row-1})"
        ws[f"{c_a}{total_row}"] = f"=SUM({c_a}3:{c_a}{total_row-1})"
        
        for col_let in [c_p, c_q, c_a]:
            cell = ws[f"{col_let}{total_row}"]
            cell.fill = fill_total
            cell.font = Font(name="Noto Sans CJK SC", bold=True)
            cell.alignment = align_right
        p_col += 3

    # 入力規則＆数量列のみ開放
    dv_qty = DataValidation(type="whole", operator="greaterThanOrEqual", formula1="0")
    dv_qty.promptTitle = "【数量の入力】"
    dv_qty.prompt = "本日の販売個数を入力してください。"
    dv_qty.showInputMessage = True
    ws.add_data_validation(dv_qty)

    for r in range(3, total_row + 1):
        for c in range(3, max_col_idx + 1):
            cell = ws.cell(row=r, column=c)
            mod = (c - 3) % 3
            
            if mod == 2:
                cell.border = border_data_block_end
                cell.alignment = align_right
            else:
                cell.border = border_data_normal
                cell.alignment = align_right
                if r != total_row and mod == 1:
                    cell.protection = Protection(locked=False)
                    dv_qty.add(cell)

    # 枠線・幅の最終調整
    for r in [1, 2]:
        for c in range(1, max_col_idx + 1):
            cell = ws.cell(row=r, column=c)
            if c == 1:
                cell.border = Border(left=thin, right=thin, top=medium, bottom=medium)
            elif c == 2:
                cell.border = Border(left=thin, right=medium, top=medium, bottom=medium)
            elif (c - 3) % 3 == 2:
                cell.border = border_header_block_end
            else:
                cell.border = border_header_normal

    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 6
    for c in range(3, max_col_idx + 1):
        col_letter = openpyxl.utils.get_column_letter(c)
        mod = (c - 3) % 3
        if mod == 0:
            ws.column_dimensions[col_letter].width = 9
        elif mod == 1:
            ws.column_dimensions[col_letter].width = 9
        else:
            ws.column_dimensions[col_letter].width = 15

    # 🌟 店長さんのマニュアル通りの完璧な命名規則でファイル確定！
    filename = f"売上高{year}{month:02d}.xlsx"
    file_path = os.path.join(PAST_FOLDER, filename)
    wb.save(file_path)
    return file_path, filename

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        year = int(request.form.get("year"))
        month = int(request.form.get("month"))
        
        product_names = request.form.getlist("prod_name")
        product_prices = request.form.getlist("prod_price")
        
        products_data = []
        for name, price in zip(product_names, product_prices):
            if name.strip():
                products_data.append({
                    "name": name.strip(),
                    "price": int(price) if price.isdigit() else 0
                })
        
        file_path, filename = generate_excel(year, month, products_data)
        return send_file(file_path, as_attachment=True, download_name=filename)
        
    return render_template("index.html")

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)