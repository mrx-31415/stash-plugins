#!/usr/bin/env python3
"""Probe a RunPod serverless Qwen-Image-Edit endpoint with one of the PoC's own images.

Viability test only, not a pipeline stage. The published worker
(github.com/wlsdml1114/qwen_image_edit) exposes a fixed ComfyUI graph whose only tunables are
prompt, seed and one to three input images -- there is no mask input, so the two masked edits
(identity, clothes) cannot run against it as published. What it can answer is whether the
mask-free stages produce comparable output for less money than a continuously running pod, and
what the fixed graph does to canvas geometry.

Default test is the skin recolor: same image, same prompt, same seed as the local run, so
skin-tone.png is a direct baseline.
"""

import argparse
import base64
import json
import math
import time
from pathlib import Path
from urllib import error, request

from PIL import Image, ImageChops, ImageStat

import layered_costume_production as production
import run_qwen2512_skin_head_clothes_poc as poc

# The worker scales every input through ImageScaleToTotalPixels(megapixels=1) before encoding, so
# the output canvas is decided by the graph, not by us. The `width`/`height` input fields the
# README documents as required are read by the handler but written to nodes 128/129, which the
# shipped workflows do not contain -- they are silently ignored.
WORKER_MEGAPIXELS = 1.0
# Named so a caller can ask for the exact stage the local pipeline ran, and get the same seed.
PROMPTS = {
    "skin": (poc.SKIN_PROMPT, "qwen2512:skin-tone"),
    "preprocess": (poc.PREPROCESS_PROMPT, "qwen2512:preprocess"),
    "identity": (poc.IDENTITY_PROMPT, "qwen2512:identity-head"),
    "clothes": (poc.CLOTHES_PROMPT, "qwen2512:clothes-victorian"),
}
# /runsync holds the connection open for at most ~90s before handing back a job id, so a cold
# start or a long edit always needs the polling path as well.
POLL_INTERVAL = 5
POLL_TIMEOUT = 900


def worker_canvas(size):
    """What ImageScaleToTotalPixels(megapixels=1, resolution_steps=1) will do to this input.

    Reproduces the node exactly (comfy_extras/nodes_post_processing.py): the budget is
    megapixels * 1024 * 1024, not 1e6. 832x1248 is 1038336 px, so the worker scales *up* by
    1.00492 to 836x1254 -- a full lanczos resample of every pixel and a canvas the PoC's
    composite cannot stack. Predicted here so a mismatch against the real output is caught as a
    wrong model of the worker rather than shrugged off.
    """
    width, height = size
    scale = math.sqrt(WORKER_MEGAPIXELS * 1024 * 1024 / (width * height))
    return round(width * scale), round(height * scale)


def encode(path):
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def post(url, api_key, payload, timeout):
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}",
                 "User-Agent": "CoverStoryServerless/1.0"},
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read())
    except error.HTTPError as exc:
        raise RuntimeError(f"{url.rsplit('/', 1)[-1]} -> HTTP {exc.code}: {exc.read()[:400].decode('utf-8', 'replace')}")


def get(url, api_key, timeout):
    req = request.Request(url, headers={"Authorization": f"Bearer {api_key}",
                                        "User-Agent": "CoverStoryServerless/1.0"})
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())


def submit(endpoint_id, api_key, payload):
    """Return the finished job envelope, following the async path when /runsync times out."""
    base = f"https://api.runpod.ai/v2/{endpoint_id}"
    started = time.time()
    result = post(f"{base}/runsync", api_key, payload, 180)
    while result.get("status") in ("IN_QUEUE", "IN_PROGRESS"):
        job = result.get("id")
        if not job:
            raise RuntimeError(f"endpoint returned {result.get('status')} with no job id: {result}")
        if time.time() - started > POLL_TIMEOUT:
            raise RuntimeError(f"job {job} still {result['status']} after {POLL_TIMEOUT}s")
        time.sleep(POLL_INTERVAL)
        result = get(f"{base}/status/{job}", api_key, 60)
        print(f"  {round(time.time() - started)}s {result.get('status')}", flush=True)
    if result.get("status") != "COMPLETED":
        raise RuntimeError(f"job {result.get('status')}: {json.dumps(result)[:600]}")
    return result, round(time.time() - started, 1)


def decode(envelope, output):
    """The worker returns {"image": <base64>} for the first output node that produced one."""
    body = envelope.get("output")
    if not isinstance(body, dict):
        raise RuntimeError(f"unexpected output shape: {json.dumps(envelope)[:600]}")
    if "error" in body:
        raise RuntimeError(f"worker error: {body['error']}")
    if "image" not in body:
        raise RuntimeError(f"no image in output; keys were {sorted(body)}")
    output.write_bytes(base64.b64decode(body["image"]))
    return output


