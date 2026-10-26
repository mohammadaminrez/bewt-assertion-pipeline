# DANTE toolchain setup (crawling + test-suite generation front-end)

This documents the local bring-up of [DANTE](https://github.com/matteobiagiola/ICST20-submission-material-DANTE)
(ICST 2020, "Dependency-Aware Web Test Generation"), used as the crawling and
Selenium-test-generation front-end for the LLM-assertion extension of this
pipeline. DANTE bundles a 2020 fork of Crawljax plus shell scripts that turn
crawl paths into JUnit/Selenium Maven projects.

## How the whole thing works

The extension turns the assertion pipeline into a fully automatic test-suite
generator. Five stages, each feeding the next:

```
Crawljax explores app  →  state-flow graph  →  Selenium tests (no assertions)
                                                        │
        compare vs Crawljax oracles  ←  LLM writes the assertions
```

1. **Crawl** — Crawljax opens the app in a real Chrome, acts like a user
   (clicks elements, fills forms from an `InputSpecification` — including the
   login form), and builds a *state-flow graph*: nodes are distinct DOM states,
   edges are the user actions between them. For every state it saves the full
   DOM (`doms/<state>.html`) and a screenshot. Every action is also recorded as
   replayable Java driver statements (`selenium-actions-<app>-fired.txt`, one
   crawl path per `-----` block).
2. **Generate** — DANTE's `TestSuiteGenerator` turns each recorded crawl path
   into a JUnit 4 test method (raw Selenium replays, **no assertions**) inside
   a standalone Maven project, `applications/<app>/testsuite-<app>/`.
   Separately, the Crawljax `testcasegenerator` plugin emits the **oracle
   suite** (`crawl-with-inputs/src/.../GeneratedTests.java`, TestNG): the same
   paths asserted with invariants + whole-DOM/screenshot state-equivalence.
   That suite is the comparison baseline.
3. **Ingest** (`bewt-pipeline ingest-crawl`) — `src/ingest/dante.py` splits the
   JUnit suite into one single-test class per path, inserts the pipeline's
   standard `// TODO: Insert the missing assertion here` placeholder, and
   renders a natural-language action trace from the test's own statements
   (opens / types into / clicks). Output: `output/crawled/<app>/tests/` +
   `state_map.json`. (An earlier version guessed each test's final page by
   walking the crawl graph; on Bludit it mapped all 30 tests to the start
   page, so that guess was removed.)
4. **Crawled reference run** (`bewt-pipeline reference-run --source crawled`) —
   the crawl ran its paths one after another without resetting app data, so
   later tests depend on earlier ones. The 30 replays run once, in crawl
   order, on a container installed exactly as for the crawl (DANTE's
   `run-docker.sh`); every replay must pass on its own. After each test the
   app state is saved and the page at the assertion point is captured —
   that page goes into every crawled prompt.
5. **Controls** (`bewt-pipeline verify-execution --source crawled`) — no gold
   exists, so the known-true assertion is the captured page title:
   `assertEquals(<title>, driver.getTitle())` must pass and its negation fail
   for every test, run through the same isolated path as the LLM answers.
6. **Generate assertions** (`bewt-pipeline run --source crawled --execute`) —
   for each test the LLM writes the missing assertion. There are no
   treatments: the prompt always holds the test code, the action-trace
   comment and the page captured at the assertion point. (Early runs
   without the page — test code alone, or with the trace — held on 0/30
   tests; those rows stay in the database under treatments A/B but are not
   reported. The single condition is stored as treatment `C`.)

   Each answer runs alone (DANTE's JDK 8 toolchain) on the app state saved
   just before its test. Results land in `output/results.db` with
   `source='crawled'`, kept apart from the BEWT benchmark rows.
7. **Evaluate** — execution decides `not_executable` (compile or runtime
   error) and `wrong_assertion` (assertion fails); an assertion that holds is
   judged on a meaningfulness rubric (`semantic` / `structural`) against the
   Crawljax oracle suite as baseline; comparison table in
   `reports/crawled_comparison.csv`.

## Full run: all commands in order

```bash
# ── toolchain env (once per terminal; needed for every DANTE-side command) ──
export JAVA_HOME=~/dante-tools/zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home
export PATH="$JAVA_HOME/bin:$HOME/dante-tools/apache-maven-3.6.0/bin:$PATH"
export JAVA_TOOL_OPTIONS="-Dchrome.binary=$HOME/dante-tools/chrome-mac-arm64/cft.app/Contents/MacOS/chrome -Dcrawljax.reset.clearCookies=true"

# ── 1. crawl + 2. generate (Docker must be running; clear old containers first) ──
docker ps -aq --filter name=bludit | xargs -r docker rm -f
cd ~/workspace/ICST20-submission-material-DANTE/dante
./run-crawling.sh bludit true 10                       # true=headless (false = watch it), 10 = minutes
./generate-java-project-from-crawling.sh bludit true

# optional: visualize the state-flow graph (CrawlOverview's own viewer is dead in modern browsers)
python3 ~/bewt-assertion-pipeline/scripts/make_crawl_graph.py \
    applications/bludit/localhost/crawl-with-inputs
(cd applications/bludit/localhost/crawl-with-inputs && python3 -m http.server 8765 &)
open http://localhost:8765/graph.html

# ── 3.–5. pipeline side ──
cd ~/bewt-assertion-pipeline && source .venv/bin/activate

bewt-pipeline ingest-crawl --app bludit                # split tests, placeholders, action traces
bewt-pipeline reference-run --app bludit --source crawled     # replay all in crawl order: snapshots + pages
bewt-pipeline verify-execution --app bludit --source crawled  # controls: must be 30/30
bewt-pipeline run --app bludit --source crawled \
    --model gpt-4o-mini --execute                      # LLM assertions + isolated execution
bewt-pipeline run-oracle-suite --app bludit            # Crawljax oracle baseline
bewt-pipeline export-excel --source crawled --pre-classify   # judge + Excel for manual review
bewt-pipeline import-excel --source crawled            # after annotating the Excel
bewt-pipeline report                                   # → reports/crawled_comparison.csv
```

Useful along the way: `bewt-pipeline llm-usage --by source` (token/cost split),
`bewt-pipeline show-prompt --app bludit --latest` (exact prompt sent).

Practical notes:

- `run-crawling.sh` starts the app container itself and waits a fixed 60 s
  before Chrome appears — the silence at the start is normal.
- Re-running the crawl **overwrites** `selenium-actions-*.txt` and the crawl
  output; back up `applications/<app>/` first if the previous crawl matters.
- `run --source crawled` is resumable (stored results are skipped) and LLM
  responses are disk-cached, so re-runs are cheap and safe.

## Layout

| What | Where |
|---|---|
| DANTE clone | `~/dante` (sibling of this repo, like `../BEWT`) |
| Required symlink | `~/workspace/ICST20-submission-material-DANTE -> ~/dante` (scripts hard-code this path) |
| Pinned JDK 8 | `~/dante-tools/zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home` (Zulu, arm64 — Temurin has no arm64 JDK 8) |
| Pinned Maven | `~/dante-tools/apache-maven-3.6.0` |
| Pinned Chrome | `~/dante-tools/chrome-mac-arm64/cft.app` (Chrome for Testing **114.0.5735.90**, arm64) |
| Chrome binary (space-free path) | `~/dante-tools/chrome-mac-arm64/cft.app/Contents/MacOS/chrome` |

The system JDK 17 / Maven 3.9 are untouched; the pinned toolchain is activated
per-shell:

```bash
export JAVA_HOME=~/dante-tools/zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home
export PATH="$JAVA_HOME/bin:$HOME/dante-tools/apache-maven-3.6.0/bin:$PATH"
export JAVA_TOOL_OPTIONS="-Dchrome.binary=$HOME/dante-tools/chrome-mac-arm64/cft.app/Contents/MacOS/chrome"
```

## Why Chrome must be pinned to 114

DANTE's Crawljax fork uses Selenium **3.141.5** + WebDriverManager **3.1.1**,
which resolves ChromeDriver from the legacy `chromedriver.storage.googleapis.com`
bucket. That bucket's `LATEST_RELEASE` is frozen at **114.0.5735.90** (Chrome
switched to the Chrome-for-Testing distribution at 115), so the crawler can only
drive a Chrome ≤ 114. Chrome for Testing publishes an arm64 build of exactly
114.0.5735.90; WebDriverManager then resolves the matching driver automatically
(the x86_64 chromedriver runs under Rosetta).

Two macOS quirks:

- JDK 8 splits `JAVA_TOOL_OPTIONS` on whitespace, so the Chrome path must not
  contain spaces. The app bundle is renamed to `cft.app` and a `chrome` symlink
  is created *inside* `Contents/MacOS/` (a symlink from elsewhere breaks
  `@executable_path`-relative framework loading).

## Patches applied to the DANTE clone

All patches are opt-in via system properties (unset = original behavior):

1. `crawljax/core/src/main/java/com/crawljax/browser/WebDriverBrowserBuilder.java`
   — in `newChromeBrowser`, `optionsChrome.setBinary(...)` when
   `-Dchrome.binary` is set (used by the crawler itself).
2. `dante/src/main/resources/staticfiles/DriverProvider.java` — same override in
   the static file copied into every *generated* test-suite project (used when
   generated suites are executed).
3. `crawljax/core/src/main/java/com/crawljax/core/Crawler.java` — in `reset()`,
   delete all browser cookies when `-Dcrawljax.reset.clearCookies=true`. Needed
   for apps with server-side login sessions (Bludit): without it, the session
   cookie survives resets, every reset lands on the dashboard instead of the
   login page, earlier states become unreachable, and the crawl exhausts after
   a handful of states.

Fixes committed in the clone (`4cb6a97`), found while validating the crawled
pipeline on Bludit — before them no generated test compiled or replayed,
after them the generated suite replays 30/30 on a fresh app:

4. Recorder (`WebDriverBackedEmbeddedBrowser`): frame names are written as
   quoted Java strings (`frame(jseditor_ifr)` did not compile).
5. Recorder: closing extra browser tabs is recorded as
   `BasePageObject.closeOtherWindows(driver);` (new helper in
   `staticfiles/BasePageObject.java`) instead of literal window handles,
   which are random per session and fail on replay ("no such window").
6. Crawler (`WebDriverBrowserBuilder`) and generated `DriverProvider` use the
   same `--window-size=1280,800`. Responsive pages show different elements at
   different widths (Bludit's collapsed navbar toggle); the crawl used
   Chrome's default width and the replay a malformed `1200x600`.
7. Generated suites (`templates/TestSuite*.java`) clear cookies before each
   test when `-Dcrawljax.reset.clearCookies=true`, mirroring the crawl reset.
8. `BluditConfig`: third-party scripts are excluded from JS coverage; an empty
   setting made the generated `Properties` throw in every test's setup.

## Bludit as a DANTE subject (added for this project)

Registered in the DANTE clone: `ApplicationNames.Name.BLUDIT`,
`com.dante.subjects.config.BluditConfig`, dispatch in `SetupApp`,
`docker/bludit/run-docker.sh` (runs `bludit/docker:3.13.1` and completes the
install wizard via a plain HTTP POST — password `e2eW3Bt3s71nGB3nchM4rK`), and
the `bludit` app name + fired-strategy entries in `run-crawling.sh` /
`generate-java-project-from-crawling.sh`.

`BluditConfig` essentials, learned the hard way:

- Seed URL is the admin panel (`http://localhost:8080/admin/`); an
  `InputSpecification` fills `#jsusername`/`#jspassword` before any submit
  button is clicked, so login happens as a normal crawl action.
- **CSRF stripping is mandatory**: Bludit embeds a fresh 40-hex `tokenCSRF` in
  every response (hidden input, hrefs, inline JS). Without a `RegexComparator`
  stripping it, every page load is a "new" DOM state — the first crawl attempt
  produced 328 states, 0 edges, 0 crawl paths. With stripping + cookie-clearing
  resets the crawl explores normally.
- `dontClick` rules exclude the logout link and `/install.php`.

Crawl command (after the toolchain env above):

```bash
export JAVA_TOOL_OPTIONS="$JAVA_TOOL_OPTIONS -Dcrawljax.reset.clearCookies=true"
cd ~/workspace/ICST20-submission-material-DANTE/dante
./run-crawling.sh bludit true 10
./generate-java-project-from-crawling.sh bludit true
```

## Install steps performed

```bash
git clone --depth 1 https://github.com/matteobiagiola/ICST20-submission-material-DANTE.git ~/dante
mkdir -p ~/workspace && ln -sfn ~/dante ~/workspace/ICST20-submission-material-DANTE

# Toolchain into ~/dante-tools: Zulu JDK 8 (arm64), Maven 3.6.0, Chrome for Testing 114 (arm64)
# (see Layout table; Chrome app renamed to cft.app + in-place "chrome" symlink)

# Atlassian custom library (unsupported sourcemap 1.7.7 dependency)
mkdir -p ~/.m2/repository/com && cp -R ~/dante/atlassian-custom-library/* ~/.m2/repository/com/

# Properties files
cd ~/dante/dante/src/main/resources
cp app.example.properties app.properties && cp log4j.example.properties log4j.properties

# Builds (with the pinned toolchain active)
cd ~/dante/crawljax && mvn install -DskipTests
cd ~/dante/dante && mvn compile
```

## Smoke test (ecommerce subject)

```bash
cd ~/workspace/ICST20-submission-material-DANTE/dante
./run-crawling.sh ecommerce true 5                        # crawl, headless, 5 min
./generate-java-project-from-crawling.sh ecommerce true   # generate + run test project
```

Requires Docker running (subject app image `dockercontainervm/ecommerce-example`,
x86 — runs under Rosetta). Crawler logs land in `~/Desktop/logs_CrawlApp_<app>.txt`
/ `errors_CrawlApp_<app>.txt`.

Verified output of the crawl (`dante/applications/ecommerce/`):

- `selenium-actions-ecommerce-fired.txt` — replayable Java driver statements,
  one crawl path per `-----`-separated block (input to the suite generator).
- `localhost/crawl-with-inputs/result.json` — state-flow graph: states
  (id, url, candidate elements) and edges (event, element, source/target state).
- `localhost/crawl-with-inputs/doms/stateN.html` — **per-state DOM snapshots**
  (these become the "HTML at the assertion point" context for LLM assertion
  generation).
- `localhost/crawl-with-inputs/screenshots/`, `states/` — visual oracle data.
- `localhost/crawl-with-inputs/src/test/java/generated/GeneratedTests.java` —
  the Crawljax **oracle suite** (TestNG): replays each path and asserts, after
  every event, `checkInvariants()` + DOM state-equivalence
  (`compareCurrentDomWithState(N)`) + screenshot equivalence. This is the
  baseline the LLM-generated assertions are compared against.

The DANTE-side suite (`generate-java-project-from-crawling.sh`) additionally
produces `applications/<app>/testsuite-<app>/` — a plain JUnit 4 + Selenium
Maven project whose tests replay the same paths with **no assertions at all**;
this is the suite the pipeline enriches with LLM-generated assertions.
