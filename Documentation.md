# Polygon Migration Tool: How It Works

## Tech Stack

This tool is for users which lets them pull coding problems from Polygon through problem id , check them, save them in our database, and upload the test cases to cloud storage.

What it's built with:
- **Frontend:** Django templates .
- **Backend:** Django (Python).
- **Database:** PostgreSQL.
- **Cache:** Redis. 
- **Cloud storage:** Upstash Blob (works like S3). 

---

## 2.1 User Flows

### Flow 1: Logging in

**What the user does:** Types their email and password on the login page and clicks "Login."

**What the page sends:** A `POST` request to `/users/login/` with `email` and `password`.

**What the backend does:**
1. `login_view` (`users/views.py`) gets the request.
2. If the user is already logged in, it logs them out first.
3. It calls `authenticate()`, which uses our custom `EmailBackend` (`users/backends.py`) to find the user by email and check the password if success then login else gives invalid username password.
4. If that works and the user has `is_staff=True`, it logs them in and sends them to the main dashboard (`problems:index`).

**External APIs:** None.

**Database / storage:** Reads from the `users_user` table.

**What the user sees:** The main migration dashboard. If the login is wrong, they see an error on the login page.

---

### Flow 2: Fetching a problem from Polygon

**What the user does:** Types a Polygon problem ID (for example `618508`) and clicks "Fetch Problem."

**What the page sends:** A `POST` request to `/` (the `index` view) with `problem_id`.

**What the backend does:**
1. `views.py::index` creates a `PolygonAPI` object.
2. It calls `api.download_and_extract_package(problem_id)`. This downloads the ZIP from Polygon, unpacks it, and reads `problem.html`.
3. It calls `api.get_all_test_cases(problem_id)`, which gets each test's input and output with separate API calls.
4. It saves the test cases in Redis .
5. It gets the problem data and test case previews ready for the page.

**External APIs:** Polygon API (`problem.packages`, `problem.package`, `problem.tests`, `problem.testInput`, `problem.testAnswer`).

**Database / storage:** Temporarily writes test case data to Redis. It expires after 30 minutes.

**What the user sees:** The problem statement, the reference solution, and previews of the test cases. The buttons for saving to the DB and storage show up.

---

### Flow 3: Saving a problem to the database

**What the user does:** Picks a difficulty, adds tags, and clicks "Create/Update problem in Database."

**What the page sends:** A `POST` request to `/` with `problem_id`, `difficulty`, `tags`, and the `migrate_to_db` flag.

**What the backend does:**
1. `views.py::index` checks whether a `Problem` with this `polygon_id` is already in the DB.
2. It wraps the writes in atomic transaction, so either everything is saved or nothing is.
3. It updates or creates the `Problem` record with the parsed HTML statement, limits, and other details.
4. It removes the old tags and links the new `ProblemTag` records.
5. It reads the sample tests from Redis and saves them in the `SampleTestCase` table.

**External APIs:** Polygon API (it fetches the reference solution again so it can be shown).

**Database / storage:** Writes to the `Problem`, `ProblemTag`, and `SampleTestCase` tables in PostgreSQL.

**What the user sees:** A success message: "Problem updated/created in database successfully."

---

### Flow 4: Saving test cases to the database

**What the user does:** Clicks "Migrate Test Description to Database."

**What the page sends:** A `POST` request to `/` with `problem_id` and the `migrate_test_cases_to_db` flag.

**What the backend does:**
1. `views.py` gets the test cases from Redis. If Redis has already expired them, it fetches them from Polygon again.
2. It goes through the test cases one by one and updates or creates rows in `ProblemTestCase`.
3. **Note:** To save space, the input and output are cut down to 260 characters (`input_data[:260]`) before saving.

**External APIs:** None (it uses the Redis cache).

**Database / storage:** Writes to the `ProblemTestCase` table.

**What the user sees:** A success message: "Test cases description migrated to database Successfully."

---

### Flow 5: Uploading test cases to cloud storage

**What the user does:** Clicks "Migrate Test Cases to Azure." The button still says Azure, but it now sends files to Upstash.

**What the page sends:** A `POST` request to `/` with `problem_id` and the `migrate_to_azure` flag.

**What the backend does:**
1. `views.py` first checks that the problem is already in the DB.
2. It calls `migrate_to_azure_blob()`, which sets up the `StorageBackend` (through `get_storage()`).
3. It gets the test cases from Redis.
4. It calls `empty_problem(db_problem_id)` to clear out the old files.
5. It goes through the test cases and calls `upload_test_case(id, idx, input, output)` for each one.
6. If the problem has a custom checker, it compiles it with `g++` and uploads the compiled file.

**External APIs:** Upstash Blob API (S3-compatible).

**Database / storage:** Writes `.in` files and `.a` (answer) files to Upstash Blob.

**What the user sees:** A success message: "Test cases migrated to Azure Blob Storage successfully."

---

## 2.2 External Services

### Polygon API

The app talks to Polygon using the `requests` library in `problems/polygon_api.py`.

**How requests are signed:**
Every request carries an `apiKey` and an `apiSig`. To make the signature, we take a random 6-character prefix, the API method, the sorted URL parameters, and the `apiSecret`, and hash them together with SHA-512.

**Endpoints we use:**
- `problem.packages` and `problem.package`: get the ZIP file that contains `problem.html`.
- `problem.tests`: get the list of test cases.
- `problem.testInput` and `problem.testAnswer`: get the raw text for each test.
- `problem.checker` and `problem.viewFile`: get the source code of a custom checker.

### Cloud Storage (Upstash Blob)

The app uses an S3-style interface in `problems/storage/`. The `STORAGE_PROVIDER` environment variable decides which provider is used.

**How files are named and organized:**
Every path starts with the problem's database ID, so files from different problems never clash. For a problem with database ID `42`:
- Test 1 input: `test_cases/42/01`
- Test 1 output: `test_cases/42/01.a`
- Test 2 input: `test_cases/42/02`
- Test 2 output: `test_cases/42/02.a`



