import os
import openpyxl
from openpyxl.chart import PieChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.styles import Side, Border, Alignment

# 🌟 回収ルートを「過去売上高」に完全復帰！
folder_path = "./過去売上高"

if os.path.exists(folder_path):
    files = [f for f in os.listdir(folder_path) if f.startswith("売上高2026") and f.endswith(".xlsx")]
    files.sort()
else:
    files = []

total_sales = {}

for file_name in files:
    file_full_path = os.path.join(folder_path, file_name)
    wb = openpyxl.load_workbook(file_full_path, data_only=True)
    ws = wb.active
    
    max_col = ws.max_column
    product_mapping = {}
    current_product = None
    
    for col_idx in range(3, max_col + 1):
        prod_name = ws.cell(row=1, column=col_idx).value
        if prod_name:
            current_product = prod_name
        
        item_type = ws.cell(row=2, column=col_idx).value
        if item_type == "合計金額" and current_product:
            product_mapping[col_idx] = current_product
            if current_product not in total_sales:
                total_sales[current_product] = 0

    for row_idx in range(3, 33):
        for col_idx, prod_name in product_mapping.items():
            amount = ws.cell(row=row_idx, column=col_idx).value
            if amount is not None and str(amount).replace('-', '').isdigit():
                total_sales[prod_name] += int(amount)

report_wb = openpyxl.Workbook()
report_ws = report_wb.active
report_ws.title = "総合売上集計"

report_ws.append(["商品名", "累計売上（円）"])
for product, amount in total_sales.items():
    report_ws.append([product, amount])

total_all = sum(total_sales.values())
report_ws.append(["パン全体売上", total_all])

thin_border = Border(
    left=Side(style='thin', color='000000'),
    right=Side(style='thin', color='000000'),
    top=Side(style='thin', color='000000'),
    bottom=Side(style='thin', color='000000')
)
for row in report_ws.iter_rows(min_row=1, max_row=len(total_sales) + 2, min_col=1, max_col=2):
    for cell in row:
        cell.border = thin_border
        if cell.column == 2:
            cell.alignment = Alignment(horizontal="right")

report_ws.column_dimensions["A"].width = 20
report_ws.column_dimensions["B"].width = 18

pie = PieChart()
pie.title = "商品別 売上構成比（デザイン修正版）"

num_products = len(total_sales)
data_ref = Reference(report_ws, min_col=2, min_row=1, max_row=num_products + 1)
labels_ref = Reference(report_ws, min_col=1, min_row=2, max_row=num_products + 1)
pie.add_data(data_ref, titles_from_data=True)
pie.set_categories(labels_ref)
pie.style = 10

pie.legend = None
pie.dataLabels = DataLabelList()
pie.dataLabels.showCatName = True   
pie.dataLabels.showPercent = True   
pie.dataLabels.showVal = True       
pie.dataLabels.showLeaderLines = False

report_ws.add_chart(pie, "D2")

report_wb.save("集計グラフ.xlsx")
print("【集計完了】「過去売上高」からデータを回収し、『集計グラフ.xlsx』を正常出力しました！")