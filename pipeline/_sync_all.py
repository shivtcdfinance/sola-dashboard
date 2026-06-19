#!/usr/bin/env python3
"""
_sync_all.py — Bidirectional sync: info.jsonl ↔ master.txt
============================================================
Run after ANY data mutation (scanner, tagger, JD fetcher, CV gen, easy apply).

Direction:
  1. info.jsonl is the canonical source for status, job metadata, and JD text.
  2. master.txt carries platform/type/updated fields the JSONL doesn't.
  3. The sync merges both: info.jsonl statuses flow into master.txt; master.txt
     platform/blocked fields enrich info.jsonl entries. Master.txt is always
     rebuilt from the merged set for a clean format.

Also writes a compact CSV for easy external consumption.
"""

import json, re, os, csv
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter

# ─── Paths ───
BASE = Path(os.path.expanduser("~/Documents/Hermes/projects/job-search"))
# Fallback for legacy symlink users
if not BASE.exists():
    BASE = Path(os.path.expanduser("~/Documents/Hermes/job_search"))

MASTER_FILE = BASE / "india_jobs_master.txt"
INFO_FILE = BASE / "india_jobs_info.jsonl"
CSV_FILE = BASE / "india_jobs_snapshot.csv"

CSV_COLUMNS = [
    "url", "status", "title", "company", "location",
    "cv_path", "jd_length", "jd_fetched_at", "keywords",
    "platform", "application_type", "blocked_reason", "updated_at",
]

HEX_RE = re.compile(r'[0-9a-f]{6,8}')

STATUS_ORDER = {
    "pending": 0,
    "jdfetched": 0.5,
    "aligned": 1,
    "working": 2,
    "cv_done": 3,
    "cv_ready": 3,
    "applied": 4,
    "wasted": 5,
    "blocked": 6,
    "not_aligned": 7,
}


# ─── Master.txt parsing ───

def parse_scanner_format(text):
    """Parse the raw scanner format: [N] ID: <number> blocks."""
    entries = {}
    # Split on [N] ID: pattern
    blocks = re.split(r'\n\[\d+\]\s+ID:\s+', text.strip())
    for block in blocks:
        if not block.strip():
            continue
        lines = block.splitlines()
        entry = {}
        for line in lines:
            if line.startswith("    Title:"):
                entry["title"] = line.split(":", 1)[1].strip()
            elif line.startswith("    Company:"):
                entry["company"] = line.split(":", 1)[1].strip()
            elif line.startswith("    Location:"):
                entry["location"] = line.split(":", 1)[1].strip()
            elif line.startswith("    URL:"):
                entry["url"] = line.split(":", 1)[1].strip()
            elif line.startswith("    Status:"):
                entry["status"] = line.split(":", 1)[1].strip()
        url = entry.get("url", "")
        if url and "linkedin.com/jobs/view/" in url:
            entries[url] = entry
    return entries

def parse_master(text):
    """Return dict mapping URL → entry dict."""
    text = text.replace("|---", "---")
    header_re = re.compile(
        r"^[─-]{3,}\s+Job\s+#[0-9a-fA-F]+\s+[─-]{3,}.*?$", re.MULTILINE
    )
    headers = [m.start() for m in header_re.finditer(text)]

    entries = {}
    for i, start in enumerate(headers):
        end = headers[i + 1] if i + 1 < len(headers) else len(text)
        block = text[start:end]
        lines = block.splitlines()

        entry = {}
        for line in lines:
            if line.startswith("  Status:"):
                entry["status"] = line.split(":", 1)[1].strip()
            elif line.startswith("  Company:"):
                entry["company"] = line.split(":", 1)[1].strip()
            elif line.startswith("  Title:"):
                entry["title"] = line.split(":", 1)[1].strip()
            elif line.startswith("  Location:"):
                entry["location"] = line.split(":", 1)[1].strip()
            elif line.startswith("  URL:"):
                entry["url"] = line.split(":", 1)[1].strip()
            elif line.startswith("  CV_Path:"):
                entry["cv_path"] = line.split(":", 1)[1].strip()
            elif line.startswith("  Platform:"):
                entry["platform"] = line.split(":", 1)[1].strip()
            elif line.startswith("  ApplicationType:"):
                entry["application_type"] = line.split(":", 1)[1].strip()
            elif line.startswith("  Blocked:"):
                entry["blocked_reason"] = line.split(":", 1)[1].strip()
            elif line.startswith("  Updated:"):
                entry["updated_at"] = line.split(":", 1)[1].strip()

        url = entry.get("url", "")
        if url:
            entries[url] = entry
    return entries


