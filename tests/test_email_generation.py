import unittest

from core.base_mailbox import generate_human_like_email_local_part


class EmailGenerationTests(unittest.TestCase):
    def test_generate_human_like_email_local_part_is_unique_and_alnum(self):
        local_parts = [generate_human_like_email_local_part() for _ in range(128)]

        self.assertEqual(len(local_parts), len(set(local_parts)))
        for item in local_parts:
            self.assertRegex(item, r"^[a-z0-9]+$")
            self.assertGreaterEqual(len(item), 10)


if __name__ == "__main__":
    unittest.main()
