import os

# ==========================================
# システム全体の設定管理ファイル値
# ==========================================

# 売上Excelファイルの保存先フォルダ名と絶対パス
PAST_FOLDER_NAME = "過去売上高"
PAST_FOLDER = os.path.join(os.path.dirname(__file__), PAST_FOLDER_NAME)

# AIアドバイス生成で使用するGeminiの最新モデル名
GEMINI_MODEL = "gemini-2.5-flash"

# データベース接続設定
# ローカル開発時はSQLite、Render本番環境ではDATABASE_URL環境変数からPostgres接続文字列を取得
SQLALCHEMY_DATABASE_URI = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{os.path.join(os.path.dirname(__file__), 'local.db')}"
)
SQLALCHEMY_TRACK_MODIFICATIONS = False

