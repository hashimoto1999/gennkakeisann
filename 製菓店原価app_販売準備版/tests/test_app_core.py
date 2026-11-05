import sys
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import app  # noqa: E402


class AppCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="bakery-tests-")
        app.DATA_DIR = Path(self.temp_dir.name)
        app.DATA_DIR.mkdir(parents=True, exist_ok=True)
        app.initialize_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    def add_ingredient(self, name, usage_unit, purchase_qty, purchase_unit, price):
        with app.connect() as conn:
            return conn.execute(
                """INSERT INTO ingredients
                (name, name_kana, unit, purchase_quantity, purchase_price, purchase_unit)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (name, name, usage_unit, purchase_qty, price, purchase_unit),
            ).lastrowid

    def test_empty_material_list_loads(self):
        self.assertTrue(app.load_ingredients().empty)

    def test_kilograms_convert_to_grams(self):
        material = {
            "purchase_unit": "kg",
            "unit": "g",
            "purchase_quantity": 1,
            "purchase_price": 500,
        }
        self.assertEqual(app.unit_cost(material), 0.5)

    def test_japanese_display_column_names_are_supported(self):
        material = {
            "購入単位": "kg",
            "使用単位": "g",
            "購入量": 1,
            "購入価格": 500,
        }
        self.assertEqual(app.unit_cost(material), 0.5)

    def test_incompatible_units_are_rejected(self):
        with self.assertRaises(ValueError):
            app.conversion_rate("L", "g")

    def test_changing_usage_unit_converts_saved_recipe_quantities(self):
        ingredient_id = self.add_ingredient("薄力粉", "g", 1, "kg", 500)
        with app.connect() as conn:
            recipe_id = conn.execute(
                "INSERT INTO recipes (name, name_kana) VALUES (?, ?)",
                ("スポンジ", "すぽんじ"),
            ).lastrowid
            conn.execute("INSERT INTO recipe_items VALUES (?, ?, ?)", (recipe_id, ingredient_id, 200))
            app.convert_saved_recipe_quantities(conn, ingredient_id, "g", "kg")
            quantity = conn.execute(
                "SELECT quantity FROM recipe_items WHERE recipe_id = ? AND ingredient_id = ?",
                (recipe_id, ingredient_id),
            ).fetchone()[0]
        self.assertAlmostEqual(quantity, 0.2)

    def test_price_change_report_shows_before_delta_and_current_cost(self):
        ingredient_id = self.add_ingredient("薄力粉", "g", 1, "kg", 500)
        with app.connect() as conn:
            recipe_id = conn.execute(
                "INSERT INTO recipes (name, name_kana) VALUES (?, ?)",
                ("スポンジ", "すぽんじ"),
            ).lastrowid
            conn.execute("INSERT INTO recipe_items VALUES (?, ?, ?)", (recipe_id, ingredient_id, 200))
            app.add_price_history(conn, ingredient_id)
            conn.execute("UPDATE ingredients SET purchase_price = 700 WHERE id = ?", (ingredient_id,))
            app.add_price_history(conn, ingredient_id)
        impact = app.price_change_impacts(ingredient_id).iloc[0]
        self.assertAlmostEqual(impact["変更前の仕込み原価"], 100)
        self.assertAlmostEqual(impact["価格変更による増減"], 40)
        self.assertAlmostEqual(impact["現在の仕込み原価"], 140)

    def test_recipe_duplicate_copies_materials_and_subrecipes(self):
        ingredient_id = self.add_ingredient("砂糖", "g", 1000, "g", 400)
        with app.connect() as conn:
            sub_id = conn.execute(
                "INSERT INTO recipes (name, name_kana, yield_quantity, yield_unit) VALUES (?, ?, ?, ?)",
                ("クリーム", "くりーむ", 100, "g"),
            ).lastrowid
            source_id = conn.execute(
                "INSERT INTO recipes (name, name_kana, yield_quantity, yield_unit) VALUES (?, ?, ?, ?)",
                ("タルト", "たると", 8, "個"),
            ).lastrowid
            conn.execute("INSERT INTO recipe_items VALUES (?, ?, ?)", (source_id, ingredient_id, 50))
            conn.execute("INSERT INTO recipe_subrecipes VALUES (?, ?, ?)", (source_id, sub_id, 25))
        new_id = app.duplicate_recipe(source_id, "タルト試作")
        with app.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM recipe_items WHERE recipe_id = ?", (new_id,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM recipe_subrecipes WHERE recipe_id = ?", (new_id,)).fetchone()[0], 1)

    def test_subrecipe_cost_is_included(self):
        sugar_id = self.add_ingredient("砂糖", "g", 1000, "g", 400)
        with app.connect() as conn:
            sub_id = conn.execute(
                "INSERT INTO recipes (name, name_kana, yield_quantity, yield_unit) VALUES (?, ?, ?, ?)",
                ("カスタード", "かすたーど", 100, "g"),
            ).lastrowid
            parent_id = conn.execute(
                "INSERT INTO recipes (name, name_kana, yield_quantity, yield_unit, packaging_cost, labor_cost) VALUES (?, ?, ?, ?, ?, ?)",
                ("シュークリーム", "しゅーくりーむ", 4, "個", 20, 100),
            ).lastrowid
            conn.execute("INSERT INTO recipe_items VALUES (?, ?, ?)", (sub_id, sugar_id, 100))
            conn.execute("INSERT INTO recipe_subrecipes VALUES (?, ?, ?)", (parent_id, sub_id, 25))

        self.assertAlmostEqual(app.recipe_cost(parent_id)["total"], 190.0)
        self.assertTrue(app.recipe_reaches(parent_id, sub_id))
        self.assertFalse(app.recipe_reaches(sub_id, parent_id))

    def test_csv_exports_work_without_missing_columns(self):
        ingredient_id = self.add_ingredient("薄力粉", "g", 1, "kg", 500)
        with app.connect() as conn:
            recipe_id = conn.execute(
                "INSERT INTO recipes (name, name_kana, yield_quantity, yield_unit) VALUES (?, ?, ?, ?)",
                ("テストケーキ", "てすとけーき", 4, "個"),
            ).lastrowid
            conn.execute("INSERT INTO recipe_items VALUES (?, ?, ?)", (recipe_id, ingredient_id, 200))

        self.assertIn(b"\xef\xbb\xbf", app.ingredients_csv()[:3])
        self.assertIn(b"\xef\xbb\xbf", app.recipes_csv()[:3])

    def test_material_csv_import_adds_skips_and_updates_with_history(self):
        data = (
            "材料名,読み,使用単位,購入単位,購入量,購入価格\n"
            "薄力粉,はくりきこ,g,kg,1,500\n"
            "砂糖,さとう,g,g,1000,400\n"
        ).encode("utf-8-sig")
        self.assertEqual(app.import_ingredients_csv(data), {"added": 2, "updated": 0, "skipped": 0})
        self.assertEqual(app.import_ingredients_csv(data), {"added": 0, "updated": 0, "skipped": 2})
        updated = data.replace(b"500", b"600")
        self.assertEqual(app.import_ingredients_csv(updated, update_existing=True), {"added": 0, "updated": 2, "skipped": 0})
        with app.connect() as conn:
            history_count = conn.execute(
                "SELECT COUNT(*) FROM price_history WHERE ingredient_id = (SELECT id FROM ingredients WHERE name = ?)",
                ("薄力粉",),
            ).fetchone()[0]
        self.assertEqual(history_count, 2)

    def test_material_csv_rejects_duplicate_rows_atomically(self):
        data = (
            "材料名,読み,使用単位,購入単位,購入量,購入価格\n"
            "塩,しお,g,g,100,100\n"
            "塩,しお,g,g,100,100\n"
        ).encode("utf-8-sig")
        with self.assertRaises(ValueError):
            app.import_ingredients_csv(data)
        self.assertTrue(app.load_ingredients().empty)

    def test_material_csv_accepts_japanese_excel_cp932(self):
        data = "材料名,読み,使用単位,購入単位,購入量,購入価格\n薄力粉,はくりきこ,g,kg,1,500\n".encode("cp932")
        result = app.import_ingredients_csv(data)
        self.assertEqual(result, {"added": 1, "updated": 0, "skipped": 0})


if __name__ == "__main__":
    unittest.main()
