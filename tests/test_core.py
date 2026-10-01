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


class OneAtATime(unittest.TestCase):
    def test_jobs_never_overlap_and_memory_is_freed_when_idle(self):
        import threading, time
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        import app
        events, active, freed = [], [], []

        class FakeJob:
            def __init__(self, jid):
                self.jid = jid

            def run(self):
                active.append(self.jid)
                events.append(("start", self.jid, len(active)))
                time.sleep(0.2)
                active.remove(self.jid)
                events.append(("end", self.jid))

            def log(self, msg):
                pass

        orig_job, orig_free = app.pipeline.Job, app.memory.free_all
        app.pipeline.Job = FakeJob
        app.memory.free_all = lambda: freed.append(1) or {"ollama_gb": 0, "voice_servers": 0}
        try:
            r = app.Runner()
            self.assertEqual(r.add("a"), 0)
            self.assertEqual(r.add("b"), 1)
            self.assertIsNone(r.add("b"))                       # already waiting
            for _ in range(50):
                if len(events) == 4 and freed:
                    break
                time.sleep(0.05)
        finally:
            app.pipeline.Job, app.memory.free_all = orig_job, orig_free
        self.assertEqual([e[:2] for e in events], [("start", "a"), ("end", "a"), ("start", "b"), ("end", "b")])
        self.assertTrue(all(e[2] == 1 for e in events if e[0] == "start"))   # never two at once
        self.assertEqual(len(freed), 1)                          # freed once, when the queue emptied


class Memory(unittest.TestCase):
    def test_unload_all_frees_every_model(self):
        from paperlamp import llm
        held = [("ornith:9b", 5_600_000_000), ("other:7b", 4_000_000_000)]
        calls = []
        orig = llm.loaded, llm.unload
        llm.loaded = lambda: list(held)
        llm.unload = lambda name: (calls.append(name), held.remove(next(h for h in held if h[0] == name)))
        try:
            self.assertEqual(llm.unload_all(wait=1), 9.6)
        finally:
            llm.loaded, llm.unload = orig
        self.assertEqual(calls, ["ornith:9b", "other:7b"])
        self.assertEqual(held, [])

    def test_freed_after_the_last_model_stage(self):
        from paperlamp import pipeline
        later_llm = lambda key: pipeline.LLM_STAGES.intersection(
            [k for k, _ in pipeline.STAGES[[k for k, _ in pipeline.STAGES].index(key) + 1:]])
        self.assertTrue(later_llm("script"))
        self.assertFalse(later_llm("quiz"))


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


class SentenceHighlight(unittest.TestCase):
    @staticmethod
    def line(page, y, text):
        x, ws = 108.0, []
        for w in text.split():
            ws.append([x, y, x + 5 * len(w), y + 9, w]); x += 5 * len(w) + 3
        return dict(page=page, box=[108, y, ws[-1][2], y + 9], text=text, words=ws)

    def test_whole_source_sentence_is_highlighted(self):
        from paperlamp import align
        idx = [self.line(2, 100, "We built a benchmark. Results on Paper2Video confirm"),
               self.line(2, 112, "the effectiveness of PaperTalker, which outperforms human-made"),
               self.line(2, 124, "presentations by 10% in PresentQuiz accuracy. Next sentence here.")]
        boxes = align.sentence_span(idx, 2, set(align.tokens("beat their talks by 10% on PresentQuiz")))
        self.assertEqual(len(boxes), 3)
        first_word = next(w for w in idx[0]["words"] if w[4] == "Results")
        last_word = next(w for w in idx[2]["words"] if w[4] == "accuracy.")
        self.assertEqual(boxes[0][0], first_word[0])          # starts at "Results", not at the line start
        self.assertEqual(boxes[2][2], last_word[2])           # ends at "accuracy.", not at the line end

    def test_abbreviations_do_not_end_a_sentence(self):
        from paperlamp import align
        self.assertFalse(align._ends_sentence("e.g."))
        self.assertFalse(align._ends_sentence("al."))
        self.assertTrue(align._ends_sentence("accuracy."))
        self.assertTrue(align._ends_sentence("work.)"))


