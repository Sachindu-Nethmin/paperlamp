"""Unit tests for the parts where a silent bug would put a wrong number on screen.

    python3 -m unittest discover -s tests
"""
import re, sys, json, pathlib, tempfile, threading, unittest, unittest.mock

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

    def test_roman_numerals_are_numbers(self):
        say = speak.to_spoken
        self.assertEqual(say("Stage III applies each PR."), "Stage three applies each P R.")
        self.assertEqual(say("Stage I collects data."), "Stage one collects data.")
        self.assertEqual(say("Stages I and II filter."), "Stages one and two filter.")
        self.assertEqual(say("Table IV and Section XII"), "Table four and Section twelve")
        self.assertEqual(say("A Type I error"), "A Type one error")
        self.assertEqual(say("(i) parse, (ii) align, (iii) render"), "(one) parse, (two) align, (three) render")
        self.assertEqual(say("I think the LLM is good."), "I think the L L M is good.")   # the pronoun stays
        self.assertEqual(say("The IV estimate"), "The I V estimate")                        # an acronym stays
        self.assertEqual(say("variable (x) here"), "variable (x) here")                     # not an enumeration

    def test_years_tildes_and_plural_acronyms(self):
        say = speak.to_spoken
        self.assertEqual(say("published at ICLR 2024."), "published at I C L R twenty twenty-four.")
        self.assertEqual(say("about ~90,000 PRs"), "about ninety thousand P Rs")
        self.assertEqual(say("~12 repositories"), "about twelve repositories")
        self.assertEqual(say("2024.5 lines"), "two thousand and twenty-four point five lines")


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


class Opening(unittest.TestCase):
    """The video must open on the title page: the paper's own title and authors first,
    then the hook. Opening on the hook dropped the viewer into the middle of the paper."""

    NOTES = [dict(title="1 Introduction", summary="s", key_numbers=[], claims=[], method=[],
                  limitations=[], figures=[], appendix=False, words=500)]

    def test_paper_comes_before_hook_in_every_depth(self):
        meta = {"title": "T", "authors": ["Ada Lovelace"], "venue": "ICLR", "year": "2024"}
        for depth in ("summary", "auto", "full"):
            keys = [c["key"] for c in script.outline(meta, self.NOTES, 8, depth=depth)]
            self.assertEqual(keys[:2], ["paper", "hook"], depth)

    def test_intro_chapters_keeps_both_budgets_with_their_own_chapter(self):
        ch = script.intro_chapters({"title": "T"}, self.NOTES, n_hook=5, n_paper=2)
        self.assertEqual([(c["key"], c["n"]) for c in ch], [("paper", 2), ("hook", 5)])

    TITLE = "Paper2Video: Automatic Video Generation from Scientific Papers"

    def lead(self, *texts):
        return [r["text"] for r in script.lead_with_title([dict(text=t) for t in texts], self.TITLE)]

    def test_title_sentence_is_moved_to_the_front(self):
        self.assertEqual(
            self.lead("It also proposes Paper2Video, a benchmark.", f'The paper is titled "{self.TITLE}".',
                      "It came out in 2025."),
            [f'The paper is titled "{self.TITLE}".', "It also proposes Paper2Video, a benchmark.", "It came out in 2025."])

    def test_order_left_alone_when_no_sentence_names_the_title(self):
        rows = ["The paper introduces PaperTalker, a multi-agent framework.", "It was published at ACL 2025."]
        self.assertEqual(self.lead(*rows), rows)

    def test_missing_title_is_not_an_error(self):
        self.assertEqual(script.lead_with_title([dict(text="Anything.")], "")[-1]["text"], "Anything.")


class License(unittest.TestCase):
    """The description has to say what the license allows, and CC BY is the only common
    one that lets the video be published and monetised without asking anyone."""

    def test_reads_the_license_off_the_paper(self):
        for text, want in [
            ("Licensed under CC BY 4.0. https://creativecommons.org/licenses/by/4.0/", "CC BY 4.0"),
            ("This work is licensed under a Creative Commons Attribution-NonCommercial-ShareAlike "
             "4.0 International License.", "CC BY-NC-SA 4.0"),
            ("Licensed under CC BY-NC-ND 4.0.", "CC BY-NC-ND 4.0"),
            ("under CC BY-NC 4.0", "CC BY-NC 4.0"),
            ("under CC BY-ND 4.0", "CC BY-ND 4.0"),
            ("https://creativecommons.org/licenses/by-nc-sa/4.0/", "CC BY-NC-SA 4.0"),
            ("arXiv:2501.12104v1 [cs.SE] 21 Jan 2025", ""),        # arXiv's default: no reuse rights
            ("(C) 2024 IEEE. All rights reserved. Personal use permitted.", ""),
        ]:
            self.assertEqual(pdf.license_hint(text), want, text[:40])

    def test_arxiv_number_is_found_with_and_without_a_version(self):
        self.assertEqual(pdf.arxiv_id("arXiv:2510.05096v1 [cs.CV] 2 Oct 2025"), "2510.05096")
        self.assertEqual(pdf.arxiv_id("see https://arxiv.org/abs/2310.06770"), "2310.06770")
        self.assertEqual(pdf.arxiv_id("no number here"), "")

    def test_cc_by_may_be_published_and_monetised(self):
        b = pdf.license_block("CC BY 4.0")
        self.assertIn("https://creativecommons.org/licenses/by/4.0/", b)
        self.assertIn("earn money", b)
        self.assertIn("say that you changed the paper", b)        # attribution requires stating the change
        self.assertIn("have not reviewed or endorsed", b)

    def test_non_commercial_says_so_and_says_ask_the_authors(self):
        b = pdf.license_block("CC BY-NC-SA 4.0")
        self.assertIn("not commercially", b)
        self.assertIn("Ask the authors first", b)
        self.assertIn("same license", b)

    def test_an_unlicensed_paper_asks_for_permission(self):
        b = pdf.license_block("")
        self.assertIn("Ask the authors or the publisher for permission", b)
        self.assertIn("arXiv's default license is not an open license", b)

    def test_a_hand_written_license_string_is_not_a_known_license(self):
        # "CC BY 4.0 (arXiv:2510.05096)" was written by hand into doc.json; the pipeline
        # strips the parenthesis before asking, so the lookup still finds the license
        self.assertNotIn("earn money", pdf.license_block("CC BY 4.0 (arXiv:2510.05096)"))
        self.assertIn("earn money", pdf.license_block(re.sub(r"\s*\(.*?\)\s*", " ", "CC BY 4.0 (arXiv:2510.05096)").strip()))


