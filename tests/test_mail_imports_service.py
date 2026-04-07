import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlmodel import SQLModel, create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.mail_imports.providers import AppleMailImportStrategy, OutlookImportStrategy
from services.mail_imports.schemas import MailImportExecuteRequest


class MailImportServiceTests(unittest.TestCase):
    def test_applemail_strategy_saves_pool_and_returns_snapshot(self):
        strategy = AppleMailImportStrategy()
        with tempfile.TemporaryDirectory() as tmp_dir:
            previous_cwd = os.getcwd()
            os.chdir(tmp_dir)
            try:
                response = strategy.execute(
                    MailImportExecuteRequest(
                        type="applemail",
                        content="demo@example.com----password----client-id----refresh-token",
                        pool_dir="mail",
                        filename="applemail_demo.json",
                        bind_to_config=False,
                    )
                )
            finally:
                os.chdir(previous_cwd)

            saved_path = Path(tmp_dir) / "mail" / "applemail_demo.json"
            self.assertTrue(saved_path.exists())
            self.assertEqual(response.summary.total, 1)
            self.assertEqual(response.summary.success, 1)
            self.assertEqual(response.summary.failed, 0)
            self.assertEqual(response.snapshot.filename, "applemail_demo.json")
            self.assertEqual(response.snapshot.pool_dir, "mail")
            self.assertEqual(response.snapshot.count, 1)
            self.assertEqual(response.snapshot.items[0].email, "demo@example.com")

    def test_outlook_strategy_imports_into_database_and_returns_snapshot(self):
        strategy = OutlookImportStrategy()
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_engine = create_engine(f"sqlite:///{Path(tmp_dir) / 'mail-imports.db'}")
            SQLModel.metadata.create_all(test_engine)

            with patch("services.mail_imports.providers.engine", test_engine):
                response = strategy.execute(
                    MailImportExecuteRequest(
                        type="outlook",
                        content=(
                            "first@outlook.com----password\n"
                            "second@outlook.com----password----client-id----refresh-token"
                        ),
                    )
                )

                self.assertEqual(response.summary.total, 2)
                self.assertEqual(response.summary.success, 2)
                self.assertEqual(response.summary.failed, 0)
                self.assertEqual(response.snapshot.count, 2)
                self.assertEqual(len(response.snapshot.items), 2)
                self.assertFalse(response.snapshot.items[0].has_oauth)
                self.assertTrue(response.snapshot.items[1].has_oauth)


if __name__ == "__main__":
    unittest.main()
