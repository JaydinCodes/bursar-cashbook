import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.categorize import categorize, learn_from_correction
from app.models import Base, Category, Rule


class CategorizeTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

        primary = Category(name="Primary", type="expense")
        secondary = Category(name="Secondary", type="expense")
        self.db.add_all([primary, secondary])
        self.db.flush()
        self.db.add_all(
            [
                Rule(
                    payee_pattern="NASHUA",
                    category_id=primary.id,
                    confidence=0.83,
                    hit_count=10,
                ),
                Rule(
                    payee_pattern="NASHUA",
                    category_id=secondary.id,
                    confidence=0.08,
                    hit_count=1,
                ),
            ]
        )
        self.db.commit()
        self.primary_id = primary.id

    def tearDown(self):
        self.db.close()

    def test_fuzzy_match_uses_highest_confidence_rule_for_a_payee(self):
        category_id, confidence, method = categorize("NASHUAH", self.db)

        self.assertEqual(category_id, self.primary_id)
        self.assertEqual(method, "fuzzy")
        self.assertGreater(confidence, 0.7)

    def test_correction_creates_a_rule_and_updates_confidence(self):
        third = Category(name="Third", type="expense")
        self.db.add(third)
        self.db.flush()

        learned = learn_from_correction("New supplier", third.id, self.db)
        self.db.commit()

        category_id, confidence, method = categorize("New supplier", self.db)
        self.assertEqual(learned.category_id, third.id)
        self.assertEqual(category_id, third.id)
        self.assertEqual(confidence, 1.0)
        self.assertEqual(method, "exact")


if __name__ == "__main__":
    unittest.main()