class Licensing(unittest.TestCase):
    """Most arXiv papers never print their license inside the PDF, so asking arXiv is the only
    way to know. Getting this wrong tells the user they may not publish a paper they may."""

    def test_the_paper_itself_wins_when_it_says(self):
        from paperlamp import pdf
        with tempfile.TemporaryDirectory() as tmp:
            got = pdf.resolve_license("Licensed under CC BY-NC 4.0.", tmp, "2310.06770")
            self.assertEqual(got, "CC BY-NC 4.0")
            self.assertFalse((pathlib.Path(tmp) / "arxiv-license.txt").exists())   # no lookup needed

    def test_the_arxiv_page_is_asked_when_the_paper_says_nothing(self):
        from paperlamp import pdf
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.object(pdf, "arxiv_license", return_value="CC BY 4.0"):
                self.assertEqual(pdf.resolve_license("no license here", tmp, "2310.06770"), "CC BY 4.0")
            self.assertEqual((pathlib.Path(tmp) / "arxiv-license.txt").read_text().strip(), "CC BY 4.0")

    def test_the_answer_is_looked_up_once_and_kept(self):
        from paperlamp import pdf
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.object(pdf, "arxiv_license", return_value="CC BY 4.0") as ask:
                pdf.resolve_license("x", tmp, "2310.06770")
                self.assertEqual(pdf.resolve_license("x", tmp, "2310.06770"), "CC BY 4.0")
                self.assertEqual(ask.call_count, 1)

    def test_no_arxiv_number_means_no_guess(self):
        from paperlamp import pdf
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.object(pdf, "arxiv_license", return_value="CC BY 4.0") as ask:
                self.assertEqual(pdf.resolve_license("x", tmp, ""), "")
                ask.assert_not_called()

    def test_an_unreachable_page_never_becomes_a_license(self):
        from paperlamp import pdf
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.object(pdf.urllib.request, "urlopen", side_effect=OSError("offline")):
                self.assertIsNone(pdf.arxiv_license("2310.06770"))       # unknown, not "no license"
                self.assertEqual(pdf.resolve_license("x", tmp, "2310.06770"), "")
            self.assertFalse((pathlib.Path(tmp) / "arxiv-license.txt").exists())   # not kept: asked again later
            with unittest.mock.patch.object(pdf, "arxiv_license", return_value="CC BY 4.0"):
                self.assertEqual(pdf.resolve_license("x", tmp, "2310.06770"), "CC BY 4.0")

    def test_arxivs_own_default_license_is_treated_as_no_permission(self):
        """arXiv's default license grants no reuse rights, so it must not be reported as open."""
        from paperlamp import pdf
        page = '<div class="abs-license"><a href="http://arxiv.org/licenses/nonexclusive-distrib/1.0/">license</a></div>'
        with unittest.mock.patch.object(pdf.urllib.request, "urlopen",
                                        return_value=unittest.mock.MagicMock(read=lambda: page.encode())):
            self.assertEqual(pdf.arxiv_license("2310.06770"), "")

    def test_each_cc_variant_is_named_correctly(self):
        from paperlamp import pdf
        for url, want in (("by/4.0", "CC BY 4.0"), ("by-nc/4.0", "CC BY-NC 4.0"),
                          ("by-nc-sa/4.0", "CC BY-NC-SA 4.0"), ("by-sa/3.0", "CC BY-SA 3.0")):
            page = f'<div class="abs-license"><a href="http://creativecommons.org/licenses/{url}/">x</a></div>'
            with unittest.mock.patch.object(pdf.urllib.request, "urlopen",
                                            return_value=unittest.mock.MagicMock(read=lambda: page.encode())):
                self.assertEqual(pdf.arxiv_license("2310.06770"), want)

    def test_an_arxiv_paper_we_could_not_check_is_not_called_unlicensed(self):
        """The old wording asserted the paper grants nothing. We only know we did not find out."""
        from paperlamp import pdf
        b = pdf.license_block("", "2310.06770")
        self.assertIn("arxiv.org/abs/2310.06770", b)
        self.assertNotIn("does not state an open license", b)

    def test_a_non_arxiv_paper_with_no_license_still_warns(self):
        from paperlamp import pdf
        b = pdf.license_block("", "")
        self.assertIn("does not state an open license", b)
        self.assertIn("Ask the authors", b)


