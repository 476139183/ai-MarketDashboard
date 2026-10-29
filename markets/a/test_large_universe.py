import unittest
from large_universe import exclusion_reason


class LargeUniverseTest(unittest.TestCase):
    def test_st_delisting_and_size_are_rejected(self):
        self.assertIsNotNone(exclusion_reason('*ST和科', 500, 2e8))
        self.assertIsNotNone(exclusion_reason('示例退', 500, 2e8))
        self.assertIsNotNone(exclusion_reason('示例', 99, 2e8))
        self.assertIsNotNone(exclusion_reason('示例', 101, 9e7))
        self.assertIsNone(exclusion_reason('示例', 101, 2e8))


if __name__ == '__main__':
    unittest.main()
