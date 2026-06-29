# 1. ベースとなる環境に Python 3.12 を指定（軽量な slim 版を採用してリソースを最適化）
FROM python:3.12-slim

# 2. コンテナ内の作業ディレクトリ（部屋）を決める
WORKDIR /app

# 3. 依存ライブラリのリストを先にコンテナ内にコピー
COPY requirements.txt .

# 4. コンテナ内で pip インストールを実行（キャッシュを使わず軽量化）
RUN pip install --no-cache-dir -r requirements.txt

# 5. アプリの全ファイルをコンテナ内にコピー
COPY . .

# 6. Flaskが外部からの接続を受け付けられるようにポート5000を開放
EXPOSE 5000

# 7. コンテナ起動時に Flask アプリを実行するコマンド
CMD ["python", "app.py"]