class Voice(unittest.TestCase):
    """The cloned voice is the better narration, so it is what a job gets by default."""

    def cloned(self, **extra):
        from paperlamp import pipeline
        return dict(chatterbox_python="/venv/bin/python", voice_reference="/tmp/ref.wav", **extra)

    def test_the_cloned_voice_is_the_default_when_it_is_set_up(self):
        from paperlamp import pipeline
        self.assertEqual(pipeline.default_voice(self.cloned()), "chatterbox")
        self.assertEqual(pipeline.default_voice({}), "say")

    def test_a_voice_you_picked_yourself_is_never_overridden(self):
        from paperlamp import pipeline
        self.assertEqual(pipeline.default_voice(self.cloned(voice="say")), "say")
        self.assertEqual(pipeline.default_voice({"voice": "say"}), "say")

    def test_half_configured_cloning_falls_back_to_the_system_voice(self):
        """Cloning without a Python or a reference recording cannot work, so it is not the default."""
        from paperlamp import pipeline
        self.assertEqual(pipeline.default_voice({"chatterbox_python": "/venv/bin/python"}), "say")
        self.assertEqual(pipeline.default_voice({"voice_reference": "/tmp/ref.wav"}), "say")

    def test_a_new_job_narrates_in_the_cloned_voice(self):
        from paperlamp import pipeline
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.object(pipeline, "JOBS", pathlib.Path(tmp)):
            job = pipeline.Job.create(b"%PDF-1.4", "p.pdf", self.cloned())
            self.assertEqual(job.state["settings"]["voice"], "chatterbox")
            other = pipeline.Job.create(b"%PDF-1.4", "q.pdf", dict(self.cloned(), voice="say"))
            self.assertEqual(other.state["settings"]["voice"], "say")


class Exported(unittest.TestCase):
    """A folder of finished videos is only browsable if the files say which paper they
    are, so the exported name comes from the paper's own metadata."""

    def safe(self, **meta):
        from paperlamp import pipeline
        return pipeline.safe_name(meta)

    def test_name_is_the_title_and_where_it_was_published(self):
        self.assertEqual(self.safe(title="SWE-bench: Can Language Models Resolve Real-World "
                                         "GitHub Issues?", venue="ICLR", year="2024"),
                         "SWE-bench Can Language Models Resolve Real-World GitHub Issues (ICLR 2024)")

    def test_venue_and_year_already_in_the_title_are_not_repeated(self):
        self.assertEqual(self.safe(title="A Paper from ICML 2024", venue="ICML", year="2024"),
                         "A Paper from ICML 2024")

    def test_only_the_year_is_repeated_when_the_title_names_the_venue(self):
        self.assertEqual(self.safe(title="Results from ICML", venue="ICML", year="2024"),
                         "Results from ICML (2024)")

    def test_path_characters_are_removed_and_the_tail_kept(self):
        n = self.safe(title="Deep " * 60 + "Models: A Study/With Slashes", venue="NeurIPS", year="2023")
        self.assertLessEqual(len(n), 90)
        self.assertNotIn("/", n)
        self.assertTrue(n.endswith("(NeurIPS 2023)"), n)

    def test_a_paper_with_no_metadata_still_gets_a_name(self):
        self.assertEqual(self.safe(), "Untitled")
        self.assertEqual(self.safe(title="   "), "Untitled")

    def test_nothing_is_exported_without_a_folder(self):
        from paperlamp import pipeline
        job = pipeline.Job.__new__(pipeline.Job)
        job.state, job.log = {"settings": {}}, lambda *a: None
        self.assertEqual(job.export(), {})

    def test_a_second_job_of_the_same_paper_gets_its_own_file(self):
        from paperlamp import pipeline
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "video.mp4"
            p.write_bytes(b"first")
            self.assertEqual(pipeline.Job.undisturbed(p).name, "video (2).mp4")

    def test_each_paper_gets_a_folder_of_its_own(self):
        """The whole point of exporting: one paper, one numbered folder, plain names inside."""
        from paperlamp import pipeline
        with tempfile.TemporaryDirectory() as tmp:
            job = self.finished_job(tmp, "Attention Is All You Need", "NeurIPS", "2017")
            out = job.export()
            folder = pathlib.Path(out["video"]).parent
            self.assertEqual(folder.name, "01 - Attention Is All You Need (NeurIPS 2017)")
            self.assertEqual(folder.parent, pathlib.Path(tmp))
            self.assertEqual(sorted(p.name for p in folder.iterdir() if p.suffix != ".json"),
                             ["captions.ass", "captions.srt", "description.txt", "narration.wav", "quiz.md",
                              "script.md", "video.mp4"])
            self.assertEqual(pathlib.Path(out["video"]).name, "video.mp4")

    def test_folders_are_numbered_in_the_order_the_videos_were_made(self):
        """The number is the order, so the folder list sorts into the order you made them."""
        from paperlamp import pipeline
        self.assertEqual(pipeline.numbered(1, "A Paper"), "01 - A Paper")
        self.assertEqual(pipeline.numbered(10, "A Paper"), "10 - A Paper")
        self.assertEqual(sorted(["10 - B", "02 - A", "01 - C"]), ["01 - C", "02 - A", "10 - B"])
        with tempfile.TemporaryDirectory() as tmp:
            first = self.finished_job(tmp, "First Paper").export()
            second = self.finished_job(tmp, "Second Paper", jid="job-2").export()
            self.assertEqual(pathlib.Path(first["video"]).parent.name, "01 - First Paper")
            self.assertEqual(pathlib.Path(second["video"]).parent.name, "02 - Second Paper")

    def test_a_job_keeps_the_number_it_was_given(self):
        """Re-exporting must not shuffle a paper to the end of the folder."""
        from paperlamp import pipeline
        with tempfile.TemporaryDirectory() as tmp:
            self.finished_job(tmp, "First Paper").export()
            keep = self.finished_job(tmp, "Later Paper", jid="job-2").export()
            self.assertEqual(pathlib.Path(keep["video"]).parent.name, "02 - Later Paper")
            again = self.finished_job(tmp, "Later Paper", jid="job-2").export()  # same job, exported again
            self.assertEqual(pathlib.Path(again["video"]).parent.name, "02 - Later Paper")

    def test_a_number_already_on_disk_is_not_handed_out_twice(self):
        from paperlamp import pipeline
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "07 - Something You Added Yourself").mkdir()
            job = self.finished_job(tmp, "Another Paper")
            self.assertEqual(pathlib.Path(job.export()["video"]).parent.name, "08 - Another Paper")

    def finished_job(self, root, title, venue="", year="", jid="job-1"):
        """A job that has already been rendered, sitting in <root>/<id>."""
        from paperlamp import pipeline
        d = pathlib.Path(root) / jid
        (d / "out").mkdir(parents=True, exist_ok=True)
        for _, rel, name in pipeline.EXPORTED:
            (d / rel).write_text(name)
        (d / "script.json").write_text(json.dumps({"meta": {"title": title, "venue": venue, "year": year}}))
        (d / "state.json").write_text(json.dumps({"id": jid, "settings": {"depth": "summary",
                                                                          "export_dir": root}}))
        with unittest.mock.patch.object(pipeline, "JOBS", pathlib.Path(root)):
            return pipeline.Job(jid)

    def test_a_job_only_overwrites_files_it_exported_itself(self):
        from paperlamp import pipeline
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / ".paperlamp-exports.json").write_text('{"A.mp4": "job-1"}')
            job = pipeline.Job.__new__(pipeline.Job)
            job.state, job.log = {"settings": {}}, lambda *a: None
            self.assertEqual(job.exports_in(tmp), {"files": {"A.mp4": "job-1"}, "papers": {}})
            (pathlib.Path(tmp) / ".paperlamp-exports.json").write_text("not json")
            self.assertEqual(job.exports_in(tmp), {})


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


