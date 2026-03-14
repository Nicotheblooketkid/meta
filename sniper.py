import asyncio
import httpx
import requests
import itertools
import os
import random
import time
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

# ── Config ────────────────────────────────────────────────────────────────────
INPUT_FILE   = "username.txt"
CONCURRENT   = 10
DEBUG        = True
MAX_RUNTIME  = 5.5 * 60 * 60
START_TIME   = time.time()

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
WEBHOOK_URL     = os.environ.get("WEBHOOK_URL", "")

META_DATR = os.environ.get("META_DATR", "")
META_FS   = os.environ.get("META_FS", "")

COOKIES = {
    "datr":   META_DATR,
    "fs":     META_FS,
    "locale": "en_US",
}

IDENTITY_ID  = os.environ.get("IDENTITY_ID", "921560754377590")
CLAIM_URL    = "https://accountscenter.meta.com/api/graphql/"
CLAIM_DOC_ID = "9672408826128267"

# ── ANSI colours ──────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RED    = "\033[91m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
PINK   = "\033[95m"
YELLOW = "\033[93m"

TAKEN_MESSAGES = [
    f"{RED}{BOLD}🔒 SNAGGED — already claimed{RESET}",
    f"{RED}{BOLD}💀 DEAD END — someone got there first{RESET}",
    f"{RED}{BOLD}🚫 NO LUCK — this one's taken{RESET}",
    f"{RED}{BOLD}😤 CLAIMED — move on{RESET}",
    f"{RED}{BOLD}🔴 LOCKED IN — not yours{RESET}",
]
AVAILABLE_MESSAGES = [
    f"{GREEN}{BOLD}✅ LET'S GO — it's yours for the taking{RESET}",
    f"{GREEN}{BOLD}💎 CLEAN — nobody has this yet{RESET}",
    f"{GREEN}{BOLD}🟢 OPEN SEASON — grab it{RESET}",
    f"{GREEN}{BOLD}🤑 FREE REAL ESTATE — unclaimed{RESET}",
    f"{GREEN}{BOLD}🚀 ALL YOURS — wide open{RESET}",
]
CLAIMED_MESSAGES = [
    f"{GREEN}{BOLD}🎯 CLAIMED — it's yours now!{RESET}",
    f"{GREEN}{BOLD}👑 SECURED — nobody can take it{RESET}",
    f"{GREEN}{BOLD}💰 LOCKED IN — username saved{RESET}",
]

# ── State ─────────────────────────────────────────────────────────────────────
available_names = []
claimed_names   = []
thread_pool     = ThreadPoolExecutor(max_workers=32)

# ── Load Files ────────────────────────────────────────────────────────────────
def load_usernames():
    if not os.path.exists(INPUT_FILE):
        print(f"{RED}  ✖  '{INPUT_FILE}' not found!{RESET}")
        return []
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        return [l.strip().lstrip("@") for l in f if l.strip()]

def load_proxies():
    proxies = []
    try:
        with open("proxies.txt") as f:
            for line in f:
                p = line.strip()
                if not p:
                    continue
                parts = p.split(":")
                if len(parts) == 4:
                    host, port, user, password = parts
                    proxies.append(f"http://{user}:{password}@{host}:{port}")
                elif len(parts) == 2:
                    proxies.append(f"http://{parts[0]}:{parts[1]}")
    except FileNotFoundError:
        pass
    return proxies

# ── Claim Sessions ────────────────────────────────────────────────────────────
claim_sessions = []
for _ in range(8):
    s = requests.Session()
    s.cookies.update(COOKIES)
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://accountscenter.meta.com",
        "Referer": f"https://accountscenter.meta.com/profiles/{IDENTITY_ID}/username/",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    })
    claim_sessions.append(s)

session_index = 0

def get_claim_session():
    global session_index
    s = claim_sessions[session_index % len(claim_sessions)]
    session_index += 1
    return s

# ── Tokens ────────────────────────────────────────────────────────────────────
def get_fresh_tokens():
    try:
        s = get_claim_session()
        r = s.get(
            f"https://accountscenter.meta.com/profiles/{IDENTITY_ID}/username/",
            timeout=10
        )
        html = r.text
        dtsg_match = (
            re.search(r'"token":"([^"]+)","isEncrypted"', html)
            or re.search(r'"DTSGInitialData"[^}]*"token":"([^"]+)"', html)
        )
        lsd_match = re.search(r'"LSD"[^}]*"token":"([^"]+)"', html)
        if dtsg_match and lsd_match:
            return dtsg_match.group(1), lsd_match.group(1)
        return None, None
    except Exception as e:
        print(f"{YELLOW}  ⚠  Token fetch error: {e}{RESET}", flush=True)
        return None, None

