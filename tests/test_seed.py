import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import seed
from app.models import Base, Rule


class SeedTests(unittest.TestCase):
    def test_running_same_workbook_twice_does_not_double_hit_counts(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        workbook = Path(__file__).parents[1] / "data" / "2020_cashbook.xls"

        with patch.object(seed, "init_db"), patch.object(seed, "SessionLocal", session_factory):
            seed.run_seed(str(workbook))
            first_total = sum(rule.hit_count for rule in session_factory().query(Rule).all())
            seed.run_seed(str(workbook))
            second_total = sum(rule.hit_count for rule in session_factory().query(Rule).all())

        self.assertGreater(first_total, 0)
        self.assertEqual(second_total, first_total)


if __name__ == "__main__":
    unittest.main()
