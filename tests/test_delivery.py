import json
import ssl
import unittest
import urllib.error
from datetime import date

from costreport import delivery
from costreport.analysis import analyze
from costreport.config import DeliverySettings, ReportSettings
from costreport.providers import sample
from costreport.report import render

END = date(2026, 11, 15)
HOOK = "https://hooks.slack.com/services/T000/B000/SECRETSECRET"
RC = ReportSettings()
DC = DeliverySettings(email_from="r@example.com", email_to=["a@example.com", "b@example.com"], smtp_host="smtp.example.com")


def make():
    s = analyze(sample.generate(END, 70), END)
    return s, render(s)


class FakeResp:
    def __init__(self, status=200, body=b"ok"):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, resp=None, exc=None):
        self.calls, self.resp, self.exc = [], resp or FakeResp(), exc

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        if self.exc:
            raise self.exc
        return self.resp


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.context = host, port, context
        self.log, self.sent = [], []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.log.append(("starttls", context))

    def login(self, user, password):
        self.log.append(("login", user, password))

    def send_message(self, msg):
        self.sent.append(msg)


class SlackTests(unittest.TestCase):
    def test_text_has_headlines_and_top_services(self):
        s, _ = make()
        t = delivery.slack_text(s, RC, top=3)
        self.assertIn("*Weekly Cloud Cost Report: 2026-11-09 to 2026-11-15*", t)
        self.assertIn("• *Total spend was", t)
        self.assertNotIn("**", t)  # Slack bold is single-asterisk
        self.assertEqual(sum(1 for line in t.splitlines() if line[:2] in ("1.", "2.", "3.")), 3)

    def test_special_characters_escaped(self):
        s, _ = make()
        s.services[0] = type(s.services[0])("A & B <x>", 1.0, 1.0, 0.1)
        self.assertIn("A &amp; B &lt;x&gt;", delivery.slack_text(s, RC))

    def test_webhook_must_be_slack(self):
        for bad in [None, "", "http://hooks.slack.com/x", "https://evil.example.com/hooks.slack.com/", "https://hooks.slack.com.evil.io/x"]:
            with self.assertRaises(delivery.DeliveryError, msg=str(bad)):
                delivery.check_webhook(bad)
        self.assertEqual(delivery.check_webhook(HOOK), HOOK)

    def test_post_payload(self):
        op = FakeOpener()
        delivery.post_slack(HOOK, "hello", op)
        self.assertEqual(len(op.calls), 1)
        req = op.calls[0]
        self.assertEqual(req.full_url, HOOK)
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data)["text"], "hello")

    def test_errors_do_not_leak_url(self):
        for exc in (urllib.error.HTTPError(HOOK, 404, "nf", {}, None), urllib.error.URLError("dns")):
            with self.assertRaises(delivery.DeliveryError) as cm:
                delivery.post_slack(HOOK, "x", FakeOpener(exc=exc))
            self.assertNotIn("SECRETSECRET", str(cm.exception))

    def test_unexpected_response_rejected(self):
        with self.assertRaises(delivery.DeliveryError):
            delivery.post_slack(HOOK, "x", FakeOpener(FakeResp(200, b"invalid_payload")))