class Study(unittest.TestCase):
    def notes(self):
        return KeySections().notes()                       # the same paper as the key-sections tests

    def test_outline_follows_the_evidence(self):
        ch = script.outline({}, self.notes(), 8, "study")
        keys = [c["key"] for c in ch]
        self.assertEqual(keys[:4], ["paper", "hook", "abstract", "terms"])   # goal and key terms first
        segs = [c for c in ch if c.get("prequestion")]
        self.assertTrue(segs and all(c["n"] <= script.SEGMENT for c in segs))
        self.assertEqual([c["title"].split(":")[0] for c in segs], ["Method", "Results", "Discussion"])  # same-kind neighbours joined
        self.assertIn("critically", segs[-1]["goal"])
        self.assertEqual(keys[-1], "takeaway")

    def test_question_before_and_answer_after_each_segment(self):
        replies = iter([{"sentences": ["The agent reads the paper first.", "Then it plans the slides."]},
                        {"question": "What does the agent do first?", "answer": "It reads the paper first."}])
        orig, real_outline = script.llm.chat, script.outline
        script.llm.chat = lambda *a, **k: next(replies)
        script.outline = lambda *a, **k: [dict(key="s4", title="Method: Agent", goal="g", n=2, prequestion=True,
                                              notes=[AutoLength.note("4 Agent", claims=2)])]
        try:
            sc = script.write({}, [], 8, "m", lambda *a: None, depth="study")
        finally:
            script.llm.chat, script.outline = orig, real_outline
        texts = [s["text"] for s in sc["chapters"][0]["sentences"]]
        self.assertEqual(texts[0], "A question to keep in mind: What does the agent do first?")
        self.assertEqual(texts[-1], "So, the answer: it reads the paper first.")
        self.assertEqual(sc["study_questions"][0]["question"], "What does the agent do first?")

    def test_answer_with_an_invented_number_is_not_asked(self):
        orig = script.llm.chat
        script.llm.chat = lambda *a, **k: {"question": "How many tasks are there in total?", "answer": "There are 999 tasks."}
        try:
            got = script.prequestion(dict(title="Data", notes=[AutoLength.note("3 Data", nums=2)]), {}, "m")
        finally:
            script.llm.chat = orig
        self.assertIsNone(got)


