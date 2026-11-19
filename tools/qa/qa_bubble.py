"""The Fill button, both hosts.

The button itself is a page's worth of JavaScript and is checked in a real
browser; this file checks the two things Python owns — the script Sushi's own
browser injects, and the door the Chrome extension knocks on. The bridge gets
the most attention because it is the only part of Sushi that listens on a
socket, and the only part a web page could reach uninvited.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, fn) -> None:
    try:
        RESULTS.append(("ok", name, str(fn() or "")[:140]))
    except Exception as e:                                  # noqa: BLE001
        RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e}"))


def post(port: int, path: str, body: dict, token: str, method: str = "POST"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode() if method == "POST" else None,
        method=method,
        headers={"Content-Type": "application/json", "X-Sushi-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> int:
    from sushi import bridge, bubble

    def p_script():
        s = bubble.script(always=True, label="Fill this application")
        assert "window.__sushiHost" in s, "no host attached"
        assert "window.__sushiOutbox.push" in s, "host does not queue"
        assert "__sushiBubble" in s, "button missing"
        assert "always: true" in s, "always flag not passed through"
        # The host has to be defined before the button looks for it.
        assert s.index("__sushiHost") < s.index("const HOST"), "host comes too late"
        return f"{len(s)} chars, host then button"
    check("button: the injected script is host-then-button", p_script)

    def p_drain():
        # The drain must empty the queue, or one click fills forever.
        js = bubble.drain_script()
        assert "window.__sushiOutbox = []" in js, "queue is never emptied"
        assert "JSON.stringify" in js, "not returning a string"
        return "takes and clears"
    check("button: draining the queue empties it", p_drain)

    seen: list[tuple[str, str]] = []
    port = bridge.start(lambda url, title: seen.append((url, title)))
    tok = bridge.token()

    def p_listening():
        assert port, "no port"
        assert bridge.running(), "not running"
        status, body = post(port, "/ping", {}, tok, method="GET")
        assert status == 200 and body.get("ok"), (status, body)
        return f"listening on 127.0.0.1:{port}"
    check("bridge: answers on loopback", p_listening)

    def p_fill():
        status, body = post(port, "/fill",
                            {"url": "https://example.com/jobs/1", "title": "Analyst"},
                            tok)
        assert status == 200 and body.get("ok"), (status, body)
        assert seen == [("https://example.com/jobs/1", "Analyst")], seen
        return f"handler got {seen[0][0]}"
    check("bridge: a press reaches the app", p_fill)

    def p_bad_token():
        before = len(seen)
        status, _ = post(port, "/fill", {"url": "https://evil.example/x"}, "not-the-token")
        assert status == 403, status
        # The point of the token: the request must not have *done* anything.
        assert len(seen) == before, "a bad token still reached the handler"
        return "403, and the handler was never called"
    check("bridge: a page without the token gets nowhere", p_bad_token)

    def p_bad_url():
        status, _ = post(port, "/fill", {"url": "file:///C:/Windows/System32"}, tok)
        assert status == 400, status
        status, _ = post(port, "/fill", {"url": ""}, tok)
        assert status == 400, status
        return "file:// and empty both refused"
    check("bridge: only http links are taken", p_bad_url)

    def p_routes():
        status, _ = post(port, "/anything", {}, tok)
        assert status == 404, status
        return "no other route acts"
    check("bridge: nothing else is exposed", p_routes)

    def p_stop():
        bridge.stop()
        assert not bridge.running(), "still running"
        try:
            post(port, "/ping", {}, tok, method="GET")
        except Exception:
            return "closed, and the port no longer answers"
        raise AssertionError("port still answering after stop()")
    check("bridge: stops with the window", p_stop)

    def p_build():
        import tempfile
        out = Path(tempfile.mkdtemp(prefix="sushi-ext-")) / "extension"
        bridge.build_extension(out)
        names = {p.name for p in out.iterdir()}
        assert {"manifest.json", "host.js", "bubble.js", "background.js",
                "config.js", "options.html", "options.js"} <= names, names
        # The button must be the *same* button, not a second copy that drifts.
        assert (out / "bubble.js").read_text(encoding="utf-8") == bubble.source(), \
            "the extension's button is not the app's button"
        json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        cfg = (out / "config.js").read_text(encoding="utf-8")
        assert "SUSHI_CONFIG" in cfg and str(bridge.port()) in cfg, "config not written"
        # And the host script has to run before the button, in the manifest too.
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        js = manifest["content_scripts"][0]["js"]
        assert js.index("host.js") < js.index("bubble.js"), js
        return f"{len(names)} files, button byte-identical to the app's"
    check("extension: generated whole, sharing one button", p_build)

    width = max(len(n) for _, n, _ in RESULTS)
    for status, name, detail in RESULTS:
        print(f"  {status:<5} {name:<{width}}  {detail}")
    failed = sum(1 for s, _, _ in RESULTS if s != "ok")
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
