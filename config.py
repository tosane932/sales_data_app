import os

# ==========================================
# システム全体の設定管理ファイル値
# ==========================================

# 売上Excelファイルの保存先フォルダ名と絶対パス
PAST_FOLDER_NAME = "過去売上高"
PAST_FOLDER = os.path.join(os.path.dirname(__file__), PAST_FOLDER_NAME)

# AIアドバイス生成で使用するGeminiの最新モデル名
GEMINI_MODEL = "gemini-2.5-flash"
