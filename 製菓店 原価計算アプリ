## 主な機能

- 材料の購入量・購入単位・購入価格を登録し、材料ごとの使用単価を計算
- kg/g と L/ml の換算
- 材料情報の編集と、材料価格を変更した履歴の保存
- 材料価格を変更したとき、影響するレシピと原価差額を表示
- 材料CSVの一括取り込み（UTF-8/Excel向けCP932、同名材料はスキップまたは価格履歴付きで更新）
- レシピの出来上がり数量、サブレシピ、包装費、作業費、その他経費の登録
- レシピと材料・サブレシピの複製
- 仕込み原価、単位原価、販売価格、粗利、原価率の表示
- 保存時点のレシピ原価履歴、材料・レシピ原価のCSV出力
- SQLiteデータベースのバックアップと、別ファイルとしての復元

食品の密度を使うg/ml換算には対応していません。材料の購入量はレシピで使う単位へ換算して登録してください（例：1kg袋なら1,000g）。

## Windows

初回は run_windows.bat をダブルクリックしてください。Python環境の作成、必要ライブラリのインストール、アプリ起動を自動で行います。Python 3.10以降が必要です。

次回以降も同じ run_windows.bat で起動できます。依存関係が変わったときだけ再インストールします。環境だけを先に作る場合は setup_windows.bat を使います。

## macOS・Linux

ターミナルでアプリのフォルダを開き、次を実行します。初回は仮想環境の作成と依存関係のインストール後、自動で起動します。

```bash
bash run_macos_linux.sh
```

次回以降も同じコマンドで起動できます。環境だけを先に作る場合は bash setup_macos_linux.sh を使います。

## データ保存

材料・レシピのSQLiteデータはOSごとのユーザーデータフォルダに保存されます。アプリフォルダに書き込み権限がなくても利用できます。アプリと同じフォルダにある既存の .db ファイルは、保存先に同名ファイルがない場合、初回起動時にコピーされます。元ファイルは削除されません。

- Windows: %LOCALAPPDATA%\confectionery-cost-app
- macOS: ~/Library/Application Support/confectionery-cost-app
- Linux: ~/.local/share/confectionery-cost-app

保存先を変更する場合は、起動前に環境変数 BAKERY_DATA_DIR を設定します。材料・レシピの保存ファイルを切り替える機能も、この保存先フォルダ内で動作します。

## アプリ終了

画面左の「アプリを終了する」から Streamlit サーバーを停止します。ブラウザのタブは手動で閉じてください。

## 開発時の確認

```bash
python -m unittest discover -s tests -v
```

GitHub Actions では Windows・macOS・Linux と Python 3.11〜3.13 の組み合わせで回帰テストを実行します。

## 販売準備

- CUSTOMER_DISCOVERY.md: 店へのヒアリング質問と記録項目
- PILOT_PLAN.md: 2週間の試用手順と評価基準
- PRODUCT_BACKLOG.md: 試用後の開発優先順位