# ── Claim ─────────────────────────────────────────────────────────────────────
def claim_username_sync(username):
    fb_dtsg, lsd = get_fresh_tokens()
    if not fb_dtsg or not lsd:
        print(f"{YELLOW}  ⚠  No tokens — could not claim @{username}{RESET}", flush=True)
        return False
    payload = {
        "av": IDENTITY_ID,
        "__user": "0",
        "__a": "1",
        "fb_dtsg": fb_dtsg,
        "lsd": lsd,
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": "useFXIMUpdateUsernameMutation",
        "server_timestamps": "true",
        "doc_id": CLAIM_DOC_ID,
        "variables": json.dumps({
            "client_mutation_id": str(uuid.uuid4()),
            "family_device_id": "device_id_fetch_datr",
            "identity_ids": [IDENTITY_ID],
            "target_fx_identifier": IDENTITY_ID,
            "username": username,
            "interface": "FRL_WEB"
        })
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "x-fb-friendly-name": "useFXIMUpdateUsernameMutation",
        "x-fb-lsd": lsd,
        "x-asbd-id": "359341",
    }
    try:
        s = get_claim_session()
        r = s.post(CLAIM_URL, data=payload, headers=headers, timeout=10)
        data = r.json()
        fxim = data.get("data", {}).get("fxim_update_identity_username", {})
        if fxim.get("error") is None and "fxim_update_identity_username" in data.get("data", {}):
            return True
        err = fxim.get("error") or (data.get("errors") or [{}])[0].get("message", "unknown")
        print(f"{YELLOW}  ⚠  Claim failed @{username}: {err}{RESET}", flush=True)
        return False
    except Exception as e:
        print(f"{YELLOW}  ⚠  Claim error @{username}: {e}{RESET}", flush=True)
        return False

# ── Discord ───────────────────────────────────────────────────────────────────
def send_discord_alert(username, claimed):
    webhook = DISCORD_WEBHOOK or WEBHOOK_URL
    if not webhook:
        return
    msg = (
        f"@everyone\n🎯 **CLAIMED:** `@{username}`"
        if claimed else
        f"@everyone\n✅ **Available (claim failed):** `@{username}`"
    )
    for _ in range(5):
        try:
            r = requests.post(
                webhook,
                json={"content": msg, "allowed_mentions": {"parse": ["everyone"]}},
                timeout=10
            )
            if r.status_code in (200, 204):
                return
            if r.status_code == 429:
                time.sleep(float(r.json().get("retry_after", 1)))
                continue
        except Exception:
            time.sleep(1)

# ── Cap Variants ──────────────────────────────────────────────────────────────
def cap_variants(name: str):
    seen = set()
    for v in [name.lower(), name.upper(), name.capitalize()]:
        if v not in seen:
            seen.add(v)
            yield v
    if len(name) <= 8:
        for combo in itertools.product([0, 1], repeat=len(name)):
            v = "".join(c.upper() if combo[i] else c.lower() for i, c in enumerate(name))
            if v not in seen:
                seen.add(v)
                yield v

# ── Horizon Check (CORRECT LOGIC) ────────────────────────────────────────────
# Meta returns:
#   302 → location: https://horizon.meta.com/          = AVAILABLE (profile not found)
#   302 → location: https://horizon.meta.com/profile/  = TAKEN (redirected to real profile)
#   200                                                 = TAKEN (profile loaded directly)
# We must NOT follow redirects — read the raw 302 location header.
async def horizon_check(client, variant):
    url = f"https://horizon.meta.com/profile/{variant}/"
    try:
        r = await client.get(url)
        status = r.status_code
        location = r.headers.get("location", "")

        if DEBUG:
            print(f"{DIM}  [DEBUG] {variant:<20} status={status} location={location!r}{RESET}", flush=True)

        if status in (401, 402, 403):
            return "UNKNOWN"

        if status in (301, 302):
            loc = location.rstrip("/").lower()
            # Redirected to homepage = profile not found = AVAILABLE
            if loc in ("https://horizon.meta.com", "https://www.meta.com"):
                return "AVAILABLE"
            # Redirected anywhere else (e.g. back to a profile) = TAKEN
            return "TAKEN"

        # 200 = profile page loaded = TAKEN
        if status == 200:
            return "TAKEN"

        # Anything else = treat as TAKEN to be safe
        return "TAKEN"

    except httpx.TooManyRedirects:
        return "TAKEN"
    except Exception as e:
        if DEBUG:
            print(f"{DIM}  [DEBUG] {variant} exception: {e}{RESET}", flush=True)
        return "UNKNOWN"

