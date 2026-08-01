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
