"""Manual Windows WebGPU acceptance on a synthetic local InvenTrack store.

Run: python scripts/verify_qwen_webgpu.py
This does NOT test a mobile GPU or the production Render deployment.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"

def wait_healthy(process):
    for _ in range(40):
        if process.poll() is not None:
            raise RuntimeError("Test FastAPI server exited during startup")
        try:
            with urllib.request.urlopen(URL + "/api/health", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise TimeoutError("Test FastAPI server did not respond")

def main():
    with tempfile.TemporaryDirectory(prefix="inventrack-qwen-e2e-") as directory:
        env = dict(os.environ)
        env.pop("DATABASE_URL", None)
        env["INVENTRACK_DB"] = str(pathlib.Path(directory) / "synthetic.sqlite3")
        env["INVENTRACK_AI_KEY_FILE"] = str(pathlib.Path(directory) / "ai.key")
        server = subprocess.Popen([sys.executable, "-m", "uvicorn",
            "backend.app:app", "--host", "127.0.0.1", "--port", "8765"],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        try:
            wait_healthy(server)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                try:
                    page = browser.new_page(viewport={"width": 1366, "height": 850})
                    page.on("pageerror", lambda error: print("PAGE ERROR:", str(error)[:240]))
                    page.goto(URL, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_function("Boolean(window.invenQwen && window.aiLoadSettings)", timeout=15000)
                    adapter = page.evaluate("""async () => {
                        const gpu = navigator.gpu;
                        const device = gpu && await gpu.requestAdapter();
                        return { webgpu: !!gpu, adapter: !!device,
                            description: device && device.info ? device.info.description : '' };
                    }""")
                    print("WebGPU:", json.dumps(adapter))
                    if not adapter["adapter"]:
                        raise RuntimeError("No WebGPU adapter in headless Edge on this machine")

                    account = {
                        "email": "qwen-" + uuid.uuid4().hex[:10] + "@example.com",
                        "mobile": "9876543210",
                        "password": "ValidPass123!",
                        "businessName": "Synthetic Qwen Test Shop",
                        "businessType": "Supermart"
                    }
                    registration = page.evaluate("""async (owner) => {
                        const response = await fetch('/api/auth/register', {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(owner)
                        });
                        return {status: response.status, detail: (await response.text()).slice(0, 100)};
                    }""", account)
                    if registration["status"] != 201:
                        raise RuntimeError("Synthetic owner registration failed: " + str(registration["status"]))
                    page.reload(wait_until="domcontentloaded")
                    page.wait_for_function("Boolean(window.invenQwen && window.aiLoadSettings)", timeout=15000)
                    page.evaluate("""async () => {
                        await window.aiLoadSettings();
                        document.getElementById('ai-bot-model-staff').value = 'browser-qwen';
                        await window.aiSaveBot('staff');
                    }""")
                    selected = page.evaluate("window.aiUsesLocalBot('staff')")
                    print("Local model selected:", selected)
                    if not selected:
                        raise RuntimeError("Qwen model selection was not retained")
                    context = page.evaluate("""async () => {
                        const response = await fetch('/api/ai/context?bot=staff');
                        return {status: response.status, body: await response.json()};
                    }""")
                    if context["status"] != 200:
                        raise RuntimeError("Store context could not be retrieved")
                    if "Synthetic Qwen Test Shop" not in context["body"]["instruction"]:
                        raise RuntimeError("Owner context missing or incorrect")
                    print("Owner-scoped context: PASS")
                    page.evaluate("""(context) => {
                        window.__qwenProgress = [];
                        window.__qwenResult = null;
                        window.invenQwen.onStatus = (state, details) => {
                            if (window.__qwenProgress.length < 100)
                                window.__qwenProgress.push({state, percent: details.percent, file: details.file});
                        };
                        window.invenQwen.generate(context, 'Respond in one short sentence: what is InvenTrack?')
                            .then(text => window.__qwenResult = {success: true, text})
                            .catch(err => window.__qwenResult = {success: false, error: err.message});
                    }""", context["body"]["instruction"])
                    started = time.monotonic()
                    previous = ""
                    while time.monotonic() - started < 210:
                        page.wait_for_timeout(1000)
                        status = page.evaluate("""() => ({
                            result: window.__qwenResult,
                            recent: (window.__qwenProgress || []).slice(-1)[0] || null
                        })""")
                        if status["result"] is not None:
                            print("Qwen result:", json.dumps(status["result"]))
                            print("Elapsed seconds:", round(time.monotonic() - started, 1))
                            if not status["result"]["success"] or not status["result"].get("text"):
                                raise RuntimeError("Real local inference failed")
                            print("REAL QWEN BROWSER GENERATION: PASS")
                            return
                        current = str(status["recent"])
                        if current != previous:
                            print("Status:", current)
                            previous = current
                    raise TimeoutError("Real Qwen browser generation exceeded acceptance timeout")
                finally:
                    browser.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()

if __name__ == "__main__":
    main()