class EmailTests(unittest.TestCase):
    def test_message_shape(self):
        s, md = make()
        m = delivery.build_email(s, md, DC, RC)
        self.assertEqual(m["To"], "a@example.com, b@example.com")
        self.assertEqual(m["Subject"], "Weekly Cloud Cost Report: 2026-11-09 to 2026-11-15")
        parts = list(m.iter_attachments())
        self.assertEqual(parts[0].get_filename(), "cloud-cost-report-2026-11-15.md")
        self.assertEqual(parts[0].get_content(), md)
        self.assertEqual(parts[0].get_content_charset(), "utf-8")
        self.assertIn("·", md)  # guards the test itself: the report really is non-ASCII
        body = m.get_body(("plain",)).get_content()
        self.assertIn("Total spend was", body)
        self.assertNotIn("**", body)

    def test_incomplete_config(self):
        s, md = make()
        with self.assertRaises(delivery.DeliveryError):
            delivery.build_email(s, md, DeliverySettings(), RC)

    def test_starttls_then_login_then_send(self):
        s, md = make()
        FakeSMTP.instances.clear()
        delivery.send_email(delivery.build_email(s, md, DC, RC), "smtp.example.com", 587,
                            {delivery.ENV_SMTP_USER: "u", delivery.ENV_SMTP_PASSWORD: "p"}, smtp_cls=FakeSMTP)
        smtp = FakeSMTP.instances[0]
        self.assertEqual([e[0] for e in smtp.log], ["starttls", "login"])
        self.assertIsInstance(smtp.log[0][1], ssl.SSLContext)
        self.assertEqual(len(smtp.sent), 1)

    def test_port_465_uses_implicit_tls(self):
        s, md = make()
        FakeSMTP.instances.clear()
        delivery.send_email(delivery.build_email(s, md, DC, RC), "h", 465, {}, smtp_cls=None, smtp_ssl_cls=FakeSMTP)
        smtp = FakeSMTP.instances[0]
        self.assertIsInstance(smtp.context, ssl.SSLContext)
        self.assertEqual(smtp.log, [])  # no starttls, no login without creds
        self.assertEqual(len(smtp.sent), 1)

    def test_half_credentials_rejected(self):
        s, md = make()
        with self.assertRaises(delivery.DeliveryError):
            delivery.send_email(delivery.build_email(s, md, DC, RC), "h", 587, {delivery.ENV_SMTP_USER: "u"}, smtp_cls=FakeSMTP)


class DeliverTests(unittest.TestCase):
    def test_without_send_nothing_leaves_the_machine(self):
        s, md = make()
        op = FakeOpener()
        FakeSMTP.instances.clear()
        out = delivery.deliver(["slack", "email"], s, md, DC, RC, {delivery.ENV_SLACK_WEBHOOK: HOOK},
                               send=False, opener=op, smtp_cls=FakeSMTP, smtp_ssl_cls=FakeSMTP)
        self.assertEqual(op.calls, [])
        self.assertEqual(FakeSMTP.instances, [])
        self.assertTrue(all(not d.sent for d in out))

    def test_preview_never_contains_secrets(self):
        s, md = make()
        env = {delivery.ENV_SLACK_WEBHOOK: HOOK, delivery.ENV_SMTP_PASSWORD: "hunter2", delivery.ENV_SMTP_USER: "u"}
        text = "\n".join(d.preview for d in delivery.deliver(["slack", "email"], s, md, DC, RC, env, send=False))
        for secret in ("SECRETSECRET", "hunter2", HOOK):
            self.assertNotIn(secret, text)

    def test_send_posts_and_mails(self):
        s, md = make()
        op = FakeOpener()
        FakeSMTP.instances.clear()
        out = delivery.deliver(["slack", "email"], s, md, DC, RC, {delivery.ENV_SLACK_WEBHOOK: HOOK},
                               send=True, opener=op, smtp_cls=FakeSMTP, smtp_ssl_cls=FakeSMTP)
        self.assertEqual(len(op.calls), 1)
        self.assertEqual(len(FakeSMTP.instances[0].sent), 1)
        self.assertTrue(all(d.sent for d in out))

    def test_bad_email_config_blocks_slack_too(self):
        s, md = make()
        op = FakeOpener()
        with self.assertRaises(delivery.DeliveryError):
            delivery.deliver(["slack", "email"], s, md, DeliverySettings(), RC, {delivery.ENV_SLACK_WEBHOOK: HOOK},
                             send=True, opener=op)
        self.assertEqual(op.calls, [])  # validated before anything was sent

    def test_send_slack_without_env_fails(self):
        s, md = make()
        with self.assertRaises(delivery.DeliveryError):
            delivery.deliver(["slack"], s, md, DC, RC, {}, send=True, opener=FakeOpener())

    def test_preview_slack_without_env_is_allowed(self):
        s, md = make()
        out = delivery.deliver(["slack"], s, md, DC, RC, {}, send=False)
        self.assertIn("NOT SET", out[0].preview)

    def test_unknown_target(self):
        s, md = make()
        with self.assertRaises(delivery.DeliveryError):
            delivery.deliver(["fax"], s, md, DC, RC, {}, send=False)


if __name__ == "__main__":
    unittest.main()
