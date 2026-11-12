"""Read page images with a vision model behind an OpenAI-compatible API.

    set -a; . ~/.gpustack.env; set +a
    python scripts/read_pages.py --images <dir> --model qwen3.8-27b \\
        --out readings/raw/readings_qwen3.8-27b.jsonl [--limit 5]

One JSON line per page (``key`` = the TEI ``facs``), appended as it comes, so a
stopped run resumes and a page is never read twice under the same settings.
The prompt is ``prompts/sg_missiven_page_v4.md`` — the one the Gemini runs
used — so the readings differ by model, not by instruction.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PB = re.compile(r'<pb\b[^>]*\bfacs="([^"]+)"')


def facs_list(data_dir: Path) -> list[str]:
    seen: dict[str, None] = {}
    for xml in sorted(data_dir.glob("*.xml")):
        for facs in PB.findall(xml.read_text(encoding="utf-8")):
            seen.setdefault(facs, None)
    return list(seen)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prompt", default=str(ROOT / "prompts" / "sg_missiven_page_v4.md"))
    ap.add_argument("--base-url", default=os.environ.get("GPUSTACK_BASE_URL"))
    ap.add_argument("--key-env", default="GPUSTACK_API_KEY")
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-thinking", action="store_true", default=True,
                    help="chat_template_kwargs enable_thinking=false (Qwen reasoning models)")
    args = ap.parse_args()

    prompt = Path(args.prompt).read_text(encoding="utf-8")
    digest = hashlib.sha256(prompt.encode()).hexdigest()[:8]
    settings = f"prompt-{digest}+nothink|max{args.max_tokens}"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("settings") == settings and not rec.get("error"):
                done.add(rec["key"])

    images = Path(args.images)
    todo = [f for f in facs_list(ROOT / "data") if f not in done
            and (images / (Path(f).stem + ".jpg")).exists()]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{args.model}: {len(done)} already read, {len(todo)} to read", flush=True)

    key = os.environ[args.key_env]
    client = httpx.Client(base_url=args.base_url, timeout=600,
                          headers={"Authorization": f"Bearer {key}"})
    lock = threading.Lock()
    counter = {"n": 0}

    def read(facs: str) -> None:
        img = (images / (Path(facs).stem + ".jpg")).read_bytes()
        body = {
            "model": args.model,
            "max_tokens": args.max_tokens,
            "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(img).decode()}},
                {"type": "text", "text": prompt},
            ]}],
        }
        if args.no_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        rec = {"key": facs, "model": args.model, "settings": settings}
        for attempt in range(4):
            try:
                r = client.post("/chat/completions", json=body)
                r.raise_for_status()
                data = r.json()
                choice = data["choices"][0]
                text = (choice["message"].get("content") or "").strip()
                text = re.sub(r"^<think>.*?</think>\s*", "", text, flags=re.S)
                rec.update(text=text, finish_reason=choice.get("finish_reason"),
                           truncated=choice.get("finish_reason") == "length",
                           prompt_tokens=data.get("usage", {}).get("prompt_tokens"),
                           completion_tokens=data.get("usage", {}).get("completion_tokens"))
                break
            except Exception as e:  # noqa: BLE001
                rec["error"] = str(e)[:300]
                time.sleep(5 * (attempt + 1))
        else:
            pass
        if "text" in rec:
            rec.pop("error", None)
        with lock:
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            counter["n"] += 1
            if counter["n"] % 25 == 0:
                print(f"  {counter['n']}/{len(todo)}", flush=True)

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(read, todo))
    print("done", flush=True)


if __name__ == "__main__":
    main()
