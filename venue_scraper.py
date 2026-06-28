import requests
from curl_cffi import requests as cffi_requests
import time
import json
import os
import subprocess

# --- CONFIGURATION ---
STATE_FILE = "brandnewday_state.json"
MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60) # 5 hours 55 mins
NTFY_URL = "https://ntfy.sh/grv_brandnewday"

# Request Details
EVENT_CODE = "ET00447840"
DATE_CODE = "20260730"
TARGET_LANGUAGE = "English"

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
    "X-Location-Selection": "manual",
    "X-Longitude": "78.48667",
    "X-Platform": "AND",
    "X-Region-Code": "HYD",
    "X-Region-Slug": "hyderabad",
    "X-Platform-Code": "ANDROID",
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
    return {"dimensions": {}, "venues": {}}

def save_state(state, commit_msg="Update Spiderman theatre state"):
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

def fetch_dimensions():
    print("\n[NETWORK] Fetching Synopsis to extract Formats/Dimensions...")
    url = f"https://in.bookmyshow.com/api/movies/v1/synopsis/init/dynamic?eventcode={EVENT_CODE}&channel=mobile"
    resp = make_bms_request(url)
    
    extracted = {}
    if not resp or resp.status_code != 200:
        print("    -> Failed to fetch synopsis.")
        return extracted
        
    try:
        data = resp.json()
        page_cta = data.get("pageCta", [])
        
        for cta in page_cta:
            if cta.get("text") == "Book tickets":
                options = cta.get("meta", {}).get("options", [])
                for opt in options:
                    if opt.get("language") == TARGET_LANGUAGE:
                        formats = opt.get("formats", [])
                        for f in formats:
                            dim = f.get("dimension")
                            ref_event_code = f.get("refEventCode")
                            if dim and ref_event_code:
                                extracted[dim] = ref_event_code
                                
        print(f"    -> Extracted {len(extracted)} {TARGET_LANGUAGE} dimensions: {list(extracted.keys())}")
    except Exception as e:
        print(f"    -> JSON Parse error in synopsis: {e}")
        
    return extracted

def extract_all_venue_names(data):
    """Recursively search for 'venueName' anywhere in the JSON object to guarantee extraction."""
    venues = set()
    if isinstance(data, dict):
        for k, v in data.items():
            if k == "venueName":
                venues.add(v)
            else:
                venues.update(extract_all_venue_names(v))
    elif isinstance(data, list):
        for item in data:
            venues.update(extract_all_venue_names(item))
    return venues

def fetch_venues_for_dimension(ref_event_code):
    print(f"\n[NETWORK] Fetching venues for refEventCode: {ref_event_code} (Date: {DATE_CODE})")
    url = (
        f"https://in.bookmyshow.com/api/movies-data/v5/showtimes-by-event/primary-dynamic?"
        f"dateCode={DATE_CODE}&etCodes={ref_event_code}&language={TARGET_LANGUAGE}&refEventCode={ref_event_code}"
    )
    
    resp = make_bms_request(url)
    if not resp or resp.status_code != 200:
        print("    -> Failed to fetch showtimes for dimension.")
        return set()
        
    try:
        data = resp.json()
        venues = extract_all_venue_names(data)
        print(f"    -> Found {len(venues)} unique venues.")
        return venues
    except Exception as e:
        print(f"    -> JSON Parse error in venues: {e}")
        return set()

def main():
    start_time = time.time()
    
    print("==================================================")
    print("🕷️ STARTING BMS THEATRE SCRAPER (Spiderman: BND)")
    print("==================================================")
    
    print("\n[GIT] Loading initial state from repository...")
    state = load_state()
    is_first_run = (len(state.get("dimensions", {})) == 0) and (len(state.get("venues", {})) == 0)
    
    if is_first_run:
        print("[STATE] Empty/Missing state found. Initializing baseline silently...")
        state = {"dimensions": {}, "venues": {}}
    else:
        print(f"[STATE] Loaded existing state. Tracking {len(state['dimensions'])} dimensions.")

    cycle_count = 1
    
    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        print(f"\n==================================================")
        print(f"🔄 STARTING POLLING CYCLE {cycle_count}")
        print(f"==================================================")
        
        state_changed_this_cycle = False
        
        # 1. Check Dimensions
        current_dims = fetch_dimensions()
        
        if current_dims:
            new_dim_keys = set(current_dims.keys()) - set(state["dimensions"].keys())
            
            if new_dim_keys:
                print(f"    -> 🟢 DETECTED NEW DIMENSIONS: {list(new_dim_keys)}")
                if not is_first_run:
                    dims_str = ", ".join(new_dim_keys)
                    msg = f"New format(s) added for Spiderman: Brand New Day! Formats: {dims_str}"
                    trigger_ntfy(msg)
                
                # Update State
                for dk in new_dim_keys:
                    state["dimensions"][dk] = current_dims[dk]
                    if dk not in state["venues"]:
                        state["venues"][dk] = []
                state_changed_this_cycle = True
            else:
                print("    -> ⚪ No new dimensions detected.")
                
            # Keep tracked state synchronized with API response for refEventCodes
            for dk, refcode in current_dims.items():
                if state["dimensions"].get(dk) != refcode:
                    state["dimensions"][dk] = refcode
                    state_changed_this_cycle = True
        
        # 2. Check Venues for each tracked dimension
        for dim, ref_event_code in state["dimensions"].items():
            current_venues = fetch_venues_for_dimension(ref_event_code)
            
            if not current_venues:
                continue
                
            previous_venues = set(state["venues"].get(dim, []))
            new_venues = current_venues - previous_venues
            
            if new_venues:
                print(f"    -> 🟢 DETECTED NEW VENUES FOR '{dim}': {len(new_venues)} new theatres!")
                if not is_first_run:
                    # Aggregated Notification
                    venues_str = ", ".join(sorted(new_venues))
                    msg = f"New theatre for Spiderman: Brand New Day, {venues_str} in {dim}"
                    trigger_ntfy(msg)
                    
                state["venues"][dim] = list(previous_venues.union(new_venues))
                state_changed_this_cycle = True
            else:
                print(f"    -> ⚪ No new venues for {dim}.")

        if state_changed_this_cycle:
            print("\n[STATE] Cycle finished. Changes detected, saving to Git...")
            save_state(state, f"State update at cycle {cycle_count}")
        else:
            print("\n[STATE] Cycle finished. No changes detected.")
            
        if is_first_run:
            is_first_run = False
            print("[STATE] First run baseline has been successfully established.")
            
        cycle_count += 1
        
    print("\n🏁 Time limit reached (5h 55m). Saving final state and gracefully shutting down.")
    final_state = load_state()
    save_state(final_state, "Final runner shutdown save")

if __name__ == "__main__":
    main()
