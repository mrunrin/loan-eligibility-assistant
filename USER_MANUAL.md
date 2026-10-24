# User Manual — Run HDFC Loan Portal Locally

Step-by-step guide for anyone who clones this repository and wants the backend (FastAPI) and frontend
(Streamlit) running on their own machine. Commands are for **Windows PowerShell**; macOS/Linux equivalents
are noted where they differ.

> Student capstone demo — not an official HDFC Bank service. Use synthetic data only.

---

## Quick start — copy and paste

First install **Python 3.13**, **PostgreSQL**, **Ollama** and **Git** (step 1), and create the `loanbot`
database (step 3). Then run these in **PowerShell**, one block at a time.

**A. Get the code and install the libraries (once, ~10–20 min)**

```powershell
mkdir C:\projects -Force; cd C:\projects
git clone https://github.com/mrunrin/loan-eligibility-assistant.git
cd loan-eligibility-assistant
python -m venv capstone
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\capstone\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.13.0
python -m pip install -r requirements.txt -c constraints.txt
python -m pip check
```

**B. Configure (once)** — create `.env`, then open it and set `JWT_SECRET_KEY` (printed by the second line),
`GROQ_API_KEY`, and `DEV_EMAIL_VERIFY=true` if you are not setting up Gmail SMTP:

```powershell
copy .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"
notepad .env
```

**C. Prepare the database, model and demo data (once)**

```powershell
ollama pull nomic-embed-text
python -m alembic upgrade head
python scripts/seed_demo.py
```

Copy the demo passwords that `seed_demo.py` prints.

**D. Start the backend** — window 1 (first start takes 5–10 min; wait for `[4/4] Server ready`):

```powershell
cd C:\projects\loan-eligibility-assistant
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\capstone\Scripts\Activate.ps1
python -m uvicorn app.main:app --port 8000
```

**E. Start the frontend** — window 2:

```powershell
cd C:\projects\loan-eligibility-assistant
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\capstone\Scripts\Activate.ps1
python -m streamlit run frontend/streamlit_app.py
```

