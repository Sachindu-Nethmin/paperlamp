"""Unit tests for the parts where a silent bug would put a wrong number on screen.

    python3 -m unittest discover -s tests
"""
import sys, pathlib, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from paperlamp import verify, speak, assemble, analyze, quiz, render, script, pdf  # noqa: E402

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


class Length(unittest.TestCase):
    def chapters(self):
        mk = lambda key, n: dict(key=key, title=key, sentences=[dict(text="word " * 20) for _ in range(n)])
        return [mk("hook", 3), mk("paper", 3), mk("s3", 12), mk("s4", 6), mk("limits", 3), mk("takeaway", 2)]

    def test_trims_body_chapters_to_budget(self):
        ch = self.chapters()
        dropped = script.fit_length(ch, minutes=3)
        self.assertGreater(dropped, 0)
        self.assertLessEqual(script.words(ch), 3 * script.WPM * 1.08)
        self.assertEqual([len(c["sentences"]) for c in ch if c["key"] in ("hook", "paper", "limits", "takeaway")],
                         [3, 3, 3, 2])

    def test_short_script_untouched(self):
        ch = self.chapters()
        self.assertEqual(script.fit_length(ch, minutes=30), 0)


class Repeats(unittest.TestCase):
    def test_copied_sentences_are_replaced(self):
        first = "The benchmark has 101 papers from machine learning, vision and language venues."
        replies = iter([
            {"sentences": [first, "Each paper comes with the authors' own talk and slides."]},
            {"sentences": [first + " Indeed.", "The metrics judge slides, subtitles and speech against human ones."]},
            {"sentences": ["The quiz metric asks questions written from the paper."]},
        ])
        orig = script.llm.chat
        script.llm.chat = lambda *a, **k: next(replies)
        try:
            notes = [dict(title="3.2 Data", parent="Benchmark", appendix=False, summary="x", key_numbers=[],
                          claims=[], method=[], limitations=[], figures=[], words=110),
                     dict(title="3.3 Metrics", parent="Benchmark", appendix=False, summary="y", key_numbers=[],
                          claims=[], method=[], limitations=[], figures=[], words=110)]
            chapters = [dict(key="s3.2", title="Data", goal="g", notes=notes[:1], n=2),
                        dict(key="s3.3", title="Metrics", goal="g", notes=notes[1:], n=2)]
            real_outline = script.outline
            script.outline = lambda *a, **k: chapters
            sc = script.write({}, notes, 8, "m", lambda *a: None)
        finally:
            script.llm.chat, script.outline = orig, real_outline
        texts = [x["text"] for c in sc["chapters"] for x in c["sentences"]]
        self.assertEqual(texts.count(first), 1)
        self.assertNotIn(first + " Indeed.", texts)
        self.assertIn("The quiz metric asks questions written from the paper.", texts)
        self.assertTrue(script.near_dup(first, first + " Indeed."))
        self.assertFalse(script.near_dup(first, "Each paper comes with the authors' own talk and slides."))


class Meta(unittest.TestCase):
    DOC = dict(first_page="Paper2Video: Automatic Video Generation from Scientific Papers\n"
                          "Zeyu Zhu* Kevin Qinghong Lin* Mike Zheng Shou\nShow Lab, National University of Singapore",
               pdf_title="Paper2Video: Automatic Video Generation from Scientific Papers")

    def test_keeps_correct_meta(self):
        m = analyze.check_meta(dict(title="Paper2Video: Automatic Video Generation from Scientific Papers",
                                    authors=["Zeyu Zhu", "Kevin Qinghong Lin", "Mike Zheng Shou"]), self.DOC)
        self.assertEqual(m["checks"], [])
        self.assertEqual(len(m["authors"]), 3)

    def test_marks_glued_to_names_and_preprint(self):
        doc = dict(self.DOC, first_page="preprint\nPaper2Video\nZeyu Zhu* Kevin Qinghong Lin* Mike Zheng ShouB\n"
                                        "arXiv:2510.05096v2 [cs.CV] 9 Oct 2025")
        m = analyze.check_meta(dict(title="Paper2Video", authors=["Mike Zheng Shou", "Zeyu Zhu"], venue=""), doc)
        self.assertEqual(m["authors"], ["Mike Zheng Shou", "Zeyu Zhu"])
        self.assertEqual(m["venue"], "arXiv preprint")

    def test_fixes_wrong_title_and_invented_author(self):
        m = analyze.check_meta(dict(title="Automatic Slide Generation with Large Language Models",
                                    authors=["Zeyu Zhu", "Jane Doe"]), self.DOC)
        self.assertEqual(m["title"], self.DOC["pdf_title"])
        self.assertEqual(m["authors"], ["Zeyu Zhu"])
        self.assertEqual(len(m["checks"]), 2)


