import time
import unittest
from email.message import EmailMessage
from email.utils import formatdate
from unittest import mock

from core.base_mailbox import MailboxAccount
from core.cf_gmail_catchall import CFGmailCatchAllMailbox


class CFGmailCatchAllMailboxTests(unittest.TestCase):
    def _build_mailbox(self):
        accounts = [
            {
                "cf_domain": "relaybases.com",
                "gmail_user": "demo@gmail.com",
                "gmail_app_pass": "app-pass",
            }
        ]
        return CFGmailCatchAllMailbox(accounts=accounts, lease_seconds=1800)

    @staticmethod
    def _message_bytes(*, to_email: str, code: str, timestamp: float) -> bytes:
        msg = EmailMessage()
        msg["Subject"] = "Your verification code"
        msg["To"] = to_email
        msg["Delivered-To"] = to_email
        msg["Date"] = formatdate(timestamp, localtime=False, usegmt=True)
        msg.set_content(f"Verification code: {code}")
        return msg.as_bytes()

    def test_wait_for_code_prefers_current_alias_and_recent_mail(self):
        mailbox = self._build_mailbox()
        account = MailboxAccount(
            email="fresh@relaybases.com",
            account_id="fresh@relaybases.com",
        )
        now = time.time()

        messages = {
            "1": self._message_bytes(
                to_email="old@relaybases.com",
                code="111111",
                timestamp=now + 5,
            ),
            "2": self._message_bytes(
                to_email="fresh@relaybases.com",
                code="222222",
                timestamp=now - 60,
            ),
            "3": self._message_bytes(
                to_email="fresh@relaybases.com",
                code="333333",
                timestamp=now + 5,
            ),
        }

        fake_conn = mock.Mock()

        def fetch_side_effect(message_id, _spec):
            return "OK", [(b"RFC822", messages[str(message_id)])]

        fake_conn.fetch.side_effect = fetch_side_effect

        with (
            mock.patch.object(
                mailbox,
                "_select_account",
                return_value=(mailbox._accounts[0], "initial", 1800),
            ),
            mock.patch.object(mailbox, "_connect_imap", return_value=fake_conn),
            mock.patch.object(mailbox, "_search_ids", return_value=["1", "2", "3"]),
        ):
            code = mailbox.wait_for_code(account, timeout=5, otp_sent_at=now)

        self.assertEqual(code, "333333")


if __name__ == "__main__":
    unittest.main()
