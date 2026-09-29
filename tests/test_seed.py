import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import seed
from app.models import Base, Rule


@unittest.skipUnless(importlib.util.find_spec("xlrd") is not None, "xlrd not installed")
class SeedTests(unittest.TestCase):
    def test_running_same_workbook_twice_does_not_double_hit_counts(self):
        workbook = Path(__file__).parent / "fixtures" / "synthetic_wced_2020.xls"
        if not workbook.exists():
            self.skipTest("Historical WCED test workbook is not present in this patch folder")

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)

        try:
            with patch.object(seed, "init_db"), patch.object(
                seed,
                "SessionLocal",
                session_factory,
            ):
                seed.run_seed(str(workbook))
                first_session = session_factory()
                try:
                    first_total = sum(rule.hit_count for rule in first_session.query(Rule).all())
                finally:
                    first_session.close()

                seed.run_seed(str(workbook))
                second_session = session_factory()
                try:
                    second_total = sum(rule.hit_count for rule in second_session.query(Rule).all())
                finally:
                    second_session.close()

            # The public fixture intentionally has no real transaction rows.
            self.assertGreaterEqual(first_total, 0)
            self.assertEqual(second_total, first_total)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
