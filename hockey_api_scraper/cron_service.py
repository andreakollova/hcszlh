import os
import time
from datetime import datetime
from dotenv import load_dotenv

from run_scrape import run_scrape

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default

def main():
    load_dotenv()
    interval_min = _env_int("CRON_INTERVAL_MINUTES", 15)
    interval_sec = max(60, interval_min * 60)

    while True:
        started = datetime.utcnow().isoformat()
        try:
            result = run_scrape()
            print(f"[{started}] scrape_ok {result}")
        except Exception as e:
            print(f"[{started}] scrape_error {type(e).__name__}: {e}")

        time.sleep(interval_sec)

if __name__ == "__main__":
    main()
