# Playlists and in-depth videos: plan

Goal: a playlist in the Library that holds a set of papers in the order they were first published,
and a new kind of video, **In depth**, that explains every sentence of a paper together with its
equations, symbols and technical terms. These videos run for hours, so the pipeline has to make,
store and resume hours-long videos on a 16 GB Mac.

## 1. The first playlist: issue resolution and documentation, 2022 to 2026

Ordered by the date each paper first appeared (arXiv v1). Licenses checked on arXiv 2026-10-08.

| # | First posted | arXiv | Paper | License | Note |
|---|---|---|---|---|---|
| 1 | 2022-12-02 | 2212.01479 | Detecting Outdated Code Element References in Software Repository Documentation | arXiv default | Use the Empirical Software Engineering 2023 version (CC BY 4.0) |
| 2 | 2023-10-10 | 2310.06770 | SWE-bench: Can Language Models Resolve Real-World GitHub Issues? | CC BY 4.0 | ICLR 2024 |
| 3 | 2024-05-06 | 2405.15793 | SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering | CC BY 4.0 | |
| 4 | 2025-05-26 | 2505.20411 | SWE-rebench | CC BY 4.0 | |
| 5 | 2025-09-21 | 2509.16941 | SWE-Bench Pro | CC BY 4.0 | |
| 6 | 2025-09-26 | 2509.25242 | A Benchmark for Localizing Code and Non-Code Issues in Software Projects | arXiv default | Blocked until the authors give permission |
| 7 | 2026-04-01 | 2604.00594 | Agent psychometrics | CC BY 4.0 | COLM 2026, the most math (IRT, ridge regression) |
| 8 | 2026-04-21 | 2604.19400 | CASCADE | CC BY 4.0 | FSE 2026 |
| 9 | 2026-05-13 | 2605.13139 | SWE-Cycle | CC BY 4.0 | |
| 10 | 2026-08-18 | 2608.18280 | What Makes Software Issue Resolution Tasks Difficult for Agents? | CC BY 4.0 | ESEM 2026 |

## 2. Playlists in the app

- **Library: All videos | Playlists.** A playlist page lists its papers as a timeline grouped by
  year, each with its first-posted date, venue, license, and the state of its in-depth video
  (waiting, making with progress, done with length, or blocked by its license).
- **Order:** by paper date, oldest first (default), newest first, or the order the videos were made.
- **Add papers:** paste arXiv links or IDs. The app reads arXiv for the title, authors, dates,
  comment and license, downloads the PDF and the LaTeX source, and makes a job per paper with the
  playlist's kind of video. A paper that already has a job is linked, not downloaded again.
- **Make all:** queues every paper whose license allows a video, in playlist order. Jobs still run
  one at a time. A blocked paper offers the same "I have permission" step as any job.
- **Export:** a playlist folder with the videos numbered in playlist order, and a text file with
  the playlist title and description (every video, its paper and its link) for YouTube.
- Stored in `playlists.json` in the app's data folder; nothing in the job folders changes.

## 3. The In depth kind of video

Built on Whole paper (every sentence, highlighter on that sentence), with five additions:

1. **Deeper explanations.** Two to four sentences per source sentence instead of one or two: what
   it says, how it follows from what came before, and why it matters. Inline math is read in
   words and each symbol is named.
2. **Equations.** Display equations are found line by line in the PDF layout (they often sit inside
   prose blocks), matched to the exact LaTeX from the paper's arXiv source, and explained in a
   walkthrough: what the equation is for, the equation read aloud, each symbol with its meaning
   (that symbol is highlighted on the page while it is explained), then the intuition. Without a
   LaTeX source, a local vision model (gemma4:E4B) transcribes the equation from the page image.
3. **Technical terms.** A glossary pass collects the paper's terms with definitions. Each term is
   defined where it is first used, the video opens with a Key terms chapter, and the glossary is
   saved as glossary.md and summarised in the description.
