import os

# ==========================================
# システム全体の設定管理ファイル値
# ==========================================

# 売上Excelファイルの保存先フォルダ名と絶対パス
PAST_FOLDER_NAME = "過去売上高"
PAST_FOLDER = os.path.join(os.path.dirname(__file__), PAST_FOLDER_NAME)

# AIアドバイス生成で使用するGeminiの最新モデル名
GEMINI_MODEL = "gemini-2.5-flash"

# ==========================================
# データベース接続設定
# ==========================================
# RenderのPostgreSQL自動生成URL（postgres://）をSQLAlchemy対応形式（postgresql://）に変換する対策
raw_db_url = os.environ.get("DATABASE_URL")

if raw_db_url:
    # 先頭が「postgres://」で始まっている場合は「postgresql://」に置換
    if raw_db_url.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = raw_db_url.replace("postgres://", "postgresql://", 1)
    else:
        SQLALCHEMY_DATABASE_URI = raw_db_url
else:
    # 環境変数がない（ローカル開発環境）場合はSQLiteを使用
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(os.path.dirname(__file__), 'local.db')}"

SQLALCHEMY_TRACK_MODIFICATIONS = False