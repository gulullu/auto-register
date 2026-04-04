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
        adjectives = [
            "alex",
            "jamie",
            "casey",
            "morgan",
            "taylor",
            "jordan",
            "sam",
            "chris",
            "cameron",
            "drew",
            "avery",
            "riley",
        ]
        nouns = [
            "carter",
            "parker",
            "miller",
            "ross",
            "cook",
            "lewis",
            "taylor",
            "walker",
            "clark",
            "hall",
            "young",
            "king",
        ]
        return f"{random.choice(adjectives)}{random.choice(nouns)}{random.randint(10, 999)}".lower()

    _imap_proxy_warned = False

    def _connect_imap(self, account_cfg: _CatchAllAccount, folder: str = "INBOX"):
        if self.proxy and not CFGmailCatchAllMailbox._imap_proxy_warned:
            CFGmailCatchAllMailbox._imap_proxy_warned = True
            self._log(
                "[CFGmailCatchAll] 当前 provider 已收到 proxy，但 Gmail IMAP 直连不复用 requests 代理配置"
            )
        conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        conn.login(account_cfg.gmail_user, account_cfg.gmail_app_pass)
        # 文件夹名含空格/特殊字符需要加引号
        quoted_folder = f'"{folder}"' if ' ' in folder or '[' in folder else folder
        conn.select(quoted_folder)
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

    def _decode_message_raw(self, message_bytes: bytes) -> str:
        """解码邮件，保留原始 HTML（含 href 属性），用于链接提取。"""
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
        return " ".join(chunks)

    def _search_ids(self, conn, criteria: str = "ALL") -> list[str]:
        # 先 NOOP 强制服务器刷新邮箱状态（解决 Gmail IMAP 新邮件延迟问题）
        try:
            conn.noop()
        except Exception:
            pass
        status, data = conn.search(None, criteria)
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

        target_email = str(getattr(account, 'email', '') or '').lower()
        _poll_count = [0]
        _last_log_time = [0.0]

        def _fetch_and_extract(conn, message_id):
            """Fetch a single message and try to extract a verification code."""
            status, data = conn.fetch(message_id, "(RFC822)")
            if status != "OK" or not data:
                return None
            message_bytes = b""
            for part in data:
                if isinstance(part, tuple) and len(part) >= 2:
                    message_bytes = part[1] or b""
                    break
            if not message_bytes:
                return None

            import email as email_mod
            try:
                msg = email_mod.message_from_bytes(message_bytes)
                to_addr = str(msg.get("To") or "")[:80]
                subj = str(msg.get("Subject") or "")[:60]
                self._log(
                    f"[CFGmailCatchAll] 新邮件 id={message_id} to={to_addr} subj={subj}"
                )
            except Exception:
                pass

            body = self._decode_message(message_bytes)
            if target_email and target_email not in body.lower():
                return None
            if keyword_lc and keyword_lc not in body.lower():
                return None
            pattern = code_pattern if code_pattern is not None else ""
            code = self._safe_extract(body, pattern)
            if code and code in exclude_codes:
                self._log(
                    f"[CFGmailCatchAll] 跳过已使用验证码 id={message_id} code={code}"
                )
                return None
            return code or None

        def poll_once() -> Optional[str]:
            _poll_count[0] += 1
            conn = self._connect_imap(account_cfg, folder="INBOX")
            try:
                # ---- 策略1: 使用 Gmail 原生搜索引擎 (X-GM-RAW) ----
                # 这跟 Gmail 网页搜索一样快，不受 IMAP 索引延迟影响
                gmail_ids = []
                if target_email:
                    try:
                        query = f"to:{target_email} newer_than:1h"
                        conn.noop()
                        status, data = conn.search(None, "X-GM-RAW", f'"{query}"')
                        if status == "OK" and data and data[0]:
                            for raw_id in data[0].split():
                                try:
                                    gmail_ids.append(raw_id.decode("utf-8", errors="ignore"))
                                except Exception:
                                    pass
                    except Exception as e:
                        if _poll_count[0] == 1:
                            self._log(f"[CFGmailCatchAll] X-GM-RAW 搜索异常: {e}")

                # ---- 策略2: 标准 IMAP UNSEEN 搜索 (兜底) ----
                unseen_ids = self._search_ids(conn, criteria="UNSEEN")

                # ---- 策略3: 标准 IMAP ALL 搜索 (兜底) ----
                all_ids = self._search_ids(conn, criteria="ALL")

                # 合并所有候选 ID，去重，排除已见
                candidate_set = set()
                for mid in gmail_ids + unseen_ids + all_ids:
                    if mid and mid not in seen:
                        candidate_set.add(mid)
                new_ids = sorted(candidate_set, key=lambda x: int(x) if x.isdigit() else 0)

                # 定期输出日志
                now = time.time()
                if _poll_count[0] == 1 or new_ids or (now - _last_log_time[0]) >= 30:
                    _last_log_time[0] = now
                    self._log(
                        f"[CFGmailCatchAll] OTP扫描 #{_poll_count[0]} "
                        f"gmail={account_cfg.gmail_user} "
                        f"GMAIL={len(gmail_ids)} UNSEEN={len(unseen_ids)} ALL={len(all_ids)} "
                        f"新邮件={len(new_ids)}"
                    )

                for message_id in new_ids:
                    seen.add(message_id)
                    code = _fetch_and_extract(conn, message_id)
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

    def wait_for_link(
        self,
        account: MailboxAccount,
        link_pattern: str = "continue-registration",
        timeout: int = 120,
        before_ids: Optional[set] = None,
        **kwargs,
    ) -> Optional[str]:
        """等待并从邮件中提取确认链接（保留原始 HTML 以读取 href）。"""
        account_cfg, _reason, _lease_left = self._select_account()
        seen = set(before_ids or [])
        target_email = str(getattr(account, 'email', '') or '').lower()

        def poll_once() -> Optional[str]:
            for folder in ["INBOX", "[Gmail]/Spam", "[Gmail]/All Mail"]:
                try:
                    conn = self._connect_imap(account_cfg, folder=folder)
                except Exception:
                    continue
                try:
                    all_ids = self._search_ids(conn)
                    new_ids = [mid for mid in all_ids if mid and mid not in seen]
                    if new_ids:
                        self._log(f"[CFGmailCatchAll] 链接扫描 [{folder}]: 总邮件={len(all_ids)} 新邮件={len(new_ids)}")
                    for message_id in new_ids:
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

                        # 解析邮件头信息用于调试
                        import email as email_mod
                        try:
                            msg = email_mod.message_from_bytes(message_bytes)
                            subject = str(msg.get("Subject") or "")[:80]
                            from_addr = str(msg.get("From") or "")[:80]
                            to_addr = str(msg.get("To") or "")[:80]
                            self._log(
                                f"[CFGmailCatchAll] 新邮件 id={message_id} "
                                f"from={from_addr} to={to_addr} subj={subject}"
                            )
                        except Exception:
                            pass

                        # 检查是否是发给目标邮箱的
                        raw_body = self._decode_message_raw(message_bytes)
                        if target_email and target_email not in raw_body.lower():
                            continue

                        link = self._extract_continue_registration_link(raw_body)
                        if link:
                            self._log(f"[CFGmailCatchAll] 收到确认链接 (folder={folder}): {link[:160]}")
                            return link
                        else:
                            self._log(f"[CFGmailCatchAll] 邮件 id={message_id} 未提取到确认链接")
                finally:
                    try:
                        conn.logout()
                    except Exception:
                        pass
            return None

        try:
            return self._run_polling_wait(
                timeout=timeout,
                poll_interval=5,
                poll_once=poll_once,
                timeout_message=f"CFGmailCatchAll 等待确认链接超时 ({timeout}s)",
            )
        except TimeoutError:
            return None
