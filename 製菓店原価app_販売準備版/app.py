from __future__ import annotations

import os
import shutil
import signal
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st
from platformdirs import user_data_dir


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get(
    "BAKERY_DATA_DIR", user_data_dir("confectionery-cost-app", appauthor=False)
)).expanduser()
DEFAULT_DB_NAME = "costing.db"
UNITS = ["g", "kg", "ml", "L", "個", "本", "枚", "袋", "箱"]
UNIT_SCALE = {"g": ("mass", 1), "kg": ("mass", 1000), "ml": ("volume", 1), "L": ("volume", 1000)}


@contextmanager
def connect():
    name = st.session_state.get("db_file", DEFAULT_DB_NAME)
    conn = sqlite3.connect(DATA_DIR / name)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def to_hiragana(text: str) -> str:
    from pykakasi import kakasi
    converter = kakasi()
    return "".join(part["hira"] for part in converter.convert(text))


def kana_sort_key(value: str) -> tuple[tuple[int, int], ...]:
    normalized = unicodedata.normalize("NFD", unicodedata.normalize("NFKC", value))
    chars: list[tuple[int, int]] = []
    for char in normalized:
        code = ord(char)
        if 0x30A1 <= code <= 0x30F6:
            char = chr(code - 0x60)
        if char == "\u3099" and chars:
            chars[-1] = (chars[-1][0], 1)
        elif char == "\u309a" and chars:
            chars[-1] = (chars[-1][0], 2)
        else:
            chars.append((ord(char), 0))
    return tuple(chars)


def conversion_rate(from_unit: str, to_unit: str) -> float:
    if from_unit == to_unit:
        return 1.0
    source, target = UNIT_SCALE.get(from_unit), UNIT_SCALE.get(to_unit)
    if source and target and source[0] == target[0]:
        return source[1] / target[1]
    raise ValueError(f"{from_unit} から {to_unit} への換算はできません。同じ単位か、g/kg・ml/Lの組み合わせにしてください。")


def unit_cost(row) -> float:
    keys = set(row.keys())

    def value(database_name: str, display_name: str):
        return row[database_name] if database_name in keys else row[display_name]

    purchase_unit = value("purchase_unit", "購入単位")
    usage_unit = value("unit", "使用単位")
    purchase_price = value("purchase_price", "購入価格")
    purchase_quantity = value("purchase_quantity", "購入量")
    rate = conversion_rate(purchase_unit, usage_unit)
    return float(purchase_price) / (float(purchase_quantity) * rate)


def prepare_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if DATA_DIR.resolve() == APP_DIR.resolve():
        return
    for source in APP_DIR.glob("*.db"):
        target = DATA_DIR / source.name
        if not target.exists():
            shutil.copy2(source, target)


