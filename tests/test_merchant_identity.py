import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.automation import trusted_exact_match
from app.categorize import categorize, learn_from_correction, move_learning_vote
from app.merchant_identity import merchant_key
from app.models import Base, Category


class MerchantIdentityTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.charges = Category(name="Bank Charges", type="expense")
        self.transport = Category(name="Transport", type="expense")
        self.db.add_all([self.charges, self.transport]); self.db.commit()

    def tearDown(self): self.db.close()

    def test_standard_bank_card_patterns_have_stable_keys(self):
        self.assertEqual(merchant_key("DL*UBER CPT ZAF 06-09-2026 20H43:22 OUTSTANDING CARD AUTHORISATION"), "UBER CPT")
        self.assertEqual(merchant_key("PANAROTTIS PAROW ZAF 06-09-2026 OUTSTANDING CARD AUTHORISATION"), "PANAROTTIS PAROW")
        self.assertEqual(merchant_key("S2S*HASSANYUS 5196*5110 04 SEP DEBIT CARD PURCHASE FROM"), "HASSANYUS")
        self.assertEqual(merchant_key("FLM TOWERS 5196*5110 04 SEP DEBIT CARD PURCHASE FROM"), "FLM TOWERS")
        self.assertNotEqual(merchant_key("DL*UBER CPT ZAF 06-09-2026"), merchant_key("DL*UBER EATS ZAF 06-09-2026"))

    def test_learning_suggests_after_one_and_trusts_after_three(self):
        raw = "DL*UBER CPT ZAF 06-09-2026 20H43:22 OUTSTANDING CARD AUTHORISATION"
        learn_from_correction(raw, self.charges.id, self.db)
        category, confidence, method = categorize("DL*UBER CPT ZAF 07-09-2026 10H30:00 OUTSTANDING CARD AUTHORISATION", self.db, category_type="expense")
        self.assertEqual((category, confidence, method), (self.charges.id, 1.0, "exact"))
        self.assertIsNone(trusted_exact_match(raw, self.db, category_type="expense"))
        learn_from_correction(raw, self.charges.id, self.db); learn_from_correction(raw, self.charges.id, self.db)
        self.assertEqual(trusted_exact_match(raw, self.db, category_type="expense").category_id, self.charges.id)

    def test_conflict_and_correction_rebalance_votes(self):
        raw = "DL*UBER CPT ZAF 06-09-2026"
        for _ in range(3): learn_from_correction(raw, self.charges.id, self.db)
        for _ in range(2): learn_from_correction(raw, self.transport.id, self.db)
        self.assertIsNone(trusted_exact_match(raw, self.db, category_type="expense"))
        move_learning_vote(raw, self.charges.id, self.transport.id, self.db)
        category, confidence, _ = categorize(raw, self.db, category_type="expense")
        self.assertEqual(category, self.transport.id); self.assertEqual(confidence, 0.6)