Open **http://localhost:8501**. Next time, only blocks D and E are needed (with PostgreSQL and Ollama running).
If anything fails, look it up in [Troubleshooting](#troubleshooting); the numbered sections below explain each step.

---

## 0. What you get from git, and what you create yourself

| Comes with the repository | You create or download locally (never committed) |
|---|---|
| All code: `app/`, `frontend/`, `migrations/`, `scripts/`, `eval/`, `docs/` | `.env` — your secrets and settings (copy of `.env.example`) |
| Knowledge base: `data/*.pdf` and `data/*.md` | `capstone/` — your Python virtual environment |
| Demo data definitions: `scripts/seed_data/*.json`, `*.csv` | `vectorstore/` — embeddings, built automatically on first backend start |
| `.env.example`, `requirements*.txt`, `alembic.ini`, `docker-compose.yml` | `uploads/*` — files users upload (only `uploads/.gitkeep` is in git) |
| `frontend/data/landing_faq.json`, `.streamlit/config.toml` | `logs/` — `audit.jsonl` request log |
| | The PostgreSQL database and its tables (created by migrations) |
| | Downloaded models: Ollama `nomic-embed-text`, the cross-encoder, NLTK data |

`.gitignore` keeps everything in the right-hand column out of git, so `.env` (passwords, API keys) is never pushed.

---

## 1. Install the prerequisites (once)

| Tool | Why | Get it |
|---|---|---|
| Python **3.13** (tested; 3.11–3.13 supported) | Backend and frontend | https://www.python.org/downloads/ — tick **"Add python.exe to PATH"** in the installer. Avoid 3.14+: some packages have no ready-made wheels for it yet |
| Microsoft Visual C++ Redistributable (Windows) | PyTorch needs it to load | https://aka.ms/vs/17/release/vc_redist.x64.exe (most PCs already have it) |
| PostgreSQL **15+** | Application database | https://www.postgresql.org/download/ **or** Docker (step 3, option B) |
| Ollama | Local embeddings for document search | https://ollama.com/download |
| Git | Clone the repository | https://git-scm.com/downloads |
| Groq API key (free) | Chat answers | https://console.groq.com/keys |
| Authenticator app | 2FA for officer/admin accounts | Google Authenticator, Microsoft Authenticator or Authy |
| Node.js 20 | *Optional* — only for the Promptfoo answer evaluation | https://nodejs.org |

Hardware: ~7 GB free RAM is enough. No GPU needed. Disk: about 3 GB for the Python environment plus
~0.5 GB for Ollama's embedding model. Internet is needed for the first setup and the first backend start.

**After installing, close and reopen PowerShell** so the new `python`, `psql`, `ollama` and `git` commands are
found. Check:

```powershell
python --version      # must print Python 3.11, 3.12 or 3.13
git --version
ollama --version
```

If `python` opens the Microsoft Store or is "not recognized", reinstall Python with "Add to PATH" ticked, or use
`py -3.13` instead of `python` in step 2.

---

## 2. Clone and create the Python environment

**Clone into a short folder path**, for example `C:\projects`. Windows limits file paths to 260 characters by
default, and PyTorch contains deeply nested files: a long project path (deep inside Documents, OneDrive or a
temp folder) makes the install fail with `OSError: [Errno 2] No such file or directory … Long Path support`.

```powershell
mkdir C:\projects
cd C:\projects
git clone https://github.com/mrunrin/loan-eligibility-assistant.git
cd loan-eligibility-assistant

python -m venv capstone
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned   # allows the activate script in this window
.\capstone\Scripts\Activate.ps1                                     # macOS/Linux: source capstone/bin/activate
```

Your prompt now starts with `(capstone)`. Install in this order — **upgrade pip, then the CPU-only PyTorch,
then everything else**. Installing PyTorch first stops pip from pulling the multi-GB GPU build:

```powershell
python -m pip install --upgrade pip
python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.13.0
python -m pip install -r requirements.txt -c constraints.txt
python -m pip check
```

`-c constraints.txt` installs the exact version of every package, including sub-dependencies, that the
project was tested with (Python 3.13). On another Python version, if pip says a pinned version is not
available, drop `-c constraints.txt` and run that line again.

This downloads about 1–2 GB and can take 10–20 minutes. `pip check` should print `No broken requirements found.`
Quick test that the key libraries load:

```powershell
python -c "import torch, fastapi, streamlit, chromadb, sentence_transformers; print('OK', torch.__version__)"
```

It should print `OK 2.13.0+cpu`.

Every later command in this manual assumes the virtual environment is active — your prompt starts with
`(capstone)`. In every new PowerShell window, `cd` into the project folder and run
`.\capstone\Scripts\Activate.ps1` again (with the `Set-ExecutionPolicy` line first if Windows blocks it).
Always use `python -m pip …`, never plain `pip`, so packages go into this environment.

---

## 3. Create the PostgreSQL database

The app expects a user `loanbot` with password `loanbot` and a database `loanbot` on `localhost:5432`
(you can change this later in `DATABASE_URL`).

**Option A — PostgreSQL installed on your machine.** During installation, remember the password you set for
the `postgres` user and keep the default port **5432**. Then open **"SQL Shell (psql)"** from the Start menu
(press Enter to accept the defaults, then type the `postgres` password) and run:

```sql
CREATE ROLE loanbot LOGIN PASSWORD 'loanbot';
CREATE DATABASE loanbot OWNER loanbot;
```

**Option B — Docker, no PostgreSQL install:**

```powershell
docker run -d --name loanbot-postgres -p 5432:5432 -e POSTGRES_USER=loanbot -e POSTGRES_PASSWORD=loanbot -e POSTGRES_DB=loanbot postgres:15-alpine
```

(Next time, start it again with `docker start loanbot-postgres`.)

You do **not** create tables by hand — the migrations in step 6 do that.

---

## 4. Configure `.env`

```powershell
copy .env.example .env          # macOS/Linux: cp .env.example .env
```

Open `.env` and set:

| Setting | Required? | What to put |
|---|---|---|
| `JWT_SECRET_KEY` | **Yes** — the backend refuses to start without it | Run `python -c "import secrets; print(secrets.token_urlsafe(64))"` and paste the output |
| `GROQ_API_KEY` | **Yes** for chat answers | Your key from console.groq.com |
| `DATABASE_URL` | Only if you changed step 3 | Default `postgresql+asyncpg://loanbot:loanbot@localhost:5432/loanbot` |
| `DEV_EMAIL_VERIFY` | Recommended `true` if you skip SMTP | Lets new accounts verify their email with one click |
| `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Optional | Gmail address + a 16-character App Password (steps are in `.env.example`). With these set, real emails are sent: verification codes, lockout notices, status updates |
| `DATA_ENCRYPTION_KEY` | Optional, recommended | Any long random string; encrypts 2FA secrets |
| `LANGFUSE_*` | Optional | Langfuse Cloud keys for tracing |
| `SEED_PASSWORD_ADMIN` / `_OFFICER` / `_BORROWER` | Optional | Fixed demo passwords; leave empty to have them generated and printed once |

Every other value in `.env.example` already equals the code default.

---

## 5. Start Ollama and download the embedding model

Ollama usually starts by itself after install (check the system tray). If not:

```powershell
ollama serve            # leave this window open
```

In another window (once only):

```powershell
ollama pull nomic-embed-text
```

---

## 6. Create the database tables

```powershell
python -m alembic upgrade head
```

The last line should end with `012` (the newest migration). The backend also runs this automatically on
every start, so later you can skip it.

---

## 7. Load demo data (optional, recommended)

```powershell
python scripts/seed_demo.py
```

This creates demo borrowers, an officer and an admin (`scripts/seed_data/users.json`), sample applications
and small stub document files. **Copy the passwords it prints** — they are shown only once (unless you set
`SEED_PASSWORD_*` in `.env`). Running it again is safe; existing rows are skipped.

---

## 8. Start the backend

```powershell
python -m uvicorn app.main:app --port 8000
```

Wait for:

```
[1/4] DB migrations applied ✓
[2/4] Reranker model loaded ✓
[3/4] Vectorstore ready ✓
[4/4] Server ready — accepting requests ✓
```

**The first start is slow (about 5–10 minutes):** it downloads the reranker model (~90 MB) and the NLTK
sentence data, then embeds every document in `data/` through Ollama. Step 3 prints nothing while it works —
that is normal. Later starts reuse everything and take seconds.

Check it: open http://localhost:8000/health — you should see `"status":"ok"`. API docs: http://localhost:8000/docs.

---

## 9. Start the frontend

In a **second** PowerShell window (activate the environment again first):

```powershell
.\capstone\Scripts\Activate.ps1
python -m streamlit run frontend/streamlit_app.py
```

Open **http://localhost:8501**.

---

## 10. Try it

| As | What to do |
|---|---|
| **New borrower** | Register → verify email (code by email, or the dev-mode button) → log in → Apply (pick a product, fill 4 steps, upload the required documents) → see the pre-qualification result → ask the AI Loan Assistant "why was my application rejected?" or "EMI for 5 lakh at 12% for 3 years" |
| **Seeded borrower** | Log in with a borrower from the seed output; their applications are already there |
| **Officer** | Log in → scan the QR code with your authenticator app (first login only) → Officer Queue → Start review → approve or reject with a reason |
| **Admin** | Same 2FA enrollment → Admin Dashboard: statistics, users, roles, activation |

Good to know:
- The browser logs you out after 5 minutes without activity.
- 5 wrong passwords lock the account for 15 minutes (longer if it happens again).
- Borrowers have a daily AI limit of 10,000 tokens; officers and admins are unlimited.

---

## Every day after the first setup

```powershell
.\capstone\Scripts\Activate.ps1
python -m uvicorn app.main:app --port 8000          # window 1
python -m streamlit run frontend/streamlit_app.py   # window 2 (activate first)
```

Make sure PostgreSQL and Ollama are running. After `git pull`, run `python -m pip install -r requirements.txt -c constraints.txt`
if requirements changed; migrations apply themselves on backend start.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `JWT_SECRET_KEY must be set…` at startup | Set it in `.env` (step 4) |
| `Ollama is not running at http://localhost:11434` | Start Ollama (step 5) and run `ollama pull nomic-embed-text` |
| `password authentication failed for user "loanbot"` / connection refused | PostgreSQL is not running or step 3 was skipped; check `DATABASE_URL` |
| Startup stuck at `[3/4]` | First-time embedding; wait. You can confirm progress: Ollama's `nomic-embed-text` is busy and `vectorstore/chroma.sqlite3` keeps growing |
| `/health` returns 503 | Read `database_ready` / `vectorstore_ready` in the response |
| Chat says "could not search the loan policy documents" | Ollama stopped after startup; start it again |
| Chat says the AI service is unavailable | `GROQ_API_KEY` missing or Groq is rate-limiting; wait a minute |
| Cannot log in after registering | Verify the email first, or set `DEV_EMAIL_VERIFY=true` (with SMTP empty) and restart the backend |
| "Too many failed sign-in attempts" | Wait 15 minutes; failed logins are counted per account and per network |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | Run the `Set-ExecutionPolicy -Scope Process …` line from step 2 in that window |
| `OSError: [Errno 2] No such file or directory … Windows Long Path support` during `pip install` | The project path is too long. Move/clone the project to a short path such as `C:\projects\loan-eligibility-assistant`, delete the `capstone` folder and repeat step 2. (Alternative: enable long paths — admin PowerShell: `New-ItemProperty -Path HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force`, then reboot) |
| `ModuleNotFoundError` when starting the backend or frontend | The virtual environment is not active in that window — run `.\capstone\Scripts\Activate.ps1`; check the prompt starts with `(capstone)` |
| `pip` downloads a huge `torch` (2 GB+) with CUDA | PyTorch was not installed first; run `python -m pip uninstall torch`, then the CPU command from step 2 |
| `OSError: [WinError 126] … torch … .dll` | Install the Microsoft Visual C++ Redistributable (step 1), then reopen PowerShell |
| `No matching distribution found for …` during install | Your Python is too new or too old — use Python 3.13 and recreate the `capstone` folder |
| `'psql' is not recognized` | Use "SQL Shell (psql)" from the Start menu, or Option B (Docker) in step 3 |
| `ollama` is not recognized | Reopen PowerShell after installing Ollama; or start the Ollama app from the Start menu |
| Port 8000 or 8501 already in use | Stop the other process, or start the backend with `--port 8001` and, in the frontend window, run `$env:API_URL = "http://localhost:8001"` before starting Streamlit (the frontend reads `API_URL` from the environment, not from `.env`) |

---

## Running tests (optional)

```powershell
python -m pytest            # needs PostgreSQL; with SMTP set in .env, tests send real emails
python -m ruff check .
python eval/retrieval_eval.py   # retrieval quality, needs Ollama
```

---

## Docker alternative

`docker compose up --build` runs PostgreSQL, the backend and the frontend in containers (Ollama stays on the
host). See the README section "Run with Docker Compose". The Docker setup is maintained separately from this
local guide.

---

## For maintainers: pushing your changes

If you added or upgraded a package, update its pin in the `requirements*.txt` file, then refresh
`constraints.txt` from your working venv: `python -m pip freeze > constraints.txt` (keep the 3-line header
comment at the top). The Docker images install from the same file.

```powershell
git status                  # review: .env, capstone/, vectorstore/, uploads/*, logs/ must NOT appear
git add -A
git status                  # check again before committing
git commit -m "Describe the change"
git push
```

Before pushing:
- `.env` must never be committed. If it ever was, rotate every key and password inside it.
- New settings go into `.env.example` (with a safe default or empty value), never into `.env` only.
- A schema change needs a new numbered migration in `migrations/versions/`.
- Changing the chunker or embedding setup means bumping `CHUNKER_VERSION` in `app/chunking.py`; everyone's
  vector store then rebuilds automatically on their next start.
