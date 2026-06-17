# Bakery Sales Management System

現場の「困った」を、Pythonで「最適解」へ。

## 📺 デモ動画
以下の画像をクリックすると、YouTubeで実際の動作デモがご覧いただけます。

[![Excel集計を秒で終わらせるPython（デモ動画）](demo_thumbnail.png)](https://youtu.be/KcIgl94h3OY)

## 📖 プロジェクト概要
ベーカリー現場における売上管理業務の自動化を目的としたWebアプリケーションです。物流現場で培った「業務フローの最適化」という知見を活かし、入力から集計、AIによる経営アドバイス生成までを統合しました。

## ⚙️ 機能一覧
* **売上データ入力:** Webフォームから日次の売上項目をスムーズに入力。
* **Excel自動集計:** 保存データを `openpyxl` でExcelフォーマットへ自動書き出し。
* **AI経営アドバイス:** Google Gemini APIを活用し、売上データに基づいた店長向けの助言を自動生成。
* **スマホ・PC連携:** プライベートIP環境下でマルチデバイスから操作可能。

## 💡 開発思想：C案の精神
「A/B/C案のうち、安全第一で最短ルートを選ぶ」という物流現場での考え方を応用。複雑な機能を詰め込まず、「店長が迷わず操作できる一列配置のUI」など、現場のミスを最小限に抑える設計を最優先しました。

## 🛠 技術スタック
* **Backend:** Python, Flask
* **AI:** Google Gemini API (gemini-2.5-flash)
* **Storage:** openpyxl (Excelファイルベースの永続化・月次管理)
* **Automation:** BeautifulSoup (スクレイピング)
* **Frontend:** JavaScript
* **Infrastructure:** Ubuntu 24.04 LTS, .venv

## 🚀 セットアップ手順
```bash
# 1. リポジトリのクローン
git clone [https://github.com/tosane932/sales_data_app.git](https://github.com/tosane932/sales_data_app.git)
cd sales_data_app

# 2. 仮想環境の作成と有効化
python3 -m venv .venv
source .venv/bin/activate

# 3. 必要なライブラリのインストール
pip install -r requirements.txt

# 4. APIキーの設定 (Linux/macOS)
export GEMINI_API_KEY="あなたのAPIキー"

# 5. アプリの起動
python app.py