4. **Background.** A short chapter on the concepts the paper assumes (for example item response
   theory or ridge regression), with no numbers that are not in the paper.
5. **Structure for long viewing.** Each main section opens with what it will do and closes with a
   short recap. Figures and tables get a walkthrough from the text inside them. YouTube chapters
   mark every section.

Every number is still checked against the paper; explanations that change a number fall back to
the paper's own sentence. Every model answer is cached in the job folder, so a job stopped after
hours of writing resumes where it stopped.

## 4. Making hours-long videos

| Problem at hours scale | Change |
|---|---|
| The narration track was built in memory (about 2 GB for 3 hours) | Write it to disk as it is assembled |
| Chapter times read "75:30" | H:MM:SS once a video passes an hour (YouTube needs this) |
| A quit during rendering lost every rendered clip | Clips are kept and reused when the job resumes |
| About 48 MB per minute of video (9 GB for 3 hours) | Long-form profile: no slow camera drift (still frames compress to almost nothing and are drawn once), CRF 23 |
| narration.wav of 2 GB exported next to the video | Long videos export narration.m4a instead |
| Disk is 97% full (29 GB free) | Check free space before voice and render; pause with a clear message instead of failing mid-way |
| Description over YouTube's 5,000 characters | Fall back to top-level chapters |

## 5. Time and disk budget (measured on this Mac, 2026-10-08)

Measured on the Agent psychometrics test (In depth, body only): 184 sentences of the paper and 4
equations became 332 narrated sentences, 14,032 words, about 94 minutes of video; the script took
about 30 minutes with ornith:9b. That is about 65 words per sentence of the paper, 250 per
equation, plus about 1,200 for the opening chapters. The cloned voice records at about 4 minutes
per minute of narration (24 minutes took 1 h 37 min); the macOS voice is about 7 times faster
than real time.

| First posted | arXiv | Sentences | Equations | Video | Cloned-voice recording |
|---|---|---|---|---|---|
| 2022-12-02 | 2212.01479 | 299 | 0 | 2.3 h | 9 h |
| 2023-10-10 | 2310.06770 | 255 | 2 | 2.0 h | 8 h |
| 2024-05-06 | 2405.15793 | 266 | 0 | 2.1 h | 8 h |
| 2025-05-26 | 2505.20411 | 193 | 0 | 1.5 h | 6 h |
| 2025-09-21 | 2509.16941 | 254 | 0 | 2.0 h | 8 h |
| 2025-09-26 | 2509.25242 | 240 | 1 | 1.9 h | 8 h |
| 2026-04-01 | 2604.00594 | 190 | 4 | 1.6 h | 6 h |
| 2026-04-21 | 2604.19400 | 498 | 4 | 3.8 h | 15 h |
| 2026-05-13 | 2605.13139 | 235 | 0 | 1.8 h | 7 h |
| 2026-08-18 | 2608.18280 | 275 | 1 | 2.1 h | 9 h |

Total: about 21 hours of video (19 without the paper that needs permission), so about
85 hours of cloned-voice recording (3 to 4 days nonstop) or about 3 hours with the macOS voice, plus
about 40 minutes of script writing and 30 to 60 minutes of rendering per paper. Disk: about 1 to 2 GB
per hour of video with the long-form profile, plus about 0.6 GB per hour of cached voice clips (WAV, kept so a video can be re-rendered without recording again).

## 6. Order of work

1. Playlist store, arXiv import, API and Library UI.
2. In depth: line-level equation detection, LaTeX source and matching, glossary, background,
   section intros and recaps, figure walkthroughs, equation symbol highlighting, caching.
3. Hours-scale fixes from section 4.
4. Unit tests; a test script and a short test render on a math-heavy paper (Agent psychometrics).
5. Rebuild the app and restart it once the running job has finished (with your go-ahead), then
   create the playlist and start Make all.
