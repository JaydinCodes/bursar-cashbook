import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.categorize import categorize, learn_from_correction, move_learning_vote
from app.models import Base, Category, Rule


class CategorizeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

        self.primary = Category(name="Primary", type="expense")
        self.secondary = Category(name="Secondary", type="expense")
        self.db.add_all([self.primary, self.secondary])
        self.db.flush()
        self.db.add_all(
            [
                Rule(
                    payee_pattern="NASHUA",
                    category_id=self.primary.id,
                    confidence=0.90,
                    hit_count=9,
                ),
                Rule(
                    payee_pattern="NASHUA",
                    category_id=self.secondary.id,
                    confidence=0.10,
                    hit_count=1,
                ),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_fuzzy_match_uses_best_rule(self):
        category_id, confidence, method = categorize("NASHUAH", self.db)
        self.assertEqual(category_id, self.primary.id)
        self.assertEqual(method, "fuzzy")
        self.assertGreater(confidence, 0.7)

    def test_learning_creates_rule(self):
        third = Category(name="Third", type="expense")
        self.db.add(third)
        self.db.flush()

        learn_from_correction("New supplier", third.id, self.db)
        self.db.commit()

        category_id, confidence, method = categorize("New supplier", self.db)
        self.assertEqual(category_id, third.id)
        self.assertEqual(confidence, 1.0)
        self.assertEqual(method, "exact")

    def test_move_learning_vote_does_not_double_count(self):
        before = sum(rule.hit_count for rule in self.db.query(Rule).all())
        move_learning_vote("NASHUA", self.primary.id, self.secondary.id, self.db)
        self.db.commit()
        after = sum(rule.hit_count for rule in self.db.query(Rule).all())

        self.assertEqual(before, after)
        primary = (
            self.db.query(Rule)
            .filter_by(payee_pattern="NASHUA", category_id=self.primary.id)
            .one()
        )
        secondary = (
            self.db.query(Rule)
            .filter_by(payee_pattern="NASHUA", category_id=self.secondary.id)
            .one()
        )
        self.assertEqual(primary.hit_count, 8)
        self.assertEqual(secondary.hit_count, 2)


if __name__ == "__main__":
    unittest.main()
