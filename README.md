# Bakery Sales Management System

現場の「困った」を、Pythonで「最適解」へ。

## 📺 デモ動画
以下の画像をクリックすると、YouTubeで実際の動作デモがご覧いただけます。

[![ベーカリー売上管理システム（デモ動画）](demo2thumbnail.png)](https://youtu.be/KcIgl94h3OY)

※デモ動画は初期バージョンのものです。現在のバージョンはPostgreSQL・Docker対応済みです。

## 📖 プロジェクト概要
ベーカリー現場における売上管理業務の自動化を目的としたWebアプリケーションです。物流現場で培った「業務フローの最適化」という知見を活かし、入力から集計、AIによる経営アドバイス生成までを統合しました。

## ⚙️ 機能一覧
- **商品登録:** 月次メニューと価格をWebフォームから登録
- **日次売上入力:** 商品ごとの販売数量を日付指定で入力
- **売上ダッシュボード:** 期間別の売上集計とグラフ表示
- **AI経営アドバイス:** Google Gemini APIを活用した売上データ分析と店長向け助言の自動生成
- **AIアシスタント挨拶:** 日次入力画面に季節感のある一言を表示

## 🛠 技術スタック
- **Backend:** Python, Flask
- **Database:** PostgreSQL
- **AI:** Google Gemini API (gemini-2.5-flash)
- **Frontend:** JavaScript (非同期通信対応)
- **Infrastructure:** Docker, Docker Compose

## 🚀 セットアップ手順（Docker使用）

```bash
# 1. リポジトリのクローン
git clone https://github.com/tosane932/sales_data_app.git
cd sales_data_app

# 2. 環境変数の設定
cp .env.example .env
# .envを編集してGEMINI_API_KEYを設定

# 3. 起動（これだけで完了）
docker compose up --build
```

ブラウザで http://127.0.0.1:5000 にアクセス。

## 💡 開発思想
「だろうコーディング」ではなく「かもしれないコーディング」を意識した設計。物流現場7年で培った安全確認の思想をコードで表現しています。Fail-Fast設計、入力バリデーションの二重チェック、環境変数による設定管理など、小さなミスが重大な事故につながる前に検知する仕組みを実装しています。
