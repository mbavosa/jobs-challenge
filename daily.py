#!/usr/bin/env python3
"""Build today's ranked job list.

Usage:
    python3 daily.py              # search, filter, check open, rank, write YYYY-MM-DD.md
    python3 daily.py --pages 2    # fetch more results (each page of 25 costs ~25 credits)
    python3 daily.py --email      # also email the top 5 to yourself
    python3 daily.py --applied <linkedin url or id> ...   # log jobs you applied to

Reads JOBSPIPE_API_KEY, GMAIL_ADDRESS, GMAIL_APP_PASSWORD and JOBS_OPENER_URL from .env in this folder.
Search settings live in filters.json; answers in answers.md.
"""
import argparse
import datetime
import html
import imaplib
import json
import re
import smtplib
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEARCH_URL = "https://api.jobspipe.dev/v1/jobs/search"
LINKEDIN_GUEST = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
# python.org Python ships without certificates; use the ones macOS provides.
SSL = ssl.create_default_context(cafile="/etc/ssl/cert.pem" if Path("/etc/ssl/cert.pem").exists() else None)

# Skills from the resume, with weights. Matched against each posting's tech list and text.
SKILLS = {
    "react": 3, "javascript": 3, "html": 2, "css": 2, "php": 3, "drupal": 3, "wordpress": 3,
    "mysql": 2, "sql": 1, "node.js": 2, "laravel": 2, "jquery": 1, "bootstrap": 1, "mjml": 2,
    "email": 1, "section 508": 3, "wcag": 3, "accessibility": 2, "public trust": 4, "figma": 1,
    "rest": 1, "jenkins": 1, "selenium": 1, "solr": 1, "memcached": 1, "landing page": 2,
}
# Technologies not on the resume: each one required lowers the score a little.
GAPS = {"typescript", "java", "spring", "c#", ".net", "python", "go", "kotlin", "swift", "rust",
        "scala", "ruby", "rails", "angular", "svelte", "kubernetes"}

STOP_SUBJECT = "STOP daily jobs"

# Jobs scoring below this go in a separate "Low fit" section.
MIN_FIT = 4

# Keys in filters.json that are applied here, not sent to JobsPipe.
LOCAL_KEYS = {"skip_companies", "skip_if_description_mentions", "boost_if_description_mentions",
              "boost_if_min_salary_usd_at_least"}


def load_env():
    env = {}
    for line in (HERE / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def search(api_key, filters, pages):
    body = {k: v for k, v in filters.items() if not k.startswith("_") and k not in LOCAL_KEYS}
    body.update(limit=25)
    jobs, cursor = [], None
    for _ in range(pages):
        if cursor:
            body["cursor"] = cursor
        req = urllib.request.Request(
            SEARCH_URL, data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, context=SSL, timeout=60) as resp:
                result = json.load(resp)
        except urllib.error.HTTPError as e:
            sys.exit(f"JobsPipe error {e.code}: {e.read().decode()[:300]}")
        jobs += result["data"]
        meta = result["metadata"]
        print(f"Fetched {len(result['data'])} jobs (credits left: {meta.get('credits_remaining')})")
        cursor = meta.get("next_cursor")
        if not cursor:
            break
    return jobs


def linkedin_status(job):
    """'open', 'closed' or 'unknown' for LinkedIn jobs; 'n/a' for other sources."""
    m = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)", job.get("url") or "")
    if "linkedin" not in (job.get("sources") or []) or not m:
        return "n/a"
    req = urllib.request.Request(LINKEDIN_GUEST.format(m.group(1)), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, context=SSL, timeout=20) as resp:
            page = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        # 404/410 means the posting was removed.
        return "closed" if e.code in (404, 410) else "unknown"
    except Exception:
        return "unknown"
    if "No longer accepting applications" in page:
        return "closed"
    return "open" if "top-card-layout__title" in page else "closed"


def text_of(job):
    return f"{job.get('job_title', '')}\n{job.get('description', '')}".lower()