class Passes(unittest.TestCase):
    TEXT = ("1 Introduction\nWe build on PPTAgent [3] and on [3, 4] and [1-2].\nPPTAgent is strong [3].\n"
            "References\n[1] Ann Lee and Bo Kim. A study of slides. In ACL, 2023.\n"
            "[2] DeepMind. Veo 3 technical report. 2025.\n"
            "[3] Hao Zheng, Xinyan Guan, and Le Sun. Pptagent: Generating and evaluating presentations.\n"
            "    arXiv preprint, 2025.\n[4] Jo Park. Talking heads. 2024.\n")

    def test_references_and_most_cited(self):
        from paperlamp import passes
        refs, body = passes.references(self.TEXT)
        self.assertEqual(sorted(refs), [1, 2, 3, 4])
        self.assertEqual(passes.cited_counts(body).most_common(1), [(3, 3)])
        self.assertEqual(passes.short_ref(refs[3], body), "PPTAgent (Zheng and colleagues, 2025)")
        self.assertEqual(passes.short_ref(refs[2], body), "Veo 3 technical report (DeepMind, 2025)")
        out = passes.reference_sentences(self.TEXT)
        self.assertEqual(out[0]["text"], "The reference list has four entries.")
        self.assertTrue(out[-1]["hold"])
        self.assertEqual(out[1]["text"], "The work it cites most is PPTAgent (Zheng and colleagues, 2025).")
        self.assertEqual(out[1]["anchor"], {"text": "[3]", "reference": True})

    def test_structure_tour(self):
        from paperlamp import passes
        doc = {"sections": [dict(num="0", title="Front matter", parent="Front matter", appendix=False),
                            dict(num="1", title="Introduction", parent="Introduction", appendix=False),
                            dict(num="2.1", title="Data", parent="Benchmark", appendix=False),
                            dict(num="2.2", title="Metrics", parent="Benchmark", appendix=False),
                            dict(num="A.1", title="Prompts", parent="Prompts", appendix=True)]}
        out = passes.structure_sentences(doc)
        self.assertEqual(out[0]["text"], "The main text has two sections.")
        self.assertEqual(out[0]["anchor"]["text"], "1 Introduction")
        self.assertEqual(out[2]["text"], "Section 2, Benchmark, covers Data and Metrics.")
        self.assertEqual(out[2]["anchor"]["text"], "2 Benchmark")
        self.assertEqual(out[-1]["text"], "An appendix adds Prompts.")

    def test_figure_falls_back_to_its_caption(self):
        from paperlamp import passes
        w = passes.Writer({}, "m", lambda *a: None, steps=3)
        w.ask = lambda *a, **k: ["This shows something else entirely, says Table 9."]
        figs = [dict(label="Figure 2", page=3, box=[0, 0, 1, 1], caption="Figure 2: Statistics of the benchmark. More.")]
        out = passes.figure_sentences(w, figs, [])
        self.assertEqual(out[0], {"text": "Figure 2 shows statistics of the benchmark.", "figure": "Figure 2"})

    def test_caption_figures_read_the_caption(self):
        from paperlamp import passes
        figs = [dict(label="Table 2", page=8, box=[0, 0, 1, 1],
                     caption="Table 2: Detailed results across three baselines. Bold and Underline indicates the "
                             "best and the second. NA means not applicable."),
                dict(label="Figure 1", page=2, box=[0, 0, 1, 1],
                     caption="Figure 1: This work solves two problems: Left: how to create a video? Right: how to "
                             "evaluate it? More text here.")]
        out = passes.caption_figures(None, figs)
        self.assertEqual([x["text"] for x in out], ["Figure 1: This work solves two problems: Left: how to create a video?",
                                                    "Right: how to evaluate it?",
                                                    "Table 2: Detailed results across three baselines."])
        self.assertEqual({x["figure"] for x in out}, {"Figure 1", "Table 2"})

    def test_anchor_on_heading_and_reference(self):
        from paperlamp import align
        line = SentenceHighlight.line
        idx = [line(3, 100, "P APER 2V IDEO B ENCHMARK"), line(3, 120, "Text of the section goes on here today."),
               line(12, 100, "[38] Hao Zheng, Xinyan Guan, and Le Sun. Pptagent: Generating"),
               line(12, 112, "and evaluating presentations. arXiv preprint, 2025."),
               line(12, 124, "[39] Next entry starts here.")]
        a = align._anchor(idx, {"text": "3 Paper2Video Benchmark", "heading": True})
        self.assertEqual((a["page"], a["highlight"]), (3, [idx[0]["box"]]))
        r = align._anchor(idx, {"text": "[38]", "reference": True})
        self.assertEqual(r["highlight"], [idx[2]["box"], idx[3]["box"]])


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
