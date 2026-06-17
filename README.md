# Bakery Sales Management System

現場の「困った」を、Pythonで「最適解」へ。

## 📺 デモ動画
以下の画像をクリックすると、YouTubeで実際の動作デモ（Android実機連携・openpyxl自動集計）がご覧いただけます。

[![Excel集計を秒で終わらせるPython（デモ動画）](demo_thumbnail.png)](https://youtu.be/KcIgl94h3OY)

## 📖 プロジェクト概要
ベーカリー現場における売上管理業務の自動化を目的としたWebアプリケーションです。物流現場で培った「業務フローの最適化」という知見を活かし、入力から集計までの工程をシンプルに構築しました。

## ⚙️ 機能一覧
* **売上データ入力:** Webフォームから日次の売上項目をスムーズに入力。
* **Excel自動集計:** 保存されたデータを `openpyxl` を活用してExcelフォーマットへ自動書き出し。
* **データベース連携:** SQLite3を用いたデータの永続化と履歴管理。
* **スマホ・PC連携:** プライベートIP環境下でマルチデバイスから操作可能。

## 💡 開発思想：C案の精神
本システムには「C案の精神」を反映させています。これは「A/B/C案のうち、安全第一で最短ルートを選ぶ」という私の物流現場での考え方です。
エンジニアリングにおいても、複雑な機能を詰め込むのではなく、「店長が迷わず操作できる一列配置のUI」など、現場でのミスを最小限に抑える頑丈な設計を最優先しました。

## 🛠 技術スタック
* **Backend:** Python, Flask (ルーティング, GET/POST, Jinja2)
* **Database:** SQLite3 (CRUD機能)
* **Automation:** openpyxl (Excel動的制御, 自動判定), BeautifulSoup (スクレイピング)
* **Frontend:** JavaScript (動的メニュー行操作)
* **Infrastructure:** Ubuntu 24.04 LTS, .venv (仮想環境), スマホ実機連携

## 🚀 セットアップ手順
ローカル環境で動作させるための手順です。

```bash
# 1. リポジトリのクローン
git clone [https://github.com/tosane932/sales_data_app.git](https://github.com/tosane932/sales_data_app.git)
cd sales_data_app

# 2. 仮想環境の作成と有効化
python3 -m venv .venv
source .venv/bin/activate

# 3. 必要なライブラリのインストール
pip install -r requirements.txt

# 4. アプリの起動
python app.py