class WholePaper(unittest.TestCase):
    @staticmethod
    def block(x0, y0, x1, text, lines=1):
        ws, x = [], x0
        for w in text.split():
            ws.append([x, y0, x + 5 * len(w), y0 + 9, w]); x += 5 * len(w) + 3
        return dict(box=[x0, y0, x1, y0 + 11 * lines], lines=[dict(box=[x0, y0, x1, y0 + 9], words=ws)])

    def test_two_columns_read_left_then_right(self):
        from paperlamp import reader
        b = self.block
        blocks = [b(60, 80, 550, "A Wide Title"), b(320, 120, 560, "right one"), b(60, 300, 290, "left two"),
                  b(60, 120, 290, "left one"), b(320, 300, 560, "right two"), b(60, 500, 550, "wide figure caption")]
        order = [reader.block_text(x) for x in reader.reading_order(blocks, 612)]
        self.assertEqual(order, ["A Wide Title", "left one", "left two", "right one", "right two", "wide figure caption"])

    def test_line_break_hyphens(self):
        from paperlamp import reader
        mk = lambda ts: [dict(w=[0, 0, 1, 1, t], eol=e) for t, e in ts]
        self.assertEqual(reader._join(mk([("an", 0), ("individ-", 1), ("ual", 0), ("function.", 0)])),
                         "an individual function.")
        self.assertEqual(reader._join(mk([("state-of-", 1), ("the-art", 0), ("models.", 0)])),
                         "state-of-the-art models.")
        self.assertEqual(reader._join(mk([("A", 0), ("BSTRACT", 0), ("Language", 0), ("models", 0), ("grow.", 0)])),
                         "Language models grow.")

    def test_explanations_keep_numbers_or_fall_back(self):
        from paperlamp import reader
        src = ["We collect 2,294 task instances from 12 repositories.", "Models struggle."]
        orig = reader.llm.chat
        reader.llm.chat = lambda *a, **k: {"explanations": [
            "The benchmark has 2,294 tasks taken from 12 real projects.", "Models fix 5% of them, so they struggle."]}
        try:
            got = reader.explain(src, "Intro", "P", "m")
        finally:
            reader.llm.chat = orig
        self.assertEqual(got[0], "The benchmark has 2,294 tasks taken from 12 real projects.")
        self.assertEqual(got[1], src[1])                      # an invented number: the paper's own words instead


class YouTubeFiles(unittest.TestCase):
    META = dict(title="SWE-Bench: Can Language Models Resolve Real-World GitHub Issues?", venue="ICLR 2024",
                year=2024, authors=["Carlos E. Jimenez", "John Yang", "Ofir Press"])

    def test_title_within_youtube_limit(self):
        from paperlamp import youtube
        for depth in youtube.KIND:
            self.assertLessEqual(len(youtube.youtube_title(self.META, depth)), 100, depth)
        long = dict(self.META, title="Agentic: " + "very long title words " * 8)
        t = youtube.youtube_title(long, "read")
        self.assertLessEqual(len(t), 100)
        self.assertTrue(t.startswith("Agentic | "))           # shortened to the part before the colon

    def test_wrap_ends_when_a_lone_word_is_too_wide(self):
        from PIL import Image, ImageDraw
        from paperlamp import youtube
        d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        title = "Why Are AI Agent–Involved Pull Requests (Fix-Related) Remain Unmerged? An Empirical Study"
        for size in range(132, 50, -6):                       # the thumbnail's sizes; 114 hung the app
            ft = render.font(size, True)
            rows = youtube._wrap(d, title, ft, 604, 2)
            self.assertLessEqual(len(rows), 2)
            self.assertLessEqual(d.textlength(rows[-1], font=ft), 604, size)

    def test_tags_and_upload_text(self):
        from paperlamp import youtube
        tags = youtube.tags(self.META, "study")
        self.assertLessEqual(sum(len(x) + 1 for x in tags), 500)
        self.assertIn("SWE-Bench", tags)
        text, info = youtube.details(self.META, "study", "Desc.", "CC BY 4.0", seconds=600)
        self.assertTrue(info["allowed"])
        self.assertIn("Category: Education.", text)
        self.assertIn("may publish this video and earn money", text)
        text, info = youtube.details(self.META, "study", "Desc.", "")
        self.assertFalse(info["allowed"])
        self.assertIn("Do not publish", text)
        text, info = youtube.details(self.META, "study", "Desc.", "", permission="Email from the authors, 6 Oct 2026")
        self.assertTrue(info["allowed"])

    def test_export_makes_a_youtube_folder(self):
        import json, tempfile
        from paperlamp import pipeline
        with tempfile.TemporaryDirectory() as d:
            old = pipeline.JOBS
            pipeline.JOBS = pathlib.Path(d) / "jobs"
            try:
                job = pipeline.Job.create(b"%PDF-1.4", "p.pdf", dict(export_dir=str(pathlib.Path(d) / "Videos")))
                (job.dir / "out/youtube").mkdir(parents=True)
                (job.dir / "out/video.mp4").write_bytes(b"video")
                (job.dir / "out/captions.srt").write_text("1")
                (job.dir / "out/youtube/thumbnail.png").write_bytes(b"png")
                (job.dir / "out/youtube/details.txt").write_text("TITLE")
                (job.dir / "out/youtube/youtube.json").write_text(json.dumps(dict(title="A Paper | Study Guide")))
                job.write("script.json", dict(meta=dict(title="A Paper", venue="X", year=2026)))
                job.export()
                yt = pathlib.Path(job.state["exported_to"]) / "YouTube"
                self.assertEqual(sorted(p.name for p in yt.iterdir()),
                                 ["A Paper - Study Guide - Captions.srt", "A Paper - Study Guide - Thumbnail.png",
                                  "A Paper - Study Guide - YouTube details.txt", "A Paper - Study Guide.mp4"])
                self.assertEqual((yt / "A Paper - Study Guide.mp4").read_bytes(), b"video")
                self.assertTrue((yt.parent / "video.json").exists())
            finally:
                pipeline.JOBS = old


