import asyncio
import httpx
import random
import json
import re
import uuid
import requests
import itertools
import os
import time
from concurrent.futures import ThreadPoolExecutor

COOKIES = {
    "datr": os.environ.get("META_DATR", ""),
    "fs": os.environ.get("META_FS", ""),
    "locale": "en_US",
}

IDENTITY_ID = "921560754377590"
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
CONCURRENT = 20
CLAIM_URL = "https://accountscenter.meta.com/api/graphql/"
CLAIM_DOC_ID = "9672408826128267"

# Run for 5.5 hours max so GitHub Actions doesn't kill us mid-run
MAX_RUNTIME_SECONDS = 5.5 * 60 * 60
START_TIME = time.time()

thread_pool = ThreadPoolExecutor(max_workers=32)

def load_usernames():
    with open("usernames.txt", encoding="utf-8") as f:
        return [x.strip().lstrip("@") for x in f if x.strip()]

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
        print(f"  [Session] Error: {e}")
        return None, None

def claim_username_sync(username):
    fb_dtsg, lsd = get_fresh_tokens()
    if not fb_dtsg or not lsd:
        print(f"  [Claim] FAILED - no tokens for @{username}")
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
            print(f"  [Claim] SUCCESS - @{username} claimed!")
            return True
        err = fxim.get("error") or (data.get("errors") or [{}])[0].get("message", "unknown")
        print(f"  [Claim] Failed @{username}: {err}")
        return False
    except Exception as e:
        print(f"  [Claim] Error @{username}: {e}")
        return False

def send_webhook_sync(username, claimed):
    if not WEBHOOK_URL:
        return
    msg = f"🎯 **CLAIMED:** `@{username}`" if claimed else f"✅ **Available (claim failed):** `@{username}`"
    try:
        requests.post(WEBHOOK_URL, json={"content": msg}, timeout=5)
    except Exception:
        pass

def cap_variants(name):
    seen = set()
    for combo in itertools.product([0, 1], repeat=len(name)):
        variant = "".join(
            c.upper() if combo[i] else c.lower()
            for i, c in enumerate(name)
        )
        if variant not in seen:
            seen.add(variant)
            yield variant

available_names = []
claimed_names = []

async def horizon_check(client, username):
    url = f"https://horizon.meta.com/profile/{username}/"
    try:
        r = await client.get(url)
        final = str(r.url).rstrip("/").lower()

        # Only mark TAKEN if it lands on that exact profile page
        if final == f"https://horizon.meta.com/profile/{username.lower()}":
            return "TAKEN"

        # Only mark AVAILABLE if it redirects to homepage exactly
        if final in ("https://horizon.meta.com", "https://www.meta.com"):
            return "AVAILABLE"

        # Anything else = unknown, treat as TAKEN to be safe
        return "TAKEN"

    except httpx.TooManyRedirects:
        return "TAKEN"  # be safe, don't try to claim
    except Exception:
        return "TAKEN"  # be safe

async def check_single_name(semaphore, client, username, i, total):
    async with semaphore:
        try:
            variants = list(cap_variants(username))
            results = await asyncio.gather(*[horizon_check(client, v) for v in variants])

            if any(r == "TAKEN" for r in results):
                print(f"[{i:>4}/{total}] @{username:<20} TAKEN")
                return

            print(f"[{i:>4}/{total}] @{username:<20} AVAILABLE — claiming now...")
            available_names.append(username)

            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(thread_pool, claim_username_sync, username)
            asyncio.ensure_future(loop.run_in_executor(thread_pool, send_webhook_sync, username, success))
            if success:
                claimed_names.append(username)

        except Exception as e:
            print(f"[{i:>4}/{total}] @{username:<20} ERROR: {e}")

async def run_cycle(names, proxies):
    semaphore = asyncio.Semaphore(CONCURRENT)
    proxy = random.choice(proxies) if proxies else None
    client_kwargs = {
        "follow_redirects": True,
        "max_redirects": 5,
        "http2": True,
        "limits": httpx.Limits(
            max_connections=200,
            max_keepalive_connections=50,
            keepalive_expiry=30,
        ),
        "timeout": 15,
    }
    if proxy:
        client_kwargs["proxy"] = proxy
    async with httpx.AsyncClient(**client_kwargs) as client:
        tasks = [
            check_single_name(semaphore, client, name, i, len(names))
            for i, name in enumerate(names, 1)
        ]
        await asyncio.gather(*tasks)

async def main():
    names = load_usernames()
    proxies = load_proxies()
    print(f"Loaded {len(names)} usernames | {len(proxies)} proxies | {CONCURRENT} concurrent")
    print(f"Will run for up to 5.5 hours then stop (GitHub Actions reschedules automatically)\n")

    cycle = 1
    while True:
        # Stop before GitHub Actions 6hr limit kills us
        elapsed = time.time() - START_TIME
        if elapsed > MAX_RUNTIME_SECONDS:
            print(f"\nApproaching 6hr limit — stopping cleanly. GitHub Actions will restart shortly.")
            break

        print(f"--- CYCLE {cycle} | Elapsed: {int(elapsed//60)}m ---\n")
        await run_cycle(names, proxies)
        print(f"\n--- CYCLE {cycle} DONE | Available: {len(available_names)} | Claimed: {len(claimed_names)} ---")

        if claimed_names:
            print(f"  Claimed so far: {', '.join(claimed_names)}")

        with open("available.txt", "w") as f:
            f.write("\n".join(available_names))
        with open("claimed.txt", "w") as f:
            f.write("\n".join(claimed_names))

        cycle += 1
        print(f"\nRestarting cycle in 5 seconds...\n")
        await asyncio.sleep(5)

asyncio.run(main())