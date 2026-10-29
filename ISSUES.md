# Issues Analysis

## Summary

| Type | Critical | High | Medium | Low | Total |
|------|:--------:|:----:|:------:|:---:|:-----:|
| Product Issues | 0 | 2 | 1 | 0 | 3 |
| Code Issues | 0 | 3 | 0 | 0 | 3 |

---

## Product Issues

### [P1] Confusing layout and flow: actions show up before the user can use them, leading to a "Migrate First" dead end

**Severity:** High

**Location:** `problems/templates/problems/index.html`

**Description:**
After fetching a problem, the page layout confuses the user about the correct order of operations. The "Migrate Test Cases to Azure" and "Copy" buttons are visible but disabled, and there's no visual explanation (like a tooltip on hover) telling the user why, so they're left guessing. Meanwhile the active "Create/Update problem in Database" button is buried further down the page, which breaks the natural top-to-bottom flow.

**Impact:**
- The user gets confused on the very first screen because the prerequisite action (saving to DB) isn't the most prominent thing on the page.
- Users don't know why the storage and copy buttons can't be clicked, which leads to frustration and trial and error.
- The UI doesn't enforce a clear mental model of the workflow (Fetch → Save to DB → Upload to Storage).

**Suggested Fix:**
1. Reorder the UI so the "Create/Update problem in Database" button sits at the top of the fetched section, right where it's immediately visible.
2. Add a tooltip (e.g. `title="Please save to database first"`) on the disabled buttons so the user understands the dependency.
3. Consider a "Stepper" UI (Step 1: Save to DB, Step 2: Migrate Tests, Step 3: Upload) to make the sequence explicit from the start.

**Priority rationale:**
This is the highest priority because it disrupts the user's main workflow. A confusing layout where the most important action is buried, and disabled actions have no explanation, creates immediate friction for any new user trying to figure out the tool.

---

### [P2] Raw HTML shows up in the UI, so the problem statement is unreadable

**Severity:** High

**Location:** `problems/templates/problems/index.html`

**Description:**
When a problem is fetched, the Legend, Input Format, Output Format and Notes sections show raw HTML tags (like `<p>`, `<ul>`, `<span>`) instead of rendering the text. The user ends up seeing the code of the page, not the actual formatted statement.

**Impact:**
- The reviewer can't read or check the statement to confirm the migration went right.
- The app looks broken and unfinished.
- Math notation (like `$$a$$`) doesn't render either.

**Suggested Fix:**
Make sure the Django template marks the statement text as safe (e.g. `{{ problem_statement|safe }}`). For formulas, add something like MathJax on the frontend so `$$...$$` actually renders.

**Priority rationale:**
This matters a lot because the whole point of a migration tool is letting the user verify the data, and if it's unreadable the tool fails at that. It's below P1 only because the user can still blindly save to the DB, while P1 stops them completely.

---

### [P3] The landing page is bare and gives no idea how to use the tool

**Severity:** Medium

**Location:** `problems/templates/problems/index.html` (initial state)

**Description:**
When the user first logs in, all they see is a title, a search bar and a "Fetch Problem" button. There are no instructions, no explanation of what the tool does, and nothing saying you need to know the Polygon ID. It also only takes an ID, so the user has to go look it up somewhere else first.

**Impact:**
- A new user (or teammate) doesn't know where to start or what the tool can do.
- ID-only search is limiting, people usually remember a problem by name, not the 6-digit Polygon ID.

**Suggested Fix:**
- Add a short subtitle or info box under the title, like: "Enter a Polygon Problem ID to fetch, review, and migrate problems and test cases to our database and cloud storage."
- Let the search input take both IDs and problem names, or add a dropdown of recent/fetched problems so it's easier to find things.

**Priority rationale:**
Medium priority. It doesn't break anything, but fixing it makes onboarding much smoother and cuts the friction for new people on the team.

---

## Code Issues

### [C1] A single `index()` view handles four unrelated migration actions in one transaction

**Severity:** High

**Location:** `problems/views.py` — `index()` (~400 lines)

**Description:**
The `index` function handles four distinct actions (fetch, migrate to DB, migrate test cases to DB, and migrate to storage), selected by POST flags. All four run inside one `with transaction.atomic():` block and share a single exception handler, which compensates for whichever branch failed using a fixed order. Because there is no seam between the actions, none can be unit-tested without triggering the others.

**Impact:**
- A failure in one action can roll back work from another, because the atomic block is shared across the whole request.
- Every new feature edits the same function, so regression risk grows with each change.
- The function cannot be tested in isolation; any test must mock Polygon, Redis, and storage together.

**Suggested Fix:**
Split the view into four separate functions, each with its own transaction and error handling. Extract the shared "load problem and test cases" logic into a helper, and move side-effecting work into a background job.

**Priority rationale:**
This is the root cause that makes C2 and C3 harder to fix safely, since any fix touches this function.

---

### [C2] Blob uploads run outside the DB transaction, and rollback deletes the entire problem's storage prefix

**Severity:** High

**Location:** `problems/views.py` (`except` handler around migration); `problems/polygon_api.py::delete_azure_blob`; `problems/storage/upstash_backend.py::empty_problem`

**Description:**
Database writes are inside `transaction.atomic()`, but blob uploads are not. On failure, the `except` block calls `delete_azure_blob(problem_id)`, which calls `storage.delete_problem(problem_id)` and removes the entire `test_cases/{db_problem_id}/` prefix, not just what this request uploaded. A failed second migration can therefore delete blobs from an earlier successful one, while the user is told that all changes were rolled back, leaving storage and the database inconsistent.