class LicenseGate(unittest.TestCase):
    def test_which_licenses_allow_the_video(self):
        self.assertTrue(pdf.video_allowed("CC BY 4.0"))
        self.assertTrue(pdf.video_allowed("CC BY-SA 4.0"))
        self.assertTrue(pdf.video_allowed("CC BY-NC-SA 4.0"))
        self.assertTrue(pdf.video_allowed("CC0 1.0"))
        self.assertTrue(pdf.video_allowed("CC BY 4.0 (arXiv:2310.06770)"))
        for lic in ("", "CC BY-ND 4.0", "CC BY-NC-ND 4.0", "arXiv default"):
            self.assertFalse(pdf.video_allowed(lic), lic)

    def test_job_stops_without_an_open_license_until_permission(self):
        import json, tempfile, os
        from paperlamp import pipeline
        with tempfile.TemporaryDirectory() as d:
            old = pipeline.JOBS
            pipeline.JOBS = pathlib.Path(d)
            try:
                job = pipeline.Job.create(b"%PDF-1.4", "p.pdf", {})
                job.write("doc.json", dict(license="", arxiv=""))
                self.assertTrue(job.stop_for_license())
                self.assertIn("license", job.state["error"])
                job.state["settings"]["permission"] = "Email from the first author, 6 October 2026"
                self.assertFalse(job.stop_for_license())
                job.state["settings"]["permission"] = ""
                job.write("doc.json", dict(license="CC BY 4.0", arxiv=""))
                self.assertFalse(job.stop_for_license())
            finally:
                pipeline.JOBS = old


class KeySections(unittest.TestCase):
    note = staticmethod(lambda *a, **k: AutoLength.note(*a, **k))

    def notes(self):
        n = self.note
        return [n("0 Front matter", nums=2, claims=3), n("1 Introduction", nums=4, claims=8),
                n("2 Related Work", claims=9), n("3.1 Task Definition", claims=4, parent="Our Benchmark"),
                n("3.2 Evaluation Metrics", nums=5, claims=6, parent="Our Benchmark"),
                n("4 Our Agent", claims=6, method=8, figs=["Figure 2"]),
                n("5.1 Main Results", nums=16, claims=10, figs=["Table 1"], parent="Experiments"),
                n("5.2 Ablations", nums=8, claims=4, parent="Experiments"),
                n("6 Threats to Validity", claims=5, limits=4), n("7 Conclusion", claims=3),
                n("A Extra Experiments", nums=8, appendix=True)]

    def test_method_results_discussion_in_order(self):
        ch = script.outline({}, self.notes(), 8, "core")
        body = [c["title"] for c in ch if c["key"].startswith("s")]
        self.assertEqual(body, ["Method: Task Definition", "Method: Evaluation Metrics", "Method: Our Agent",
                                "Results: Main Results", "Results: Ablations", "Discussion: Threats to Validity"])
        self.assertEqual([c["key"] for c in ch[:2]] + [ch[-1]["key"]], ["paper", "hook", "takeaway"])
        self.assertNotIn("limits", [c["key"] for c in ch])     # the discussion section covers them

    def test_sized_in_detail(self):
        ch = {c["title"]: c for c in script.outline({}, self.notes(), 8, "core")}
        auto = {c["key"]: c["n"] for c in script.outline({}, self.notes(), 8, "auto")}
        self.assertGreaterEqual(ch["Results: Main Results"]["n"], auto["s5.1"])
        self.assertTrue(all(c["n"] >= 3 for t, c in ch.items() if ":" in t))


