"""Unit tests for playlists, in-depth videos and hours-long assembly.

    python3 -m unittest discover -s tests
"""
import json, pathlib, shutil, sys, tempfile, unittest, unittest.mock, wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from paperlamp import arxiv, assemble, deep, pdf, pipeline, playlist, reader, texsrc  # noqa: E402

TEX = r"""
\documentclass{article}
\newcommand{\R}{\mathbb{R}}
\newcommand{\abil}[1]{\theta_{#1}}
\begin{document}
IRT is a model. The chance is
\begin{equation}\label{eq:irt}
P(y_{ij} = 1 \mid \abil{i}, \beta_j) = \sigma(\abil{i} - \beta_j).
\end{equation}
We fit $\beta_j$ from features $f_j \in \R^d$ with ridge regression and pick $\lambda$ by cross-validation.
\begin{align}
a &= b + c \\
d &= e \nonumber \\
f &= g
\end{align}
An unnumbered one: \[ x^2 + y^2 = z^2 \]
\begin{equation*} E = mc^2 \end{equation*}
\appendix
\begin{equation} L = \sum_{i} y_i \log p_i \end{equation}
\begin{thebibliography}{9}\bibitem{a} $$ not = an equation $$ \end{thebibliography}
\end{document}
"""


class ArxivIds(unittest.TestCase):
    def test_links_and_ids(self):
        text = """https://arxiv.org/abs/2608.18280
        see arxiv.org/pdf/2604.00594v2 and 2310.06770v3, again 2310.06770
        old style cs/0601001; not a number 12345.678"""
        self.assertEqual(arxiv.ids(text), ["2608.18280", "2604.00594", "2310.06770", "cs/0601001"])

    def test_venue_comes_only_from_what_arxiv_says(self):
        self.assertEqual(arxiv.venue_hint("To appear in ESEM 2026"), "ESEM 2026")
        self.assertEqual(arxiv.venue_hint("23 pages. To appear in the proceedings of FSE 2026. Code at x"), "FSE 2026")
        self.assertEqual(arxiv.venue_hint("Data at https://x. ICLR 2024, https://openreview.net"), "ICLR 2024")
        self.assertEqual(arxiv.venue_hint(""), "")
        self.assertEqual(arxiv.venue_hint("Code, data, and demo available"), "")
        self.assertEqual(arxiv.venue_hint("", "Empirical Software Engineering 29, 5 (2024)"),
                         "Empirical Software Engineering 29, 5 (2024)")


class LatexSource(unittest.TestCase):
    def setUp(self):
        self.eqs = texsrc.equations(TEX)

    def test_every_display_equation_in_order(self):
        self.assertEqual([e["env"] for e in self.eqs], ["equation", "align", "display", "equation*", "equation"])

    def test_numbers_follow_latex(self):
        # align: three rows, one \nonumber; starred and \[ \] are unnumbered; the appendix continues
        self.assertEqual([e["numbers"] for e in self.eqs], [[1], [2, 3], [], [], [4]])
        self.assertEqual(self.eqs[0]["label"], "eq:irt")
        self.assertTrue(self.eqs[-1]["appendix"])

    def test_macros_are_expanded_and_labels_dropped(self):
        self.assertEqual(self.eqs[0]["latex"], r"P(y_{ij} = 1 \mid \theta_{i}, \beta_j) = \sigma(\theta_{i} - \beta_j)")

    def test_bibliography_is_not_an_equation(self):
        self.assertFalse(any("not = an" in e["latex"] for e in self.eqs))

    def test_pdf_text_matches_its_latex(self):
        pdf_text = "P ( y ij = 1 | θ i , β j ) = σ ( θ i − β j ) ,"
        self.assertGreater(texsrc.similarity(pdf_text, self.eqs[0]["latex"]), 0.8)
        self.assertLess(texsrc.similarity(pdf_text, self.eqs[4]["latex"]), 0.5)

    def test_sentence_with_inline_math(self):
        sents = texsrc.math_sentences(TEX)
        hit = texsrc.best_sentence("We fit β j from features f j ∈ R d with ridge regression and pick λ by cross-validation.", sents)
        self.assertIsNotNone(hit)
        self.assertIn(r"\lambda", hit["latex"])
        self.assertIn(r"\mathbb{R}", hit["latex"])                       # \R expanded
        self.assertIsNone(texsrc.best_sentence("Completely different words about pull requests merged.", sents))