# ─── Master.txt writing ───

def job_id(url):
    """Deterministic short hex ID from URL."""
    h = hex(hash(url) & 0xFFFFFFFF)[2:]
    return h.zfill(8)[:8]


def write_master(entries_sorted):
    """Write sorted master.txt from a merged list of entry dicts."""
    lines = []
    fixed_count = 0
    for e in entries_sorted:
        # ── GUARD: cv_done without CV_Path is a scanner bug — auto-correct ──
        status = e.get('status', 'pending')
        if status == 'cv_done' and not e.get('cv_path'):
            status = 'aligned' if e.get('platform') else 'pending'
            fixed_count += 1
        lines.append(f"─── Job #{job_id(e['url'])} {'─' * 55}")
        lines.append("")
        lines.append(f"  Status:      {status}")
        if e.get("platform"):
            lines.append(f"  Platform:    {e['platform']}")
        if e.get("application_type"):
            lines.append(f"  ApplicationType: {e['application_type']}")
        lines.append(f"  Company:     {e.get('company', 'Unknown')}")
        lines.append(f"  Title:       {e.get('title', 'Unknown')}")
        if e.get("location"):
            lines.append(f"  Location:    {e['location']}")
        lines.append(f"  URL:         {e['url']}")
        # ── GUARD: only emit cv_path and blocked_reason when status supports them ──
        cv_valid = status in ("cv_done", "cv_ready", "applied", "blocked")
        if e.get("cv_path") and cv_valid:
            lines.append(f"  CV_Path:     {e['cv_path']}")
        if e.get("updated_at"):
            lines.append(f"  Updated:      {e['updated_at']}")
        elif e.get("jd_fetched_at"):
            lines.append(f"  Updated:      {e['jd_fetched_at']}")
        blocked_valid = status in ("cv_done", "cv_ready", "applied", "blocked")
        if e.get("blocked_reason") and blocked_valid:
            lines.append(f"  Blocked:     {e['blocked_reason']}")
        lines.append("")

    MASTER_FILE.write_text("\n".join(lines) + "\n")
    if fixed_count:
        print(f"  ⚠️  Auto-corrected {fixed_count} cv_done jobs without CV_Path → aligned/pending")


# ─── CSV writing ───

