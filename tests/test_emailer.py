import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import requests

from scripts.emailer import send_resend_email


class SendResendEmailTest(unittest.TestCase):
    def test_success_with_non_json_response_and_idempotency_key(self):
        response = mock.Mock(ok=True, headers={})
        response.json.side_effect = ValueError("not json")
        with mock.patch("scripts.emailer.requests.post", return_value=response) as post:
            sent = send_resend_email(
                "subject",
                "body",
                api_key="re_test",
                to_addr="test@example.com",
                idempotency_key="finpulse-news/2026-08-01",
            )

        self.assertTrue(sent)
        self.assertEqual(
            "finpulse-news/2026-08-01",
            post.call_args.kwargs["headers"]["Idempotency-Key"],
        )

    def test_error_body_is_not_written_to_log(self):
        response = mock.Mock(
            ok=False,
            status_code=400,
            headers={"x-request-id": "request-1"},
            text="private-user@example.com",
        )
        error = requests.HTTPError(response=response)
        response.raise_for_status.side_effect = error
        output = io.StringIO()

        with mock.patch("scripts.emailer.requests.post", return_value=response), redirect_stdout(output):
            sent = send_resend_email(
                "subject",
                "body",
                api_key="re_test",
                to_addr="private-user@example.com",
            )

        self.assertFalse(sent)
        self.assertNotIn("private-user@example.com", output.getvalue())
        self.assertIn("request-1", output.getvalue())

    def test_same_day_key_already_used_is_reported_as_sent(self):
        response = mock.Mock(
            ok=False,
            status_code=409,
            headers={"x-request-id": "request-2"},
            text="private-user@example.com",
        )
        response.json.return_value = {
            "name": "invalid_idempotent_request",
            "message": "private-user@example.com",
        }
        output = io.StringIO()

        with mock.patch("scripts.emailer.requests.post", return_value=response), redirect_stdout(output):
            sent = send_resend_email(
                "subject",
                "body",
                api_key="re_test",
                to_addr="private-user@example.com",
                raise_on_error=True,
                idempotency_key="finpulse-news/2026-08-01",
            )

        self.assertTrue(sent)
        self.assertIn("送信済み", output.getvalue())
        self.assertIn("send_report.py", output.getvalue())
        self.assertNotIn("private-user@example.com", output.getvalue())
        response.raise_for_status.assert_not_called()

    def test_other_conflicts_remain_failures(self):
        response = mock.Mock(ok=False, status_code=409, headers={})
        response.json.return_value = {"name": "concurrent_idempotent_requests"}
        response.raise_for_status.side_effect = requests.HTTPError(response=response)

        with mock.patch("scripts.emailer.requests.post", return_value=response), redirect_stdout(io.StringIO()):
            sent = send_resend_email(
                "subject",
                "body",
                api_key="re_test",
                to_addr="test@example.com",
                idempotency_key="finpulse-news/2026-08-01",
            )

        self.assertFalse(sent)

    def test_non_object_success_response_is_still_success(self):
        response = mock.Mock(ok=True, status_code=200, headers={})
        response.json.return_value = ["unexpected"]

        with mock.patch("scripts.emailer.requests.post", return_value=response), redirect_stdout(io.StringIO()):
            sent = send_resend_email(
                "subject",
                "body",
                api_key="re_test",
                to_addr="test@example.com",
                raise_on_error=True,
            )

        self.assertTrue(sent)

    def test_timeout_can_be_raised_after_safe_log(self):
        with mock.patch(
            "scripts.emailer.requests.post",
            side_effect=requests.Timeout("private detail"),
        ), self.assertRaises(requests.Timeout):
            send_resend_email(
                "subject",
                "body",
                api_key="re_test",
                to_addr="test@example.com",
                raise_on_error=True,
                idempotency_key="finpulse-news/2026-08-01",
            )


if __name__ == "__main__":
    unittest.main()
