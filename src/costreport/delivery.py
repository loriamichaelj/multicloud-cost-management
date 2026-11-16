"""Deliver the weekly report to Slack or email.

Safety rules:
  * Nothing is sent unless the caller passes send=True (the CLI's --send).
  * Secrets come from environment variables only, never from config files, and
    are never printed (previews show the destination host, not the URL).
  * The Slack webhook must be an https://hooks.slack.com/ URL, so a typo in the
    environment cannot post spend data to an arbitrary host.
  * Email always uses TLS (STARTTLS, or implicit TLS on port 465).

Network clients are injectable so tests run with no network.
"""

from __future__ import annotations

import json
import smtplib
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable, Mapping

from .analysis import Summary
from .config import DeliverySettings, ReportSettings
from .report import delta_money, headlines, money, pct

ENV_SLACK_WEBHOOK = "COSTREPORT_SLACK_WEBHOOK_URL"
ENV_SMTP_USER = "COSTREPORT_SMTP_USER"
ENV_SMTP_PASSWORD = "COSTREPORT_SMTP_PASSWORD"
SLACK_PREFIX = "https://hooks.slack.com/"
TARGETS = ("slack", "email")


class DeliveryError(RuntimeError):
    pass


def title(s: Summary) -> str:
    return f"Weekly Cloud Cost Report: {s.window.start} to {s.window.end}"


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_text(s: Summary, report_cfg: ReportSettings, top: int = 5) -> str:
    """Compact Slack mrkdwn summary. Slack cannot render tables, so the full report is not inlined."""
    lines = [f"*{_slack_escape(title(s))}*", ""]
    for h in headlines(s, report_cfg):
        lines.append("• " + _slack_escape(h).replace("**", "*"))
    lines += ["", f"*Top {min(top, len(s.services))} services*"]
    for i, d in enumerate(s.services[:top], 1):
        lines.append(f"{i}. {_slack_escape(d.name)}: {money(d.current)} ({delta_money(d.delta)}, {pct(d.pct)})")
    return "\n".join(lines)


def check_webhook(url: str | None) -> str:
    if not url:
        raise DeliveryError(f"{ENV_SLACK_WEBHOOK} is not set")
    if not url.startswith(SLACK_PREFIX):
        raise DeliveryError(f"{ENV_SLACK_WEBHOOK} must start with {SLACK_PREFIX}")
    return url


def post_slack(url: str, text: str, opener: Callable = urllib.request.urlopen) -> None:
    check_webhook(url)
    req = urllib.request.Request(
        url,
        data=json.dumps({"text": text, "mrkdwn": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(req, timeout=15) as resp:
            body = resp.read().decode(errors="replace").strip()
            if resp.status != 200 or body != "ok":
                raise DeliveryError(f"slack: unexpected response {resp.status} {body[:80]!r}")
    except urllib.error.HTTPError as e:  # message deliberately omits the URL
        raise DeliveryError(f"slack: HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise DeliveryError(f"slack: connection failed ({e.reason})") from None


def _plain(text: str) -> str:
    return text.replace("**", "").replace("`", "")


def build_email(
    s: Summary, markdown: str, cfg: DeliverySettings, report_cfg: ReportSettings
) -> EmailMessage:
    if not (cfg.email_from and cfg.email_to and cfg.smtp_host):
        raise DeliveryError("config: [delivery] needs email_from, email_to and smtp_host for email")
    msg = EmailMessage()
    msg["Subject"] = title(s)
    msg["From"] = cfg.email_from
    msg["To"] = ", ".join(cfg.email_to)
    body = [title(s), ""] + [f"- {_plain(h)}" for h in headlines(s, report_cfg)]
    body += ["", "The full report is attached as Markdown."]
    msg.set_content("\n".join(body))
    # str content (not bytes) so the part carries an explicit charset; the report has non-ASCII characters.
    msg.add_attachment(
        markdown,
        subtype="markdown",
        charset="utf-8",
        filename=f"cloud-cost-report-{s.window.end}.md",
    )
    return msg


def send_email(
    msg: EmailMessage,
    host: str,
    port: int,
    env: Mapping[str, str],
    smtp_cls=smtplib.SMTP,
    smtp_ssl_cls=smtplib.SMTP_SSL,
) -> None:
    user, password = env.get(ENV_SMTP_USER), env.get(ENV_SMTP_PASSWORD)
    if bool(user) != bool(password):
        raise DeliveryError(f"set both {ENV_SMTP_USER} and {ENV_SMTP_PASSWORD}, or neither")
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            with smtp_ssl_cls(host, port, context=ctx, timeout=30) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtp_cls(host, port, timeout=30) as smtp:
                smtp.starttls(context=ctx)
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        # str(e) can echo server banners but never our password; keep only the type and reason.
        raise DeliveryError(f"email: {type(e).__name__}: {e}") from None


def preflight(targets: list[str], cfg: DeliverySettings, env: Mapping[str, str], send: bool) -> None:
    """Cheap config checks with no network. The weekly run calls this BEFORE any paid fetch."""
    for t in targets:
        if t not in TARGETS:
            raise DeliveryError(f"unknown delivery target {t!r}; expected one of {TARGETS}")
    if "email" in targets:
        if not (cfg.email_from and cfg.email_to and cfg.smtp_host):
            raise DeliveryError("config: [delivery] needs email_from, email_to and smtp_host for email")
        if send and bool(env.get(ENV_SMTP_USER)) != bool(env.get(ENV_SMTP_PASSWORD)):
            raise DeliveryError(f"set both {ENV_SMTP_USER} and {ENV_SMTP_PASSWORD}, or neither")
    if "slack" in targets and send:
        check_webhook(env.get(ENV_SLACK_WEBHOOK))


@dataclass
class Delivered:
    target: str
    sent: bool
    preview: str


def deliver(
    targets: list[str],
    s: Summary,
    markdown: str,
    delivery_cfg: DeliverySettings,
    report_cfg: ReportSettings,
    env: Mapping[str, str],
    send: bool,
    opener: Callable = urllib.request.urlopen,
    smtp_cls=smtplib.SMTP,
    smtp_ssl_cls=smtplib.SMTP_SSL,
) -> list[Delivered]:
    """Validate everything first, then send. Without send=True nothing leaves the machine."""
    # Validate every target before sending any, so a bad email config cannot leave Slack half-done.
    preflight(targets, delivery_cfg, env, send)
    slack_url = env.get(ENV_SLACK_WEBHOOK) if "slack" in targets else None
    msg = build_email(s, markdown, delivery_cfg, report_cfg) if "email" in targets else None

    results: list[Delivered] = []
    for t in targets:
        if t == "slack":
            text = slack_text(s, report_cfg, delivery_cfg.slack_top)
            if send:
                post_slack(slack_url, text, opener)
            note = "set" if slack_url else f"NOT SET ({ENV_SLACK_WEBHOOK})"
            results.append(Delivered("slack", send, f"Slack webhook (hooks.slack.com, env var {note}):\n{text}"))
        else:
            if send:
                send_email(msg, delivery_cfg.smtp_host, delivery_cfg.smtp_port, env, smtp_cls, smtp_ssl_cls)
            pv = (
                f"Email via {delivery_cfg.smtp_host}:{delivery_cfg.smtp_port} (TLS)\n"
                f"  From: {msg['From']}\n  To: {msg['To']}\n  Subject: {msg['Subject']}\n"
                f"  Attachment: {msg.get_payload()[1].get_filename()} ({len(markdown):,} chars)\n\n"
                + msg.get_body(("plain",)).get_content()
            )
            results.append(Delivered("email", send, pv))
    return results
