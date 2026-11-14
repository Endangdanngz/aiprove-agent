#!/usr/bin/env python3
"""aiprove-agent — agen otonom penuh untuk arena bounty AIProve.

Loop end-to-end (persis alur aiprove.fun/skill.md), tanpa manusia di tengah:
  1. membaca skill.md (unduhan tangguh: range-resume),
  2. autentikasi dengan keypair sendiri (tantangan EIP-191 -> tanda tangan
     -> token sesi),
  3. mendaftar bounty yang terbuka,
  4. masuk (enter) ke bounty target,
  5. submit hasil kerja: repo ini sendiri @ commit untuk bounty jenis repo,
     dan daftar laporan bug terverifikasi (bugs.json) untuk bug-hunt-utils.

Setiap baris log juga dikumpulkan ke buffer transcript; isi buffer itulah
yang dikirim sebagai `transcript` pada submission — bukti provenance bahwa
agen ini sendiri yang menjalankan loop-nya. Private key dan token sesi
tidak pernah masuk log, jadi transcript aman dibagikan.

Konfigurasi lewat environment:
  AGENT_PRIVATE_KEY   kunci keypair agen (wajib)
  AGENT_HANDLE        handle arena (default: kipas77)
  API_BASE_URL        default https://aiprove.fun
  AIPROVE_REPO_URL    URL repo ini (untuk submission jenis repo)
  AIPROVE_COMMIT      hash commit repo ini
  AIPROVE_BUGS_FILE   path daftar bug (default: bugs.json di folder ini)

Pemakaian:
  AGENT_PRIVATE_KEY=0x... python3 agent.py [slug ...]
"""
import json
import os
import sys

from eth_account import Account

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import ArenaClient, ArenaError

API = os.environ.get("API_BASE_URL", "https://aiprove.fun")
HANDLE = os.environ.get("AGENT_HANDLE", "kipas77")
DEFAULT_TARGETS = ["sample-agent-skillmd", "bug-hunt-utils", "best-autonomous-agent"]
REPO_BOUNTIES = ["sample-agent-skillmd", "best-autonomous-agent"]
BUGHUNT_SLUG = "bug-hunt-utils"

_transcript = []


def log(line=""):
    print(line, flush=True)
    _transcript.append(line)


def transcript_text():
    return "\n".join(_transcript) + "\n"


def main():
    targets = sys.argv[1:] or DEFAULT_TARGETS
    here = os.path.dirname(os.path.abspath(__file__))
    client = ArenaClient(API, log=log)

    log("=== aiprove-agent: loop otonom dimulai ===")
    log("[1/5] membaca skill.md ...")
    status, _ = client.request("GET", "/api/health")
    log(f"      arena health: HTTP {status}")
    skill = client.fetch_text("/skill.md")
    log(f"      skill.md utuh: {len(skill)} karakter; baris pertama: {skill.strip().splitlines()[0]}")

    log("[2/5] autentikasi keypair sendiri (EIP-191) ...")
    key = os.environ.get("AGENT_PRIVATE_KEY")
    if not key:
        log("      AGENT_PRIVATE_KEY tidak diset — berhenti.")
        return 2
    account = Account.from_key(key)
    log(f"      address: {account.address}")
    client.authenticate(account)

    log("[3/5] mendaftar bounty terbuka ...")
    bounties = client.list_open_bounties()
    log(f"      {len(bounties)} bounty terbuka:")
    for b in bounties:
        mark = "  <= TARGET" if b["slug"] in targets else ""
        log(f"      - {b['slug']}: {b['amt']} {b['unit']} (subs {b.get('subs')}){mark}")

    log("[4/5] masuk ke bounty target ...")
    entered = []
    for slug in targets:
        status, _res = client.enter(slug, HANDLE)
        ok = status in (200, 201)
        log(f"      enter {slug}: HTTP {status} {'OK' if ok else 'GAGAL'}")
        if ok:
            entered.append(slug)

    log("[5/5] submit hasil kerja ...")
    repo_url = os.environ.get("AIPROVE_REPO_URL")
    commit = os.environ.get("AIPROVE_COMMIT")
    submitted = []
    if repo_url and commit:
        for slug in REPO_BOUNTIES:
            if slug not in targets:
                continue
            status, res = client.submit_repo(slug, repo_url, commit, transcript_text(), HANDLE)
            sub_id = res.get("id") or (res.get("submission") or {}).get("id") or "-"
            log(f"      submit {slug}: HTTP {status} id={sub_id}")
            if status in (200, 201):
                submitted.append(slug)
    else:
        log("      AIPROVE_REPO_URL/AIPROVE_COMMIT tidak diset — submission repo dilewati.")

    if BUGHUNT_SLUG in targets:
        bugs_path = os.environ.get("AIPROVE_BUGS_FILE", os.path.join(here, "bugs.json"))
        with open(bugs_path) as f:
            bugs = json.load(f)
        log(f"      {len(bugs)} laporan bug terverifikasi dimuat dari {os.path.basename(bugs_path)}")
        status, res = client.submit_bughunt(BUGHUNT_SLUG, bugs, transcript_text(), HANDLE)
        sub_id = res.get("id") or (res.get("submission") or {}).get("id") or "-"
        log(f"      submit {BUGHUNT_SLUG}: HTTP {status} id={sub_id}")
        if status in (200, 201):
            submitted.append(BUGHUNT_SLUG)

    status, me = client.me()
    competing = me.get("competing", [])
    log(f"      /api/me: HTTP {status}; competing={len(competing)} submission tercatat di profil")
    log(f"LOOP SELESAI: entered {len(entered)}/{len(targets)}, submitted {len(submitted)} "
        f"({', '.join(submitted) or '-'}) sebagai {HANDLE} ({account.address})")
    return 0 if len(entered) == len(targets) and len(submitted) == len(targets) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ArenaError as e:
        log(f"ERROR: {e}")
        sys.exit(1)
