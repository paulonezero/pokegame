#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
CHROME = (
    os.environ.get("CHROME_BIN")
    or shutil.which("google-chrome")
    or shutil.which("chromium")
    or shutil.which("chromium-browser")
    or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)


def wait_json(url: str, timeout: float = 15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return json.load(response)
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"Timed out waiting for {url}")


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def main() -> int:
    profile = Path("/tmp/pokegame-chrome-smoke")
    backend_port = available_port()
    debug_port = available_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    shutil.rmtree(profile, ignore_errors=True)
    backend = subprocess.Popen(
        [str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "server.app:app", "--port", str(backend_port)],
        cwd=ROOT,
        env={
            **os.environ,
            "POKEGAME_LEADERBOARD_DB_PATH": str(profile / "leaderboard.sqlite3"),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    chrome = None
    try:
        wait_json(f"{backend_url}/api/state", timeout=150.0)
        chrome = subprocess.Popen(
            [
                CHROME,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                f"--remote-debugging-port={debug_port}",
                "--remote-allow-origins=*",
                f"--user-data-dir={profile}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        targets = wait_json(f"http://127.0.0.1:{debug_port}/json")
        target = next(item for item in targets if item["type"] == "page")
        messages: list[dict] = []
        counter = 0

        with connect(target["webSocketDebuggerUrl"], origin="http://127.0.0.1:9222") as socket:
            def command(method: str, params: dict | None = None):
                nonlocal counter
                counter += 1
                current = counter
                socket.send(json.dumps({"id": current, "method": method, "params": params or {}}))
                while True:
                    payload = json.loads(socket.recv())
                    if payload.get("id") == current:
                        return payload
                    messages.append(payload)

            command("Page.enable")
            command("Runtime.enable")
            command("Log.enable")

            results = []
            for width, height in ((402, 874), (834, 1086)):
                command(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": True},
                )
                command("Page.navigate", {"url": f"{backend_url}/"})
                time.sleep(1.0)
                command(
                    "Runtime.evaluate",
                    {
                        "expression": "(()=>{const input=document.querySelector('#username');if(input){const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;setter.call(input,'Smoke Player');input.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('.username-form')?.requestSubmit();}})()"
                    },
                )
                command("Page.reload")
                time.sleep(1.5)
                measurement = command(
                    "Runtime.evaluate",
                    {
                        "expression": "JSON.stringify((()=>{const s=document.querySelector('.stage')?.getBoundingClientRect();const a=document.querySelector('.app')?.getBoundingClientRect();return {screen:!!document.querySelector('.play-screen'),stage:s&&{width:s.width,height:s.height,top:s.top,bottom:s.bottom},app:a&&{width:a.width,height:a.height},answers:document.querySelectorAll('.answer-row').length}})())",
                        "returnByValue": True,
                    },
                )
                value = measurement["result"]["result"]["value"]
                result = json.loads(value)
                submission = command(
                    "Runtime.evaluate",
                    {
                        "expression": "(async()=>{const state=await fetch('/api/state').then(r=>r.json());await fetch('/api/round/guess',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answer_id:state.question.target_id})});await fetch('/api/round/expire',{method:'POST'});return true})()",
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                )
                command("Page.reload")
                time.sleep(2.0)
                board = command(
                    "Runtime.evaluate",
                    {
                        "expression": "(async()=>JSON.stringify({screen:!!document.querySelector('.leaderboard-screen'),rows:document.querySelectorAll('.leaderboard-row:not(.leaderboard-row--head)').length,current:!!document.querySelector('.leaderboard-row.is-current'),callout:!!document.querySelector('.rank-callout'),result:!!document.querySelector('.result-screen'),play:!!document.querySelector('.play-screen'),state:await fetch('/api/state').then(r=>r.json())}))()",
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                )
                result["leaderboard"] = json.loads(board["result"]["result"]["value"])
                result["submission"] = submission.get("result", {}).get("result", {}).get("value")
                results.append((width, height, result))
                command(
                    "Runtime.evaluate",
                    {
                        "expression": "fetch('/api/player/logout',{method:'POST'}).finally(()=>window.localStorage.clear())",
                        "awaitPromise": True,
                    },
                )

            errors = [
                item
                for item in messages
                if item.get("method") in {"Runtime.exceptionThrown", "Log.entryAdded"}
                and (
                    item.get("method") == "Runtime.exceptionThrown"
                    or item.get("params", {}).get("entry", {}).get("level") == "error"
                )
            ]

        for width, height, result in results:
            stage = result.get("stage")
            if not result["screen"] or result["answers"] != 4:
                raise AssertionError(f"Round did not render at {width}×{height}: {result}")
            if stage is None or abs(stage["width"] - stage["height"]) > 1:
                raise AssertionError(f"Stage is not square at {width}×{height}: {stage}")
            expected = 366 if width == 402 else 462
            if abs(stage["width"] - expected) > 5:
                raise AssertionError(
                    f"Stage does not match the {expected}pt target at {width}×{height}: {stage}"
                )
            board = result["leaderboard"]
            if not board["screen"] or not board["rows"] or not board["current"] or not board["callout"]:
                raise AssertionError(f"Qualifying leaderboard did not render at {width}×{height}: {board}")
            print(f"{width}x{height}: stage={stage['width']:.1f}x{stage['height']:.1f}, answers={result['answers']}")
        if errors:
            raise AssertionError(f"Browser errors: {errors}")
        print("browser console errors: 0")
        return 0
    finally:
        if chrome is not None:
            chrome.terminate()
            try:
                chrome.wait(timeout=5)
            except subprocess.TimeoutExpired:
                chrome.kill()
        backend.terminate()
        try:
            backend.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