# ── Check Single Name ─────────────────────────────────────────────────────────
async def check_single_name(semaphore, client, username, idx, total):
    async with semaphore:
        prefix = f"{DIM}[{idx:04}/{total:04}]{RESET} {BOLD}{CYAN}{username:<20}{RESET}"

        variants = list(cap_variants(username))
        results  = await asyncio.gather(*[horizon_check(client, v) for v in variants])

        if any(r == "TAKEN" for r in results):
            print(f"{prefix}  {random.choice(TAKEN_MESSAGES)}", flush=True)
            return

        if all(r == "UNKNOWN" for r in results):
            print(f"{YELLOW}  ⚠  All variants inconclusive for @{username} — skipping{RESET}", flush=True)
            return

        # Double check before claiming
        await asyncio.sleep(1)
        recheck = await horizon_check(client, username.lower())
        if recheck != "AVAILABLE":
            print(f"{prefix}  {random.choice(TAKEN_MESSAGES)}", flush=True)
            return

        print(f"{prefix}  {random.choice(AVAILABLE_MESSAGES)}", flush=True)
        available_names.append(username)

        loop    = asyncio.get_event_loop()
        success = await loop.run_in_executor(thread_pool, claim_username_sync, username)
        await loop.run_in_executor(thread_pool, send_discord_alert, username, success)

        if success:
            claimed_names.append(username)
            print(f"{prefix}  {random.choice(CLAIMED_MESSAGES)}", flush=True)

# ── Run One Cycle ─────────────────────────────────────────────────────────────
async def run_cycle(usernames, proxies, cycle):
    total = len(usernames)
    batch = usernames[:]
    random.shuffle(batch)

    semaphore = asyncio.Semaphore(CONCURRENT)
    proxy     = random.choice(proxies) if proxies else None

    client_kwargs = {
        "follow_redirects": False,  # CRITICAL — read raw 302 location directly
        "http2": True,
        "cookies": COOKIES,
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
        "limits": httpx.Limits(
            max_connections=100,
            max_keepalive_connections=30,
            keepalive_expiry=30,
        ),
        "timeout": 15,
    }
    if proxy:
        client_kwargs["proxy"] = proxy

    async with httpx.AsyncClient(**client_kwargs) as client:
        tasks = [
            check_single_name(semaphore, client, name, i, total)
            for i, name in enumerate(batch, 1)
        ]
        await asyncio.gather(*tasks)

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    print(f"\n{CYAN}{BOLD}{'=' * 50}{RESET}")
    print(f"{PINK}{BOLD}      💻  M E L L O W 'S  U S E R  F I N D E R  💻{RESET}")
    print(f"{DIM}     24/7 mode — checks + auto-claims — loops 5.5hrs{RESET}")
    print(f"{CYAN}{BOLD}{'=' * 50}{RESET}\n")

    if not META_DATR or not META_FS:
        print(f"{RED}  ✖  META_DATR or META_FS secrets not set — cookies required!{RESET}")
        return

    usernames = load_usernames()
    if not usernames:
        return
    proxies = load_proxies()

    print(f"{DIM}  Loaded {len(usernames)} usernames | {len(proxies)} proxies | {CONCURRENT} concurrent{RESET}\n", flush=True)

    cycle = 1
    while True:
        elapsed = time.time() - START_TIME
        if elapsed > MAX_RUNTIME:
            print(f"\n{YELLOW}{BOLD}  ⏱  Approaching 6hr limit — stopping cleanly. GitHub Actions will restart.{RESET}\n")
            break

        print(f"\n{CYAN}{DIM}  ── Cycle {cycle} | Elapsed: {int(elapsed // 60)}m | Available: {len(available_names)} | Claimed: {len(claimed_names)} ──{RESET}\n", flush=True)

        await run_cycle(usernames, proxies, cycle)

        with open("available.txt", "w") as f:
            f.write("\n".join(available_names))
        with open("claimed.txt", "w") as f:
            f.write("\n".join(claimed_names))

        cycle += 1
        print(f"\n{DIM}  Cycle done. Restarting in 5 seconds...{RESET}", flush=True)
        await asyncio.sleep(5)

    print(f"\n{CYAN}{BOLD}{'=' * 50}{RESET}")
    print(f"{GREEN}{BOLD}  💎  AVAILABLE: {len(available_names)}  |  🎯  CLAIMED: {len(claimed_names)}{RESET}")
    if claimed_names:
        print(f"{GREEN}  Claimed: {', '.join(claimed_names)}{RESET}")
    print(f"{CYAN}{BOLD}{'=' * 50}{RESET}\n")

if __name__ == "__main__":
    asyncio.run(main())
