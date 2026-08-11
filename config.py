import os

# ==========================================
# システム全体の設定管理ファイル値
# ==========================================

# AIアドバイス生成で使用するGeminiの最新モデル名
GEMINI_MODEL = "gemini-2.5-flash"

# ==========================================
# 単一管理者認証の設定
# ==========================================
SECRET_KEY = os.environ.get("SECRET_KEY")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH")

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