class AutoLength(unittest.TestCase):
    @staticmethod
    def note(title, nums=0, claims=0, method=0, figs=(), limits=0, parent="", appendix=False):
        return dict(title=title, parent=parent, appendix=appendix, summary="s", words=400,
                    key_numbers=[dict(value=str(100 + i), meaning="m") for i in range(nums)],
                    claims=[f"{title} claim {i}" for i in range(claims)],
                    method=[f"{title} step {i}" for i in range(method)],
                    limitations=[f"{title} limit {i}" for i in range(limits)], figures=list(figs))

    def notes(self, limits=0):
        return [self.note("0 Front matter", nums=2, claims=3),
                self.note("2 Related Work", nums=5, claims=9),
                self.note("3 Method", claims=2, method=12, figs=["Figure 2"]),
                self.note("4 Results", nums=16, claims=10, method=4, figs=["Table 1", "Fig. 3", "Figure 3"]),
                self.note("5 Short Note", claims=1),
                self.note("6 Threats to Validity", claims=2, limits=limits),
                self.note("A Extra Experiments", nums=8, claims=4, appendix=True)]

    def test_sections_sized_by_content(self):
        ch = {c["key"]: c for c in script.outline({}, self.notes(), 8, "auto")}
        self.assertNotIn("s2", ch)                            # related work left out
        self.assertNotIn("sA", ch)                            # so is the appendix
        self.assertEqual(ch["s6"]["title"], "Discussion: Threats to Validity")   # discussion is explained
        self.assertNotIn("limits", ch)                        # ...so no separate limitations chapter
        self.assertEqual(ch["abstract"]["title"], "Abstract")
        self.assertEqual(ch["s3"]["title"], "Method")
        self.assertEqual(ch["s4"]["title"], "Results")
        self.assertGreater(ch["s4"]["n"], ch["s3"]["n"])
        self.assertGreater(ch["s3"]["n"], ch["s5"]["n"])
        self.assertEqual(ch["s5"]["n"], 2)                    # the floor for a thin section
        self.assertEqual(script.content_sentences(self.notes()[3:4]),
                         round(1.5 * (16 + 14 / 2) ** 0.5) + 2)  # Fig. 3 and Figure 3 are one figure

    def test_limits_sized_by_what_authors_report(self):
        # no discussion section: the limitations reported elsewhere get their own chapter
        notes = [n for n in self.notes() if not n["title"].startswith("6 ")]
        notes[3]["limitations"] = [f"limit {i}" for i in range(4)]
        ch = {c["key"]: c for c in script.outline({}, notes, 8, "auto")}
        self.assertEqual(ch["limits"]["n"], 3)

    def test_never_trimmed_to_a_target(self):
        counter = iter(range(10 ** 6))
        fresh = lambda: " ".join(f"w{next(counter)}" for _ in range(20))
        orig = script.llm.chat
        script.llm.chat = lambda *a, **k: {"sentences": [fresh() for _ in range(30)]}
        try:
            auto = script.write({}, self.notes(), 1, "m", lambda *a: None, depth="auto")
            summary = script.write({}, self.notes(), 1, "m", lambda *a: None, depth="summary")
        finally:
            script.llm.chat = orig
        planned = sum(c["n"] for c in script.outline({}, self.notes(), 1, "auto"))
        self.assertEqual(auto["trimmed"], 0)
        self.assertEqual(sum(len(c["sentences"]) for c in auto["chapters"] if c["key"] != "outro"), planned)
        self.assertGreater(summary["trimmed"], 0)

    def test_best_installed_model_by_benchmark(self):
        from paperlamp import llm
        self.assertEqual(llm.best_model(["qwen3:14b", "ornith:9b-agent", "ornith:9b"]), "ornith:9b")
        self.assertEqual(llm.best_model(["qwen3:14b", "gemma4:E4B"]), "gemma4:E4B")
        self.assertEqual(llm.best_model(["llama3:8b", "mistral:7b"], fallback="mistral:7b"), "mistral:7b")
        self.assertEqual(llm.best_model(["llama3:8b"]), "llama3:8b")
        self.assertEqual(llm.best_model([]), llm.DEFAULT_MODEL)