def page(*blocks, w=612, h=792):
    """A layout page from blocks given as lists of (x0, y0, x1, y1, text) lines."""
    out = []
    for lines in blocks:
        lns = []
        for x0, y0, x1, y1, text in lines:
            ws, x = [], x0
            for t in text.split():
                ws.append([x, y0, x + 6 * len(t), y1, t]); x += 6 * len(t) + 4
            lns.append(dict(box=[x0, y0, x1, y1], words=ws))
        out.append(dict(box=[min(l["box"][0] for l in lns), min(l["box"][1] for l in lns),
                             max(l["box"][2] for l in lns), max(l["box"][3] for l in lns)], lines=lns))
    return dict(page=1, w=w, h=h, blocks=out)


PROSE = "we fit a linear model to predict the difficulty of each task from its feature vector here"


class SplitMath(unittest.TestCase):
    def test_equation_inside_a_paragraph_is_cut_out(self):
        lines = [(108, 100 + 11 * k, 504, 110 + 11 * k, PROSE) for k in range(5)]
        lines += [(209, 160, 403, 172, "ŵ, b̂ = arg min ∑ β j − w ⊤ f j − b + λ ∥ w ∥ 22 ."), (493, 162, 505, 171, "(2)"),
                  (108, 180, 504, 190, PROSE)]
        pg = reader.split_math([page(lines, [(258, 173, 269, 179, "w,b")])])[0]
        kinds = [("eq" if b.get("eq") else "text", len(b["lines"])) for b in pg["blocks"]]
        self.assertEqual(kinds, [("text", 5), ("eq", 1), ("text", 1)])
        eq = pg["blocks"][1]
        self.assertEqual(eq["nums"], ["(2)"])
        self.assertGreaterEqual(eq["box"][3], 179)                       # the subscript block joined it

    def test_number_in_its_own_block_joins_the_equation(self):
        prose = [(108, 80 + 11 * k, 504, 90 + 11 * k, PROSE) for k in range(4)]
        pg = reader.split_math([page(prose, [(240, 130, 373, 141, "P ( y ij = 1 | θ i , β j ) = σ ( θ i − β j ) ,")],
                                     [(493, 130, 505, 140, "(1)")], prose)])[0]
        eqs = [b for b in pg["blocks"] if b.get("eq")]
        self.assertEqual(len(eqs), 1)
        self.assertEqual(eqs[0]["nums"], ["(1)"])
        self.assertEqual(len(pg["blocks"]), 3)

    def test_code_and_pseudo_code_are_not_equations(self):
        for t in ("diop_type =", "if all ( coeff [ k ] == 1 ) diop_type =", "T ← GenerateTests(doc)",
                  "5 for 𝑖 ← 1 to 3 do", "foreach 𝑡 ∈ T do", "of SWE-B ENCH P RO (N=276). Commercial prob-"):
            self.assertFalse(reader._mathish(t), t)
        for t in ("P ( y ij = 1 | θ i , β j ) = σ ( θ i − β j ) ,", "max j∈prompt |ϕ j (x)|",
                  "L Bern ( θ, β ) = ∑ y ij log σ ( θ i − β j )"):
            self.assertTrue(reader._mathish(t), t)

    def test_prose_with_inline_math_and_urls_are_not_equations(self):
        prose = [(108, 80 + 11 * k, 504, 90 + 11 * k, PROSE) for k in range(4)]
        inline = [(108, 140, 504, 150, "feature vector f j ∗ as β̂ j ∗ = ŵ ⊤ f j ∗ + b̂, and use the predicted difficulty together")]
        url = [(118, 160, 353, 170, "URL https://openreview.net/forum?id=6s5uXNWGIh.")]
        pg = reader.split_math([page(prose, inline, url)])[0]
        self.assertFalse(any(b.get("eq") for b in pg["blocks"]))


class Footnotes(unittest.TestCase):
    def test_author_footnotes_are_not_the_paper(self):
        for t in ("∗ Equal contribution; order randomized.", "1 Correspondence to {cge7, daria_k}@mit.edu",
                  "Equal contribution. Correspondence to carlosej@princeton.edu.",
                  "Authors’ Contact Information: Tobias Kiecker, Humboldt-Universität zu Berlin"):
            self.assertTrue(reader.FOOTNOTE.search(t), t)
        for t in ("Building a good benchmark is difficult since tasks must be challenging.",
                  "We contribute three findings about agents."):
            self.assertFalse(reader.FOOTNOTE.search(t), t)


