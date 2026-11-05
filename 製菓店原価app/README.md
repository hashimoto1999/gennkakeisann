# 製菓店 原価計算アプリ

Windows・macOS・Linux の各PCで使える、Python と Streamlit のローカルアプリです。Python 3.10 以降と Streamlit 1.57 以降が必要です。

## 主な機能

- 材料の購入量・購入単位・購入価格を登録し、材料ごとの使用単価を計算
- kg/g と L/ml の換算
- 材料情報の編集と、材料価格を変更した履歴の保存
- レシピの出来上がり数量、サブレシピ、包装費、作業費、その他経費の登録
- 仕込み原価、単位原価、販売価格、粗利、原価率の表示
- 保存時点のレシピ原価履歴、材料・レシピ原価のCSV出力
- SQLiteデータベースのバックアップと、別ファイルとしての復元

食品の密度を使うg/ml換算には対応していません。材料の購入量はレシピで使う単位へ換算して登録してください（例：1kg袋なら1,000g）。

## Windows

PowerShell でこのフォルダを開き、初回だけセットアップします。

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

起動:

```powershell
.venv\Scripts\python.exe -m streamlit run app.py
```

## macOS・Linux

ターミナルでこのフォルダを開き、初回だけセットアップします。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

起動:

```bash
.venv/bin/python -m streamlit run app.py
```

セットアップと起動は、同梱の `setup_windows.bat` / `run_windows.bat`、または `bash setup_macos_linux.sh` / `bash run_macos_linux.sh` でも実行できます。

## データ保存

材料・レシピの SQLite データはOSごとのユーザーデータフォルダに保存されます。アプリのフォルダに書き込み権限がなくても利用できます。アプリと同じフォルダにある既存の `.db` ファイルは、保存先に同名ファイルがない場合、初回起動時にコピーされます。元ファイルは削除されません。

- Windows: `%LOCALAPPDATA%\confectionery-cost-app`
- macOS: `~/Library/Application Support/confectionery-cost-app`
- Linux: `~/.local/share/confectionery-cost-app`

保存先を変更する場合は、起動前に環境変数 `BAKERY_DATA_DIR` を設定します。材料・レシピの保存ファイルを切り替える機能も、この保存先フォルダ内で動作します。

## アプリ終了

画面左の「アプリを終了する」から Streamlit サーバーを停止します。ブラウザのタブは手動で閉じてください。
