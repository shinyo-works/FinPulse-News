import unittest
from unittest import mock

import send_resend


class SendResendCliTest(unittest.TestCase):
    def test_requires_exactly_subject_and_body(self):
        for argv in (
            ["send_resend.py"],
            ["send_resend.py", "件名"],
            ["send_resend.py", "件名", "本文", "余分"],
        ):
            with self.subTest(argv=argv), mock.patch.object(
                send_resend, "send_via_resend"
            ) as sender:
                self.assertEqual(1, send_resend.main(argv))
                sender.assert_not_called()

    def test_sends_when_argument_count_is_exact(self):
        with mock.patch.object(
            send_resend, "send_via_resend", return_value=True
        ) as sender:
            self.assertEqual(
                0,
                send_resend.main(["send_resend.py", "件名", "本文"]),
            )

        sender.assert_called_once_with("件名", "本文")


if __name__ == "__main__":
    unittest.main()