class InDepth(unittest.TestCase):
    def test_equations_pair_in_order(self):
        pdf_eqs = [dict(text="P ( y ij = 1 | θ i , β j ) = σ ( θ i − β j ) ,", nums=["(1)"]),
                   dict(text="a = b + c f = g", nums=["(2)", "(3)"]),
                   dict(text="L = ∑ y i log p i", nums=["(4)"])]
        tex = texsrc.equations(TEX)
        self.assertEqual(deep.match_latex(pdf_eqs, tex), {0: 0, 1: 1, 2: 4})

    def test_no_match_below_the_threshold(self):
        self.assertEqual(deep.match_latex([dict(text="completely unrelated words", nums=[])], texsrc.equations(TEX)), {})

    def test_symbol_found_on_the_page(self):
        words = [[240, 106, 246, 117, "P"], [288, 108, 292, 116, "θ"], [300, 108, 305, 116, "β"], [327, 108, 332, 116, "σ"],
                 [337, 108, 341, 116, "θ"], [350, 108, 356, 116, "β̂"], [360, 108, 366, 116, "ŵ"]]
        self.assertEqual(len(deep.symbol_boxes(words, r"\theta_i")), 2)
        self.assertEqual(len(deep.symbol_boxes(words, r"\hat\beta_{j^\ast}")), 2)    # the accent is ignored
        self.assertEqual(len(deep.symbol_boxes(words, r"\hat{w}")), 1)
        self.assertEqual(deep.symbol_boxes(words, r"\lambda"), [])

    def test_terms_are_found_as_written(self):
        self.assertTrue(deep.term_re("IRT").search("standard IRT parameters"))
        self.assertFalse(deep.term_re("IRT").search("a dirty test"))
        self.assertTrue(deep.term_re("ridge regression").search("fit a Ridge-Regression model"))
        self.assertTrue(deep.term_re("scaffold").search("two scaffolds"))

    def test_math_is_said_in_words(self):
        self.assertEqual(deep.say_math(r"where $\theta_i$ is ability and beta_j difficulty"),
                         "where theta i is ability and beta j difficulty")
        self.assertTrue(deep.spoken_ok(deep.say_math("the norm of w_{2} squared")))
        self.assertFalse(deep.spoken_ok(r"\frac{a}{b} is a fraction"))

    def test_numbers_must_be_in_the_paper(self):
        known = {"2294", "12"}
        self.assertTrue(deep.numbers_ok("It has 2,294 tasks from 12 repositories.", known))
        self.assertFalse(deep.numbers_ok("About 2,300 tasks.", known))
        self.assertTrue(deep.numbers_ok("One minus 1 over 2.", known, extra=["1", "2"]))

    def test_cache_keeps_answers(self):
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.object(deep.llm, "chat", return_value={"a": 1}) as chat:
            c = deep.Cache(pathlib.Path(tmp) / "c.json")
            self.assertEqual(c.ask("x", "m", "prompt"), {"a": 1})
            self.assertEqual(deep.Cache(pathlib.Path(tmp) / "c.json").ask("x", "m", "prompt"), {"a": 1})
            self.assertEqual(chat.call_count, 1)                          # the second job asked nobody

    def test_main_sections_group_their_subsections(self):
        ch = [dict(key="abstract", num=""), dict(key="s1", num="1"), dict(key="s2", num="2"), dict(key="s2.1", num="2.1"),
              dict(key="s2.1.1", num="2.1.1"), dict(key="s3", num="3")]
        self.assertEqual([[c["key"] for c in g] for g in deep.top_groups(ch)],
                         [["abstract"], ["s1"], ["s2", "s2.1", "s2.1.1"], ["s3"]])


