# Coding-agent prompt — complete the missing assertions

> Usage: copy everything below the line into the agent (Claude Code, Cursor,
> Codex, …) with the treatment folder as its working directory.
> The folder is agent-ready as-is (ground truth lives outside it, in
> `clones/manifests/`); just give the agent no access to any path outside the
> folder. Afterwards, score with the functional harness (assemble full project,
> deploy app, run TestSuite) or by diffing against the manifest.

---

You are completing an incomplete Java/Selenium test project. It is a Maven
project whose JUnit 4 test classes each had their assertion(s) removed and
replaced with this placeholder line:

    // TODO: Insert the missing assertion here

## Your task

For every test class under `src/test/java/` that contains the placeholder,
replace that one line with the JUnit 4 assertion statement(s) that the test
should perform at that point. Nothing else.

## What you may use

Use only what is inside this folder. Depending on the folder you may find:

- the test classes themselves (the actions before the placeholder tell you
  what the test just did);
- a `// Verify …` comment above some placeholders describing what to check;
- a `pages/` directory: `pages/<TestClass>.html` is the real page source of
  the application at the exact moment that test's assertion runs — expected
  values and element states can be read from it;
- `src/main/java/`: the project's Page Object classes and constants — when
  present, call only methods that actually exist there.

If a source of information is absent, do not invent it — write the most
reasonable assertion the available context supports.

## Hard rules

1. Edit **only** the placeholder lines in test classes. Do not modify any
   other line, and do not touch `BaseTest.java`, `TestSuite.java`,
   `Installer.java`, `pom.xml`, anything under `src/main/`, or `pages/`.
2. Do not create, delete, rename, or move any file.
3. Replace each placeholder with one or more plain JUnit 4 assertion
   statements (`assertEquals`, `assertTrue`, `assertFalse`, `assertNotNull`,
   `assertNull`) — no helper methods, no try/catch, no waits, no comments,
   no imports (the needed static imports already exist in each file).
4. Assertions must reference only variables in scope at the placeholder and
   methods that exist in this folder's code. Do not add calls that perform new
   page actions — assert on the state the test has already reached.
5. Match the expected values exactly as the application would render them
   (use `pages/` when available rather than guessing wording).
6. Complete every test class that has a placeholder — none may be skipped or
   left with the TODO in place.

When done, list each test class and the assertion(s) you inserted, and state
any file where you were uncertain and why.