**Impact:**
- Previously working storage is silently lost when a re-migration fails.
- The database and storage can disagree about which tests exist, with no visible warning.
- The only recovery is to re-run the migration, which re-uploads whatever Polygon currently has rather than what was previously verified.

**Suggested Fix:**
Version uploads by a per-run ID (`test_cases/{db_problem_id}/{run_id}/{nn}`) and roll back only the current run's prefix. Promote a run to "current" only after the DB commit succeeds; uploads cannot be transactional, so they need explicit versioning.

**Priority rationale:**
This is the only issue of the three that can silently destroy previously working data. It ranks below C1 because C1 is the root cause, but the damage per event is higher.

---

### [C3] The Polygon API client has no timeout or retry and makes N+2 sequential HTTPS calls inline

**Severity:** High

**Location:** `problems/polygon_api.py` — `_make_request`, `get_all_test_cases`, `download_and_extract_package`

**Description:**
Every `requests.post(...)` call in `_make_request` and `download_and_extract_package` lacks a `timeout=` argument. `get_all_test_cases` makes 1 + 2N sequential calls, which is 42 in series for a 20-test problem, with no shared `requests.Session`, no retry or backoff, and no `Retry-After` handling. All of this runs in the request thread inside `transaction.atomic`, holding row locks on `Problem`.

**Impact:**
- One flaky Polygon response fails the whole migration, and the user sees only a generic "Migration failed" message.
- A slow Polygon response can hang the worker thread indefinitely while it holds DB locks.
- The user cannot retry a single failed step; the whole flow restarts from the top.

**Suggested Fix:**
Add a shared `requests.Session` with `timeout=(10, 30)` and retry with exponential backoff on 5xx and 429 responses. Parallelize test-case fetches with a small thread pool, and move the whole operation to a background job.

**Priority rationale:**
It directly causes user-visible failures, but those failures are loud rather than silent, so it ranks below C2. It can also be fixed locally without the C1 refactor.

---

## Edge Case Analysis

### Q1. Zero sample test cases

> A Polygon problem has 0 sample test cases but 15 regular test cases. What happens when you migrate this problem?

**Tested / Read:** *It will just schow true or false no other change on UI*

**Answer:**
The migration runs normally and nothing crashes. The `migrate_to_db` branch filters for `is_sample=True` and writes zero rows to `SampleTestCase`, while `migrate_test_cases_to_db` iterates over all 15 tests and writes 15 `ProblemTestCase` rows with `is_sample=False`. The resulting problem simply has no sample block.

**Why:**
The sample filter in `migrate_to_db` matches nothing, and the test-case loop treats every test as non-sample.

---

### Q2. Re-migrating after tests are removed on Polygon

> A problem is migrated with 20 test cases. Later, the problem setter removes 8 test cases on Polygon (now 12 remain). The problem is re-migrated. What happens?

**Tested / Read:** *It is getting updated in postgres and storage both like i can see on ui but in code it states db update the existing cases but old data stays as it is *

**Answer:**
The `migrate_test_cases_to_db` branch updates existing rows by index and creates rows for new indices, but it never deletes rows beyond the new count. The database ends up with 20 rows while Polygon has 12, and tests 13–20 keep their old data. In addition, `Problem.test_case_count` is only updated in the `migrate_to_db` branch, so a user who re-runs only the test-case migration is left with a stale count.

**Why:**
The loop covers only indices present in Polygon's current list, and there is no delete call for the leftover rows.

---

### Q3. Two problems with the same title

> Two different Polygon problems have the exact same title: "Two Sum." You migrate the first one successfully. Then you try to migrate the second one. What happens?

**Tested / Read:** *Polygon doesnot allows to create two problems with same name ut if somehow it exists then our code will raise integrity error*

**Answer:**
`Problem.slug` is a `SlugField(unique=True)` populated from `slugify(title)`, so the second migration tries to write `slug='two-sum'` on a different row and raises an `IntegrityError`. The code has no specific handling for this, so the error falls into the generic handler and the user sees "Migration failed and all changes have been rolled back" with no explanation. The DB rollback works because of the atomic block, and storage is untouched because the failure occurs before the storage step.

**Why:**
The unique slug constraint collides, and there is no dedicated `IntegrityError` handling to report the cause.

---

### Q4. Data discarded when saving test cases to the database

> When test cases are saved to the database via "Migrate Test Cases to DB", some data is intentionally discarded. What data is lost? Why might this cause problems?

**Tested / Read:** *In views.py in problems I noticed that for sample problems complete problem is getting saved where as for ProblemTestCase only input_data[:260] this is taken*

**Answer:**
Two kinds of data are lost. First, input and output are truncated to the first 260 characters in `ProblemTestCase` rows; the full content remains on Polygon and in storage, but the DB copy is shortened. Second, Polygon test metadata such as points, groups, per-test time and memory limits, and the manual flag is not stored at all, because the model has no columns for it. Downstream consumers that read `ProblemTestCase` (a judge or a stats tool, for example) will see shorter tests than actually exist, and the database cannot be used to regenerate a full Polygon package.

**Why:**
The `[:260]` slice truncates the content, and the `ProblemTestCase` model defines no fields for the metadata.

---

