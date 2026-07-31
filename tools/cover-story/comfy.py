#!/usr/bin/env python3
"""Queue an API-format ComfyUI workflow and download its images."""

import argparse
import errno
import json
import os
import struct
import tempfile
import time
import uuid
from pathlib import Path
from urllib import error, parse, request


def retry_refused(operation, timeout, poll=5):
    deadline = time.monotonic() + timeout
    attempts = 0
    while True:
        try:
            return operation()
        except error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if getattr(reason, "errno", None) != errno.ECONNREFUSED or time.monotonic() >= deadline:
                raise
            if attempts % 12 == 0:
                print("ComfyUI connection refused; retrying…", flush=True)
            attempts += 1
            time.sleep(min(poll, max(0, deadline - time.monotonic())))


def endpoint(server, path, query=None):
    parts = parse.urlsplit(server)
    parameters = parse.parse_qsl(parts.query, keep_blank_values=True)
    if query:
        parameters.extend(query.items())
    return parse.urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path.rstrip("/") + path,
        parse.urlencode(parameters),
        parts.fragment,
    ))


def api(server, path, payload=None, timeout=30):
    data = None if payload is None else json.dumps(payload).encode()
    req = request.Request(endpoint(server, path), data=data)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read()
    except error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"ComfyUI returned HTTP {exc.code}: {body}") from exc
    return json.loads(body)


def upload_image(server, path, subfolder="cover-story/corridorkey", timeout=300):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in (("subfolder", subfolder), ("type", "input"), ("overwrite", "true")):
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{value}\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
        f"filename=\"{path.name}\"\r\nContent-Type: image/png\r\n\r\n".encode()
        + path.read_bytes() + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    req = request.Request(endpoint(server, "/upload/image"), data=b"".join(parts))
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    def send():
        with request.urlopen(req, timeout=60) as response:
            return json.loads(response.read())
    try:
        result = retry_refused(send, timeout)
    except error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"ComfyUI upload returned HTTP {exc.code}: {body}") from exc
    return "/".join(filter(None, (result.get("subfolder"), result["name"])))


def prepare(workflow, prompt, seed, filename_prefix=None):
    samplers = [node for node in workflow.values() if "KSampler" in node.get("class_type", "")]
    positive_ids = {
        str(node.get("inputs", {}).get("positive", [None])[0])
        for node in samplers
        if isinstance(node.get("inputs", {}).get("positive"), list)
    }
    if not samplers or len(positive_ids) != 1 or next(iter(positive_ids)) not in workflow:
        raise ValueError("samplers have no single resolvable positive prompt")
    prompt_node = workflow[next(iter(positive_ids))]
    prompt_key = next((key for key in ("text", "prompt") if key in prompt_node.get("inputs", {})), None)
    if not prompt_key:
        raise ValueError("positive prompt node has no text or prompt input")
    prompt_input = prompt_node["inputs"][prompt_key]
    if isinstance(prompt_input, list) and prompt_input and str(prompt_input[0]) in workflow:
        source = workflow[str(prompt_input[0])]["inputs"]
        source[next((key for key in ("value", "text", "prompt") if key in source), "value")] = prompt
    else:
        prompt_node["inputs"][prompt_key] = prompt
    for sampler in samplers:
        seed_input = sampler["inputs"].get("seed")
        if isinstance(seed_input, list) and seed_input and str(seed_input[0]) in workflow:
            workflow[str(seed_input[0])]["inputs"]["seed"] = seed
        else:
            sampler["inputs"]["seed"] = seed
    if filename_prefix:
        outputs = [node for node in workflow.values() if node.get("class_type") == "SaveImage"]
        if not outputs:
            raise ValueError("workflow has no SaveImage node")
        for node in outputs:
            node["inputs"]["filename_prefix"] = filename_prefix
    return workflow


