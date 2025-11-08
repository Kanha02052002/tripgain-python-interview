from playwright.sync_api import sync_playwright
from fastapi import FastAPI
import os
import json
import re
from datetime import datetime, timezone
import requests
from typing import List, Dict, Any, Tuple
import traceback
from dotenv import load_dotenv

load_dotenv()

TARGET_FRAGMENT = "/domestic-flights/"

def _HEAD(msg): print("\n" + "="*80 + f"\n[HEAD] {msg}\n" + "="*80)
def _STEP(msg): print(f"[HEAD] {msg}")
def _OK(msg="OK"): print(f"[HEAD] ✅ {msg}")
def _WARN(msg): print(f"[HEAD] ⚠️ {msg}")
def _ERR(msg): print(f"[HEAD] ❌ {msg}")


def get_destination_input(page):
    selectors = [
        "[placeholder='SELECT DESTINATION CITY']",
        "[placeholder*='DESTINATION']",
        "[placeholder*='Destination']",
        "[placeholder*='destination']",
        "xpath=//input[contains(translate(@placeholder,'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'DESTINATION')]",
        # second angular field in DOM
        "xpath=(//input[contains(@id,'anguScroll')])[2]",
        # input after the "To" label
        "xpath=//label[contains(., 'To')]/following::input[1]",
        # fallback: any input in destination container
        "xpath=//div[contains(@class,'to') or contains(@class,'destination')]//input",
    ]

    # Try all selectors one by one
    for sel in selectors:
        try:
            el = page.wait_for_selector(sel, timeout=2000)
            print(f"[HEAD] ✅ Found destination input with selector: {sel}")
            return el
        except:
            continue

    # Final fallback → Tab from origin box
    try:
        page.click("#anguScroll_value", timeout=1000)
        page.keyboard.press("Tab")
        dest = page.evaluate_handle("document.activeElement")
        print("[HEAD] ✅ Using fallback: activeElement after TAB")
        return dest
    except:
        pass

    print("[HEAD] ❌ Destination input not found, using last input on page")
    return page.query_selector("input:last-of-type")


def _safe_capture_results_page(results_page):
    """Capture URL + HTML from a results page, even if some waits fail."""
    try:
        _STEP("Ensuring DOM is ready on results page…")
        try:
            results_page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception as e:
            _WARN(f"domcontentloaded wait skipped/failed: {e}")

        try:
            results_page.wait_for_timeout(1000)
        except Exception:
            pass

        try:
            _STEP("Light scroll to trigger lazy-load…")
            results_page.evaluate("""() => new Promise(res => {
                let y=0, h=document.body.scrollHeight;
                const id=setInterval(()=>{y+=600; window.scrollTo(0,y);
                    if(y>=h-200){clearInterval(id); setTimeout(res,400);} },120);
            })""")
        except Exception as e:
            _WARN(f"Scroll skipped/failed: {e}")

        url = results_page.url
        html = results_page.content()
        _OK(f"Captured {len(html)} characters from {url}")

        with open("results_page.html", "w", encoding="utf-8") as f:
            f.write(html)
        with open("results_page_url.txt", "w", encoding="utf-8") as f:
            f.write(url + "\n")

        _OK("Saved results_page.html and results_page_url.txt")
        print(f"[HEAD] Results URL: {url}")
        return True
    except Exception as e:
        _ERR(f"Failed to capture results page: {e}")
        return False

def _fallback_capture_from_context(context, current_page):
    """Fallback when timeouts happen: pick best candidate page and capture."""
    _HEAD("FALLBACK: Searching for best page to capture")
    pages = list(context.pages)
    # Prefer any page whose URL contains the target fragment
    for pg in pages:
        if TARGET_FRAGMENT in (pg.url or ""):
            _OK(f"Found matching page via URL: {pg.url}")
            return _safe_capture_results_page(pg)

    # Otherwise, try the newest page (last opened tab)
    if pages:
        candidate = pages[-1]
        _WARN(f"No URL matched '{TARGET_FRAGMENT}'. Using last opened page: {candidate.url}")
        return _safe_capture_results_page(candidate)

    # Last resort: try current page
    if current_page:
        _WARN("No extra pages detected. Using current page as last resort.")
        return _safe_capture_results_page(current_page)

    _ERR("No pages available to capture.")
    return False