def write_csv(entries_sorted):
    """Write a compact CSV snapshot."""
    with open(CSV_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for e in entries_sorted:
            writer.writerow(e)
    return len(entries_sorted)


# ─── Main sync ───

def sync():
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── 1. Load info.jsonl (canonical) ──
    info_by_url = {}
    if INFO_FILE.exists():
        with open(INFO_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    url = entry.get("url", "")
                    if url:
                        info_by_url[url] = entry
                except json.JSONDecodeError:
                    continue

    print(f"  info.jsonl: {len(info_by_url)} entries")

    # ── 2. Load master.txt (supplementary fields) ──
    master_entries = {}
    master_orphans = []
    if MASTER_FILE.exists():
        text = MASTER_FILE.read_text()
        # Try standard format first, fall back to scanner format
        master_entries = parse_master(text)
        if not master_entries:
            scanner_entries = parse_scanner_format(text)
            if scanner_entries:
                print(f"  master.txt: fallback scanner format → {len(scanner_entries)} entries")
                master_entries = scanner_entries
        # Find jobs in master.txt that are NOT in info.jsonl
        for url, me in master_entries.items():
            if url not in info_by_url:
                master_orphans.append(me)
        print(f"  master.txt: {len(master_entries)} entries (orphans: {len(master_orphans)})")
    else:
        print(f"  master.txt: does not exist yet")

    # ── 3. Merge ──
    merged = {}  # url → dict

    for url, info in info_by_url.items():
        merged[url] = dict(info)  # shallow copy
        # Merge master.txt fields that info.jsonl doesn't have
        m = master_entries.get(url, {})
        if m.get("platform"):
            merged[url]["platform"] = m["platform"]
        if m.get("application_type"):
            merged[url]["application_type"] = m["application_type"]
        if m.get("updated_at"):
            merged[url]["updated_at"] = m["updated_at"]
        # ── GUARD: only preserve cv_path / blocked_reason from master.txt when
        #    the info.jsonl status actually supports them — prevents stale
        #    CV_Path from a previous lifecycle polluting pending/jdfetched/aligned.
        info_status = merged[url].get("status", "pending")
        cv_valid_statuses = {"cv_done", "cv_ready", "applied", "blocked"}
        blocked_valid_statuses = {"cv_done", "cv_ready", "applied", "blocked"}
        if m.get("cv_path") and not merged[url].get("cv_path") and info_status in cv_valid_statuses:
            merged[url]["cv_path"] = m["cv_path"]
        if m.get("blocked_reason") and not merged[url].get("blocked_reason") and info_status in blocked_valid_statuses:
            merged[url]["blocked_reason"] = m["blocked_reason"]
        # If info.jsonl doesn't have location but master.txt does
        if m.get("location") and not merged[url].get("location"):
            merged[url]["location"] = m["location"]

    # Add master.txt orphans (jobs in master.txt but not in info.jsonl)
    for m in master_orphans:
        url = m.get("url", "")
        if url and url not in merged:
            merged[url] = m
            # Give it a minimal jd_text so it's complete
            if "jd_text" not in merged[url]:
                merged[url]["jd_text"] = ""

    # ── 3b. Clean up location pollution ──
    auto_fix_cv_blocked = 0
    auto_fix_no_reason = 0
    for url, e in merged.items():
        loc = e.get("location", "")
        if loc and loc.strip().startswith("URL:"):
            e["location"] = ""
        # Also clean up title pollution (URL in title)
        title = e.get("title", "")
        if title and "URL:" in title:
            e["title"] = title.split("URL:")[0].strip()
        # Clean cv_path if polluted
        cv = e.get("cv_path", "")
        if cv and cv.strip().endswith("/CV"):
            e["cv_path"] = ""
        # ── AUTO-CORRECT: cv_done + Blocked → blocked ──
        # cv_done means "ready to apply"; Blocked means "can't apply".
        # The Blocked tag always wins because we physically cannot apply.
        status = e.get("status", "pending")
        blocked_reason = e.get("blocked_reason", "")
        if status == "cv_done" and blocked_reason:
            e["status"] = "blocked"
            auto_fix_cv_blocked += 1
        # ── AUTO-CORRECT: blocked without reason → add default reason ──
        if status == "blocked" and not blocked_reason:
            e["blocked_reason"] = "unknown"
            auto_fix_no_reason += 1

    print(f"  merged: {len(merged)} unique jobs")
    if auto_fix_cv_blocked or auto_fix_no_reason:
        print(f"  🔧 auto-corrected: {auto_fix_cv_blocked} cv_done+Blocked → blocked, {auto_fix_no_reason} blocked → +reason")

    # ── 4. Sort ──
    def sort_key(item):
        url, e = item
        s = e.get("status", "pending")
        order = STATUS_ORDER.get(s, 99)
        title = e.get("title", "").lower()
        return (order, title)

    sorted_items = sorted(merged.items(), key=sort_key)

    # ── 5. Update info.jsonl (canonical source gets backfilled fields) ──
    # Preserve all fields, but add any master-only fields back
    INFO_FILE.write_text(
        "\n".join(
            json.dumps(e, ensure_ascii=False, default=str)
            for _, e in sorted_items
        ) + "\n"
    )

    # ── 6. Rebuild master.txt ──
    entries_list = [e for _, e in sorted_items]
    write_master(entries_list)

    # ── 7. Write CSV ──
    csv_count = write_csv(entries_list)

    # ── 8. Report ──
    statuses = Counter()
    for _, e in sorted_items:
        statuses[e.get("status", "?")] += 1

    print(f"\n  Sync complete @ {now_iso}")
    print(f"  info.jsonl → {len(sorted_items)} entries")
    print(f"  master.txt → {len(sorted_items)} jobs")
    print(f"  CSV        → {csv_count} rows")
    print(f"\n  Status breakdown:")
    for s in ["pending", "jdfetched", "aligned", "working", "cv_done", "cv_ready",
              "applied", "wasted", "blocked", "not_aligned"]:
        c = statuses.get(s, 0)
        if c:
            print(f"    {s}: {c}")

    # ── 9. Mirror CSV to Google Sheets (fire-and-forget backup) ──
    _sync_sheets_backup()


def _sync_sheets_backup():
    """Push dashboard CSV to Google Sheets as off-machine backup.
    Fire-and-forget — failures here never block the main sync."""
    import subprocess
    sheets_script = os.path.expanduser("~/.hermes/scripts/sheets_sync.py")
    try:
        r = subprocess.run(
            ["python3", sheets_script, "--cron"],
            capture_output=True, text=True, timeout=45
        )
        if r.returncode == 0:
            print(f"  📊 Sheets backup: OK")
        else:
            print(f"  📊 Sheets backup: skipped (exit {r.returncode})")
    except Exception as e:
        print(f"  📊 Sheets backup: skipped ({e})")


if __name__ == "__main__":
    sync()
