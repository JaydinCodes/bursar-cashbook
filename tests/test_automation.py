import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.automation import trusted_exact_match
from app.models import Base, Category, Rule


class AutomationPolicyTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        category = Category(name="Stationery", type="expense")
        self.db.add(category)
        self.db.flush()
        self.category_id = category.id

    def tearDown(self):
        self.db.close()

    def test_trusted_exact_match_requires_confidence_and_history(self):
        self.db.add(
            Rule(
                payee_pattern="PAPER SUPPLIER",
                category_id=self.category_id,
                confidence=0.98,
                hit_count=4,
            )
        )
        self.db.commit()

        result = trusted_exact_match("Paper Supplier", self.db)
        self.assertIsNotNone(result)
        self.assertEqual(result.category_id, self.category_id)

    def test_few_hits_are_not_trusted(self):
        self.db.add(
            Rule(
                payee_pattern="PAPER SUPPLIER",
                category_id=self.category_id,
                confidence=1.0,
                hit_count=2,
            )
        )
        self.db.commit()

        self.assertIsNone(trusted_exact_match("Paper Supplier", self.db))


if __name__ == "__main__":
    unittest.main()