def image_info(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return "PNG", *struct.unpack(">II", data[16:24])
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "WebP", None, None
    if data.startswith(b"\xff\xd8"):
        return "JPEG", None, None
    raise ValueError("response is not a recognized image")


def run(server, workflow, output_dir, timeout, poll=2, queued_event=None):
    queued = retry_refused(
        lambda: api(server, "/prompt", {"prompt": workflow}),
        timeout, poll,
    )
    prompt_id = queued.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI did not return a prompt_id: {queued}")
    if queued_event:
        queued_event.set()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            history = api(server, f"/history/{parse.quote(prompt_id)}")
        except (error.URLError, TimeoutError, ConnectionError):
            time.sleep(poll)
            continue
        if prompt_id in history:
            break
        time.sleep(poll)
    else:
        raise TimeoutError(f"prompt {prompt_id} did not finish within {timeout:g}s")

    entry = history[prompt_id]
    status = entry.get("status", {})
    if status.get("status_str") == "error":
        raise RuntimeError(f"ComfyUI execution failed: {status.get('messages', status)}")
    images = [
        image
        for output in entry.get("outputs", {}).values()
        for image in output.get("images", [])
    ]
    if not images:
        raise RuntimeError(f"prompt {prompt_id} completed without images")

    output_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for image in images:
        query = {
            "filename": image["filename"],
            "subfolder": image.get("subfolder", ""),
            "type": image.get("type", "output"),
        }
        for attempt in range(5):
            try:
                with request.urlopen(endpoint(server, "/view", query), timeout=30) as response:
                    data = response.read()
                break
            except error.HTTPError:
                raise
            except (error.URLError, TimeoutError, ConnectionError):
                if attempt == 4:
                    raise
                time.sleep(poll)
        kind, width, height = image_info(data)
        target = output_dir / Path(image["filename"]).name
        if target.exists():
            raise FileExistsError(f"refusing to overwrite {target}")
        with tempfile.NamedTemporaryFile(dir=output_dir, delete=False) as tmp:
            tmp.write(data)
            temporary = Path(tmp.name)
        os.replace(temporary, target)
        saved.append({
            "path": str(target),
            "format": kind,
            "width": width,
            "height": height,
            "bytes": len(data),
            "remote": image,
        })
    return {"prompt_id": prompt_id, "images": saved}


def self_test():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    png = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 1, 1) + b"\x08\x02\0\0\0"
    attempts = iter((
        error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "refused")),
        "connected",
    ))
    def connect():
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result
    assert retry_refused(connect, 1, 0) == "connected"

    class Handler(BaseHTTPRequestHandler):
        mode = "success"
        failed_once = False

        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path.startswith("/upload/image"):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                assert b'name="image"' in body and b"test.png" in body
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"name":"test.png","subfolder":"cover-story/corridorkey","type":"input"}')
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["prompt"]["6"]["inputs"]["text"] == "test prompt"
            assert body["prompt"]["8"]["inputs"]["seed"] == 7
            if self.mode == "validation":
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":"invalid prompt"}')
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"prompt_id":"test-id"}')

        def do_GET(self):
            if self.mode == "flaky" and not self.failed_once:
                Handler.failed_once = True
                self.connection.close()
                return
            if self.path.startswith("/view?"):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(png)
                return
            history = {}
            if self.mode in {"success", "flaky"}:
                history = {"test-id": {"status": {"status_str": "success"}, "outputs": {
                    "11": {"images": [{"filename": "result.png", "subfolder": "", "type": "output"}]}
                }}}
            elif self.mode == "execution":
                history = {"test-id": {"status": {"status_str": "error", "messages": ["failed"]}}}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(history).encode())

    workflow = {
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "8": {"class_type": "KSampler", "inputs": {"positive": ["6", 0], "seed": 0}},
        "11": {"class_type": "SaveImage", "inputs": {"filename_prefix": "test"}},
    }
    prepared = prepare(workflow, "test prompt", 7)
    linked = {
        "1": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": ""}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": ["1", 0]}},
        "3": {"class_type": "Seed (rgthree)", "inputs": {"seed": -1}},
        "4": {"class_type": "ClownsharKSampler_Beta", "inputs": {"positive": ["2", 0], "seed": ["3", 0]}},
        "5": {"class_type": "ClownsharKSampler_Beta", "inputs": {"positive": ["2", 0], "seed": ["3", 0]}},
        "6": {"class_type": "SaveImage", "inputs": {"filename_prefix": ""}},
    }
    prepare(linked, "linked prompt", 9, "linked")
    assert linked["1"]["inputs"]["value"] == "linked prompt"
    assert linked["3"]["inputs"]["seed"] == 9
    assert linked["6"]["inputs"]["filename_prefix"] == "linked"
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}?token=test"
    try:
        with tempfile.TemporaryDirectory() as directory:
            upload = Path(directory) / "test.png"
            upload.write_bytes(png)
            assert upload_image(url, upload) == "cover-story/corridorkey/test.png"
            queued_event = threading.Event()
            result = run(url, prepared, Path(directory), 1, 0.01, queued_event)
            assert queued_event.is_set()
            assert result["images"][0]["width"] == 1
            try:
                run(url, prepared, Path(directory), 1, 0.01)
            except FileExistsError:
                pass
            else:
                raise AssertionError("existing output was overwritten")
        Handler.mode = "validation"
        try:
            run(url, prepared, Path(tempfile.gettempdir()), 1, 0.01)
        except RuntimeError as exc:
            assert "HTTP 400" in str(exc)
        else:
            raise AssertionError("validation error was ignored")
        Handler.mode = "execution"
        try:
            run(url, prepared, Path(tempfile.gettempdir()), 1, 0.01)
        except RuntimeError as exc:
            assert "execution failed" in str(exc)
        else:
            raise AssertionError("execution error was ignored")
        Handler.mode = "flaky"
        Handler.failed_once = False
        with tempfile.TemporaryDirectory() as directory:
            assert run(url, prepared, Path(directory), 1, 0.01)["images"]
        Handler.mode = "timeout"
        try:
            run(url, prepared, Path(tempfile.gettempdir()), 0.01, 0.001)
        except TimeoutError:
            pass
        else:
            raise AssertionError("timeout was ignored")
    finally:
        server.shutdown()
    print("self-test passed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server")
    parser.add_argument("--workflow", type=Path)
    parser.add_argument("--prompt")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--filename-prefix")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    missing = [name for name in ("server", "workflow", "prompt", "seed", "output_dir") if getattr(args, name) is None]
    if missing:
        parser.error("required unless --self-test: " + ", ".join(f"--{name.replace('_', '-')}" for name in missing))
    workflow = json.loads(args.workflow.read_text())
    prepared = prepare(workflow, args.prompt, args.seed, args.filename_prefix)
    print(json.dumps(run(args.server, prepared, args.output_dir, args.timeout), indent=2))


if __name__ == "__main__":
    main()