class LongVideos(unittest.TestCase):
    def test_chapter_times_past_an_hour(self):
        f = pipeline.clock(4000)
        self.assertEqual((f(0), f(65), f(3725)), ("0:00:00", "0:01:05", "1:02:05"))
        self.assertEqual(pipeline.clock(600)(75), "1:15")

    def test_no_chapter_shorter_than_ten_seconds(self):
        rows = [(0, "The paper", 0), (12, "Roadmap", 0), (30, "Methods", 0), (36, "Setup", 1), (90, "Results", 0)]
        out = pipeline.youtube_chapters(rows, 200)
        self.assertEqual([t for _, t, _ in out], ["The paper", "Roadmap", "Methods", "Results"])
        self.assertEqual(out[2][0], 30)
        starts = [a for a, _, _ in out] + [200]
        self.assertTrue(all(b - a >= 10 for a, b in zip(starts, starts[1:])))

    def test_narration_is_written_as_it_goes(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            for name, n in (("a", 4410), ("b", 8820)):
                with wave.open(str(d / f"{name}.wav"), "wb") as w:
                    w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
                    w.writeframes(b"\x01\x00" * 2 * n)
            rows = [dict(id="a", start=0.5), dict(id="b", start=2.0)]
            def fake_run(cmd, **kw):                        # keep the track ffmpeg would normalise
                shutil.copy(cmd[cmd.index("-i") + 1], d / "kept.wav")
                return unittest.mock.Mock(stderr='{"input_i": "-20", "input_tp": "-3", "input_lra": "1", '
                                                 '"input_thresh": "-30", "target_offset": "0"}')
            with unittest.mock.patch.object(assemble, "run", side_effect=fake_run):
                assemble.build_audio(rows, 4.0, d, d / "narration.wav")
            with wave.open(str(d / "kept.wav")) as w:
                self.assertEqual(w.getnframes(), 4 * 44100)
                frames = w.readframes(w.getnframes())
            at = lambda t: frames[int(t * 44100) * 4: int(t * 44100) * 4 + 2]
            self.assertEqual(at(0.4), b"\x00\x00")
            self.assertEqual(at(0.55), b"\x01\x00")
            self.assertEqual(at(1.5), b"\x00\x00")
            self.assertEqual(at(2.1), b"\x01\x00")

    def test_open_access_notice_at_the_end(self):
        text = "Title\n" + "words " * 6000 + ("\nThis article is licensed under a Creative Commons Attribution 4.0 "
                                               "International License ... http://creativecommons.org/licenses/by/4.0/.")
        self.assertEqual(pdf.license_hint(text), "CC BY 4.0")
        self.assertEqual(pdf.license_hint("Title\n" + "words " * 6000 + "\nWe used a CC BY dataset."), "")


class LongSentences(unittest.TestCase):
    def test_long_text_is_recorded_in_pieces(self):
        from paperlamp import tts
        text = "Table 3 lists the results. " + "Model A scores 0.75, model B scores 0.82, " * 20 + "and model C is last."
        parts = tts.pieces(text)
        self.assertTrue(all(len(x) <= tts.MAX_CHARS for x in parts))
        self.assertEqual(" ".join(parts).split(), text.split())                 # nothing lost or reordered
        self.assertEqual(tts.pieces("A short sentence."), ["A short sentence."])


class Venue(unittest.TestCase):
    def test_arxiv_comment_names_the_venue(self):
        m = pipeline.arxiv_venue(dict(venue="arXiv preprint", year="2025"), dict(comment="To appear in ESEM 2026"))
        self.assertEqual((m["venue"], m["year"]), ("ESEM 2026", "2026"))
        self.assertIn("venue set from arXiv's comment", m["checks"][-1])

    def test_the_papers_own_venue_wins(self):
        m = pipeline.arxiv_venue(dict(venue="COLM 2026", year="2026"), dict(comment="NeurIPS 2025"))
        self.assertEqual(m["venue"], "COLM 2026")
        self.assertEqual(pipeline.arxiv_venue(dict(venue="arXiv preprint"), dict(comment="23 pages"))["venue"], "arXiv preprint")


class Playlists(unittest.TestCase):
    def test_lines_with_a_pdf_of_their_own(self):
        text = ("https://arxiv.org/abs/2310.06770\n2212.01479 https://link.springer.com/content/pdf/10.1007/x.pdf\n"
                "nothing here\n2310.06770 again")
        self.assertEqual(playlist.lines_of(text), [("2310.06770", ""),
                                                   ("2212.01479", "https://link.springer.com/content/pdf/10.1007/x.pdf")])

    def test_order_by_the_date_papers_first_appeared(self):
        items = [dict(title="C", published="2026-08-18", added=1), dict(title="A", published="2022-12-02", added=2),
                 dict(title="X", published="", added=3), dict(title="B", published="2023-10-10", added=4)]
        self.assertEqual([i["title"] for i in playlist.ordered(items, "paper_date")], ["A", "B", "C", "X"])
        self.assertEqual([i["title"] for i in playlist.ordered(items, "paper_date_desc")], ["C", "B", "A", "X"])
        self.assertEqual([i["title"] for i in playlist.ordered(items, "added")], ["C", "A", "X", "B"])

    def test_store_and_view(self):
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.object(pipeline, "HOME", pathlib.Path(tmp)), \
                unittest.mock.patch.object(pipeline, "JOBS", pathlib.Path(tmp) / "jobs"):
            pl = playlist.create("Agents", "deep")
            playlist.update(pl["id"], lambda p: p["items"].extend([
                dict(arxiv="2", title="Later", published="2025-01-01", status="ready", job=None),
                dict(arxiv="1", title="Earlier", published="2023-01-01", status="ready", job="nope")]))
            v = playlist.view(pl["id"])
            self.assertEqual([i["title"] for i in v["items"]], ["Earlier", "Later"])
            self.assertEqual(v["items"][0]["status_info"]["state"], "missing")
            self.assertEqual(json.loads((pathlib.Path(tmp) / "playlists.json").read_text())["playlists"][0]["name"], "Agents")
            playlist.remove(pl["id"], "2")
            self.assertEqual(len(playlist.get(pl["id"])["items"]), 1)


if __name__ == "__main__":
    unittest.main()
