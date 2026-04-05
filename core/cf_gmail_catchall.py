import email
import imaplib
import json
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from .base_mailbox import BaseMailbox, MailboxAccount
from .proxy_utils import build_requests_proxy_config


@dataclass
class _CatchAllAccount:
    cf_domain: str
    gmail_user: str
    gmail_app_pass: str


class CFGmailCatchAllMailbox(BaseMailbox):
    """Cloudflare Email Routing catch-all + Gmail IMAP 邮箱服务。"""

    def __init__(
        self, accounts: Any, lease_seconds: Any = 1800, proxy: Optional[str] = None
    ):
        self._accounts = self._parse_accounts(accounts)
        if not self._accounts:
            raise RuntimeError("cf_gmail_accounts / CF_GMAIL_ACCOUNTS 未配置或为空")
        try:
            self._lease_seconds = max(int(lease_seconds or 1800), 1)
        except Exception:
            self._lease_seconds = 1800
        self.proxy = build_requests_proxy_config(proxy)
        self._leased_account: Optional[_CatchAllAccount] = None
        self._lease_deadline = 0.0
        self._lease_lock = threading.Lock()

    @staticmethod
    def _normalize_domain(domain: Any) -> str:
        value = str(domain or "").strip().lower()
        if value.startswith("@"):
            value = value[1:]
        return value.strip(".")

    @classmethod
    def _parse_accounts(cls, accounts: Any) -> list[_CatchAllAccount]:
        if isinstance(accounts, list):
            raw_items = accounts
        else:
            raw_text = str(accounts or "").strip()
            if not raw_text:
                return []
            try:
                raw_items = json.loads(raw_text)
            except Exception as e:
                raise RuntimeError(f"CF_GMAIL_ACCOUNTS JSON 解析失败: {e}")

        parsed: list[_CatchAllAccount] = []
        for item in raw_items or []:
            if not isinstance(item, dict):
                continue
            cf_domain = cls._normalize_domain(item.get("cf_domain"))
            gmail_user = str(item.get("gmail_user") or "").strip()
            gmail_app_pass = str(item.get("gmail_app_pass") or "").strip()
            if cf_domain and gmail_user and gmail_app_pass:
                parsed.append(
                    _CatchAllAccount(
                        cf_domain=cf_domain,
                        gmail_user=gmail_user,
                        gmail_app_pass=gmail_app_pass,
                    )
                )
        return parsed

    def _select_account(self) -> tuple[_CatchAllAccount, str, int]:
        with self._lease_lock:
            now = time.time()
            if self._leased_account and now < self._lease_deadline:
                lease_left = int(max(self._lease_deadline - now, 0))
                return self._leased_account, "lease-active", lease_left
            self._leased_account = random.choice(self._accounts)
            self._lease_deadline = now + self._lease_seconds
            lease_left = int(max(self._lease_deadline - now, 0))
            return self._leased_account, "initial", lease_left

    def _generate_local_part(self) -> str:
        from .base_mailbox import generate_human_like_email_local_part
        return generate_human_like_email_local_part()

    def _connect_imap(self, account_cfg: _CatchAllAccount):
        if self.proxy:
            self._log(
                "[CFGmailCatchAll] 当前 provider 已收到 proxy，但 Gmail IMAP 直连不复用 requests 代理配置"
            )
        conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        conn.login(account_cfg.gmail_user, account_cfg.gmail_app_pass)
        conn.select("INBOX")
        return conn

    def _decode_message(self, message_bytes: bytes) -> str:
        try:
            msg = email.message_from_bytes(message_bytes)
        except Exception:
            return ""
        chunks = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                disposition = str(part.get("Content-Disposition") or "")
                if "attachment" in disposition.lower():
                    continue
                payload = part.get_payload(decode=True)
                if payload is None or not isinstance(payload, (bytes, bytearray)):
                    continue
                charset = part.get_content_charset() or "utf-8"
                try:
                    chunks.append(payload.decode(charset, errors="ignore"))
                except Exception:
                    chunks.append(payload.decode("utf-8", errors="ignore"))
        else:
            payload = msg.get_payload(decode=True)
            if payload is not None and isinstance(payload, (bytes, bytearray)):
                charset = msg.get_content_charset() or "utf-8"
                try:
                    chunks.append(payload.decode(charset, errors="ignore"))
                except Exception:
                    chunks.append(payload.decode("utf-8", errors="ignore"))
        combined = " ".join(chunks)
        subject = str(msg.get("Subject") or "")
        to_field = str(msg.get("To") or "")
        return self._decode_raw_content(f"{subject} {to_field} {combined}")

    def _search_ids(self, conn) -> list[str]:
        status, data = conn.search(None, "ALL")
        if status != "OK":
            return []
        ids = []
        for raw_id in (data[0] or b"").split():
            try:
                ids.append(raw_id.decode("utf-8", errors="ignore"))
            except Exception:
                continue
        return ids

    def get_email(self) -> MailboxAccount:
        account_cfg, reason, lease_left = self._select_account()
        email_address = f"{self._generate_local_part()}@{account_cfg.cf_domain}"
        self._log(
            f"[CFGmailCatchAll] 选择域名: {account_cfg.cf_domain} reason={reason} lease_left={lease_left}s"
        )
        self._log(f"[CFGmailCatchAll] catch-all 邮箱已生成: {email_address}")
        return MailboxAccount(
            email=email_address,
            account_id=email_address,
            extra={
                "cf_domain": account_cfg.cf_domain,
                "gmail_user": account_cfg.gmail_user,
            },
        )

    def get_current_ids(self, account: MailboxAccount) -> set:
        account_cfg, _reason, _lease_left = self._select_account()
        conn = self._connect_imap(account_cfg)
        try:
            return set(self._search_ids(conn))
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def wait_for_code(
        self,
        account: MailboxAccount,
        keyword: str = "",
        timeout: int = 120,
        before_ids: Optional[set] = None,
        code_pattern: Optional[str] = None,
        **kwargs,
    ) -> str:
        account_cfg, _reason, _lease_left = self._select_account()
        seen = set(before_ids or [])
        exclude_codes = {
            str(code).strip()
            for code in (kwargs.get("exclude_codes") or set())
            if str(code or "").strip()
        }
        keyword_lc = str(keyword or "").strip().lower()

        def poll_once() -> Optional[str]:
            conn = self._connect_imap(account_cfg)
            try:
                for message_id in self._search_ids(conn):
                    if not message_id or message_id in seen:
                        continue
                    seen.add(message_id)
                    status, data = conn.fetch(message_id, "(RFC822)")
                    if status != "OK" or not data:
                        continue
                    message_bytes = b""
                    for part in data:
                        if isinstance(part, tuple) and len(part) >= 2:
                            message_bytes = part[1] or b""
                            break
                    if not message_bytes:
                        continue
                    body = self._decode_message(message_bytes)
                    if keyword_lc and keyword_lc not in body.lower():
                        continue
                    pattern = code_pattern if code_pattern is not None else ""
                    code = self._safe_extract(body, pattern)
                    if code and code in exclude_codes:
                        self._log(
                            f"[CFGmailCatchAll] 跳过已使用验证码 message_id={message_id} code={code}"
                        )
                        continue
                    if code:
                        self._log(f"[CFGmailCatchAll] 收到验证码: {code}")
                        return code
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass
            return None

        return self._run_polling_wait(
            timeout=timeout,
            poll_interval=5,
            poll_once=poll_once,
            timeout_message=f"CFGmailCatchAll 等待验证码超时 ({timeout}s)",
        )
