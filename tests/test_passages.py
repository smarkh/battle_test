import unittest

from battle_test import passages


class SplitTest(unittest.TestCase):
    def test_a_short_section_is_one_passage(self):
        self.assertEqual(passages.split("A party may\n  move for summary judgment."),
                         ["A party may move for summary judgment."])
        self.assertEqual(passages.split("   \n "), [])

    def test_a_long_section_is_cut_in_order_without_losing_or_repeating_text(self):
        text = "\n".join(f"({n}) Paragraph {n} says something about deposits and days. " * 3
                         for n in range(1, 40))
        cut = passages.split(text, limit=500)
        self.assertGreater(len(cut), 5)
        self.assertTrue(all(len(p) <= 500 for p in cut))
        self.assertEqual(" ".join(cut).split(), text.split())

    def test_a_block_with_no_breaks_is_cut_at_spaces(self):
        table = " ".join(f"row{n} 12.50" for n in range(400))  # no full stops or subdivisions
        cut = passages.split(table, limit=300)
        self.assertTrue(all(len(p) <= 300 for p in cut))
        self.assertEqual(" ".join(cut).split(), table.split())

    def test_passages_are_built_from_the_excerpt_blocks(self):
        # Stage 3 embeds these passages and stage 1 excerpts pick from the
        # blocks: a passage boundary always falls on a block boundary.
        text = "\n".join(f"(a) First part of rule {n}. (b) Second part of rule {n}." * 8 for n in range(10))
        blocks = passages.blocks(text)
        i = 0
        for p in passages.split(text, limit=900):
            j = i + 1
            while " ".join(blocks[i:j]) != p:
                j += 1
                self.assertLessEqual(j, len(blocks), f"passage isn't whole blocks: {p[:60]}")
            i = j
        self.assertEqual(i, len(blocks))


if __name__ == "__main__":
    unittest.main()