def skip_reason(job, filters):
    company = (job.get("company") or "").lower()
    for c in filters.get("skip_companies", []):
        if c.lower() in company:
            return f"reposter ({job.get('company')})"
    text = text_of(job)
    for phrase in filters.get("skip_if_description_mentions", []):
        if phrase.lower() in text:
            return f"mentions '{phrase}'"
    return None


def score(job, filters):
    text = text_of(job)
    tech = {t.lower() for t in job.get("technology_slugs") or []}
    hits = [s for s in SKILLS if s in tech or re.search(rf"\b{re.escape(s)}\b", text)]
    gaps = sorted(GAPS & tech)
    raw = sum(SKILLS[s] for s in hits) - len(gaps)
    floor = filters.get("boost_if_min_salary_usd_at_least")
    if floor and (job.get("min_annual_salary_usd") or 0) >= floor:
        raw += 3
    return max(1, min(10, round(raw / 2))), hits, gaps


def salary(job):
    lo, hi = job.get("min_annual_salary_usd"), job.get("max_annual_salary_usd")
    if not lo:
        return "not listed"
    return f"${lo / 1000:.0f}k" + (f"–${hi / 1000:.0f}k" if hi and hi != lo else "")


def job_id(text):
    """Pull the numeric job id out of a LinkedIn URL, or return the text as-is."""
    m = re.search(r"(\d{6,})/?$", text.strip().split("?")[0])
    return m.group(1) if m else text.strip()


def mark_applied(items):
    path = HERE / "applied.json"
    applied = json.loads(path.read_text()) if path.exists() else {}
    seen_path = HERE / "seen.json"
    seen = set(json.loads(seen_path.read_text())) if seen_path.exists() else set()
    for item in items:
        jid = job_id(item)
        applied[jid] = datetime.date.today().isoformat()
        seen.add(jid)
        print(f"Marked {jid} as applied")
    path.write_text(json.dumps(applied, indent=2))
    seen_path.write_text(json.dumps(sorted(seen)))


def unsubscribed(env):
    """True if a 'STOP daily jobs' email arrived since subscribing. Turns the schedule off if so."""
    flag = HERE / "unsubscribed.txt"
    if flag.exists():
        return True
    since = (HERE / "subscribed_at.txt")
    since = since.read_text().strip() if since.exists() else datetime.date.today().isoformat()
    since_imap = datetime.date.fromisoformat(since).strftime("%d-%b-%Y")
    with imaplib.IMAP4_SSL("imap.gmail.com", ssl_context=SSL) as imap:
        imap.login(env["GMAIL_ADDRESS"], env["GMAIL_APP_PASSWORD"])
        imap.select('"[Gmail]/All Mail"', readonly=True)
        _, data = imap.search(None, f'(SINCE {since_imap} SUBJECT "{STOP_SUBJECT}")')
    if not data[0]:
        return False
    flag.write_text(datetime.date.today().isoformat())
    send_email(env, "Daily job emails stopped",
               "<p>You're unsubscribed. No more daily job emails will be sent.</p>"
               "<p>To turn them back on, run: <code>bash ~/jobs-challenge/schedule.sh on</code></p>")
    subprocess.run(["bash", str(HERE / "schedule.sh"), "off"], check=False)
    return True


def send_email(env, subject, html_body):
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, env["GMAIL_ADDRESS"], env["GMAIL_ADDRESS"]
    msg.set_content("This email needs an HTML-capable mail app.")
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=SSL) as smtp:
        smtp.login(env["GMAIL_ADDRESS"], env["GMAIL_APP_PASSWORD"])
        smtp.send_message(msg)


