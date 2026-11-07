#!/usr/bin/env python3
"""CLI client for the unified gateway (manual 2.1: CLI as a gateway client).

Usage:
  python3 cli.py submit '{"intent":"echo","instructions":"hi"}' [key]
  python3 cli.py tools
  python3 cli.py task <task_id>
"""
import json
import sys
import urllib.request

BASE = "http://localhost:8080"


def _req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json", "Authorization": "Bearer dev-key"})
    with urllib.request.urlopen(r) as resp:
        return json.loads(resp.read())


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tools"
    if cmd == "tools":
        print(json.dumps(_req("GET", "/v1/tools"), indent=2))
    elif cmd == "submit":
        request = json.loads(sys.argv[2])
        key = sys.argv[3] if len(sys.argv) > 3 else "cli-" + str(abs(hash(json.dumps(request))) % 10**8)
        out = _req("POST", "/v1/tasks", {"project_id": "cli", "idempotency_key": key,
                                         "request": request, "permissions": []})
        print(json.dumps(out, indent=2))
    elif cmd == "task":
        print(json.dumps(_req("GET", f"/v1/tasks/{sys.argv[2]}"), indent=2))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
