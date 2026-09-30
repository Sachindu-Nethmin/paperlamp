"""Unit tests for the parts where a silent bug would put a wrong number on screen.

    python3 -m unittest discover -s tests
"""
import sys, pathlib, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from p2v import verify, speak, assemble  # noqa: E402

PAPER = """Our evaluation covers 2,294 task instances from 12 repositories. The best model resolves
1.96% of issues. Codebases average 438K lines. See Table 5 and Figure 3 for details. In 2023 we
collected 93,139 pull requests; 40% of instances have at least two tests."""


class Verify(unittest.TestCase):
    def setUp(self):
        self.known = verify.paper_numbers(PAPER)

    def test_exact_numbers_pass(self):
        self.assertEqual(verify.check("It has 2,294 tasks from 12 repositories.", self.known), [])
        self.assertEqual(verify.check("The best model resolved 1.96% of them.", self.known), [])

    def test_invented_numbers_fail(self):
        self.assertEqual(verify.check("The best model resolved 2.1% of issues.", self.known), ["2.1"])
        self.assertEqual(verify.check("About 93,000 pull requests were crawled.", self.known), ["93,000"])

    def test_labels_are_not_claims(self):
        self.assertEqual(verify.numbers_in("Table 5 and Figure 3 show it, as does Section 4.2."), [])

    def test_k_suffix(self):
        self.assertEqual(verify.check("Codebases average 438,000 lines.", self.known), [])

    def test_trailing_comma(self):
        self.assertEqual(verify.numbers_in("In 2023, there were 12 repositories."), ["2023", "12"])


class Speak(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(speak.to_spoken("2,294 tasks"), "two thousand two hundred and ninety-four tasks")
        self.assertEqual(speak.to_spoken("1.96%"), "one point nine six percent")
        self.assertEqual(speak.to_spoken("14.0%"), "fourteen percent")

    def test_year_and_acronym(self):
        self.assertEqual(speak.to_spoken("In 2023 the LLM"), "In twenty twenty-three the L L M")

    def test_abbreviations(self):
        self.assertIn("for example", speak.to_spoken("e.g., this"))
        self.assertIn("and colleagues", speak.to_spoken("Jimenez et al. show"))


class Captions(unittest.TestCase):
    def test_lines_fit(self):
        text = ("Researchers took 567 pull requests created with an AI coding agent on real open-source "
                "projects, and compared them with 567 written by humans.")
        for card in assemble.chunk(text):
            for line in assemble.wrap(card):
                self.assertLessEqual(len(line), assemble.LINE)

    def test_short_sentence_single_line(self):
        self.assertEqual(assemble.wrap("Thanks for watching."), ["Thanks for watching."])


if __name__ == "__main__":
    unittest.main()
