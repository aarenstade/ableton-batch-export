"""App-bound macOS Accessibility and Quartz transport for Live 12."""

import os
import subprocess
import sys
import time
from pathlib import Path

if sys.platform == "darwin":
    import ApplicationServices as AX
    import Quartz as CG
    from AppKit import (
        NSApplicationActivateIgnoringOtherApps,
        NSRunningApplication,
        NSWorkspace,
    )
    from Foundation import NSDate, NSRunLoop


class Stopped(Exception):
    """The operator requested a clean stop."""


def attribute(element, name):
    """Read an optional Accessibility attribute."""
    error, value = AX.AXUIElementCopyAttributeValue(element, name, None)
    return value if error == 0 else None


class MacLive:
    """Control one Live process, respecting the configured foreground policy."""

    def __init__(self, config, status=None):
        if sys.platform != "darwin":
            raise RuntimeError("Native rendering requires macOS")
        if not AX.AXIsProcessTrusted():
            raise RuntimeError("macOS Accessibility permission is unavailable")
        self.config = config
        self.status = status or (lambda *args, **kwargs: None)
        self.paused_seconds = 0
        self.bind()

    def bind(self):
        """Bind the current Live process and bound each AX request."""
        self.refresh()
        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(
            "com.ableton.live"
        )
        if len(apps) != 1:
            raise RuntimeError(f"Expected one Live process; found {len(apps)}")
        self.app = apps[0]
        self.pid = self.app.processIdentifier()
        self.root = AX.AXUIElementCreateApplication(self.pid)
        AX.AXUIElementSetMessagingTimeout(self.root, 3.0)

    @staticmethod
    def refresh():
        NSRunLoop.currentRunLoop().runUntilDate_(
            NSDate.dateWithTimeIntervalSinceNow_(0.01)
        )

    def terminated(self):
        try:
            os.kill(self.pid, 0)
            return False
        except ProcessLookupError:
            return True

    def clock(self):
        """Return elapsed active time, excluding pauses for another foreground app."""
        return time.monotonic() - self.paused_seconds

    def check_stop(self):
        if (
            not getattr(self, "stopping", False)
            and (Path(self.config.runtime_state_root) / "STOP").exists()
        ):
            raise Stopped("Stop file requested")

    def activate(self):
        """Bring Live forward once when explicitly starting or relaunching a batch."""
        self.app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        time.sleep(0.4)

    def guard(self):
        """Wait for Live focus; events are additionally posted to its exact PID."""
        started = None
        while True:
            self.check_stop()
            self.refresh()
            if self.terminated():
                raise RuntimeError("Live exited")
            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            if front and front.processIdentifier() == self.pid:
                if started is not None:
                    self.paused_seconds += time.monotonic() - started
                    self.status("resumed")
                return
            if self.config.unattended_mode:
                if started is None:
                    started = time.monotonic()
                    self.status("bringing_live_forward")
                if time.monotonic() - started > 10:
                    raise RuntimeError(
                        "Could not bring Live forward for an export UI action"
                    )
                self.activate()
                continue
            if started is None:
                started = time.monotonic()
                self.status("paused_for_focus")
            time.sleep(0.5)

    def windows(self):
        """Read Live's windows without traversing the arrangement."""
        return list(attribute(self.root, "AXWindows") or [])

    def document(self):
        return next(
            (
                str(attribute(w, "AXDocument"))
                for w in self.windows()
                if attribute(w, "AXDocument")
            ),
            None,
        )

    def scan(self, root=None):
        """Snapshot control identities and values, retaining native element handles."""
        nodes = []
        deadline = time.monotonic() + 30
        stack = list(reversed(self.windows())) if root is None else [root]
        keys = {
            "role": "AXRole",
            "subrole": "AXSubrole",
            "title": "AXTitle",
            "id": "AXIdentifier",
            "description": "AXDescription",
            "value": "AXValue",
            "document": "AXDocument",
            "modal": "AXModal",
            "enabled": "AXEnabled",
        }
        while stack:
            self.check_stop()
            if time.monotonic() > deadline:
                raise TimeoutError("Accessibility scan exceeded 30 seconds")
            element = stack.pop()
            values = {key: attribute(element, name) for key, name in keys.items()}
            nodes.append(
                {
                    "element": element,
                    **{k: v for k, v in values.items() if v is not None},
                }
            )
            if len(nodes) > 25000:
                raise RuntimeError("Accessibility tree exceeds bounded traversal")
            stack.extend(reversed(list(attribute(element, "AXChildren") or [])))
        return nodes

    @staticmethod
    def find(nodes, **criteria):
        """Require exactly one control matching its observed attributes."""
        found = [n for n in nodes if all(n.get(k) == v for k, v in criteria.items())]
        if len(found) != 1:
            raise RuntimeError(f"Expected one control {criteria}; found {len(found)}")
        return found[0]

    @staticmethod
    def diagnostic(nodes):
        return [{k: str(v) for k, v in n.items() if k != "element"} for n in nodes]

    def press(self, node):
        self.guard()
        error = AX.AXUIElementPerformAction(node["element"], "AXPress")
        # Live may return cannot-complete while entering a native modal loop.
        # Callers verify the resulting dialog or document before the next action.
        if error not in (0, -25204):
            raise RuntimeError(
                f"AXPress failed ({error}): {node.get('id') or node.get('title')}"
            )
        time.sleep(0.12)

    def menu(self, title):
        nodes = self.scan(attribute(self.root, "AXMenuBar"))
        self.press(self.find(nodes, role="AXMenuItem", title=title))

    def set_text(self, node, value):
        self.guard()
        error, settable = AX.AXUIElementIsAttributeSettable(
            node["element"], "AXValue", None
        )
        if error or not settable:
            raise RuntimeError(f"Text field is not writable: {node.get('id')}")
        error = AX.AXUIElementSetAttributeValue(node["element"], "AXValue", str(value))
        if error or str(attribute(node["element"], "AXValue")) != str(value):
            raise RuntimeError("AX text write did not stick")

    def key(self, code, command=False, shift=False):
        """Send a guarded key to Live's PID, never to the global event stream."""
        self.guard()
        flags = (CG.kCGEventFlagMaskCommand if command else 0) | (
            CG.kCGEventFlagMaskShift if shift else 0
        )
        for down in (True, False):
            event = CG.CGEventCreateKeyboardEvent(None, code, down)
            CG.CGEventSetFlags(event, flags)
            CG.CGEventPostToPid(self.pid, event)
        time.sleep(0.15)

    def type_text(self, text):
        self.guard()
        for down in (True, False):
            event = CG.CGEventCreateKeyboardEvent(None, 0, down)
            CG.CGEventKeyboardSetUnicodeString(
                event, len(text.encode("utf-16-le")) // 2, text
            )
            CG.CGEventPostToPid(self.pid, event)
        time.sleep(0.1)

    def native_key(self, code, command=False, shift=False):
        """Use System Events for Cocoa save sheets, with a second PID focus guard."""
        self.guard()
        modifiers = [
            name + " down"
            for name, enabled in [("command", command), ("shift", shift)]
            if enabled
        ]
        suffix = " using {" + ", ".join(modifiers) + "}" if modifiers else ""
        script = (
            'tell application "System Events"\n'
            "set frontProcess to first application process whose frontmost is true\n"
            f'if (unix id of frontProcess) is not {self.pid} then error "Live lost focus"\n'
            f"tell frontProcess to key code {int(code)}{suffix}\n"
            "end tell"
        )
        subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            check=True,
            timeout=10,
            capture_output=True,
        )
        time.sleep(0.2)

    def focus(self, node):
        """Focus the exact control without moving or clicking the mouse."""
        self.guard()
        error = AX.AXUIElementSetAttributeValue(node["element"], "AXFocused", True)
        focused = attribute(self.root, "AXFocusedUIElement")
        if error or attribute(focused, "AXIdentifier") != node.get("id"):
            raise RuntimeError("Could not focus the expected numeric field")

    def number(self, identifier, value):
        node = self.find(self.scan(), id=identifier)
        if float(node["value"]) == float(value):
            return
        self.focus(node)
        self.type_text(str(value))
        self.key(48)
        node = self.find(self.scan(), id=identifier)
        if float(node["value"]) != float(value):
            raise RuntimeError(f"Export range did not stick: {identifier}")

    def go_to(self, destination):
        """Navigate a native save panel through its Go to Folder field."""
        self.native_key(5, command=True, shift=True)
        deadline = time.monotonic() + 5
        while True:
            fields = [n for n in self.scan() if n.get("id") == "PathTextField"]
            if fields:
                node = fields[0]
                break
            if time.monotonic() > deadline:
                raise RuntimeError("Go to Folder did not open")
            time.sleep(0.1)
        self.set_text(node, str(destination))
        self.native_key(36)

    def dialogs(self):
        """Exclude macOS's invisible sharing window from actual Live dialogs."""
        result = []
        for window in self.windows():
            subrole, title = (
                attribute(window, "AXSubrole"),
                attribute(window, "AXTitle"),
            )
            if subrole != "AXDialog" and not attribute(window, "AXModal"):
                continue
            nodes = self.scan(window)
            if title == "Window" and any(
                n.get("title") == "WindowSharingSessionButton" for n in nodes
            ):
                continue
            result.append(nodes)
        return result
