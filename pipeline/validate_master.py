#!/usr/bin/env python3
"""
validate_master.py — Pre-sync validation of india_jobs_master.txt
Exit 0 = clean, 1 = warnings, 2 = errors (block sync)

Run before _sync_all.py to prevent contradictory states from propagating.
Mechanical guard — no LLM, deterministic regex.
"""
import re, sys, os
from pathlib import Path

MASTER = Path(os.path.expanduser(
    "~/Documents/Hermes/projects/job-search/india_jobs_master.txt"
))

# ── Config ──
PRE_CV_STATUSES = {"pending", "jdfetched", "aligned", "working", "not_aligned"}
POST_CV_STATUSES = {"cv_done", "cv_ready", "applied", "blocked"}

# ── Parse ──
def parse_blocks(content):
    header_re = re.compile(
        r'^[─-]{3,}\s+Job\s+#[0-9a-fA-F]+\s+[─-]{3,}.*?$', re.MULTILINE
    )
    headers = [(m.start(), m.group(0)) for m in header_re.finditer(content)]
    blocks = []
    for i, (start, hdr) in enumerate(headers):
        end = headers[i+1][0] if i+1 < len(headers) else len(content)
        blocks.append(content[start:end])
    return blocks

def extract_field(block, field):
    m = re.search(rf'{field}:\s+(.+)', block)
    return m.group(1).strip() if m else None

def validate():
    if not MASTER.exists():
        print(f"⚠️  {MASTER} not found — nothing to validate")
        sys.exit(0)

    text = MASTER.read_text()
    blocks = parse_blocks(text)
    
    errors = []
    warnings = []
    urls_seen = set()
    
    for i, block in enumerate(blocks):
        status = extract_field(block, 'Status')
        blocked = extract_field(block, 'Blocked')
        cv_path = extract_field(block, 'CV_Path')
        url = extract_field(block, 'URL')
        title = extract_field(block, 'Title') or f"block #{i}"
        company = extract_field(block, 'Company') or "?"
        
        # Check 1: cv_done + Blocked contradiction (sync will auto-fix → blocked)
        if status == 'cv_done' and blocked:
            warnings.append(
                f"WILL_AUTOFIX: {company} — {title} | "
                f"Status=cv_done + Blocked={blocked} → sync corrects to blocked"
            )
        
        # Check 2: blocked without reason (sync will auto-add unknown)
        if status == 'blocked' and not blocked:
            warnings.append(
                f"WILL_AUTOFIX: {company} — {title} | "
                f"Blocked without reason → sync adds blocked=unknown"
            )
        
        # Check 3: stale CV_Path on pre-cv_done stages
        if status in PRE_CV_STATUSES and cv_path:
            errors.append(
                f"STALE_CV: {company} — {title} | "
                f"Status={status} but has CV_Path"
            )
        
        # Check 4: Company placeholder in CV_Path (nuisance — sync will preserve it)
        if cv_path and ('Company_CV.docx' in cv_path or '/Company/' in cv_path):
            warnings.append(
                f"COMPANY_PLACEHOLDER: {company} — {title} | {cv_path}"
            )
        
        # Check 5: duplicate URL
        if url:
            if url in urls_seen:
                errors.append(f"DUPLICATE_URL: {url}")
            urls_seen.add(url)
        
        # Check 6: no Status field at all
        if not status:
            errors.append(f"MISSING_STATUS: {company} — {title}")
    
    # ── Report ──
    if errors:
        print(f"❌ {len(errors)} ERROR(S) — sync blocked:")
        for e in errors:
            print(f"   {e}")
        sys.exit(2)
    
    if warnings:
        print(f"⚠️  {len(warnings)} warning(s):")
        for w in warnings:
            print(f"   {w}")
        sys.exit(1)
    
    print(f"✅ validate_master: {len(blocks)} jobs — clean")
    sys.exit(0)

if __name__ == "__main__":
    validate()
