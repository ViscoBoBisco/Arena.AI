"""Exercise one live Ollama task through an isolated Conflux Compose project."""

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


CONFLUX = "e5a1c761f612a28ddccee037261a6071379787a4"
MERIDIAN = "3282944eeb4265d9a07caac0dbf8502528bccce0"
PROJECT = "conflux-qualification"
OLLAMA = "http://127.0.0.1:11434"
HTTP = build_opener(ProxyHandler({}))


def run(*command, **kwargs):
    return subprocess.check_output(command, text=True, **kwargs).strip()


def http(url, body=None, headers=None, timeout=8):
    data = None if body is None else json.dumps(body).encode()
    request = Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    with HTTP.open(request, timeout=timeout) as response:
        return response.status, json.load(response)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def publishable_receipts(receipts):
    results = []
    for receipt in receipts:
        data = dict(receipt["data"])
        completion = data.pop("completion", None)
        if completion is not None:
            data["completion_sha256"] = sha256(completion.encode())
        results.append({**receipt, "data": data})
    return results


def token():
    while True:
        value = secrets.token_hex(32)
        if not any(word in value for word in ("test", "replace", "dev-key", "dev-secret")):
            return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("conflux_source", type=Path)
    parser.add_argument("meridian_repo", type=Path)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--model", default="phi4-mini:3.8b-q4_K_M")
    args = parser.parse_args()
    source = args.conflux_source.resolve()
    manifest = json.loads(Path(__file__).with_name("source-blobs.json").read_text())
    for path, expected in manifest.items():
        assert run("git", "hash-object", str(source / path)) == expected, path
    readme = subprocess.check_output(
        ["git", "-C", str(args.meridian_repo), "show", f"{MERIDIAN}:README.md"])
    excerpt = readme.decode().split("\n\n")[1].replace("\n", " ")
    prompt = (
        "Read the excerpt below. Copy only the clause that starts with the word "
        "'suggestions' and ends with 'work'. Return it verbatim on one line.\n\n" + excerpt
    )
    clause_start = excerpt.index("suggestions")
    clause_end = excerpt.index("work.", clause_start) + len("work")
    expected = excerpt[clause_start:clause_end]
    tag = next(model for model in http(OLLAMA + "/api/tags")[1]["models"] if model["name"] == args.model)
    evidence = {
        "status": "IN_PROGRESS", "conflux_commit": CONFLUX,
        "verified_source_blobs": len(manifest),
        "meridian_source": {"commit": MERIDIAN, "path": "README.md", "paragraph": 1,
                            "sha256": sha256(readme), "excerpt_sha256": sha256(excerpt.encode()),
                            "prompt_sha256": sha256(prompt.encode())},
        "ollama": {"version": http(OLLAMA + "/api/version")[1]["version"],
                   "model": args.model, "digest": tag["digest"]},
        "docker_version": run("docker", "version", "--format", "{{.Server.Version}}"),
        "compose_version": run("docker", "compose", "version", "--short"),
        "provider_dispatches": [],
    }
    allowed_ip = None

    class Relay(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.client_address[0] != allowed_ip or self.path != "/api/chat":
                self.send_error(403)
                return
            nbytes = int(self.headers.get("Content-Length", "0"))
            if not 0 < nbytes <= 8192 or self.headers.get("Authorization"):
                self.send_error(400)
                return
            body = json.loads(self.rfile.read(nbytes))
            if (body.get("model") != args.model or body.get("stream") is not False
                    or body.get("messages") != [{"role": "user", "content": prompt}]):
                self.send_error(400)
                return
            entry = {"request_model": body["model"], "max_output_tokens": body["options"]["num_predict"]}
            evidence["provider_dispatches"].append(entry)
            if len(evidence["provider_dispatches"]) != 1:
                self.send_error(409)
                return
            try:
                status, result = http(OLLAMA + "/api/chat", body, timeout=80)
            except HTTPError as exc:
                entry["upstream_status"] = exc.code
                self.send_error(exc.code)
                return
            entry.update({key: result.get(key) for key in (
                "model", "done", "done_reason", "prompt_eval_count", "eval_count", "total_duration")})
            entry["upstream_status"] = status
            entry["completion_sha256"] = sha256(result["message"]["content"].encode())
            data = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    bridge = run("docker", "network", "inspect", "bridge", "--format", "{{(index .IPAM.Config 0).Gateway}}")
    run_id = "confluxqual-" + secrets.token_hex(5)
    environment = {**os.environ}
    for name in ("COMPOSE_PROFILES", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "CONFLUX_MODEL_BILLABLE",
                 "CONFLUX_MODEL_INPUT_COST_PER_MILLION", "CONFLUX_MODEL_OUTPUT_COST_PER_MILLION"):
        environment.pop(name, None)
    evidence["compose_project"] = run_id
    with tempfile.TemporaryDirectory(prefix="conflux-qualification-") as temp:
        temp = Path(temp)
        api_key = token()
        (temp / "keys.json").write_text(json.dumps({api_key: {"projects": [PROJECT],
                                                             "grants": ["model.infer", "ops.read"]}}))
        (temp / "callback").write_text(token())
        (temp / "empty-model-key").touch()
        for name in ("keys.json", "callback", "empty-model-key"):
            (temp / name).chmod(0o444)
        override = temp / "override.yml"
        override.write_text(
            "services:\n  gateway:\n"
            f"    image: {run_id}:local\n"
            "    ports: !override ['127.0.0.1::8080']\n"
            "    restart: 'no'\n"
        )
        compose = ("docker", "compose", "--project-directory", str(source), "-p", run_id,
                   "-f", str(source / "docker-compose.yml"), "-f", str(override))
        with ThreadingHTTPServer((bridge, 0), Relay) as relay:
            thread = threading.Thread(target=relay.serve_forever, daemon=True)
            thread.start()
            environment.update(
                CALLBACK_SECRET_FILE=str(temp / "callback"),
                UNIFIED_API_KEYS_FILE=str(temp / "keys.json"),
                CONFLUX_MODEL_API_KEY_FILE=str(temp / "empty-model-key"),
                UNIFIED_WEB_ALLOWLIST="[]", CONFLUX_MODEL_PROVIDER="ollama",
                CONFLUX_MODEL_NAME=args.model, CONFLUX_MODEL_BILLABLE="false",
                CONFLUX_MODEL_BASE_URL=f"http://host.docker.internal:{relay.server_port}")
            try:
                dockerfile = (source / "Dockerfile").read_text()
                base_image = "python:3.11-slim"
                cached = subprocess.run(["docker", "image", "inspect", base_image],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
                if not cached:
                    base_image = "python:3.12-slim"
                    dockerfile = dockerfile.replace("FROM python:3.11-slim\n", f"FROM {base_image}\n", 1)
                evidence["base_image"] = {"tag": base_image, "id": run(
                    "docker", "image", "inspect", base_image, "--format", "{{.Id}}")}
                evidence["dockerfile_sha256"] = sha256(dockerfile.encode())
                run("docker", "build", "--network", "none", "-q", "-t", f"{run_id}:local",
                    "-f", "-", str(source), input=dockerfile, timeout=90)
                evidence["gateway_image_id"] = run(
                    "docker", "image", "inspect", f"{run_id}:local", "--format", "{{.Id}}")
                run(*compose, "config", "--quiet", env=environment)
                run(*compose, "up", "-d", "--no-build", "--pull", "never", env=environment, timeout=30)
                container = run(*compose, "ps", "-q", "gateway", env=environment)
                allowed_ip = run("docker", "inspect", container, "--format",
                                 "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}")
                address = run(*compose, "port", "gateway", "8080", env=environment)
                assert address.startswith("127.0.0.1:")
                base = "http://" + address
                evidence["gateway_python"] = run("docker", "exec", container, "python3", "--version")
                headers = {"Authorization": "Bearer " + api_key, "X-Project-Id": PROJECT}

                def wait_ready():
                    for _ in range(60):
                        try:
                            if http(base + "/readyz", timeout=1)[0] == 200:
                                return
                        except OSError:
                            time.sleep(0.25)
                    raise RuntimeError("Gateway readiness timed out")

                def get(path):
                    return http(base + path, headers=headers)[1]

                def committed_receipts(task_id):
                    query = (
                        "import json,sqlite3,sys\n"
                        "with sqlite3.connect('file:/data/ledger.db?mode=ro', uri=True) as db:\n"
                        " print(json.dumps(db.execute('SELECT committed, receipt FROM receipts "
                        "WHERE task_id = ?', (sys.argv[1],)).fetchall()))\n"
                    )
                    rows = json.loads(run("docker", "exec", container, "python3", "-c", query, task_id))
                    return [(committed, json.loads(receipt)) for committed, receipt in rows]

                wait_ready()
                try:
                    http(base + "/v1/tasks")
                except HTTPError as exc:
                    assert exc.code == 401
                    evidence["unauthenticated_tasks_http"] = exc.code
                else:
                    raise AssertionError("Unauthenticated task list was accessible")
                payload = {
                    "idempotency_key": "meridian-readme-extract-v1",
                    "request": {"intent": "model_complete", "instructions": prompt},
                    "permissions": ["model.infer"],
                    "budgets": {"tool_calls": 1, "model_calls": 1, "model_tokens": 256,
                                "time_seconds": 90, "concurrency": 1, "cpu_seconds": 10, "memory_mb": 256},
                }
                status, submitted = http(base + "/v1/tasks", payload, headers)
                evidence["submission"] = {"http_status": status, **submitted}
                assert status == 202
                task_id = submitted["task_id"]
                path = f"/v1/tasks/{task_id}"
                assert submitted["status_url"] == path
                deadline = time.monotonic() + 100
                while True:
                    task = get(path)
                    if task["state"] in {"COMPLETED", "FAILED", "CANCELLED", "REJECTED", "RECOVERY_REQUIRED"}:
                        break
                    assert time.monotonic() < deadline, "Task completion timed out"
                    time.sleep(0.5)
                evidence["task_state"] = task["state"]
                evidence["receipts"] = publishable_receipts(task["receipts"])
                evidence["attempts"] = [{key: a[key] for key in (
                    "attempt_id", "attempt_number", "state", "dispatched_at", "finished_at")}
                    for a in task["attempts"]]
                assert task["state"] == "COMPLETED", task["state"]
                assert len(task["attempts"]) == 1
                receipt, = task["receipts"]
                assert receipt["status"] == "succeeded" and receipt["result_type"] == "model.complete.v1"
                assert committed_receipts(task_id) == [(1, receipt)]
                assert receipt["data"]["completion"].strip().rstrip(".") == expected
                evidence["completion_matches_source"] = True
                upstream, = evidence["provider_dispatches"]
                assert upstream["upstream_status"] == 200 and upstream["done"] is True
                assert upstream["completion_sha256"] == sha256(receipt["data"]["completion"].encode())
                usage = receipt["usage"]
                assert usage["provider"] == "ollama" and usage["usage_source"] == "provider"
                assert usage["token_consumption_uncertain"] is False and "cost_usd" not in usage
                assert usage["input_tokens"] == upstream["prompt_eval_count"] > 0
                assert usage["output_tokens"] == upstream["eval_count"] > 0
                assert usage["model_tokens"] == usage["input_tokens"] + usage["output_tokens"]
                metrics = get("/v1/metrics")
                evidence["metrics_before_restart"] = metrics
                for key in ("model_calls", "provider_calls", "attempts", "receipt_count", "unpriced_provider_calls"):
                    assert metrics[key] == 1, key
                assert metrics["provider_reported_tokens"] == usage["model_tokens"]
                assert metrics["priced_provider_calls"] == metrics["uncertain_token_consumption_calls"] == 0
                before = run("docker", "inspect", container, "--format", "{{.State.StartedAt}}")
                evidence["gateway_before_restart"] = {"started_at": before, "address": address}
                run(*compose, "restart", "gateway", env=environment, timeout=30)
                allowed_ip = run("docker", "inspect", container, "--format",
                                 "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}")
                address = run(*compose, "port", "gateway", "8080", env=environment)
                assert address.startswith("127.0.0.1:")
                base = "http://" + address
                after = run("docker", "inspect", container, "--format", "{{.State.StartedAt}}")
                assert before != after
                evidence["gateway_after_restart"] = {"started_at": after, "address": address}
                wait_ready()
                assert get(path)["receipts"] == [receipt]
                assert committed_receipts(task_id) == [(1, receipt)]
                status, replay = http(base + "/v1/tasks", payload, headers)
                evidence["duplicate_submission"] = {"http_status": status, **replay}
                assert status == 200 and replay["duplicate"] and replay["task_id"] == task_id
                evidence["metrics_after_restart_and_duplicate"] = get("/v1/metrics")
                assert evidence["metrics_after_restart_and_duplicate"] == metrics
                assert len(evidence["provider_dispatches"]) == 1
                audit = get("/v1/audit/verify")
                evidence["audit"] = audit
                assert audit["intact"] and audit["checked"] > 0
                evidence["status"] = "PASS"
            except BaseException as exc:
                evidence["status"] = "FAIL"
                evidence["error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                relay.shutdown()
                thread.join()
                cleanup = subprocess.run((*compose, "down", "--volumes", "--remove-orphans"),
                                         env=environment, capture_output=True, text=True, timeout=35)
                removed = subprocess.run(("docker", "image", "rm", f"{run_id}:local"),
                                         capture_output=True, text=True, timeout=15)
                evidence["cleanup"] = {"compose_exit": cleanup.returncode, "image_exit": removed.returncode}
                args.evidence.write_text(json.dumps(evidence, indent=2) + "\n")
                if cleanup.returncode:
                    print(cleanup.stderr)
                print(evidence["status"], args.evidence)


if __name__ == "__main__":
    main()
