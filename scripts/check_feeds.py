"""Pre-flight check. Run before the first build, and as a weekly job.

Publishers move feed URLs without telling anyone. Without this, the failure
mode is a paper that quietly gets thinner over six months.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import feedparser
import requests
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = yaml.safe_load(Path("config/feeds.yaml").read_text(encoding="utf-8"))
    ua = cfg["defaults"]["user_agent"]
    failures = []

    for src in cfg["sources"]:
        if src["kind"] != "rss":
            print(f"  {src['id']:<18} (api — skipped)")
            continue
        try:
            resp = requests.get(src["url"], headers={"User-Agent": ua}, timeout=25)
            resp.raise_for_status()
            entries = feedparser.parse(resp.content).entries
            if len(entries) < 3:
                failures.append((src["id"], f"only {len(entries)} entries"))
                print(f"  {src['id']:<18} THIN  {len(entries)} entries")
            else:
                print(f"  {src['id']:<18} ok    {len(entries)} entries")
        except Exception as exc:
            failures.append((src["id"], str(exc)))
            print(f"  {src['id']:<18} FAIL  {exc}")

    for i, sub in enumerate(cfg["reddit"]["subs"]):
        if i:
            time.sleep(cfg["reddit"]["delay_sec"])
        url = (f"https://www.reddit.com/r/{sub['sub']}/{sub['sort']}/.rss"
               f"?t={sub['t']}&limit={sub['limit']}")
        try:
            r = requests.get(url, headers={"User-Agent": ua}, timeout=25)
            status = "ok" if r.ok else f"HTTP {r.status_code}"
            if r.status_code == 429:
                status = "429 (throttle — usually fine for a daily run)"
            print(f"  r/{sub['sub']:<16} {status}")
        except Exception as exc:
            print(f"  r/{sub['sub']:<16} FAIL  {exc}")

    if failures:
        print(f"\n{len(failures)} feed(s) need attention:")
        for fid, why in failures:
            print(f"  - {fid}: {why}")
        return 1
    print("\nall feeds healthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
