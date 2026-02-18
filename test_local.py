#!/usr/bin/env python3
"""
Local end-to-end test for the agent.

Usage:
  1. Start the agent:
     OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.openai.com/v1 uvicorn main:app --port 8000

     Or with the gateway:
     OPENAI_BASE_URL=http://localhost:9000/openai/v1 uvicorn main:app --port 8000

  2. Run this test:
     python test_local.py
"""

import json
import sys

import httpx

AGENT_URL = "http://localhost:8000"

# A realistic login page HTML
LOGIN_HTML = """
<html>
<head><title>Demo Store - Login</title></head>
<body>
  <h1>Welcome to Demo Store</h1>
  <nav>
    <a href="/">Home</a>
    <a href="/products">Products</a>
    <a href="/login">Login</a>
  </nav>
  <form id="login-form" action="/login" method="post">
    <label for="email">Email</label>
    <input type="email" id="email" name="email" placeholder="Enter your email" required>
    <label for="password">Password</label>
    <input type="password" id="password" name="password" placeholder="Enter your password" required>
    <button type="submit" id="login-btn">Log In</button>
  </form>
  <a href="/register">Don't have an account? Register</a>
</body>
</html>
"""

TASK = {
    "id": "test-login-001",
    "instruction": "Log in to the demo store with email 'user@test.com' and password 'secret123'",
    "url": "http://demostore.com/login",
    "tests": [
        {"type": "url", "url": "/dashboard", "method": "url_matches"},
        {"type": "text", "text": "Welcome back", "method": "text_contains"},
    ],
}


def main():
    print("=" * 60)
    print("Local Agent Test")
    print("=" * 60)

    # 1. Health check
    print("\n[1] Health check...")
    try:
        r = httpx.get(f"{AGENT_URL}/health", timeout=5)
        r.raise_for_status()
        print(f"    OK: {r.json()}")
    except Exception as e:
        print(f"    FAIL: {e}")
        print("    Is the agent running? Start it with:")
        print("    OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.openai.com/v1 uvicorn main:app --port 8000")
        sys.exit(1)

    # 2. Step 0 — first action
    print("\n[2] Step 0 — sending login page...")
    try:
        r = httpx.post(
            f"{AGENT_URL}/act",
            json={
                "task": TASK,
                "snapshot_html": LOGIN_HTML,
                "url": "http://demostore.com/login",
                "step_index": 0,
                "history": [],
            },
            timeout=120,
        )
        r.raise_for_status()
        actions = r.json()
        print(f"    Response: {json.dumps(actions, indent=2)}")

        if actions:
            action = actions[0]
            print(f"    Action type: {action.get('type')}")
            print(f"    XPath: {action.get('xpath', 'N/A')}")
            print(f"    Text: {action.get('text', 'N/A')}")

            # Validate it makes sense
            atype = action.get("type")
            if atype == "fill" and "email" in action.get("xpath", ""):
                print("    PASS: Agent correctly chose to fill the email field first")
            elif atype == "fill" and "password" in action.get("xpath", ""):
                print("    PASS: Agent chose to fill password field")
            elif atype == "navigate":
                print(f"    INFO: Agent navigated to {action.get('url')}")
            else:
                print(f"    INFO: Agent chose {atype} — review if this makes sense")
        else:
            print("    WARN: Agent returned NOOP on step 0")

    except httpx.TimeoutException:
        print("    TIMEOUT: LLM call took too long. Check your OPENAI_BASE_URL and API key.")
        sys.exit(1)
    except Exception as e:
        print(f"    FAIL: {e}")
        sys.exit(1)

    # 3. Step 1 — simulate that email was filled, now what?
    print("\n[3] Step 1 — email filled, what's next?")
    html_after_fill = LOGIN_HTML.replace(
        'placeholder="Enter your email"',
        'placeholder="Enter your email" value="user@test.com"',
    )
    try:
        r = httpx.post(
            f"{AGENT_URL}/act",
            json={
                "task": TASK,
                "snapshot_html": html_after_fill,
                "url": "http://demostore.com/login",
                "step_index": 1,
                "history": [
                    {"step": 0, "action": "fill", "text": "user@test.com", "exec_ok": True, "error": None}
                ],
            },
            timeout=120,
        )
        r.raise_for_status()
        actions = r.json()
        print(f"    Response: {json.dumps(actions, indent=2)}")

        if actions:
            action = actions[0]
            atype = action.get("type")
            if atype == "fill" and "password" in action.get("xpath", ""):
                print("    PASS: Agent correctly fills password next")
            elif atype == "click":
                print("    INFO: Agent clicked something — check if form is ready")
            else:
                print(f"    INFO: Agent chose {atype}")
        else:
            print("    WARN: Agent returned NOOP")

    except Exception as e:
        print(f"    FAIL: {e}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("Test complete! Review the actions above.")
    print("=" * 60)


if __name__ == "__main__":
    main()
