# AI Job Search & Application Agent

See [PRODUCT_BACKLOG.md](PRODUCT_BACKLOG.md) for the consolidated implementation and product roadmap.

A self-contained AI-powered assistant designed to streamline job discovery, resume tailoring, and a reliable guided-manual application process.

The application features a **FastAPI backend** that doubles as a static file server to deliver a **custom, premium dark-mode dashboard** built with clean, native web technologies (no NPM or React build pipeline required).

---

## 🌟 Key Features

1. **Intelligent Multi-Platform Discovery**:
   - Crawls major job boards: **Greenhouse**, **Lever**, **Ashby**, and **SmartRecruiters** using optimized Yahoo Search queries.
   - Runs domain-specific search queries separately to bypass search engine ranking caps, boosting relevant matches by over 300%.
   - Generalized to support Greenhouse's modern `job-boards.greenhouse.io` migration.
   - Uses a modern browser compatibility profile while rejecting job-board fallback and browser-error pages.

2. **Cost-Efficient AI Matching**:
   - Matches candidate resumes against crawled listings and computes match scores (0-100%) with key pros/cons.
   - **Batch AI Matching**: Groups discovered job listings into a single batch call to fit within strict Gemini Free Tier daily limits (20 requests/day).
   - **Keyword Caching**: Analyzes your resume to auto-suggest and store search terms, preventing redundant API calls.

3. **Combined Resume & Cover Letter Tailoring (50% Quota Savings)**:
   - Rewrites your resume highlights for specific postings and drafts custom cover letters.
   - Uses a single consolidated Gemini request returning JSON to halve quota usage, letting you process twice as many jobs.
   - Converts Markdown and plain text outputs into polished, print-ready PDF files using Playwright.

4. **Address Resolution (with Google Places API + Gemini Fallback)**:
   - Automatically queries and logs the full street address (including ZIP code/international details) of company headquarters.
   - Optionally integrates with the **Google Places API** to resolve company locations and conserve Gemini API calls.
   - Automatically falls back to the Gemini API if the Google Maps key is missing or invalid.

5. **Guided Manual Applications**:
   - Reviews and saves tailored materials before application work begins.
   - Provides explicit, recruiter-friendly Resume PDF and Cover Letter TXT downloads.
   - Opens the verified employer posting in a new tab; you complete the employer's form and then confirm the lifecycle state with **Mark Applied**.

6. **Local Security**:
   - Stored completely on your local machine. Profile details and private API keys stay in local SQLite (`data/jobapplier.db`), while generated PDF and TXT artifacts remain in the ignored local output directory.

---

## 🛠️ Architecture

* **Frontend**: Vanilla HTML5, Vanilla CSS3 (custom dark-mode grid design, glassmorphic cards, transition glows), and Vanilla ES6+ JavaScript.
* **Backend**: FastAPI (Python), SQLite (database), Playwright (scraping and browser control), and the `google-genai` SDK.

---

## 🚀 Getting Started