def compare(source, remote, baseline):
    """Geometry first: the worker's own rescale decides the canvas, and every downstream stage in
    the PoC composites by exact pixel coordinates. A canvas change is disqualifying on its own, so
    report it plainly before the resized similarity numbers, which exist only to say whether the
    picture is the same picture."""
    with Image.open(source) as opened:
        source_size = opened.size
    with Image.open(remote) as opened:
        image = opened.convert("RGB")
    predicted = worker_canvas(source_size)
    rows = [
        {"name": "serverless_preserves_canvas", "passed": image.size == source_size,
         "detail": {"input": list(source_size), "output": list(image.size)}},
        {"name": "canvas_matches_prediction", "passed": image.size == predicted,
         "detail": {"predicted": list(predicted), "observed": list(image.size)}},
    ]
    if baseline and Path(baseline).is_file():
        with Image.open(baseline) as opened:
            local = opened.convert("RGB")
        scaled = image if image.size == local.size else image.resize(local.size, Image.Resampling.LANCZOS)
        difference = sum(ImageStat.Stat(ImageChops.difference(scaled, local)).mean) / 3
        # Same prompt and seed on a different graph: the Lightning 4-step LoRA and shift=3 make an
        # identical result impossible, so this only distinguishes "same subject, same framing" from
        # "a different picture". The PoC's own collapse gate uses 20 on this scale.
        rows.append({"name": "matches_local_baseline", "passed": difference < 20,
                     "detail": {"mean_abs_diff": round(difference, 2), "baseline": str(baseline),
                                "resized_for_comparison": image.size != local.size}})
    return rows, image


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=sorted(PROMPTS), default="skin",
                        help="reuse a PoC prompt and its seed (default: skin)")
    parser.add_argument("--prompt", help="override the stage prompt")
    parser.add_argument("--seed", type=int, help="override the stage seed")
    parser.add_argument("--image", help="first image; default is the run's carrier.png")
    parser.add_argument("--image-2", help="optional second reference")
    parser.add_argument("--image-3", help="optional third reference")
    parser.add_argument("--output", help="where to write the returned PNG")
    parser.add_argument("--baseline", help="local image to compare against; default is the run's own stage output")
    parser.add_argument("--endpoint", help="RunPod serverless endpoint id")
    parser.add_argument("--api-key", help="RunPod API key")
    parser.add_argument("--output-dir", help="run root holding carrier.png (default: config output_dir)")
    args = parser.parse_args()

    config = poc.load_config(poc.CONFIG_PATH)
    sources = {}
    resolve = poc.resolver(config, poc.CONFIG_PATH, sources)
    endpoint_id = resolve("runpod_endpoint", args.endpoint, "RUNPOD_ENDPOINT")
    api_key = resolve("runpod_api_key", args.api_key, "RUNPOD_API_KEY")
    root = Path(resolve("output_dir", args.output_dir, "COVER_STORY_OUTPUT_DIR", str(poc.DEFAULT_ROOT)))
    missing = [name for name, value in (("runpod_endpoint", endpoint_id), ("runpod_api_key", api_key)) if not value]
    if missing:
        raise SystemExit(f"missing {', '.join(missing)}; add them to {poc.CONFIG_PATH} "
                         f"(run the PoC with --init-config if it does not exist yet)")

    prompt, seed_label = PROMPTS[args.stage]
    prompt = args.prompt or prompt
    seed = args.seed if args.seed is not None else production.seed_for(seed_label)
    source = Path(args.image) if args.image else root / "carrier.png"
    default_baseline = {"skin": "skin-tone.png", "preprocess": "preprocessed.png",
                        "identity": "identity.png", "clothes": "clothes.png"}[args.stage]
    baseline = Path(args.baseline) if args.baseline else root / default_baseline
    out_dir = root / "serverless"
    out_dir.mkdir(parents=True, exist_ok=True)
    output = Path(args.output) if args.output else out_dir / f"{args.stage}.png"

    if not source.is_file():
        raise SystemExit(f"missing input image {source}")
    print(f"endpoint  {endpoint_id} (from {sources['runpod_endpoint']})")
    print(f"api key   {'*' * 8} (from {sources['runpod_api_key']})")
    print(f"stage     {args.stage}  seed {seed}")
    print(f"image     {source}")
    print(f"prompt    {prompt}")

    payload = {"input": {"prompt": prompt, "seed": seed, "image_base64": encode(source)}}
    for flag, key in ((args.image_2, "image_base64_2"), (args.image_3, "image_base64_3")):
        if flag:
            payload["input"][key] = encode(flag)
    print(f"posting   {round(len(json.dumps(payload)) / 1e6, 2)} MB", flush=True)

    envelope, elapsed = submit(endpoint_id, api_key, payload)
    decode(envelope, output)
    rows, image = compare(source, output, baseline)
    record = {
        "stage": args.stage, "prompt": prompt, "seed": seed,
        "source": str(source), "output": str(output),
        "delay_seconds": envelope.get("delayTime", 0) / 1000 if envelope.get("delayTime") else None,
        "execution_seconds": envelope.get("executionTime", 0) / 1000 if envelope.get("executionTime") else None,
        "wall_seconds": elapsed,
        "worker_id": envelope.get("workerId"),
        "checks": rows,
    }
    (out_dir / f"{args.stage}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote     {output}  {image.size[0]}x{image.size[1]}")
    print(f"timing    queue {record['delay_seconds']}s  execute {record['execution_seconds']}s  wall {elapsed}s")
    for row in rows:
        print(f"  {'PASS' if row['passed'] else 'FAIL'} {row['name']}: {json.dumps(row['detail'])}")
    if not all(row["passed"] for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
