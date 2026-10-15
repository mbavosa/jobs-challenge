# jobs-challenge

A daily job finder: searches live postings through [JobsPipe](https://jobspipe.dev), drops reposters, clearance and travel roles, checks that LinkedIn postings are still open, scores each job against your skills, and emails you the top 5 every morning.

You still apply yourself. Nothing here submits applications.

## Files

| File | What it does |
|------|--------------|
| `daily.py` | Search, filter, check open, rank, write `YYYY-MM-DD.md`, optionally email the top 5 |
| `filters.json` | Search filters (titles, remote, seniority, freshness) plus local skip/boost rules |
| `schedule.sh` | Turns the 7:00am email on or off (macOS launchd) |
| `opener.html` | Static page that opens every job link passed after `#` in new tabs |
| `answers.example.md` | Template for your standard application answers (copy to `answers.md`) |

## Setup

1. Put a `.env` file in the folder **above** this one:
   ```
   JOBSPIPE_API_KEY=jp_live_...
   GMAIL_ADDRESS=you@gmail.com
   GMAIL_APP_PASSWORD=abcdabcdabcdabcd
   JOBS_OPENER_URL=https://<you>.github.io/jobs-challenge/opener.html
   ```
   The Gmail app password comes from https://myaccount.google.com/apppasswords.
2. Edit `SKILLS` and `GAPS` in `daily.py` and the titles in `filters.json` to match you.
3. Copy `answers.example.md` to `answers.md` and fill it in.

## Use

```
python3 daily.py                 # build today's list
python3 daily.py --email         # ...and email the top 5
python3 daily.py --applied <url> # log a job you applied to
bash schedule.sh on|off|status   # daily 7:00am email
```

To unsubscribe from inside an email, click **Stop these emails** and send the pre-filled message; the next run turns the schedule off.

Personal files (`.env`, `resume.pdf`, `answers.md`, `seen.json`, `applied.json`, daily lists, logs) are in `.gitignore`.