def scrape_flights(l1,l2):
    print("Starting flight scraping process...")
    results = []
    with sync_playwright() as p:
        print("Launching browser...")
        browser = p.chromium.launch(headless=False)

        # Use a context so we can listen for new tabs
        context = browser.new_context()
        opened_pages = []
        context.on("page", lambda pg: opened_pages.append(pg))

        page = context.new_page()

        print("Navigating to budgetticket.in...")
        page.goto("https://www.budgetticket.in")

        print(f"Filling origin: {l1}")
        page.evaluate(f"document.querySelector('#anguScroll_value').value = '{l1}'")
        page.evaluate("document.querySelector('#anguScroll_value').dispatchEvent(new Event('input', { bubbles: true }))")
        page.wait_for_timeout(2000)
        try:
            page.click("text=BLR")
        except:
            print("Could not find BLR option, trying alternative")
            page.click(f"text={l1}")

        print("Locating destination input field...")
        dest_input = get_destination_input(page)
        print(f"Filling destination: {l2}")
        # FIX: Changed to an arrow function (el => ...) instead of using arguments[0]
        page.evaluate(f"el => el.value = '{l2}'", dest_input)
        # FIX: Changed to an arrow function (el => ...) instead of using arguments[0]
        page.evaluate("el => el.dispatchEvent(new Event('input', { bubbles: true }))", dest_input)
        page.wait_for_timeout(2000)
        try:
            page.click(f"text={l2}")
        except:
            print("Could not find DEL option, trying alternative")
            page.click(f"text={l2}")

        print("Selecting journey date...")
        date_selectors = ["[placeholder*='Journey Date']", "[ng-model*='date']", "#departureDate"]
        date_clicked = False
        for selector in date_selectors:
            try:
                page.click(selector)
                print(f"Clicked date field using selector: {selector}")
                date_clicked = True
                break
            except:
                continue
        if not date_clicked:
            print("Could not find date input field")
            # Fallback capture before exit
            _fallback_capture_from_context(context, page)
            browser.close()
            return

        page.click('text="2025-11-20"')

        print("Clicking search button...")
        search_selectors = ["button[type='submit']", "[ng-click*='search']", ".search-btn", "text='SEARCH FLIGHTS'"]
        search_clicked = False
        for selector in search_selectors:
            try:
                page.click(selector)
                print(f"Clicked search button using selector: {selector}")
                search_clicked = True
                break
            except:
                continue
        if not search_clicked:
            print("Could not find search button")
            # Fallback capture before exit
            _fallback_capture_from_context(context, page)
            browser.close()
            return

        # ----- Robust results capture with global timeout safety -----
        try:
            _HEAD("WAITING FOR RESULTS PAGE (/domestic-flights/...)")
            results_page = None

            # Prefer: a brand-new tab whose URL already matches
            try:
                results_page = context.wait_for_event(
                    "page",
                    timeout=20000,
                    predicate=lambda pg: TARGET_FRAGMENT in (pg.url or "")
                )
                _OK(f"New tab detected: {results_page.url}")
            except Exception as e:
                _WARN(f"New tab not detected within timeout: {e}")
                # Fallback: same tab navigation
                try:
                    page.wait_for_url(f"**{TARGET_FRAGMENT}**", timeout=15000)
                    _OK(f"Same tab navigated to: {page.url}")
                    results_page = page
                except Exception as e2:
                    _WARN(f"Same-tab wait_for_url failed: {e2}")

            if results_page:
                # Primary capture path
                _safe_capture_results_page(results_page)
            else:
                # Ultimate fallback: pick last opened page or best candidate
                _fallback_capture_from_context(context, page)

        except Exception as e:
            _ERR("Unexpected error during results capture. Falling back to last opened page.")
            print(traceback.format_exc())
            _fallback_capture_from_context(context, page)

        print("Closing browser...")
        browser.close()

    # Keep placeholders for compatibility
    print(f"Saving {len(results)} flights to flight_results.json")
    with open("flight_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Total flights extracted: {len(results)}")
    


OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
# Pick any model available to your OpenRouter key. You can change this.
DEFAULT_MODEL = "nvidia/nemotron-nano-12b-v2-vl:free"

def _extract_json_array(text: str) -> List[Dict[str, Any]]:
    """
    Tries to extract a top-level JSON array from model output.
    Accepts outputs with extra prose by slicing from first '[' to last ']'.
    """
    text = text.strip()
    # Fast path: already a pure JSON array
    if text.startswith("[") and text.endswith("]"):
        return json.loads(text)

    # Fallback: find first '[' and last ']' to isolate array
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end+1])

    # Last resort: try to parse as JSON object containing 'flights'
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "flights" in obj and isinstance(obj["flights"], list):
            return obj["flights"]
    except Exception:
        pass

    raise ValueError("Could not extract a JSON array of flights from the model response.")

def evaluate(path: str,
             model: str = DEFAULT_MODEL,
             site_url: str = None,
             site_title: str = None) -> Tuple[List[Dict[str, Any]], int]:
    """
    Read text content from `path`, send to OpenRouter, and parse a JSON array like:
    [
      {
        "airline": "IndiGo",
        "flight_number": "6E-123",
        "departure": "06:30",
        "arrival": "09:10",
        "price": "₹5,450",
        "origin": "Bangalore",
        "destination": "Delhi",
        "searchdatetime": "2025-10-17T09:10:00Z"
      },
      ...
    ]
    Returns (flights, total_count) and prints "Total Flights Extracted: N".
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENROUTER_API_KEY in your environment.")

    # Read input text
    with open(path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    # We'll freeze a timestamp and ask the model to use exactly this value for `searchdatetime`
    now_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    system_prompt = (
        "You are a strict JSON extraction engine. "
        "From the given text, extract EVERY flight mentioned into a JSON array. "
        "Output ONLY the JSON array (no prose). "
        "Each flight object MUST contain exactly these string fields:\n"
        " - airline\n - flight_number\n - departure\n - arrival\n - price\n"
        " - origin\n - destination\n - searchdatetime\n\n"
        f"Set 'searchdatetime' to this exact value for all items: {now_utc}\n"
        "If any field is missing in the source, infer conservatively or leave it as an empty string \"\" "
        "(but keep the field present)."
    )

    user_prompt = (
        "Extract flights from the following text. Keep times in HH:MM 24-hour format,\n"
        "keep prices exactly as seen (including currency symbol), keep city names as-is.\n\n"
        "TEXT START\n"
        f"{raw_text}\n"
        "TEXT END\n"
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if site_url:
        headers["HTTP-Referer"] = site_url
    if site_title:
        headers["X-Title"] = site_title

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        # Strongly nudge models to return strict JSON only:
        "temperature": 0,
    }

    resp = requests.post(OPENROUTER_API_URL, headers=headers, data=json.dumps(payload), timeout=120)
    resp.raise_for_status()
    data = resp.json()

    try:
        content = data["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"Unexpected API response shape: {data}") from e

    flights = _extract_json_array(content)

    # Print + return
    total = len(flights)
    print(json.dumps(flights, ensure_ascii=False, indent=2))
    print(f"Total Flights Extracted: {total}")
    return flights, total

# Example usage:
# flights, total = evaluate("sample_flights.txt")

app=FastAPI()

@app.get("/scrape-flights/{l1}-{l2}")
def api_scrape_flights(l1: str, l2: str):
    result = scrape_flights(l1, l2)
    flight, total = evaluate("results_page.html")
    return {
        "message": "Scraping completed",
        "origin": l1,
        "destination": l2,
        "data": flight,
        "flights_extracted": total
    }
    
import uvicorn
if __name__ == "__main__":
    uvicorn.run(
        "main:app",    # file_name:app_instance
        host="0.0.0.0",
        port=8000,
        reload=True
    )