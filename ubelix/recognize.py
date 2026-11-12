"""Read page images with a fine-tuned VLM (base + LoRA) under vLLM, offline, on UBELIX.

    python ubelix/recognize.py --images DIR --out FILE.jsonl \\
        --base allenai/olmOCR-2-7B-1025 --adapter DIR --prompt-file FILE \\
        [--max-pixels 4194304] [--shard 0 --shards 4] [--strip-front-matter]

One JSON line per page: {"key": <image stem>, "text": ..., "finish_reason": ...,
"truncated": bool, "model": ..., "settings": ...}. Pages already in FILE are
skipped, so a job that was preempted or ran out of time resumes by starting
again. Pages are assigned to shards by a stable hash of their name, so shards
never overlap and adding pages to DIR later only adds work.

The prompt is the model's own (the one it was trained and served with), given
as a file so it is versioned beside the readings it produced.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import time
import zlib
from pathlib import Path


def shard_of(name: str, shards: int) -> int:
    return zlib.crc32(name.encode()) % shards


def strip_front_matter(text: str) -> str:
    """olmOCR answers with a YAML front matter block before the text."""
    return re.sub(r"\A\s*---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.S).strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--model-id", required=True, help="name recorded in the readings")
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--max-pixels", type=int, default=None)
    ap.add_argument("--max-new-tokens", type=int, default=4096)
    ap.add_argument("--max-model-len", type=int, default=16384)
    ap.add_argument("--max-num-seqs", type=int, default=32)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--strip-front-matter", action="store_true")
    args = ap.parse_args()

    from vllm import LLM, SamplingParams
    from vllm.lora.request import LoRARequest

    prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    digest = hashlib.sha256(prompt.encode()).hexdigest()[:8]
    settings = f"prompt-{digest}|px{args.max_pixels}|max{args.max_new_tokens}"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("settings") == settings and "text" in rec:
                done.add(rec["key"])

    images = sorted(p for p in Path(args.images).iterdir()
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".tif", ".tiff"))
    todo = [p for p in images if shard_of(p.stem, args.shards) == args.shard and p.stem not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{args.model_id} shard {args.shard}/{args.shards}: {len(done)} done, {len(todo)} to read", flush=True)
    if not todo:
        return

    rank = json.loads((Path(args.adapter) / "adapter_config.json").read_text())["r"]
    mm_kwargs = {"max_pixels": args.max_pixels} if args.max_pixels else None
    llm = LLM(model=args.base, enable_lora=True, max_lora_rank=max(8, rank),
              max_model_len=args.max_model_len, max_num_seqs=args.max_num_seqs,
              limit_mm_per_prompt={"image": 1}, mm_processor_kwargs=mm_kwargs,
              gpu_memory_utilization=0.90, seed=0)
    lora = LoRARequest(args.model_id, 1, args.adapter)
    params = SamplingParams(temperature=0.0, max_tokens=args.max_new_tokens)

    t0, n = time.time(), 0
    for i in range(0, len(todo), args.batch):
        chunk = todo[i:i + args.batch]
        convs = []
        for p in chunk:
            mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
            b64 = base64.b64encode(p.read_bytes()).decode()
            convs.append([{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": prompt}]}])
        results = llm.chat(convs, params, lora_request=lora, use_tqdm=False)
        with out.open("a", encoding="utf-8") as fh:
            for p, res in zip(chunk, results):
                o = res.outputs[0]
                text = o.text
                if args.strip_front_matter:
                    text = strip_front_matter(text)
                fh.write(json.dumps({"key": p.stem, "text": text.strip(),
                                     "finish_reason": o.finish_reason,
                                     "truncated": o.finish_reason == "length",
                                     "model": args.model_id, "settings": settings},
                                    ensure_ascii=False) + "\n")
        n += len(chunk)
        rate = n / max(1e-9, time.time() - t0)
        print(f"  {n}/{len(todo)} pages, {rate * 60:.1f} pages/min", flush=True)


if __name__ == "__main__":
    main()
