#!/usr/bin/env python3
"""Serve a local browser UI for reviewing generated Cover Story headshots."""

import argparse
import json
import mimetypes
import os
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from PIL import Image


INDEX = r"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cover Story headshot review</title>
<style>
  :root { color-scheme: dark; font: 15px/1.4 system-ui, sans-serif; background: #111; color: #eee; }
  * { box-sizing: border-box; }
  body { margin: 0; }
  header { position: sticky; top: 0; z-index: 2; padding: .8rem 1rem; background: #181818ee; backdrop-filter: blur(8px); border-bottom: 1px solid #333; }
  h1 { font-size: 1.1rem; margin: 0 0 .65rem; }
  .tools { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
  select, input, button, textarea { font: inherit; color: inherit; background: #242424; border: 1px solid #444; border-radius: .35rem; }
  select, input, button { padding: .45rem .65rem; }
  input { min-width: 16rem; }
  button { cursor: pointer; }
  button:disabled { cursor: default; opacity: .5; }
  #summary { margin-left: auto; color: #bbb; }
  .auto-keep { display: flex; gap: .4rem; align-items: center; color: #bbb; white-space: nowrap; }
  .auto-keep input { min-width: 0; }
  main { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 1rem; padding: 1rem; }
  article { overflow: hidden; background: #1b1b1b; border: 2px solid #333; border-radius: .6rem; }
  article.keep { border-color: #3ca96b; } article.maybe { border-color: #d39a35; } article.reject { opacity: .58; border-color: #b34b4b; }
  article img { display: block; width: 100%; aspect-ratio: 2/3; object-fit: cover; background: #222; }
  .body { padding: .7rem; }
  h2 { margin: 0; font-size: .9rem; overflow-wrap: anywhere; }
  .meta { color: #aaa; font-size: .8rem; margin: .2rem 0 .6rem; }
  .choices { display: grid; grid-template-columns: repeat(4, 1fr); gap: .35rem; }
  .choices button.active { color: #111; font-weight: 700; }
  .choices [data-status=keep].active { background: #59d58c; }
  .choices [data-status=maybe].active { background: #efb953; }
  .choices [data-status=reject].active { background: #e36d6d; }
  .apparent-age { display: flex; gap: .5rem; align-items: center; margin-top: .55rem; color: #bbb; font-size: .8rem; }
  .apparent-age input { width: 6rem; min-width: 0; }
  textarea { width: 100%; min-height: 4.5rem; resize: vertical; margin-top: .55rem; padding: .5rem; }
  details { margin-top: .45rem; color: #bbb; font-size: .8rem; }
  details div { white-space: pre-wrap; overflow-wrap: anywhere; margin-top: .35rem; }
  #empty { grid-column: 1/-1; text-align: center; color: #aaa; padding: 4rem; }
  #pair { display: none; min-height: calc(100vh - 5.5rem); padding: 1rem; flex-direction: column; gap: .7rem; }
  body.pairing #grid { display: none; }
  body.pairing #pair { display: flex; }
  .pair-tools { display: flex; flex-wrap: wrap; gap: .5rem; justify-content: center; align-items: center; }
  #pair-progress { min-width: 12rem; text-align: center; color: #bbb; }
  #pair-stage { flex: 1; min-height: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  .pair-choice { min-width: 0; min-height: 0; width: 100%; padding: 0; overflow: hidden; display: flex; flex: 1; flex-direction: column; background: #1b1b1b; border: 3px solid #444; }
  .pair-choice:hover, .pair-choice:focus-visible { border-color: #ddd; outline: none; }
  .pair-choice.winner { border-color: #59d58c; }
  .pair-choice.tie { border-color: #efb953; }
  .pair-choice.neither { border-color: #e36d6d; }
  .pair-choice img { width: 100%; min-height: 0; flex: 1; object-fit: contain; background: #080808; }
  .pair-label { padding: .55rem; font-weight: 700; }
  #pair-stage.single .pair-choice { display: none; }
  #pair-stage.single .pair-choice.active-side { display: flex; grid-column: 1/-1; }
  #pair-comments { position: fixed; z-index: 4; right: 1rem; bottom: 1rem; left: 1rem; display: flex; flex-direction: column; align-items: end; gap: .5rem; }
  #pair-comment-fields { width: 100%; padding: .65rem; display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; background: #181818ee; border: 1px solid #555; border-radius: .5rem; box-shadow: 0 .5rem 1.5rem #000a; backdrop-filter: blur(8px); }
  #pair-comment-fields[hidden] { display: none; }
  #pair-comment-fields label { display: grid; gap: .25rem; font-weight: 700; }
  #pair-comment-fields textarea { min-height: 3rem; margin: 0; }
  #pair:fullscreen { width: 100%; height: 100%; padding: 1rem; background: #111; }
  #pair:fullscreen .pair-choice img { max-height: calc(100vh - 5rem); }
  @media (max-width: 700px) {
    #pair-stage { grid-template-columns: 1fr; }
    .pair-choice img { max-height: 42vh; }
  }
</style>
<header>
  <h1>Cover Story headshot review</h1>
  <div class="tools">
    <select id="group" aria-label="Experiment folder"></select>
    <select id="status" aria-label="Review status">
      <option value="">All statuses</option><option value="unreviewed">Unreviewed</option>
      <option value="keep">Keep</option><option value="keep-missing-age">Keep missing age</option>
      <option value="maybe">Maybe</option><option value="reject">Reject</option>
    </select>
    <input id="search" type="search" placeholder="Filter filename or prompt">
    <label class="auto-keep"><input id="auto-keep" type="checkbox"> Auto-keep viewed</label>
    <button id="pair-mode">Pair review</button>
    <button id="refresh">Refresh</button>
    <span id="summary"></span>
  </div>
</header>
<main id="grid"><div id="empty">Loading…</div></main>
<main id="pair">
  <div class="pair-tools">
    <button id="pair-prev">Previous</button>
    <span id="pair-progress"></span>
    <button id="pair-next">Next</button>
    <button id="pair-tie">Tie ↑</button>
    <button id="pair-neither">Neither ↓</button>
    <button id="pair-layout">Single view S</button>
    <button id="pair-toggle">Toggle A/B Space</button>
    <button id="pair-fullscreen">Fullscreen F</button>
  </div>
  <div id="pair-stage"></div>
  <div id="pair-comments">
    <button id="pair-comments-toggle" aria-controls="pair-comment-fields" aria-expanded="false">Comments C</button>
    <div id="pair-comment-fields" hidden>
      <label>A <textarea data-side="A" aria-label="Comment for A" placeholder="Enter closes · Shift+Enter adds a line"></textarea></label>
      <label>B <textarea data-side="B" aria-label="Comment for B" placeholder="Enter closes · Shift+Enter adds a line"></textarea></label>
    </div>
  </div>
</main>
<script>
  const state = {
    items: [], reviews: {}, timers: new Map(), observer: null,
    pairs: [], pairIndex: 0, pairing: false, single: false, activeSide: "A", saving: false
  };
  const group = document.querySelector("#group"), status = document.querySelector("#status");
  const search = document.querySelector("#search"), grid = document.querySelector("#grid");
  const summary = document.querySelector("#summary"), autoKeep = document.querySelector("#auto-keep");
  const pair = document.querySelector("#pair"), pairStage = document.querySelector("#pair-stage");
  const pairProgress = document.querySelector("#pair-progress"), pairMode = document.querySelector("#pair-mode");
  const pairCommentsToggle = document.querySelector("#pair-comments-toggle");
  const pairCommentFields = document.querySelector("#pair-comment-fields");
  autoKeep.checked = localStorage.getItem("cover-story-auto-keep") === "true";

  async function load() {
    const selected = group.value;
    const groups = await fetch("/api/groups").then(r => r.json());
    group.replaceChildren(...groups.map(option => new Option(option.label, option.value)));
    group.value = groups.some(option => option.value === selected) ? selected : groups[0]?.value || "";
    if (!group.value) {
      state.items = []; state.reviews = {}; state.pairs = []; state.pairing = false; render(); return;
    }
    const data = await fetch(`/api/items?group=${encodeURIComponent(group.value)}`).then(r => r.json());
    state.items = data.items; state.reviews = data.reviews;
    buildPairs();
    state.pairing = /(?:^|-)ab(?:-|$)/i.test(group.value) && state.pairs.length > 0;
    state.pairIndex = Math.max(0, state.pairs.findIndex(pair => !pairResult(pair)));
    render();
  }

  async function save(path, review) {
    state.reviews[path] = review;
    renderSummary();
    const response = await fetch("/api/review", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path, ...review})
    });
    if (!response.ok) alert(await response.text());
  }

  function schedule(path, review) {
    clearTimeout(state.timers.get(path));
    state.timers.set(path, setTimeout(() => save(path, review), 500));
  }

  function renderSummary(visible) {
    const values = Object.values(state.reviews);
    const counts = Object.fromEntries(["keep","maybe","reject"].map(key => [key, values.filter(x => x.status === key).length]));
    summary.textContent = `${visible ?? state.items.length}/${state.items.length} shown · ${counts.keep} keep · ${counts.maybe} maybe · ${counts.reject} reject`;
  }

  function render() {
    document.body.classList.toggle("pairing", state.pairing);
    pairMode.textContent = state.pairing ? "Grid review" : `Pair review${state.pairs.length ? ` (${state.pairs.length})` : ""}`;
    if (state.pairing) {
      state.observer?.disconnect();
      renderPair();
      return;
    }
    state.observer?.disconnect();
    state.observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        const card = entry.target, {item, review, choices} = card.reviewData;
        if (entry.intersectionRatio >= .5) card.dataset.viewed = "true";
        if (autoKeep.checked && card.dataset.viewed
            && entry.boundingClientRect.bottom <= document.querySelector("header").offsetHeight
            && !review.status) {
          review.status = "keep";
          card.className = "keep";
          choices.querySelector("[data-status=keep]").classList.add("active");
          save(item.path, review);
        }
      });
    }, {threshold: [0, .5]});
    const query = search.value.trim().toLowerCase();
    const items = state.items.filter(item => {
      const review = state.reviews[item.path] || {};
      const statusMatches = !status.value
        || (status.value === "unreviewed" ? !review.status
          : status.value === "keep-missing-age" ? review.status === "keep" && review.apparent_age == null
          : review.status === status.value);
      return statusMatches
        && (!query || `${item.path} ${item.prompt}`.toLowerCase().includes(query));
    });
    grid.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("div"); empty.id = "empty"; empty.textContent = "No matching images.";
      grid.append(empty);
    }
    items.forEach(item => {
      const review = state.reviews[item.path] || {status: "", notes: ""};
      const card = document.createElement("article"); card.className = review.status || "";
      const link = document.createElement("a"); link.href = `/image/${encodeURIComponent(item.path)}`; link.target = "_blank";
      const image = document.createElement("img"); image.loading = "lazy"; image.src = link.href; image.alt = item.filename;
      link.append(image); card.append(link);
      const body = document.createElement("div"); body.className = "body";
      const title = document.createElement("h2"); title.textContent = item.filename; body.append(title);
      const meta = document.createElement("div"); meta.className = "meta";
      meta.textContent = `${item.group} · ${item.width ?? "?"}×${item.height ?? "?"} · seed ${item.seed ?? "?"} · ${item.steps ?? "?"} steps`;
      body.append(meta);
      const choices = document.createElement("div"); choices.className = "choices";
      [["keep","Keep"],["maybe","Maybe"],["reject","Reject"],["","Clear"]].forEach(([value,label]) => {
        const button = document.createElement("button"); button.dataset.status = value; button.textContent = label;
        button.classList.toggle("active", review.status === value && value !== "");
        button.addEventListener("click", () => {
          review.status = value; card.className = value; choices.querySelectorAll("button").forEach(x => x.classList.toggle("active", x.dataset.status === value && value !== ""));
          save(item.path, review);
        });
        choices.append(button);
      });
      body.append(choices);
      card.reviewData = {item, review, choices};
      const ageLabel = document.createElement("label"); ageLabel.className = "apparent-age";
      ageLabel.append("Apparent age");
      const age = document.createElement("input"); age.type = "number"; age.min = "18"; age.max = "90";
      age.value = review.apparent_age ?? "";
      age.addEventListener("input", () => {
        review.apparent_age = age.value === "" ? null : age.valueAsNumber;
        schedule(item.path, review);
      });
      ageLabel.append(age); body.append(ageLabel);
      const notes = document.createElement("textarea"); notes.placeholder = "Feedback…"; notes.value = review.notes || "";
      notes.addEventListener("input", () => { review.notes = notes.value; schedule(item.path, review); });
      body.append(notes);
      const details = document.createElement("details"), heading = document.createElement("summary");
      heading.textContent = "Prompt"; details.append(heading);
      const prompt = document.createElement("div"); prompt.textContent = item.prompt || "No embedded prompt metadata.";
      details.append(prompt); body.append(details); card.append(body); grid.append(card);
      state.observer.observe(card);
    });
    renderSummary(items.length);
  }

  function buildPairs() {
    const pairs = new Map();
    state.items.forEach(item => {
      const match = item.filename.match(/^(.*)_([AB])(__.*)?(\.[^.]+)$/);
      if (!match) return;
      const key = `${match[1]}${match[3] || ""}${match[4]}`;
      const entry = pairs.get(key) || {key};
      entry[match[2]] = item;
      pairs.set(key, entry);
    });
    state.pairs = [...pairs.values()].filter(pair => pair.A && pair.B).sort(
      (left, right) => (parseInt(left.A.filename) || 0) - (parseInt(right.A.filename) || 0)
    );
  }

  function pairResult(pair) {
    const a = state.reviews[pair.A.path]?.status || "";
    const b = state.reviews[pair.B.path]?.status || "";
    if (a === "keep" && b === "reject") return "A";
    if (a === "reject" && b === "keep") return "B";
    if (a === "maybe" && b === "maybe") return "tie";
    if (a === "reject" && b === "reject") return "neither";
    return "";
  }

  async function choose(aStatus, bStatus) {
    if (state.saving || !state.pairs.length) return;
    state.saving = true;
    const current = state.pairs[state.pairIndex];
    for (const side of ["A", "B"]) clearTimeout(state.timers.get(current[side].path));
    const review = item => ({...(state.reviews[item.path] || {}), notes: state.reviews[item.path]?.notes || ""});
    const aReview = {...review(current.A), status: aStatus};
    const bReview = {...review(current.B), status: bStatus};
    try {
      await Promise.all([save(current.A.path, aReview), save(current.B.path, bReview)]);
      const next = state.pairs.findIndex((pair, index) => index > state.pairIndex && !pairResult(pair));
      if (next >= 0) state.pairIndex = next;
      renderPair();
    } finally {
      state.saving = false;
    }
  }

  function movePair(offset) {
    if (!state.pairs.length) return;
    state.pairIndex = (state.pairIndex + offset + state.pairs.length) % state.pairs.length;
    renderPair();
  }

  function setSingle(value) {
    state.single = value;
    pairStage.classList.toggle("single", value);
    document.querySelector("#pair-layout").textContent = value ? "Side by side S" : "Single view S";
    pairStage.querySelectorAll(".pair-choice").forEach(button =>
      button.classList.toggle("active-side", button.dataset.side === state.activeSide)
    );
  }

  function toggleImage() {
    if (!state.single) setSingle(true);
    else {
      state.activeSide = state.activeSide === "A" ? "B" : "A";
      setSingle(true);
    }
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else pair.requestFullscreen();
  }

  function setComments(open) {
    pairCommentFields.hidden = !open;
    pairCommentsToggle.setAttribute("aria-expanded", open);
  }

  function toggleComments() {
    setComments(pairCommentFields.hidden);
  }

  function renderPair() {
    pairStage.replaceChildren();
    if (!state.pairs.length) {
      pairProgress.textContent = "No complete A/B pairs";
      return;
    }
    const current = state.pairs[state.pairIndex], result = pairResult(current);
    const decided = state.pairs.filter(pairResult).length;
    pairProgress.textContent = `${state.pairIndex + 1}/${state.pairs.length} · ${decided} decided${result ? ` · ${result}` : ""}`;
    pairCommentFields.querySelectorAll("textarea").forEach(notes => {
      notes.value = state.reviews[current[notes.dataset.side].path]?.notes || "";
    });
    pairCommentsToggle.textContent =
      [...pairCommentFields.querySelectorAll("textarea")].some(notes => notes.value) ? "Comments • C" : "Comments C";
    for (const side of ["A", "B"]) {
      const item = current[side];
      const button = document.createElement("button");
      button.className = `pair-choice${result === side ? " winner" : ["tie", "neither"].includes(result) ? ` ${result}` : ""}`;
      button.dataset.side = side;
      button.title = `Choose ${side}`;
      const image = document.createElement("img");
      image.src = `/image/${encodeURIComponent(item.path)}`;
      image.alt = item.filename;
      const label = document.createElement("span");
      label.className = "pair-label";
      label.textContent = `${side} · ${item.filename}`;
      button.append(image, label);
      button.addEventListener("click", () => choose(side === "A" ? "keep" : "reject", side === "B" ? "keep" : "reject"));
      pairStage.append(button);
    }
    setSingle(state.single);
    renderSummary();
  }

  group.addEventListener("change", load); status.addEventListener("change", render);
  autoKeep.addEventListener("change", () => localStorage.setItem("cover-story-auto-keep", autoKeep.checked));
  search.addEventListener("input", render); document.querySelector("#refresh").addEventListener("click", load);
  pairMode.addEventListener("click", () => {
    state.pairing = !state.pairing && state.pairs.length > 0;
    render();
  });
  document.querySelector("#pair-prev").addEventListener("click", () => movePair(-1));
  document.querySelector("#pair-next").addEventListener("click", () => movePair(1));
  document.querySelector("#pair-tie").addEventListener("click", () => choose("maybe", "maybe"));
  document.querySelector("#pair-neither").addEventListener("click", () => choose("reject", "reject"));
  document.querySelector("#pair-layout").addEventListener("click", () => setSingle(!state.single));
  document.querySelector("#pair-toggle").addEventListener("click", toggleImage);
  document.querySelector("#pair-fullscreen").addEventListener("click", toggleFullscreen);
  pairCommentsToggle.addEventListener("click", toggleComments);
  pairCommentFields.querySelectorAll("textarea").forEach(notes => notes.addEventListener("input", () => {
    const item = state.pairs[state.pairIndex][notes.dataset.side];
    const review = {...(state.reviews[item.path] || {}), notes: notes.value};
    state.reviews[item.path] = review;
    schedule(item.path, review);
    pairCommentsToggle.textContent =
      [...pairCommentFields.querySelectorAll("textarea")].some(field => field.value) ? "Comments • C" : "Comments C";
  }));
  document.addEventListener("keydown", event => {
    if (!state.pairing) return;
    if (event.key === "Escape") {
      event.preventDefault();
      setComments(false);
      return;
    }
    if (event.key === "Enter" && event.target.matches("#pair-comment-fields textarea") && !event.shiftKey) {
      event.preventDefault();
      event.target.blur();
      setComments(false);
      return;
    }
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
    const actions = {
      ArrowLeft: () => choose("keep", "reject"),
      ArrowRight: () => choose("reject", "keep"),
      ArrowUp: () => choose("maybe", "maybe"),
      ArrowDown: () => choose("reject", "reject"),
      n: () => movePair(1),
      N: () => movePair(1),
      p: () => movePair(-1),
      P: () => movePair(-1),
      " ": toggleImage,
      c: toggleComments,
      C: toggleComments,
      r: load,
      R: load,
      s: () => setSingle(!state.single),
      S: () => setSingle(!state.single),
      f: toggleFullscreen,
      F: toggleFullscreen,
    };
    if (actions[event.key]) {
      event.preventDefault();
      actions[event.key]();
    }
  });
  load().catch(error => { grid.textContent = error; });
</script>
</html>
"""

IMAGE_SUFFIXES = {".png", ".webp", ".avif", ".jpg", ".jpeg"}
STATUSES = {"", "keep", "maybe", "reject"}


def valid_apparent_age(value):
    return value is None or (
        isinstance(value, int) and not isinstance(value, bool) and 18 <= value <= 90
    )


def metadata(path):
    with Image.open(path) as image:
        width, height = image.size
        prompt_graph = json.loads(image.info.get("prompt", "{}"))
    samplers = [node for node in prompt_graph.values() if "KSampler" in node.get("class_type", "")]
    sampler = samplers[0].get("inputs", {}) if samplers else {}
    positive = sampler.get("positive", [None])[0]
    node = prompt_graph.get(str(positive), {})
    text = next((node.get("inputs", {}).get(key) for key in ("text", "prompt") if node.get("inputs", {}).get(key)), "")
    if isinstance(text, list):
        text = prompt_graph.get(str(text[0]), {}).get("inputs", {}).get("value", "")
    seed = sampler.get("seed")
    if isinstance(seed, list):
        seed = prompt_graph.get(str(seed[0]), {}).get("inputs", {}).get("seed")
    steps = "+".join(str(node.get("inputs", {}).get("steps", "?")) for node in samplers) if len(samplers) > 1 else sampler.get("steps")
    return width, height, seed, steps, text


def groups(root):
    tops = sorted(
        (path for path in root.iterdir() if path.is_dir() and path.name != "@eaDir"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    options = []
    for top in tops:
        candidates = [top / name for name in ("assets", "raw", "qc", "corridorkey")]
        candidates += [
            top / screen / name
            for screen in ("green", "blue")
            for name in ("assets", "qc", "corridorkey")
        ]
        children = [
            path for path in candidates
            if path.is_dir()
            and any(
                image.is_file() and image.suffix.lower() in IMAGE_SUFFIXES
                for image in path.rglob("*")
            )
        ]
        for path in children or [top]:
            value = path.relative_to(root).as_posix()
            options.append({
                "value": value,
                "label": f"{top.name} - {path.relative_to(top).as_posix()}" if children else top.name,
            })
    return options


def scan(root, group=None):
    items = []
    folder = root / group if group else root
    for path in sorted(folder.rglob("*")):
        if path.is_file() and "@eaDir" not in path.parts and path.suffix.lower() in IMAGE_SUFFIXES:
            if path.suffix.lower() == ".avif":
                width = height = seed = steps = None
                prompt = ""
            else:
                try:
                    width, height, seed, steps, prompt = metadata(path)
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
            relative = path.relative_to(root).as_posix()
            items.append({
                "path": relative,
                "group": relative.split("/", 1)[0],
                "filename": path.name,
                "width": width,
                "height": height,
                "bytes": path.stat().st_size,
                "seed": seed,
                "steps": steps,
                "prompt": prompt,
            })
    return items


def read_reviews(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("reviews", {})


def write_review(path, image, status, notes, apparent_age=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": 1, "reviews": read_reviews(path)}
    data["reviews"][image] = {
        "status": status,
        "notes": notes,
        "apparent_age": apparent_age,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as temporary:
        json.dump(data, temporary, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def handler(root, reviews):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            print(f"{self.address_string()} - {format % args}")

        def send_bytes(self, body, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.send_bytes(INDEX.encode(), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/groups":
                self.send_bytes(json.dumps(groups(root)).encode(), "application/json")
                return
            if parsed.path == "/api/items":
                group = parse_qs(parsed.query).get("group", [""])[0]
                folder = (root / group).resolve()
                if not group or root not in folder.parents or not folder.is_dir():
                    self.send_error(400, "invalid group")
                    return
                with lock:
                    items = scan(root, group)
                    all_reviews = read_reviews(reviews)
                    body = {
                        "items": items,
                        "reviews": {item["path"]: all_reviews[item["path"]] for item in items if item["path"] in all_reviews},
                    }
                self.send_bytes(json.dumps(body).encode(), "application/json")
                return
            if parsed.path.startswith("/image/"):
                relative = unquote(parsed.path.removeprefix("/image/"))
                image = (root / relative).resolve()
                if root not in image.parents or not image.is_file() or image.suffix.lower() not in IMAGE_SUFFIXES:
                    self.send_error(404)
                    return
                self.send_bytes(image.read_bytes(), mimetypes.guess_type(image.name)[0] or "application/octet-stream")
                return
            self.send_error(404)

        def do_POST(self):
            if urlparse(self.path).path != "/api/review":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 1_000_000:
                    raise ValueError("request too large")
                data = json.loads(self.rfile.read(length))
                image, status, notes = data["path"], data.get("status", ""), data.get("notes", "")
                apparent_age = data.get("apparent_age")
                if status not in STATUSES or not isinstance(notes, str) or not valid_apparent_age(apparent_age):
                    raise ValueError("invalid review")
                candidate = (root / image).resolve()
                if root not in candidate.parents or not candidate.is_file():
                    raise ValueError("unknown image")
                with lock:
                    write_review(reviews, image, status, notes, apparent_age)
                self.send_bytes(b'{"ok":true}', "application/json")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self.send_bytes(str(exc).encode(), "text/plain; charset=utf-8", 400)

    return Handler


def self_test():
    from PIL.PngImagePlugin import PngInfo

    with tempfile.TemporaryDirectory() as directory:
        assert all(
            marker in INDEX
            for marker in (
                "Auto-keep viewed", "IntersectionObserver", "Pair review",
                "requestFullscreen", "ArrowLeft", "Toggle A/B",
                "pair-choice.tie", "pair-choice.neither", "pair-comments-toggle",
                "n: () => movePair(1)", "p: () => movePair(-1)",
                "r: load", 'event.key === "Escape"',
                'event.key === "Enter"', "!event.shiftKey",
            )
        )
        assert ".avif" in IMAGE_SUFFIXES
        assert all(valid_apparent_age(value) for value in (None, 18, 34, 90))
        assert not any(valid_apparent_age(value) for value in (True, 17, 91, 34.5, "34"))
        root = Path(directory)
        folder = root / "experiment"
        folder.mkdir()
        graph = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "test prompt"}},
            "2": {"class_type": "KSampler", "inputs": {"positive": ["1", 0], "seed": 7, "steps": 6}},
        }
        info = PngInfo()
        info.add_text("prompt", json.dumps(graph))
        Image.new("RGB", (2, 3)).save(folder / "01.png", pnginfo=info)
        thumbnail = folder / "@eaDir" / "01.png"
        thumbnail.mkdir(parents=True)
        Image.new("RGB", (1, 1)).save(thumbnail / "SYNOPHOTO_THUMB_XL.jpg")
        items = scan(root)
        assert len(items) == 1
        assert groups(root) == [{"value": "experiment", "label": "experiment"}]
        assert scan(root, "experiment") == items
        assert (items[0]["path"], items[0]["width"], items[0]["height"]) == ("experiment/01.png", 2, 3)
        assert items[0]["bytes"] > 0
        assert (items[0]["group"], items[0]["seed"], items[0]["steps"], items[0]["prompt"]) == ("experiment", 7, 6, "test prompt")
        assets = root / "assets"
        assets.mkdir()
        (assets / "performer-001.avif").write_bytes(b"avif")
        avif = scan(root, "assets")
        assert len(avif) == 1 and avif[0]["width"] is None
        production = root / "production"
        (production / "assets").mkdir(parents=True)
        (production / "raw").mkdir()
        (production / "green" / "qc").mkdir(parents=True)
        (production / "assets" / "performer-001.avif").write_bytes(b"avif")
        Image.new("RGB", (2, 3)).save(production / "raw" / "01.png", pnginfo=info)
        Image.new("RGB", (2, 3)).save(production / "green" / "qc" / "performer-001.png")
        options = groups(root)
        assert {"value": "production/assets", "label": "production - assets"} in options
        assert {"value": "production/raw", "label": "production - raw"} in options
        assert {
            "value": "production/green/qc", "label": "production - green/qc",
        } in options
        assert len(scan(root, "production/assets")) == 1
        reviews = root / "reviews.json"
        write_review(reviews, items[0]["path"], "keep", "good", 34)
        saved = read_reviews(reviews)[items[0]["path"]]
        assert (saved["notes"], saved["apparent_age"]) == ("good", 34)
    print("self-test passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/mnt/Misc/sd/cover-story/experiments"))
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")
    reviews = (args.reviews or root / "reviews.json").resolve()
    server = ThreadingHTTPServer((args.host, args.port), handler(root, reviews))
    print(f"Reviewing {root}")
    print(f"Writing feedback to {reviews}")
    print(f"Open http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
