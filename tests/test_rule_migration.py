import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db as database
from app.models import Base, Category, Rule


class RuleMigrationTests(unittest.TestCase):
    """Exercise startup migration against an installation-like SQLite file."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "cashbook.db"
        self.url = f"sqlite:///{self.path.as_posix()}"
        self.engine = create_engine(self.url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.engine_patch = patch.object(database, "engine", self.engine)
        self.url_patch = patch.object(database, "DATABASE_URL", self.url)
        self.engine_patch.start()
        self.url_patch.start()

        session = self.Session()
        self.expense = Category(name="Supplies", type="expense")
        self.transport = Category(name="Transport", type="expense")
        session.add_all([self.expense, self.transport])
        session.commit()
        session.refresh(self.expense)
        session.refresh(self.transport)
        session.close()

    def tearDown(self):
        self.url_patch.stop()
        self.engine_patch.stop()
        self.engine.dispose()
        self.directory.cleanup()

    def add_rule(self, pattern, category_id, hits, confidence, narrative=None):
        session = self.Session()
        rule = Rule(
            payee_pattern=pattern,
            category_id=category_id,
            hit_count=hits,
            confidence=confidence,
            cashbook_narrative=narrative,
        )
        session.add(rule)
        session.commit()
        session.refresh(rule)
        session.close()
        return rule.id

    def rules(self):
        session = self.Session()
        try:
            return [
                (rule.id, rule.payee_pattern, rule.category_id, rule.hit_count,
                 Decimal(rule.confidence), rule.cashbook_narrative)
                for rule in session.query(Rule).order_by(Rule.category_id, Rule.id)
            ]
        finally:
            session.close()

    def test_narrative_survives_init_db(self):
        self.add_rule(
            "DL*UBER CPT ZAF 01-09-2021",
            self.expense.id,
            3,
            Decimal("1"),
            "DP MACK",
        )

        database.init_db()

        self.assertEqual(
            self.rules(),
            [(1, "UBER CPT", self.expense.id, 3, Decimal("1.00000000"), "DP MACK")],
        )

    def test_init_db_twice_is_idempotent_after_legacy_merge(self):
        self.add_rule("DL*UBER CPT ZAF 01-09-2021", self.expense.id, 2, Decimal("1"), "DP MACK")
        self.add_rule("DL*UBER CPT ZAF 02-09-2021", self.expense.id, 3, Decimal("1"), "DP MACK")

        database.init_db()
        after_first_start = self.rules()
        database.init_db()

        self.assertEqual(after_first_start, self.rules())
        self.assertEqual(after_first_start[0][3:], (5, Decimal("1.00000000"), "DP MACK"))

    def test_simulated_restart_preserves_learned_narrative(self):
        self.add_rule(
            "DL*UBER CPT ZAF 01-09-2021",
            self.expense.id,
            1,
            Decimal("1"),
            "DP MACK",
        )
        database.init_db()

        # Disposing the pool simulates the next desktop application process
        # opening the same installed database file.
        self.engine.dispose()
        database.init_db()

        self.assertEqual(self.rules()[0][-1], "DP MACK")

    def test_legacy_rules_migrate_votes_confidence_and_narrative(self):
        self.add_rule("DL*UBER CPT ZAF 01-09-2021", self.expense.id, 3, Decimal("1"), "DP MACK")
        self.add_rule("DL*UBER CPT ZAF 02-09-2021", self.transport.id, 2, Decimal("1"))

        database.init_db()

        migrated = self.rules()
        self.assertEqual(
            migrated,
            [
                (1, "UBER CPT", self.expense.id, 3, Decimal("0.60000000"), "DP MACK"),
                (2, "UBER CPT", self.transport.id, 2, Decimal("0.40000000"), None),
            ],
        )

    def test_conflicting_legacy_narratives_are_not_merged_arbitrarily(self):
        first_id = self.add_rule(
            "DL*UBER CPT ZAF 01-09-2021", self.expense.id, 2, Decimal("1"), "DP MACK"
        )
        second_id = self.add_rule(
            "DL*UBER CPT ZAF 02-09-2021", self.expense.id, 3, Decimal("1"), "MACK DP"
        )

        database.init_db()

        self.assertEqual(
            self.rules(),
            [
                (first_id, "DL*UBER CPT ZAF 01-09-2021", self.expense.id, 2, Decimal("1.00000000"), "DP MACK"),
                (second_id, "DL*UBER CPT ZAF 02-09-2021", self.expense.id, 3, Decimal("1.00000000"), "MACK DP"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