class Parse(unittest.TestCase):
    def test_text_beside_a_float_keeps_reading_order_and_the_gap(self):
        # left column text ends at 337; a side caption starts 9 pt later and sits 1 pt lower
        ws = [(346, 540.5, 380, 549, "Figure"), (383, 540.5, 392, 549, "5:"), (395, 540.5, 506, 549, "Tree Search"),
              (108, 539.5, 140, 548, "variants"), (143, 539.5, 337, 548, "for the current slide")]
        (line,) = pdf.lines_of(ws)
        self.assertEqual(line["text"], "variants for the current slide Figure 5: Tree Search")
        self.assertEqual(line["gaps"], [(337, 346)])
        # a column gap (12 pt or more) splits the line
        far = [(w[0] + 20, w[1], w[2] + 20, w[3], w[4]) if w[0] > 340 else w for w in ws]
        self.assertEqual(len(pdf.lines_of(far)), 2)

    def test_squash_handles_small_caps_and_maths_letters(self):
        self.assertEqual(pdf.squash("1    I NTRODUCTION"), pdf.squash("1 INTRODUCTION"))
        self.assertEqual(pdf.squash("4.1 \U0001d445\U0001d444 1 : How"), "41rq1how")

    def test_nice_title_keeps_names_and_acronyms(self):
        body = "PaperTalker uses a VLM. The VLM judges. Paper2Video is a benchmark of papers and videos."
        self.assertEqual(pdf.nice_title("PAPERTALKER AGENT", body), "PaperTalker Agent")
        self.assertEqual(pdf.nice_title("PAPER2VIDEO BENCHMARK", body), "Paper2Video Benchmark")
        self.assertEqual(pdf.nice_title("TREE SEARCH WITH A VLM", body), "Tree Search with a VLM")
        self.assertEqual(pdf.nice_title("Mixed Case Stays", body), "Mixed Case Stays")


class Quiz(unittest.TestCase):
    def setUp(self):
        self.known = verify.paper_numbers(PAPER)

    def q(self, **kw):
        base = dict(question="How many task instances are in the evaluation?",
                    options=["2,294", "1,200", "500", "12"], answer=0, kind="detail",
                    explanation="The evaluation covers 2,294 task instances.")
        base.update(kw)
        return quiz.normalise(base, self.known)

    def test_good_question(self):
        q, why = self.q()
        self.assertIsNone(why)
        self.assertEqual(q["answer"], 0)

    def test_letter_answer_and_option_prefixes(self):
        q, why = self.q(answer="B", options=["A) 2,294 tasks", "B) 12 repositories", "C) 5 tables", "D) 3 figures"],
                        question="How many repositories?", explanation="They come from 12 repositories.")
        self.assertIsNone(why)
        self.assertEqual((q["answer"], q["options"][1]), (1, "12 repositories"))

    def test_invented_number_in_answer_rejected(self):
        _, why = self.q(options=["2,300", "1,200", "500", "12"], explanation="About 2,300 instances.")
        self.assertIn("not in the paper", why)

    def test_malformed_rejected(self):
        self.assertIsNotNone(self.q(options=["a", "b", "c"])[1])
        self.assertIsNotNone(self.q(options=["a", "a", "b", "c"])[1])
        self.assertIsNotNone(self.q(answer=4)[1])
        self.assertIsNotNone(self.q(options=["x", "y", "z", "All of the above"])[1])

    def test_shuffle_keeps_the_right_answer(self):
        q, _ = self.q()
        s = quiz.shuffled(q)
        self.assertEqual(s["options"][s["answer"]], q["options"][q["answer"]])
        self.assertEqual(sorted(s["options"]), sorted(q["options"]))
        self.assertEqual(quiz.shuffled(q), s)                       # same question, same order

    def test_unconfirmed_answer_is_dropped(self):
        q, _ = self.q()
        orig = quiz.llm.chat
        try:
            quiz.llm.chat = lambda *a, **k: {"answer": "ABCD"[q["answer"]]}
            self.assertTrue(quiz.confirmed(q, "facts", "m"))
            quiz.llm.chat = lambda *a, **k: {"answer": "none"}
            self.assertFalse(quiz.confirmed(q, "facts", "m"))
        finally:
            quiz.llm.chat = orig

    def test_video_chapter_reads_then_reveals(self):
        q, _ = self.q()
        ch = quiz.video_chapter(dict(questions=[q, q]), k=3, think=4.0)
        self.assertEqual(len(ch["sentences"]), 4)
        first, second = ch["sentences"][:2]
        self.assertEqual((first["pause"], first["card"]["reveal"], second["card"]["reveal"]), (4.0, False, True))
        self.assertIn("option A,", first["spoken"])
        self.assertTrue(second["text"].startswith("The answer is A:"))


class Timeline(unittest.TestCase):
    def test_pause_after_quiz_question(self):
        s = [dict(id="a", chapter="Q", text="q", pause=4.0), dict(id="b", chapter="Q", text="a")]
        rows, _ = assemble.timeline(s, {"a": 2.0, "b": 1.0})
        self.assertAlmostEqual(rows[1]["start"] - rows[0]["end"], 4.0 + assemble.GAP_IN, places=3)


class Render(unittest.TestCase):
    def test_sweep_mask_progress(self):
        rect, size = [0, 0, 1920, 1080], (4000, 4000)
        boxes = [(100, 100, 1100, 140), (100, 160, 1100, 200)]
        cover = lambda p: sum(render.sweep_mask(boxes, p, rect, size).histogram()[255:])
        self.assertEqual(cover(0.0), 0)
        self.assertAlmostEqual(cover(0.5) / cover(1.0), 0.5, delta=0.03)

    def test_quiz_card_fits(self):
        long = "Which component of the PaperTalker framework is responsible for making sure that " * 2
        im, bar_y = render.quiz_card(long, ["An option with quite a few words in it"] * 4, 2, 1, 3, reveal=True,
                                     explanation="Because the explanation also needs room on the card. " * 2)
        self.assertEqual(im.size, (render.W, render.H))


if __name__ == "__main__":
    unittest.main()