class Tidy(unittest.TestCase):
    def test_figure_named_once_per_chapter_and_authors_now_and_then(self):
        texts = ["Table 1 shows that the mean task edits 32.8 lines.",
                 "Table 1 shows that the maximum is 5,888 lines.",
                 "In Table 1, the median is 51 tests.",
                 "Figure 4 shows that resolution rates differ by repository.",
                 "The authors argue that models struggle to localize code.",
                 "The authors note that 32% of instances contain images.",
                 "The authors designed SWE-bench Lite for faster evaluation."]
        ch = [dict(key="s4", title="Results", sentences=[dict(text=t) for t in texts]),
              dict(key="s5", title="More", sentences=[dict(text="Table 1 shows that the average issue has 195 words.")])]
        self.assertEqual(script.tidy(ch), 3)
        got = [s["text"] for s in ch[0]["sentences"]]
        self.assertEqual(got[0], texts[0])                     # the first naming stays
        self.assertEqual(got[1], "The maximum is 5,888 lines.")
        self.assertEqual(got[2], "The median is 51 tests.")
        self.assertEqual(ch[0]["sentences"][1]["figure"], "Table 1")   # the camera still shows Table 1
        self.assertEqual(got[3], texts[3])                     # a different figure is named
        self.assertEqual(got[4], texts[4])                     # one attribution is kept...
        self.assertEqual(got[5], "32% of instances contain images.")   # ...the next one right after is not
        self.assertEqual(got[6], texts[6])                     # actions ("designed") are not opinions
        self.assertEqual(ch[1]["sentences"][0]["text"], "Table 1 shows that the average issue has 195 words.")

    def test_every_author_is_read(self):
        seven = [f"Name{i} Last{i}" for i in range(7)]
        self.assertEqual([s["text"] for s in script.author_sentences(seven)],
                         ["It was written by " + ", ".join(seven[:6]) + " and Name6 Last6."])
        many = script.author_sentences([f"N{i} L{i}" for i in range(14)])
        self.assertEqual(many[0]["text"], "It has 14 authors.")
        self.assertEqual(sum(len(s["authors"]) for s in many), 14)  # nobody left out
        self.assertTrue(all(len(s["authors"]) <= 6 for s in many))
        self.assertTrue(many[-1]["text"].startswith("And finally, "))

    def test_model_author_list_replaced_by_the_full_one(self):
        authors = ["Carlos E. Jimenez", "John Yang", "Alexander Wettig", "Shunyu Yao", "Kexin Pei",
                   "Ofir Press", "Karthik Narasimhan"]
        replies = iter([{"sentences": ["The paper is titled SWE-bench and appeared at ICLR 2024.",
                                       "It was written by Carlos E. Jimenez, John Yang and Ofir Press.",
                                       "It asks whether models can fix real GitHub issues."]}])
        orig, real_outline = script.llm.chat, script.outline
        script.llm.chat = lambda *a, **k: next(replies)
        script.outline = lambda *a, **k: [dict(key="paper", title="The paper", goal="g", notes=[], n=3)]
        try:
            sc = script.write(dict(title="SWE-bench", authors=authors), [], 8, "m", lambda *a: None)
        finally:
            script.llm.chat, script.outline = orig, real_outline
        texts = [s["text"] for s in sc["chapters"][0]["sentences"]]
        self.assertEqual(texts[0], "The paper is titled SWE-bench and appeared at ICLR 2024.")
        self.assertEqual(texts[1], "It was written by " + ", ".join(authors[:6]) + " and Karthik Narasimhan.")
        self.assertEqual(texts[2], "It asks whether models can fix real GitHub issues.")
        self.assertEqual(len(texts), 3)                        # the model's partial list is gone

    def test_venue_year_not_doubled(self):
        self.assertEqual(assemble.venue_year(dict(venue="ICLR 2024", year=2024)), "ICLR 2024")
        self.assertEqual(assemble.venue_year(dict(venue="TOSEM", year="2026"), " · "), "TOSEM · 2026")


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

    def test_sentence_cut_by_the_search_window_is_completed(self):
        from paperlamp import align
        idx = [self.line(2, 100, "Earlier work stops here. The sentence we want"),
               self.line(2, 112, "starts above the matched line and keeps going for"),
               self.line(2, 124, "a while until it ends. Another sentence follows.")]
        got = align._complete(idx, [(1, w) for w in idx[1]["words"]], 8)
        self.assertEqual(" ".join(w[4] for _, w in got),
                         "The sentence we want starts above the matched line and keeps going for a while until it ends.")

    def test_whole_caption_is_highlighted_not_its_neighbour(self):
        from paperlamp import align
        def two_columns(y, body, cap):                    # body text at x 100-290, caption at x 300+
            ws, x = [], 100.0
            for w in body.split():
                ws.append([x, y, x + 4 * len(w), y + 9, w]); x += 4 * len(w) + 3
            x = 300.0
            for w in cap.split():
                ws.append([x, y, x + 4 * len(w), y + 9, w]); x += 4 * len(w) + 3
            return dict(page=1, box=[100, y, ws[-1][2], y + 9], text=body + " " + cap, words=ws)
        idx = [two_columns(100, "body text on the left", "Table 1: Average numbers for"),
               two_columns(111, "more body text here", "each task instance."),
               two_columns(140, "a new paragraph starts", "unrelated words")]
        f = dict(label="Table 1", page=1, box=[300, 160, 480, 260], caption_box=[300, 100, 410, 109],
                 caption="Table 1: Average numbers for each task instance.")
        boxes = align.caption_span(idx, f)
        self.assertEqual(len(boxes), 2)
        self.assertTrue(all(b[0] >= 300 for b in boxes))     # the body column is never marked

    def test_weak_match_is_not_highlighted(self):
        from paperlamp import align
        filler = [self.line(1, 100 + 11 * k, f"model data results method approach number {k}") for k in range(40)]
        idx = filler + [self.line(2, 100, "Our pipeline handles quirky inputs gracefully in practice."),
                        self.line(2, 112, "The pipeline resolves 1.96% of quirky issues overall.")]
        sc = dict(chapters=[dict(key="s1", title="Results", sentences=[
            dict(text="The pipeline treats odd inputs well."),             # two rare words only
            dict(text="It resolves 1.96% of the quirky issues.")])])      # a shared number too
        out = align.run(sc, idx, [], lambda *a: None)
        weak, strong = out["chapters"][0]["sentences"]
        self.assertEqual(weak["align"]["highlight"], [])
        self.assertEqual(strong["align"]["page"], 2)
        self.assertTrue(strong["align"]["highlight"])

    def test_author_names_found_on_the_title_page(self):
        from paperlamp import align
        idx = [self.line(1, 80, "A Benchmark Title"),
               self.line(1, 136, "Carlos E. Jimenez*1,2 John Yang*1,2 Li Wei3"),
               self.line(1, 150, "1 Princeton University"),
               self.line(1, 200, "ABSTRACT"),
               self.line(1, 220, "Linares and Yang also wrote about this in the abstract.")]
        found = align.author_boxes(idx, ["Carlos E. Jimenez", "John Yang", "Li Wei"])
        self.assertEqual(set(found), {"Carlos E. Jimenez", "John Yang", "Li Wei"})
        words = {w[4]: w for w in idx[1]["words"]}
        self.assertEqual(found["Carlos E. Jimenez"][0], words["Carlos"][0])
        self.assertEqual(found["Carlos E. Jimenez"][2], words["Jimenez*1,2"][2])
        self.assertTrue(all(b[1] < 200 for b in found.values()))   # never in the abstract

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