### Prerequisites
* Python 3.10 or higher.
* Google Chrome or Chromium (installed automatically by Playwright).
* A **Gemini API Key** (Get one free from [Google AI Studio](https://aistudio.google.com/)).
* *Optional*: A Google Maps API Key with the **Places API** enabled.

### Quick Start (Windows)
We have provided automated startup scripts in the root directory:

#### Option A: Using Windows Command Prompt (Recommended)
* Double-click `run.bat` or run it from CMD:
  ```cmd
  run.bat
  ```

#### Option B: Using Windows PowerShell
1. Open PowerShell and navigate to the project directory.
2. Execute the start script:
   ```powershell
   .\run.ps1
   ```
   If your organization blocks local PowerShell scripts, use `run.bat`. A process-only alternative that does not change the system execution policy is:
   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
   ```

The startup scripts will automatically set up a Python virtual environment, install requirements, download the browser binaries, start the FastAPI server, and open the dashboard in your default browser at:
👉 **[http://127.0.0.1:8001/](http://127.0.0.1:8001/)**

Port `8001` is the default. To choose another port, run `.\run.ps1 -Port 9000` in PowerShell or `run.bat 9000` in Command Prompt.

---

### Manual Installation (All OS)
If you prefer setting up manually or are on macOS/Linux:

1. **Set up Virtual Environment**:
   ```bash
   cd backend
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Install Playwright Browsers**:
   ```bash
   playwright install chromium
   ```
4. **Start the FastAPI Server**:
   ```bash
   uvicorn app:app --host 127.0.0.1 --port 8001 --reload
   ```
5. Open your browser and navigate to `http://127.0.0.1:8001/`.

---

## ⚙️ Configuration & Usage

1. **Set up Profile**:
   - Go to the **Profile & Resume** tab.
   - Enter your contact details, personal links, and paste your **Gemini API Key** (and optional **Google Maps API Key**).
   - Keep **Prefer a U.S. headquarters address** selected when U.S. unemployment reporting requires a domestic employer address; clear it to prefer the employer's primary global headquarters.
   - Upload your base resume as a `.txt` or `.md` file, or copy-paste it directly.
   - Click **Save Settings**.
2. **Search for Jobs**:
   - Go to the **Search & Match** tab.
   - Enter search keywords (e.g. `"Enterprise Architect"`) or leave blank to use keywords auto-suggested from your resume.
   - Enter locations (e.g. `San Francisco, CA; Remote` separated by semicolons) to search multiple markets at once.
   - Click **Search & Analyze**.
3. **Tailor Materials**:
   - Under **Discovered Job Postings**, click **Tailor Materials** next to a matched job.
   - Review your tailored resume and cover letter.
4. **Apply**:
   - Click **Apply Manually**, review and save edits, and download the **Resume PDF** and **Cover Letter TXT**.
   - Click **Open Application Site**, complete the employer's form yourself, and return to use **Mark Applied** with the correct date and method.

### Preview and clean up untouched jobs

1. Open **Search & Match** and click **Clean Up** beside **Refresh**.
2. Review the exact counts for jobs eligible to archive, permanently delete, or restore.
3. Choose **Archive Untouched** for a reversible cleanup. Tailored jobs and every job with application history are protected.
4. Use **Restore Archived** to return archived jobs to the active list, or **Delete Permanently** only after reviewing both confirmation prompts.

The server validates that the candidate set has not changed since the preview. If a search adds or changes jobs, refresh the cleanup preview before continuing.

### P1 workflow controls

- Use **Mark Applied** on a matched or tailored job to record a manually completed application with its date, method, notes, and follow-up date.
- Use **Update Status** to record interviews, offers, rejections, withdrawals, or closed roles. **Undo Last Change** restores the previous recorded state.
- Save frequently used keyword/location combinations above the search form. Use the score, status, and sort controls to focus the active list.
- Saved searches can optionally provide daily or weekly local reminders while the app is running; rerunning the saved search advances its next reminder.
- Use the circular-arrow action on a job row to recheck whether the employer posting remains available.
- Provider health diagnostics detect likely ATS URL or page-format changes and show a local warning instead of silently hiding a failing source.
- Choose a resume mode under **Profile & Resume** before tailoring. Generated resume and cover-letter text can be edited in **View Materials**; **Save Edits** regenerates both downloadable artifacts.
- Each resume mode uses a deterministic section template before PDF generation. Manual application dates accept direct `MM/DD/YYYY` or `YYYY-MM-DD` entry and also provide a calendar picker.

The app is designed for local access at `127.0.0.1`. Cross-origin browser access is intentionally disabled, and profile reads return only API-key configuration flags—not stored key values.

### Optional anonymous analytics

PostHog analytics is disabled unless `POSTHOG_PROJECT_TOKEN` is present in the process environment. `POSTHOG_HOST` is optional and defaults to the US ingestion endpoint; use the ingestion host for your PostHog region. The variable names are also listed in `backend/.env.example`, but JobApplier does not automatically read `.env` files or include any real token in the repository.

When enabled, JobApplier creates a random installation UUID in the local ignored `data/` directory. It never uses the profile database ID and does not identify a person. Only `job_search_started`, `resume_tailored`, `manual_application_opened`, `material_downloaded`, and `job_lifecycle_updated` are sent, with allowlisted low-cardinality status, source-category, material-type, lifecycle-transition, duration-bucket, and application-version properties. Names, contact details, résumé or application text, employer and job details, URLs, search terms, filenames, paths, API keys, and database content are never included.

Analytics capture is isolated from request handling and uses short network timeouts. Failures are silently dropped. GeoIP enrichment, session replay, automatic exception capture, and local-variable capture are disabled. To opt out, remove `POSTHOG_PROJECT_TOKEN` and restart JobApplier; the local installation UUID may be deleted separately if you do not plan to re-enable analytics.

### Startup troubleshooting

The Windows launchers validate the project virtual environment before opening the dashboard and repair it when a usable Python installation is available. Use `run.bat` when Windows execution policy blocks `.ps1` files. If the dashboard was already open during an unsuccessful launch, close that tab and launch again. The browser opens only after the expected backend build responds, and local UI assets are served without stale caching. If the configured port (default `8001`) is already occupied by an older copy, stop that terminal with Ctrl+C before relaunching.