def email_top(env, picks, total, today):
    esc = html.escape
    stop = f"mailto:{env['GMAIL_ADDRESS']}?subject={urllib.parse.quote(STOP_SUBJECT)}&body=Send%20this%20email%20to%20stop%20the%20daily%20job%20emails."
    if not picks:
        body = "<p>No new matching jobs today.</p>"
    else:
        urls = [job.get("url") for _, job, _, _ in picks]
        opener = env.get("JOBS_OPENER_URL")
        button = ""
        if opener:
            link = opener + "#" + "&".join("u=" + urllib.parse.quote(u, safe="") for u in urls)
            button = (f'<p><a href="{esc(link)}" style="background:#0a66c2;color:#fff;padding:12px 22px;'
                      f'border-radius:24px;text-decoration:none;font-weight:bold">Open all {len(picks)} jobs</a></p>')
        items = "".join(
            f'<li style="margin:8px 0"><b>{s}/10</b>: <a href="{esc(job.get("url") or "")}">{esc(job["job_title"])}</a>'
            f' at {esc(job.get("company") or "")} ({esc(salary(job))})</li>'
            for s, job, _, _ in picks)
        body = f"{button}<ol>{items}</ol><p>{total} new matches today. Full list: ~/jobs-challenge/{today}.md on your Mac.</p>"
    body += (f'<hr><p style="font-size:12px;color:#666">Sent by your daily job search. '
             f'<a href="{stop}">Stop these emails</a> (opens a pre-filled email; send it to unsubscribe).</p>')
    n = len(picks)
    send_email(env, f"{n} new job{'s' if n != 1 else ''} for {today}" if n else f"No new jobs for {today}", body)
    print(f"Emailed {n} jobs to {env['GMAIL_ADDRESS']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--email", action="store_true", help="email the top 5 to yourself")
    ap.add_argument("--applied", nargs="+", metavar="URL_OR_ID", help="mark jobs as applied and exit")
    args = ap.parse_args()

    if args.applied:
        return mark_applied(args.applied)

    env = load_env()
    api_key = env.get("JOBSPIPE_API_KEY")
    if not api_key:
        sys.exit("Add JOBSPIPE_API_KEY=... to .env first.")
    if args.email and unsubscribed(env):
        return print("Unsubscribed; not sending.")
    filters = json.loads((HERE / "filters.json").read_text())

    seen_path = HERE / "seen.json"
    seen = set(json.loads(seen_path.read_text())) if seen_path.exists() else set()

    kept, skipped, dupes = [], [], set()
    for job in search(api_key, filters, args.pages):
        jid = str(job["id"])
        key = (job.get("company"), job.get("job_title"))
        if jid in seen or key in dupes:
            continue
        dupes.add(key)
        reason = skip_reason(job, filters)
        if not reason:
            status = linkedin_status(job)
            time.sleep(1)
            if status == "closed":
                reason = "closed on LinkedIn"
        if reason:
            skipped.append((job, reason))
            continue
        s, hits, gaps = score(job, filters)
        kept.append((s, job, hits, gaps))
        seen.add(jid)

    kept.sort(key=lambda r: (-r[0], -(r[1].get("min_annual_salary_usd") or 0)))
    today = datetime.date.today().isoformat()
    lines = [f"# Jobs for {today}", "",
             f"{len(kept)} new matches, {len(skipped)} skipped. Answers: answers.md", "",
             "| Fit | Job | Company | Pay | Matches | Gaps |", "|---|---|---|---|---|---|"]
    def row(s, job, hits, gaps):
        return (f"| {s}/10 | [{job['job_title']}]({job.get('url')}) | {job.get('company')} | "
                f"{salary(job)} | {', '.join(hits[:6])} | {', '.join(gaps) or '-'} |")
    lines += [row(*k) for k in kept if k[0] >= MIN_FIT]
    low = [k for k in kept if k[0] < MIN_FIT]
    if low:
        lines += ["", f"## Low fit (below {MIN_FIT}/10)", "",
                  "| Fit | Job | Company | Pay | Matches | Gaps |", "|---|---|---|---|---|---|"]
        lines += [row(*k) for k in low]
    lines += ["", "## Skipped", ""] + [f"- {j['job_title']} at {j.get('company')}: {r}" for j, r in skipped]
    out = HERE / f"{today}.md"
    if kept or not out.exists():  # don't replace an earlier list with an empty one
        out.write_text("\n".join(lines) + "\n")
    seen_path.write_text(json.dumps(sorted(seen)))
    print(f"Wrote {out} ({len(kept)} jobs, {len(skipped)} skipped)")
    if args.email:
        email_top(env, kept[:5], len(kept), today)


if __name__ == "__main__":
    main()
