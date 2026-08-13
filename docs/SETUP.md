# Setup (one-time)

Do these in order. Each step says exactly what to click/run and which repo
file it corresponds to. Total time: 30-60 minutes, mostly Google Cloud
console clicking.

## 0. Prerequisites

- A GitHub account and the [GitHub CLI](https://cli.github.com) (`gh`) if
  you want to create the repo from the terminal instead of the website.
- [Claude Code](https://code.claude.com) installed locally
  (`curl -fsSL https://claude.ai/install.sh | bash` on macOS/Linux) — used
  for development, not required on the GitHub Actions runner (that installs
  its own copy).
- Python 3.12 locally, for running the pipeline before you trust it in CI.
  After `pip install -r requirements.txt`, also run `playwright install
  chromium` once — the renderer screenshots the cheat sheet with headless
  Chromium (see ARCHITECTURE.md "Why HTML/CSS instead of Pillow").
- A Google account you're willing to grant a service account access to
  (for Drive uploads).

## 1. Create the repository

This scaffold was generated directly in your local folder. From inside it:

```bash
cd leetcode-daily-cheatsheet
git init
git add .
git commit -m "Initial scaffold: architecture, prompts, schemas, pipeline skeleton"
```

Create the empty remote (pick one):

```bash
# Using GitHub CLI (creates + adds remote + pushes in one step)
gh repo create leetcode-daily-cheatsheet --private --source=. --remote=origin --push

# OR: create it manually at https://github.com/new (name: leetcode-daily-cheatsheet,
# visibility: your choice — Private recommended since it will reference your
# personal Drive folder structure), then:
git remote add origin git@github.com:<your-username>/leetcode-daily-cheatsheet.git
git branch -M main
git push -u origin main
```

Recommend **Private**: nothing in the repo is secret (secrets are never
committed — see `.gitignore`), but the repo will contain your daily coding
practice, which you may not want public by default.

## 2. Anthropic API key (Claude Code, headless)

1. Go to the [Claude Console](https://console.anthropic.com) and create an
   API key. This is billed separately from a Claude.ai subscription.
2. Confirm it works locally first:
   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...
   claude --bare -p "reply with OK" --allowedTools ""
   ```
3. You'll add this as a GitHub secret in step 5 — don't put it in any repo
   file. `config/settings.yaml` only names *which model* to use, never a key.

Cost note: each daily run makes 2-3 Claude calls (solve, verify,
compress) against one problem statement — a few thousand tokens per run.
This is the only recurring paid API in the whole pipeline (no image-model
cost — see ARCHITECTURE.md).

## 3. Google Drive (service account — required for unattended uploads)

A **service account** is used instead of your personal OAuth login because
GitHub Actions has no browser to complete an interactive OAuth consent
screen, and a service account key can be scoped to exactly one shared
folder.

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and
   create a new project (or reuse one you already have) — e.g.
   `leetcode-cheatsheet`.
2. Enable the **Google Drive API** for that project: APIs & Services ->
   Library -> search "Google Drive API" -> Enable.
3. Create a service account: APIs & Services -> Credentials -> Create
   Credentials -> Service account. Name it e.g. `cheatsheet-uploader`. You
   don't need to grant it any project-level IAM role.
4. Open the new service account -> Keys -> Add key -> Create new key ->
   JSON. This downloads a JSON file — **treat it like a password**. Do not
   commit it. Save it locally as e.g. `secrets/google-service-account.json`
   (already gitignored).
5. In Google Drive, create the folder structure you want to archive into:
   `posts/LeetCode/`. Open the `LeetCode` folder (or `posts` if you prefer
   the pipeline to create the `LeetCode` subfolder itself — it will, if
   missing) and copy its folder ID from the URL:
   `https://drive.google.com/drive/folders/<THIS_IS_THE_FOLDER_ID>`.
6. **Share that folder** with the service account's email address (looks
   like `cheatsheet-uploader@leetcode-cheatsheet.iam.gserviceaccount.com`,
   visible on the service account's detail page) — give it **Editor**
   access. Without this share, uploads will fail with a 403 even though the
   credentials are valid, because a service account has no Drive storage
   or access of its own.
7. Test locally:
   ```bash
   export GOOGLE_SERVICE_ACCOUNT_JSON=./secrets/google-service-account.json
   export GOOGLE_DRIVE_FOLDER_ID=<the folder id from step 5>
   python -m src.main --problem-slug two-sum --dry-run   # skips Drive
   python -m src.main --problem-slug two-sum --skip-drive=false --force
   ```

## 4. Contact card asset

Already done — `assets/contact-card.png` is your LeetCode visit card and is
already wired into the renderer (bottom-right, scaled to
`contact_card.max_width_px` from `config/settings.yaml`, default 210px wide
on the 1080px canvas). The preview render in this repo's setup already
confirmed it fits cleanly. To swap it for a different image later, just
overwrite `assets/contact-card.png` — no code change needed — then run
`python -m src.main --dry-run` and check
`output/<date>/<problem>/cheatsheet.png` to confirm the new one still fits.

## 5. GitHub repository secrets

Repo -> Settings -> Secrets and variables -> Actions -> New repository
secret. Add:

| Secret name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | from step 2 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | paste the **entire contents** of the JSON key file from step 3.4 |
| `GOOGLE_DRIVE_FOLDER_ID` | the folder ID from step 3.5 |

Never put any of these in `config/settings.yaml`, `CLAUDE.md`, `README.md`,
or a committed `.env` — `.gitignore` already blocks `.env` and
`*credentials*.json`, but double-check `git status` before your first
commit if you experimented locally with real keys in the repo folder.

## 6. Enable the schedule

The workflow file `.github/workflows/daily.yml` is already committed with
the correct cron (see ARCHITECTURE.md "Daylight saving time" for why there
are two cron lines). GitHub only runs scheduled workflows on the **default
branch**, and disables a schedule after 60 days of no repository activity
on a public repo — so once you `git push` to `main`, the schedule is live
with no further action.

Verify it's wired correctly without waiting for tomorrow:

1. Repo -> Actions -> "Daily LeetCode Cheat Sheet" -> Run workflow
   (`workflow_dispatch`) -> pick `dry_run: true` -> Run.
2. Watch the run log. It should fetch today's daily challenge, solve,
   verify, render, and skip the Drive upload (dry run).
3. Once that's green, run it again with `dry_run: false` to do a real
   publish and confirm the PNG lands in Drive at
   `posts/LeetCode/<year>/<month>/`.

## 7. (Optional) Connect Claude Code to GitHub for interactive development

This is unrelated to production scheduling — it's for asking Claude, in an
interactive session, to inspect this repo and reason about a change before
you commit it. In Claude Code: `/install-github-app` if you want the
Claude Code GitHub Action (`@claude` mentions on issues/PRs) — not required
for the daily pipeline to run, since `daily.yml` shells out to the `claude`
CLI directly rather than using that Action. See ARCHITECTURE.md if you're
unsure which one applies to you.

## Done

At this point: pushing is optional going forward (the schedule runs from
whatever is on `main`), the pipeline runs unattended at 9am ET, and every
run's outcome is visible in Actions -> workflow runs, plus
`state/manifest.json` on `main` after each successful publish (the workflow
commits the updated manifest back — see `docs/OPERATIONS.md`).