def ensure_column(conn: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def initialize_db() -> None:
    with connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS ingredients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            name_kana TEXT NOT NULL DEFAULT '',
            unit TEXT NOT NULL,
            purchase_quantity REAL NOT NULL CHECK (purchase_quantity > 0),
            purchase_price REAL NOT NULL CHECK (purchase_price >= 0),
            purchase_unit TEXT NOT NULL DEFAULT ''
        )""")
        ensure_column(conn, "ingredients", "name_kana", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "ingredients", "purchase_unit", "TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE ingredients SET purchase_unit = unit WHERE purchase_unit = ''")
        conn.execute("""CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            name_kana TEXT NOT NULL DEFAULT '',
            yield_quantity REAL NOT NULL DEFAULT 1,
            yield_unit TEXT NOT NULL DEFAULT '個',
            sale_price REAL NOT NULL DEFAULT 0,
            packaging_cost REAL NOT NULL DEFAULT 0,
            labor_cost REAL NOT NULL DEFAULT 0,
            other_cost REAL NOT NULL DEFAULT 0
        )""")
        ensure_column(conn, "recipes", "name_kana", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "recipes", "yield_quantity", "REAL NOT NULL DEFAULT 1")
        ensure_column(conn, "recipes", "yield_unit", "TEXT NOT NULL DEFAULT '個'")
        ensure_column(conn, "recipes", "sale_price", "REAL NOT NULL DEFAULT 0")
        ensure_column(conn, "recipes", "packaging_cost", "REAL NOT NULL DEFAULT 0")
        ensure_column(conn, "recipes", "labor_cost", "REAL NOT NULL DEFAULT 0")
        ensure_column(conn, "recipes", "other_cost", "REAL NOT NULL DEFAULT 0")
        conn.execute("""CREATE TABLE IF NOT EXISTS recipe_items (
            recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
            quantity REAL NOT NULL CHECK (quantity >= 0),
            PRIMARY KEY (recipe_id, ingredient_id)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS recipe_subrecipes (
            recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            subrecipe_id INTEGER NOT NULL REFERENCES recipes(id),
            quantity REAL NOT NULL CHECK (quantity >= 0),
            PRIMARY KEY (recipe_id, subrecipe_id)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
            recorded_at TEXT NOT NULL,
            purchase_quantity REAL NOT NULL,
            purchase_unit TEXT NOT NULL,
            usage_unit TEXT NOT NULL DEFAULT '',
            purchase_price REAL NOT NULL,
            unit_cost REAL NOT NULL
        )""")
        ensure_column(conn, "price_history", "usage_unit", "TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE price_history SET usage_unit = (SELECT unit FROM ingredients WHERE ingredients.id = price_history.ingredient_id) WHERE usage_unit = ''")
        conn.execute("""CREATE TABLE IF NOT EXISTS recipe_cost_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL REFERENCES recipes(id),
            recorded_at TEXT NOT NULL,
            batch_cost REAL NOT NULL,
            unit_cost REAL NOT NULL,
            sale_price REAL NOT NULL,
            yield_quantity REAL NOT NULL,
            yield_unit TEXT NOT NULL
        )""")
        # Fill readings for older records and seed initial price history once.
        for row in conn.execute("SELECT id, name FROM ingredients WHERE name_kana = ''").fetchall():
            conn.execute("UPDATE ingredients SET name_kana = ? WHERE id = ?", (to_hiragana(row["name"]), row["id"]))
        for row in conn.execute("SELECT * FROM ingredients").fetchall():
            if not conn.execute("SELECT 1 FROM price_history WHERE ingredient_id = ? LIMIT 1", (row["id"],)).fetchone():
                conn.execute("INSERT INTO price_history (ingredient_id, recorded_at, purchase_quantity, purchase_unit, usage_unit, purchase_price, unit_cost) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (row["id"], datetime.now().isoformat(timespec="seconds"), row["purchase_quantity"], row["purchase_unit"], row["unit"], row["purchase_price"], unit_cost(row)))
        for row in conn.execute("SELECT id, name FROM recipes WHERE name_kana = ''").fetchall():
            conn.execute("UPDATE recipes SET name_kana = ? WHERE id = ?", (to_hiragana(row["name"]), row["id"]))


def database_picker() -> None:
    files = sorted(path.name for path in DATA_DIR.glob("*.db"))
    if DEFAULT_DB_NAME not in files:
        files.insert(0, DEFAULT_DB_NAME)
    current = st.session_state.get("db_file", DEFAULT_DB_NAME)
    if current not in files:
        files.append(current)
    st.sidebar.subheader("保存データ")
    st.sidebar.selectbox("保存ファイル", files, key="db_file")
    active_path = DATA_DIR / st.session_state.get("db_file", DEFAULT_DB_NAME)
    if active_path.exists():
        st.sidebar.download_button("データベースをバックアップ", active_path.read_bytes(), file_name=active_path.name, mime="application/x-sqlite3", key="db_backup")
    st.sidebar.download_button("材料をCSV出力", ingredients_csv(), file_name="materials.csv", mime="text/csv", key="export_ingredients")
    st.sidebar.download_button("レシピ原価をCSV出力", recipes_csv(), file_name="recipes.csv", mime="text/csv", key="export_recipes")
    uploaded = st.sidebar.file_uploader("DBバックアップを別ファイルとして復元", type=["db", "sqlite"], key="restore_db")
    restore_name = st.sidebar.text_input("復元後のファイル名", placeholder="例：復元データ", key="restore_db_name")
    if uploaded and st.sidebar.button("バックアップを復元", key="restore_db_button"):
        safe_name = Path(restore_name.strip().replace("\\", "/")).name
        if not safe_name:
            st.sidebar.error("復元後のファイル名を入力してください。")
        else:
            if not safe_name.lower().endswith(".db"):
                safe_name += ".db"
            target = DATA_DIR / safe_name
            if target.exists():
                st.sidebar.error("同じ名前の保存ファイルがあります。別の名前を指定してください。")
            else:
                target.write_bytes(uploaded.getvalue())
                try:
                    check = sqlite3.connect(target)
                    integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
                    tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    check.close()
                    if integrity != "ok" or not {"ingredients", "recipes"}.issubset(tables):
                        raise ValueError("有効な原価計算データベースではありません。")
                    st.session_state["db_file"] = safe_name
                    st.success("バックアップを復元しました。")
                    st.rerun()
                except Exception as exc:
                    target.unlink(missing_ok=True)
                    st.sidebar.error(f"復元できませんでした：{exc}")
    with st.sidebar.expander("保存ファイルを新規作成"):
        new_name = st.text_input("ファイル名（.db は自動付与）", key="new_db_name")
        if st.button("新規作成", key="create_db_file"):
            name = Path(new_name.strip().replace("\\", "/")).name
            if not name:
                st.error("ファイル名を入力してください。")
            else:
                if not name.lower().endswith(".db"):
                    name += ".db"
                if (DATA_DIR / name).exists():
                    st.error("そのファイル名はすでにあります。")
                else:
                    st.session_state["db_file"] = name
                    initialize_db()
                    st.rerun()


def ingredients_csv() -> bytes:
    try:
        df = load_ingredients()
        return df.drop(columns=["id"]).to_csv(index=False).encode("utf-8-sig")
    except sqlite3.OperationalError:
        return "材料名,読み,使用単位,購入単位,購入量,購入価格\n".encode("utf-8-sig")


def recipes_csv() -> bytes:
    try:
        with connect() as conn:
            rows = conn.execute("SELECT * FROM recipes ORDER BY name_kana, name").fetchall()
            data = []
            for row in rows:
                batch = recipe_cost(int(row["id"]))["total"]
                unit = batch / max(float(row["yield_quantity"]), 0.000001)
                data.append({"レシピ": row["name"], "出来上がり数": row["yield_quantity"], "単位": row["yield_unit"], "仕込み原価": batch, "1個原価": unit, "販売価格": row["sale_price"], "粗利": row["sale_price"] - unit})
        return pd.DataFrame(data).to_csv(index=False).encode("utf-8-sig")
    except (sqlite3.OperationalError, ValueError):
        return "レシピ,出来上がり数,単位,仕込み原価,1個原価,販売価格,粗利\n".encode("utf-8-sig")


def load_ingredients() -> pd.DataFrame:
    with connect() as conn:
        df = pd.read_sql_query("SELECT id, name AS 材料名, name_kana AS 読み, unit AS 使用単位, purchase_unit AS 購入単位, purchase_quantity AS 購入量, purchase_price AS 購入価格 FROM ingredients", conn)
    if df.empty:
        return df
    df["_sort"] = df.apply(lambda r: kana_sort_key(r["読み"] or r["材料名"]), axis=1)
    return df.sort_values("_sort", kind="stable").drop(columns="_sort").reset_index(drop=True)


def add_price_history(conn: sqlite3.Connection, ingredient_id: int) -> None:
    row = conn.execute("SELECT * FROM ingredients WHERE id = ?", (ingredient_id,)).fetchone()
    conn.execute("INSERT INTO price_history (ingredient_id, recorded_at, purchase_quantity, purchase_unit, usage_unit, purchase_price, unit_cost) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (ingredient_id, datetime.now().isoformat(timespec="seconds"), row["purchase_quantity"], row["purchase_unit"], row["unit"], row["purchase_price"], unit_cost(row)))


def convert_saved_recipe_quantities(conn: sqlite3.Connection, ingredient_id: int, old_unit: str, new_unit: str) -> None:
    rate = conversion_rate(old_unit, new_unit)
    if old_unit != new_unit:
        conn.execute("UPDATE recipe_items SET quantity = quantity * ? WHERE ingredient_id = ?", (rate, ingredient_id))


def import_ingredients_csv(data: bytes, update_existing: bool = False) -> dict[str, int]:
    required = ["材料名", "読み", "使用単位", "購入単位", "購入量", "購入価格"]
    try:
        frame = pd.read_csv(BytesIO(data), encoding="utf-8-sig")
    except UnicodeDecodeError:
        frame = pd.read_csv(BytesIO(data), encoding="cp932")
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError("CSVに必要な列がありません: " + ", ".join(missing))
    seen: set[str] = set()
    added = updated = skipped = 0
    with connect() as conn:
        for index, row in frame.iterrows():
            name = str(row["材料名"]).strip()
            if not name or name.lower() == "nan":
                raise ValueError(f"{index + 2}行目: 材料名が空です。")
            if name in seen:
                raise ValueError(f"CSV内に「{name}」が重複しています。")
            seen.add(name)
            usage_unit = str(row["使用単位"]).strip()
            purchase_unit = str(row["購入単位"]).strip()
            if usage_unit not in UNITS or purchase_unit not in UNITS:
                raise ValueError(f"{index + 2}行目: 使用単位または購入単位が未対応です。")
            try:
                quantity, price = float(row["購入量"]), float(row["購入価格"])
            except (TypeError, ValueError):
                raise ValueError(f"{index + 2}行目: 購入量と購入価格は数値で入力してください。")
            if not pd.notna(quantity) or quantity <= 0 or not pd.notna(price) or price < 0:
                raise ValueError(f"{index + 2}行目: 購入量は0より大きく、購入価格は0以上にしてください。")
            conversion_rate(purchase_unit, usage_unit)
            reading = "" if pd.isna(row["読み"]) else str(row["読み"]).strip()
            reading = reading or to_hiragana(name)
            existing = conn.execute("SELECT * FROM ingredients WHERE name = ?", (name,)).fetchone()
            values = (reading, usage_unit, quantity, price, purchase_unit)
            if existing and not update_existing:
                skipped += 1
                continue
            if existing:
                if existing["unit"] != usage_unit:
                    convert_saved_recipe_quantities(conn, int(existing["id"]), existing["unit"], usage_unit)
                changed = existing["unit"] != usage_unit or existing["purchase_unit"] != purchase_unit or float(existing["purchase_quantity"]) != quantity or float(existing["purchase_price"]) != price
                conn.execute("UPDATE ingredients SET name_kana = ?, unit = ?, purchase_quantity = ?, purchase_price = ?, purchase_unit = ? WHERE id = ?", (*values, int(existing["id"])))
                if changed:
                    add_price_history(conn, int(existing["id"]))
                updated += 1
            else:
                cursor = conn.execute("INSERT INTO ingredients (name, name_kana, unit, purchase_quantity, purchase_price, purchase_unit) VALUES (?, ?, ?, ?, ?, ?)", (name, *values))
                add_price_history(conn, int(cursor.lastrowid))
                added += 1
    return {"added": added, "updated": updated, "skipped": skipped}


def ingredient_usage_in_recipe(recipe_id: int, ingredient_id: int, stack: set[int] | None = None) -> float:
    """Return ingredient quantity used per batch, including nested subrecipes."""
    stack = stack or set()
    if recipe_id in stack:
        raise ValueError("サブレシピが循環しています。")
    stack = set(stack)
    stack.add(recipe_id)
    with connect() as conn:
        direct = conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM recipe_items WHERE recipe_id = ? AND ingredient_id = ?",
            (recipe_id, ingredient_id),
        ).fetchone()[0]
        subs = conn.execute(
            "SELECT rs.quantity, r.id, r.yield_quantity FROM recipe_subrecipes rs JOIN recipes r ON r.id = rs.subrecipe_id WHERE rs.recipe_id = ?",
            (recipe_id,),
        ).fetchall()
    usage = float(direct)
    for sub in subs:
        nested_usage = ingredient_usage_in_recipe(int(sub["id"]), ingredient_id, stack)
        usage += float(sub["quantity"]) / max(float(sub["yield_quantity"]), 0.000001) * nested_usage
    return usage


def price_change_impacts(ingredient_id: int) -> pd.DataFrame:
    with connect() as conn:
        history = conn.execute(
            "SELECT unit_cost, usage_unit FROM price_history WHERE ingredient_id = ? ORDER BY id DESC LIMIT 2",
            (ingredient_id,),
        ).fetchall()
        current = conn.execute("SELECT unit FROM ingredients WHERE id = ?", (ingredient_id,)).fetchone()
        recipes = conn.execute("SELECT id, name, yield_quantity, yield_unit FROM recipes ORDER BY name").fetchall()
    columns = ["レシピ", "変更前の仕込み原価", "価格変更による増減", "現在の仕込み原価"]
    if len(history) < 2:
        return pd.DataFrame(columns=columns)
    rate = conversion_rate(history[1]["usage_unit"], current["unit"])
    old_cost = float(history[1]["unit_cost"]) / rate
    new_cost = float(history[0]["unit_cost"])
    rows = []
    for recipe in recipes:
        used = ingredient_usage_in_recipe(int(recipe["id"]), ingredient_id)
        if used <= 0:
            continue
        current_total = float(recipe_cost(int(recipe["id"]))["total"])
        delta = used * (new_cost - old_cost)
        rows.append({
            "レシピ": recipe["name"],
            "変更前の仕込み原価": current_total - delta,
            "価格変更による増減": delta,
            "現在の仕込み原価": current_total,
        })
    return pd.DataFrame(rows, columns=columns)


def duplicate_recipe(source_id: int, new_name: str) -> int:
    clean_name = new_name.strip()
    if not clean_name:
        raise ValueError("複製後のレシピ名を入力してください。")
    with connect() as conn:
        source = conn.execute("SELECT * FROM recipes WHERE id = ?", (source_id,)).fetchone()
        kana = to_hiragana(clean_name)
        cursor = conn.execute(
            "INSERT INTO recipes (name, name_kana, yield_quantity, yield_unit, sale_price, packaging_cost, labor_cost, other_cost) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (clean_name, kana, source["yield_quantity"], source["yield_unit"], source["sale_price"], source["packaging_cost"], source["labor_cost"], source["other_cost"]),
        )
        new_id = int(cursor.lastrowid)
        conn.execute("INSERT INTO recipe_items (recipe_id, ingredient_id, quantity) SELECT ?, ingredient_id, quantity FROM recipe_items WHERE recipe_id = ?", (new_id, source_id))
        conn.execute("INSERT INTO recipe_subrecipes (recipe_id, subrecipe_id, quantity) SELECT ?, subrecipe_id, quantity FROM recipe_subrecipes WHERE recipe_id = ?", (new_id, source_id))
    return new_id


def master_page() -> None:
    st.header("材料マスタ")
    st.caption("購入単位はkg/Lにも対応します。個数単位など換算できない材料は、購入量を使用単位に換算して登録してください。")
    with st.form("ingredient_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        name = c1.text_input("材料名", placeholder="例：薄力粉")
        reading = c1.text_input("読み（かな・空欄なら自動推定）")
        usage_unit = c2.selectbox("レシピで使う単位", UNITS, key="new_usage_unit")
        purchase_unit = c2.selectbox("購入単位", UNITS, key="new_purchase_unit")
        c3, c4 = st.columns(2)
        quantity = c3.number_input("購入量（購入単位）", min_value=0.001, value=1.0, step=1.0)
        price = c4.number_input("購入価格（円）", min_value=0.0, value=0.0, step=10.0)
        submit = st.form_submit_button("材料を登録", type="primary")
        if submit:
            clean_name = name.strip()
            try:
                kana = reading.strip() or to_hiragana(clean_name)
                conversion_rate(purchase_unit, usage_unit)
                if not clean_name:
                    raise ValueError("材料名を入力してください。")
                with connect() as conn:
                    cur = conn.execute("INSERT INTO ingredients (name, name_kana, unit, purchase_quantity, purchase_price, purchase_unit) VALUES (?, ?, ?, ?, ?, ?)", (clean_name, kana, usage_unit, quantity, price, purchase_unit))
                    add_price_history(conn, cur.lastrowid)
                st.success(f"「{clean_name}」を登録しました。")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
            except sqlite3.IntegrityError:
                st.error("同じ材料名がすでに登録されています。")

    with st.expander("CSVから材料を一括登録"):
        st.caption("材料名・読み・使用単位・購入単位・購入量・購入価格の列を含むCSVを読み込みます。既存名は初期設定ではスキップします。")
        st.download_button(
            "CSVひな形をダウンロード",
            "材料名,読み,使用単位,購入単位,購入量,購入価格\n".encode("utf-8-sig"),
            file_name="材料マスタひな形.csv",
            mime="text/csv",
            key="ingredient_csv_template",
        )
        uploaded_csv = st.file_uploader("材料CSV", type=["csv"], key="ingredient_csv_upload")
        update_existing = st.checkbox("同名の材料をCSVの内容で更新", key="ingredient_csv_update")
        if uploaded_csv and st.button("CSVを取り込む", key="ingredient_csv_import"):
            try:
                result = import_ingredients_csv(uploaded_csv.getvalue(), update_existing)
                st.success(f"新規 {result['added']}件、更新 {result['updated']}件、スキップ {result['skipped']}件")
                st.rerun()
            except (ValueError, pd.errors.ParserError, UnicodeDecodeError) as exc:
                st.error(f"取り込めませんでした：{exc}")

    ingredients = load_ingredients()
    if ingredients.empty:
        st.info("材料はまだ登録されていません。")
        return
    st.subheader("登録済み材料の編集")
    editing = st.data_editor(
        ingredients[["材料名", "読み", "使用単位", "購入単位", "購入量", "購入価格"]],
        width="stretch", hide_index=True, key="ingredients_editor",
        column_config={
            "材料名": st.column_config.TextColumn("材料名"),
            "読み": st.column_config.TextColumn("読み（かな）"),
            "使用単位": st.column_config.SelectboxColumn("レシピ単位", options=UNITS),
            "購入単位": st.column_config.SelectboxColumn("購入単位", options=UNITS),
            "購入量": st.column_config.NumberColumn("購入量", min_value=0.001, step=1.0),
            "購入価格": st.column_config.NumberColumn("購入価格（円）", min_value=0.0, step=10.0),
        },
    )
    if st.button("材料情報を保存", type="primary", key="save_ingredients"):
        try:
            with connect() as conn:
                for row, ingredient_id in zip(editing.to_dict("records"), ingredients["id"]):
                    new = {"name": str(row["材料名"]).strip(), "name_kana": str(row["読み"]).strip(), "unit": row["使用単位"], "purchase_unit": row["購入単位"], "purchase_quantity": float(row["購入量"]), "purchase_price": float(row["購入価格"])}
                    if not new["name"] or not new["name_kana"]:
                        raise ValueError("材料名と読みを入力してください。")
                    conversion_rate(new["purchase_unit"], new["unit"])
                    old = conn.execute("SELECT * FROM ingredients WHERE id = ?", (int(ingredient_id),)).fetchone()
                    changed_price = any(float(old[k]) != float(new[k]) for k in ("purchase_quantity", "purchase_price")) or old["purchase_unit"] != new["purchase_unit"] or old["unit"] != new["unit"]
                    if old["unit"] != new["unit"]:
                        convert_saved_recipe_quantities(conn, int(ingredient_id), old["unit"], new["unit"])
                    conn.execute("UPDATE ingredients SET name = ?, name_kana = ?, unit = ?, purchase_unit = ?, purchase_quantity = ?, purchase_price = ? WHERE id = ?", (new["name"], new["name_kana"], new["unit"], new["purchase_unit"], new["purchase_quantity"], new["purchase_price"], int(ingredient_id)))
                    if changed_price:
                        add_price_history(conn, int(ingredient_id))
            st.success("材料情報を保存しました。原価に反映されました。")
            st.rerun()
        except (ValueError, sqlite3.IntegrityError) as exc:
            st.error(f"保存できませんでした：{exc}")

    cost_view = ingredients[["材料名", "使用単位", "購入量", "購入単位", "購入価格"]].copy()
    cost_view["現在の単価（円/使用単位）"] = ingredients.apply(unit_cost, axis=1)
    st.dataframe(cost_view, width="stretch", hide_index=True)

    with st.expander("材料価格の履歴"):
        choice = st.selectbox("材料", ingredients["材料名"].tolist(), key="price_history_choice")
        ingredient_id = int(ingredients.loc[ingredients["材料名"] == choice, "id"].iloc[0])
        with connect() as conn:
            history = pd.read_sql_query("SELECT recorded_at AS 更新日時, purchase_quantity AS 購入量, purchase_unit AS 購入単位, usage_unit AS レシピ単位, purchase_price AS 購入価格, unit_cost AS 使用単位あたり単価 FROM price_history WHERE ingredient_id = ? ORDER BY id DESC", conn, params=(ingredient_id,))
        st.dataframe(history, width="stretch", hide_index=True)
        impacts = price_change_impacts(ingredient_id)
        st.markdown("**価格変更のレシピ影響**")
        if impacts.empty:
            st.caption("価格変更履歴が2件になると、影響を受けるレシピを表示します。")
        else:
            st.dataframe(impacts.style.format({column: "¥{:,.2f}" for column in impacts.columns if column != "レシピ"}), width="stretch", hide_index=True)

    with st.expander("材料を削除"):
        choice = st.selectbox("削除する材料", ingredients["材料名"].tolist(), key="delete_ingredient")
        if st.button("選択した材料を削除", key="delete_ingredient_button"):
            ingredient_id = int(ingredients.loc[ingredients["材料名"] == choice, "id"].iloc[0])
            with connect() as conn:
                usage_count = conn.execute("SELECT COUNT(*) FROM recipe_items WHERE ingredient_id = ?", (ingredient_id,)).fetchone()[0]
                if usage_count:
                    st.error("レシピで使用中の材料です。先にレシピから外してください。")
                else:
                    conn.execute("DELETE FROM price_history WHERE ingredient_id = ?", (ingredient_id,))
                    conn.execute("DELETE FROM ingredients WHERE id = ?", (ingredient_id,))
                    st.success("材料を削除しました。")
                    st.rerun()


def recipe_reaches(start: int, target: int, seen: set[int] | None = None) -> bool:
    if start == target:
        return True
    seen = seen or set()
    if start in seen:
        return False
    seen.add(start)
    with connect() as conn:
        children = [r[0] for r in conn.execute("SELECT subrecipe_id FROM recipe_subrecipes WHERE recipe_id = ?", (start,))]
    return any(recipe_reaches(child, target, seen) for child in children)


def recipe_cost(recipe_id: int, stack: set[int] | None = None) -> dict:
    stack = stack or set()
    if recipe_id in stack:
        raise ValueError("サブレシピが循環しています。")
    stack = set(stack)
    stack.add(recipe_id)
    with connect() as conn:
        recipe = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        rows = conn.execute("SELECT ri.quantity, i.* FROM recipe_items ri JOIN ingredients i ON i.id = ri.ingredient_id WHERE ri.recipe_id = ?", (recipe_id,)).fetchall()
        subs = conn.execute("SELECT rs.quantity, r.id, r.name, r.yield_quantity, r.yield_unit FROM recipe_subrecipes rs JOIN recipes r ON r.id = rs.subrecipe_id WHERE rs.recipe_id = ?", (recipe_id,)).fetchall()
    materials = []
    material_total = 0.0
    for row in rows:
        cost = float(row["quantity"]) * unit_cost(row)
        material_total += cost
        materials.append({"name": row["name"], "quantity": row["quantity"], "unit": row["unit"], "cost": cost})
    sub_rows = []
    sub_total = 0.0
    for sub in subs:
        info = recipe_cost(int(sub["id"]), stack)
        per_unit = info["total"] / max(float(sub["yield_quantity"]), 0.000001)
        cost = float(sub["quantity"]) * per_unit
        sub_total += cost
        sub_rows.append({"name": sub["name"], "quantity": sub["quantity"], "unit": sub["yield_unit"], "cost": cost})
    yield_qty = float(recipe["yield_quantity"])
    packaging = float(recipe["packaging_cost"]) * yield_qty
    labor = float(recipe["labor_cost"])
    other = float(recipe["other_cost"])
    return {"recipe": recipe, "materials": materials, "subrecipes": sub_rows, "material_total": material_total, "subrecipe_total": sub_total, "packaging_total": packaging, "labor": labor, "other": other, "total": material_total + sub_total + packaging + labor + other}


def create_recipe_form() -> None:
    with st.form("new_recipe_form", clear_on_submit=True):
        name = st.text_input("レシピ名", placeholder="例：いちごのショートケーキ")
        reading = st.text_input("読み（かな・空欄なら自動推定）")
        c1, c2 = st.columns(2)
        yield_qty = c1.number_input("出来上がり数量", min_value=0.001, value=1.0, step=1.0)
        yield_unit = c2.selectbox("出来上がり単位", UNITS, key="new_recipe_yield_unit")
        submitted = st.form_submit_button("レシピを作成", type="primary")
        if submitted:
            clean_name = name.strip()
            if not clean_name:
                st.error("レシピ名を入力してください。")
            else:
                try:
                    kana = reading.strip() or to_hiragana(clean_name)
                    with connect() as conn:
                        conn.execute("INSERT INTO recipes (name, name_kana, yield_quantity, yield_unit) VALUES (?, ?, ?, ?)", (clean_name, kana, yield_qty, yield_unit))
                    st.success("レシピを作成しました。")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("同じレシピ名がすでにあります。")


def recipe_page() -> None:
    st.header("レシピマスター・原価計算")
    with connect() as conn:
        recipes = pd.read_sql_query("SELECT * FROM recipes", conn)
    if recipes.empty:
        st.info("新しいレシピを作成してください。")
        create_recipe_form()
        return
    recipes["_sort"] = recipes.apply(lambda row: kana_sort_key(row["name_kana"] or row["name"]), axis=1)
    recipes = recipes.sort_values("_sort", kind="stable")
    mode = st.radio("レシピ", ["既存レシピ", "新しいレシピ"], horizontal=True)
    if mode == "新しいレシピ":
        create_recipe_form()
        return
    names = recipes["name"].tolist()
    selected_name = st.selectbox("レシピを選択", names)
    recipe_id = int(recipes.loc[recipes["name"] == selected_name, "id"].iloc[0])
    recipe = recipes.loc[recipes["id"] == recipe_id].iloc[0]

    with st.form(f"recipe_info_{recipe_id}"):
        st.subheader("レシピ情報と販売条件")
        c1, c2 = st.columns(2)
        name = c1.text_input("レシピ名", value=recipe["name"], key=f"recipe_name_{recipe_id}")
        reading = c2.text_input("読み（かな）", value=recipe["name_kana"], key=f"recipe_reading_{recipe_id}")
        c3, c4 = st.columns(2)
        yield_qty = c3.number_input("出来上がり数量", min_value=0.001, value=float(recipe["yield_quantity"]), step=1.0, key=f"yield_qty_{recipe_id}")
        yield_unit = c4.selectbox("出来上がり単位", UNITS, index=UNITS.index(recipe["yield_unit"]) if recipe["yield_unit"] in UNITS else 0, key=f"yield_unit_{recipe_id}")
        c5, c6 = st.columns(2)
        sale_price = c5.number_input("販売価格（1単位・円）", min_value=0.0, value=float(recipe["sale_price"]), step=10.0, key=f"sale_price_{recipe_id}")
        packaging = c6.number_input("包装費（1単位・円）", min_value=0.0, value=float(recipe["packaging_cost"]), step=1.0, key=f"packaging_{recipe_id}")
        c7, c8 = st.columns(2)
        labor = c7.number_input("作業費（仕込み全体・円）", min_value=0.0, value=float(recipe["labor_cost"]), step=100.0, key=f"labor_{recipe_id}")
        other = c8.number_input("その他経費（仕込み全体・円）", min_value=0.0, value=float(recipe["other_cost"]), step=100.0, key=f"other_{recipe_id}")
        info_save = st.form_submit_button("レシピ情報を保存")
        if info_save:
            clean_name = name.strip()
            try:
                kana = reading.strip() or to_hiragana(clean_name)
                with connect() as conn:
                    conn.execute("UPDATE recipes SET name = ?, name_kana = ?, yield_quantity = ?, yield_unit = ?, sale_price = ?, packaging_cost = ?, labor_cost = ?, other_cost = ? WHERE id = ?", (clean_name, kana, yield_qty, yield_unit, sale_price, packaging, labor, other, recipe_id))
                st.success("レシピ情報を保存しました。")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("同じレシピ名がすでにあります。")

    ingredients = load_ingredients()
    if ingredients.empty:
        st.info("材料マスタに材料を登録してください。")
        return
    st.subheader("材料とサブレシピ")
    with connect() as conn:
        saved = pd.read_sql_query("SELECT ingredient_id, quantity FROM recipe_items WHERE recipe_id = ?", conn, params=(recipe_id,))
        saved_subs = pd.read_sql_query("SELECT subrecipe_id, quantity FROM recipe_subrecipes WHERE recipe_id = ?", conn, params=(recipe_id,))
        all_recipes = pd.read_sql_query("SELECT id, name, yield_quantity, yield_unit FROM recipes WHERE id != ?", conn, params=(recipe_id,))
    direct_by_id = dict(zip(saved["ingredient_id"], saved["quantity"])) if not saved.empty else {}
    ids = ingredients["id"].astype(int).tolist()
    editor = st.data_editor(pd.DataFrame({"材料": ingredients["材料名"], "使用量": [float(direct_by_id.get(i, 0.0)) for i in ids]}),
        width="stretch", hide_index=True, disabled=["材料"], key=f"recipe_materials_{recipe_id}",
        column_config={"材料": st.column_config.TextColumn("材料"), "使用量": st.column_config.NumberColumn("使用量", min_value=0.0, step=1.0, format="%.3f")})

    if all_recipes.empty:
        eligible = all_recipes.copy()
    else:
        excluded = all_recipes["id"].map(
            lambda candidate: recipe_reaches(int(candidate), recipe_id)
        ).astype(bool)
        eligible = all_recipes.loc[~excluded].copy()
    subs_by_id = dict(zip(saved_subs["subrecipe_id"], saved_subs["quantity"])) if not saved_subs.empty else {}
    sub_ids = eligible["id"].astype(int).tolist()
    sub_labels = [f"{r['name']}（{r['yield_unit']}）" for _, r in eligible.iterrows()]
    sub_editor = st.data_editor(pd.DataFrame({"サブレシピ": sub_labels, "使用量": [float(subs_by_id.get(i, 0.0)) for i in sub_ids]}),
        width="stretch", hide_index=True, disabled=["サブレシピ"], key=f"recipe_subs_{recipe_id}",
        column_config={"サブレシピ": st.column_config.TextColumn("サブレシピ"), "使用量": st.column_config.NumberColumn("使用量（サブレシピの出来上がり単位）", min_value=0.0, step=1.0, format="%.3f")})
    st.caption("出来上がり数量を基準に、サブレシピはその単位で使用量を入力します。循環する組み合わせは選択肢から除外されます。")

    material_cost = 0.0
    material_lines = []
    ingredient_rows = {int(row["id"]): row for _, row in ingredients.iterrows()}
    try:
        for row, ingredient_id in zip(editor.to_dict("records"), ids):
            qty = float(row["使用量"] or 0)
            if qty > 0:
                ing = ingredient_rows[ingredient_id]
                cost = qty * float(ing["購入価格"]) / (float(ing["購入量"]) * conversion_rate(ing["購入単位"], ing["使用単位"]))
                material_cost += cost
                material_lines.append({"内訳": ing["材料名"], "使用量": f"{qty:g} {ing['使用単位']}", "原価": cost})
        sub_cost = 0.0
        sub_lines = []
        for row, sub_id in zip(sub_editor.to_dict("records"), sub_ids):
            qty = float(row["使用量"] or 0)
            if qty > 0:
                sub_info = recipe_cost(sub_id)
                sub_record = eligible.loc[eligible["id"] == sub_id].iloc[0]
                cost = qty * sub_info["total"] / max(float(sub_record["yield_quantity"]), 0.000001)
                sub_cost += cost
                sub_lines.append({"内訳": sub_record["name"], "使用量": f"{qty:g} {sub_record['yield_unit']}", "原価": cost})
        yield_qty = float(recipe["yield_quantity"])
        packaging_total = float(recipe["packaging_cost"]) * yield_qty
        total = material_cost + sub_cost + packaging_total + float(recipe["labor_cost"]) + float(recipe["other_cost"])
        per_unit = total / max(yield_qty, 0.000001)
        multiplier = st.number_input("仕込み倍率", min_value=0.1, value=1.0, step=0.5, key=f"multiplier_{recipe_id}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("仕込み原価", f"¥{total * multiplier:,.2f}")
        c2.metric(f"1 {recipe['yield_unit']}あたり原価", f"¥{per_unit:,.2f}")
        c3.metric("販売価格", f"¥{float(recipe['sale_price']):,.2f}")
        profit = float(recipe["sale_price"]) - per_unit
        ratio = per_unit / float(recipe["sale_price"]) * 100 if float(recipe["sale_price"]) > 0 else 0
        c4.metric("粗利 / 原価率", f"¥{profit:,.2f} / {ratio:.1f}%")
        st.caption(f"倍率 {multiplier:g} の仕込み量：{yield_qty * multiplier:g} {recipe['yield_unit']}")
        if material_lines or sub_lines:
            details = material_lines + sub_lines
            details.extend([
                {"内訳": "包装費", "使用量": f"{yield_qty:g} × ¥{recipe['packaging_cost']:g}", "原価": packaging_total},
                {"内訳": "作業費", "使用量": "仕込み全体", "原価": float(recipe["labor_cost"])},
                {"内訳": "その他経費", "使用量": "仕込み全体", "原価": float(recipe["other_cost"])},
            ])
            table = pd.DataFrame(details)
            table["原価"] = table["原価"].map(lambda x: f"¥{x:,.2f}")
            st.dataframe(table, width="stretch", hide_index=True)
        if st.button("材料・サブレシピを保存し、原価履歴を記録", type="primary", key=f"save_recipe_{recipe_id}"):
            with connect() as conn:
                conn.execute("DELETE FROM recipe_items WHERE recipe_id = ?", (recipe_id,))
                conn.executemany("INSERT INTO recipe_items (recipe_id, ingredient_id, quantity) VALUES (?, ?, ?)", [(recipe_id, i, float(r["使用量"])) for r, i in zip(editor.to_dict("records"), ids) if float(r["使用量"] or 0) > 0])
                conn.execute("DELETE FROM recipe_subrecipes WHERE recipe_id = ?", (recipe_id,))
                conn.executemany("INSERT INTO recipe_subrecipes (recipe_id, subrecipe_id, quantity) VALUES (?, ?, ?)", [(recipe_id, i, float(r["使用量"])) for r, i in zip(sub_editor.to_dict("records"), sub_ids) if float(r["使用量"] or 0) > 0])
                conn.execute("INSERT INTO recipe_cost_history (recipe_id, recorded_at, batch_cost, unit_cost, sale_price, yield_quantity, yield_unit) VALUES (?, ?, ?, ?, ?, ?, ?)", (recipe_id, datetime.now().isoformat(timespec="seconds"), total, per_unit, recipe["sale_price"], yield_qty, recipe["yield_unit"]))
            st.success("レシピ内容と原価スナップショットを保存しました。")
            st.rerun()
    except ValueError as exc:
        st.error(str(exc))

    with st.expander("原価履歴"):
        with connect() as conn:
            history = pd.read_sql_query("SELECT recorded_at AS 記録日時, batch_cost AS 仕込み原価, unit_cost AS 単位原価, sale_price AS 販売価格, yield_quantity AS 出来上がり数量, yield_unit AS 単位 FROM recipe_cost_history WHERE recipe_id = ? ORDER BY recorded_at DESC", conn, params=(recipe_id,))
        st.dataframe(history, width="stretch", hide_index=True)
    with st.expander("レシピを複製"):
        duplicate_name = st.text_input("複製後のレシピ名", value=f"{selected_name} コピー", key=f"duplicate_name_{recipe_id}")
        if st.button("材料・サブレシピも複製", key=f"duplicate_recipe_{recipe_id}"):
            try:
                duplicate_recipe(recipe_id, duplicate_name)
                st.success("レシピを複製しました。")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("同じレシピ名がすでにあります。")
            except ValueError as exc:
                st.error(str(exc))
    with st.expander("レシピを削除"):
        confirmed = st.checkbox(f"「{selected_name}」を削除することを確認しました", key=f"confirm_delete_recipe_{recipe_id}")
        if st.button("選択したレシピを削除", disabled=not confirmed, key=f"delete_recipe_{recipe_id}"):
            with connect() as conn:
                conn.execute("DELETE FROM recipe_subrecipes WHERE subrecipe_id = ? OR recipe_id = ?", (recipe_id, recipe_id))
                conn.execute("DELETE FROM recipe_items WHERE recipe_id = ?", (recipe_id,))
                conn.execute("DELETE FROM recipe_cost_history WHERE recipe_id = ?", (recipe_id,))
                conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
            st.success("レシピを削除しました。")
            st.rerun()


def main() -> None:
    st.set_page_config(page_title="製菓店 原価計算", page_icon="🍰", layout="wide")
    prepare_data_dir()
    st.title("🍰 製菓店 原価計算")
    initialize_db()
    database_picker()
    initialize_db()
    st.sidebar.caption(f"保存先：{st.session_state.get('db_file', DEFAULT_DB_NAME)}")
    st.sidebar.divider()
    if st.sidebar.button("アプリを終了する", key="exit_app"):
        st.session_state["confirm_exit_app"] = True
    if st.session_state.get("confirm_exit_app"):
        st.sidebar.warning("アプリを終了しますか？")
        if st.sidebar.button("終了を確定", key="confirm_exit"):
            os.kill(os.getpid(), signal.SIGTERM)
        if st.sidebar.button("キャンセル", key="cancel_exit"):
            st.session_state["confirm_exit_app"] = False
            st.rerun()
    page = st.sidebar.radio("ページ", ["材料マスタ", "レシピマスター"])
    if page == "材料マスタ":
        master_page()
    else:
        recipe_page()


if __name__ == "__main__":
    main()
