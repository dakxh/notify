import requests
from curl_cffi import requests as cffi_requests
import time
import json
import os
import subprocess

# --- CONFIGURATION ---
STATE_FILE = "prhn_state.json"
MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60) # 5 hours 55 mins
NTFY_URL = "https://ntfy.sh/pcx_unblock"
TARGET_URL = "https://in.bookmyshow.com/api/v3/mobile/showtimes/byvenue?appCode=MOBAND2&appVersion=18234&venueCode=PRHN"

# Track WARP State natively
USE_WARP = True

# Cloudflare WARP local proxy
PROXIES = {
    "http": "socks5://127.0.0.1:40000",
    "https": "socks5://127.0.0.1:40000"
}

HEADERS = {
    "Host": "in.bookmyshow.com",
    "X-Latitude": "17.385044",
    "X-Subregion-Code": "HYD",
    "X-App-Code": "MOBAND2",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 10; Android SDK built for x86_64 Build/QSR1.211112.011)",
    "X-Longitude": "78.48667",
    "X-Platform": "AND",
    "X-Region-Code": "HYD",
    "X-Platform-Code": "ANDROID",
    "X-App-Version": "18.2.3",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive"
}

def quiet_git_pull():
    subprocess.run(["git", "pull", "origin", "main", "--rebase"], capture_output=True, text=True, check=False)

def quiet_git_push():
    res = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True, check=False)
    return res.returncode == 0

def load_state():
    quiet_git_pull()
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {"farthest_datecode": ""}

def save_state(state, commit_msg="Update PRHN farthest date state"):
    quiet_git_pull()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    
    subprocess.run(["git", "add", STATE_FILE], capture_output=True, check=False)
    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    
    if STATE_FILE in status.stdout:
        print(f"[GIT] Committing changes to {STATE_FILE}...")
        subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True, check=False)
        for attempt in range(3):
            if quiet_git_push():
                print(f"[GIT] Successfully pushed state to repository.")
                break
            print(f"[GIT] Push attempt {attempt+1} failed, retrying...")
            time.sleep(2)
            quiet_git_pull()

def trigger_ntfy(message):
    print(f"\n[!] ALERTING VIA NTFY: {message}")
    for i in range(3): # 3 Ping Burst Strategy
        try:
            resp = requests.post(
                NTFY_URL,
                data=message.encode('utf-8'),
                headers={"Priority": "urgent"},
                timeout=10
            )
            print(f"    -> Ntfy ping {i+1}/3 sent! Status: {resp.status_code}")
        except Exception as e:
            print(f"    -> Ntfy ping {i+1} failed: {e}")
        if i < 2:
            print("    -> Waiting 15s before next ping...")
            time.sleep(15)

def toggle_warp():
    """Toggles Cloudflare WARP on/off and updates the proxy state."""
    global USE_WARP
    if USE_WARP:
        print("    -> 🚨 [IP ROTATION] WARP is currently ON. Disconnecting WARP (Switching to Runner IP)...")
        subprocess.run(["warp-cli", "--accept-tos", "disconnect"], capture_output=True, check=False)
        USE_WARP = False
    else:
        print("    -> 🚨 [IP ROTATION] WARP is currently OFF. Connecting to WARP (Switching to Cloudflare Proxy)...")
        subprocess.run(["warp-cli", "--accept-tos", "connect"], capture_output=True, check=False)
        time.sleep(5)  # Wait for the tunnel to establish
        USE_WARP = True

def make_bms_request(url, max_retries=3):
    """Network wrapper that enforces 25s delay, intercepts 429s, toggles WARP, and retries."""
    
    print("    -> [WAIT] Sleeping 25 seconds before request...")
    time.sleep(25)
    
    for attempt in range(1, max_retries + 1):
        current_proxies = PROXIES if USE_WARP else None
        
        try:
            resp = cffi_requests.get(url, proxies=current_proxies, headers=HEADERS, impersonate="chrome", timeout=15)
            print(f"    -> Status: {resp.status_code} (Using WARP: {USE_WARP})")
            
            if resp.status_code == 429:
                print(f"    -> ⚠️ Rate limited (429) on attempt {attempt}/{max_retries}.")
                if attempt < max_retries:
                    toggle_warp()
                    print("    -> Retrying request...")
                    continue 
                else:
                    print("    -> ❌ Max retries reached for this request.")
            
            return resp
            
        except Exception as e:
            print(f"    -> ⚠️ Network exception on attempt {attempt}: {e}")
            if attempt < max_retries:
                time.sleep(3)
                continue
    
    return None

def fetch_farthest_date():
    print("\n[NETWORK] Fetching showtimes for Prasads Multiplex (PRHN)...")
    resp = make_bms_request(TARGET_URL)
    
    if not resp or resp.status_code != 200:
        print("    -> Failed to fetch showtimes data.")
        return None
        
    try:
        data = resp.json()
        show_dates_array = data.get("ShowDatesArray", [])
        
        if not show_dates_array:
            print("    -> No ShowDatesArray found or array is empty.")
            return None
            
        # Extract all valid DateCodes
        date_codes = [item.get("DateCode") for item in show_dates_array if item.get("DateCode")]
        
        if date_codes:
            # max() safely returns the farthest date chronologically for YYYYMMDD strings
            farthest = max(date_codes)
            print(f"    -> Extracted {len(date_codes)} available dates. Farthest DateCode is: {farthest}")
            return farthest
        else:
            print("    -> No valid DateCodes found within ShowDatesArray.")
            return None
            
    except Exception as e:
        print(f"    -> JSON Parse error: {e}")
        return None

def main():
    start_time = time.time()
    
    print("==================================================")
    print("🍿 STARTING BMS VENUE SCRAPER (Prasads Multiplex)")
    print("==================================================")
    
    print("\n[GIT] Loading initial state from repository...")
    state = load_state()
    is_first_run = (state.get("farthest_datecode") == "")
    
    if is_first_run:
        print("[STATE] Empty/Missing state found. Establishing baseline silently on first fetch...")
    else:
        print(f"[STATE] Loaded existing state. Currently tracking farthest date: {state['farthest_datecode']}")

    cycle_count = 1
    
    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        print(f"\n==================================================")
        print(f"🔄 STARTING POLLING CYCLE {cycle_count}")
        print(f"==================================================")
        
        current_farthest = fetch_farthest_date()
        
        if current_farthest:
            stored_farthest = state.get("farthest_datecode", "")
            
            # Since "20260730" > "20260729" evaluates correctly, we use a simple string comparison
            if current_farthest > stored_farthest:
                print(f"    -> 🟢 DETECTED NEW FARTHEST DATE: {current_farthest} (Previous: {stored_farthest or 'None'})")
                
                if not is_first_run:
                    msg = f"{current_farthest} bookings are now open at Prasads multiplex"
                    trigger_ntfy(msg)
                
                # Update & Save State
                state["farthest_datecode"] = current_farthest
                print("\n[STATE] Cycle finished. Changes detected, saving to Git...")
                save_state(state, f"Update PRHN farthest date to {current_farthest}")
            else:
                print(f"    -> ⚪ No newer dates detected. Current farthest remains {stored_farthest}.")
        
        if is_first_run:
            is_first_run = False
            print("\n[STATE] First run baseline has been successfully established. Alerting is now armed.")
            
        cycle_count += 1
        
    print("\n🏁 Time limit reached (5h 55m). Saving final state and gracefully shutting down.")
    final_state = load_state()
    save_state(final_state, "Final runner shutdown save")

if __name__ == "__main__":
    main()
