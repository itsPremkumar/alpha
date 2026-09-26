# Production Implementation Roadmap: Integrating Missing Frontier Agentic Features

**Document:** `references/07-system1-and-os-computer-use/IMPLEMENTATION_ROADMAP_MISSING_FEATURES.md`  
**Project:** [itsPremkumar/alpha](https://github.com/itsPremkumar/alpha)  
**Status:** Living Engineering Plan & Roadmap  
**Authors:** Alpha Core Architecture Team  
**Focus:** Concrete phased implementation of System 1 Reflex Harness, OS Computer Use, Sentinel Interceptor, and Device Nodes in Alpha.

---

## 1. Roadmap Overview & Phased Milestones

To bring Alpha to the absolute state-of-the-art across all frontier benchmarks (OpenClaw 2.0, Hermes Swarm, Grok, Meta Muse, UI-TARS) while keeping everything **100% free and local-first**, the implementation is structured into 4 sequential phases:

```
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 1: System 1 Fast Reflex Harness & Jev Integration (Weeks 1–2)    │
│ - Non-autoregressive decision engine (Choice, Score, Noul)             │
│ - Local ONNX CPU fallback (0-cost, 8ms latency, zero API key)          │
│ - Pre-flight tool pruning (116 tools -> 5 tools in 12ms)               │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 2: Zero-Cost OS Computer Use & Laptop Automation (Weeks 3–4)     │
│ - Accessibility Tree Bridge (pywinauto / UIAutomation - 0 token UI)    │
│ - Mouse, keyboard, application lifecycle tools (pyautogui, pynput)     │
│ - Set-of-Marks visual fallback (mss + local VLM)                       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 3: Meta Muse-Style Sentinel Side-Effect Sandbox (Weeks 5–6)      │
│ - Intercepts destructive OS/CLI actions before execution               │
│ - Window boundary clamping and panic-corner kill switch                │
│ - Two-Keys approval protocol for external side-effects                 │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 4: OpenClaw-Style Paired Device Node Bridge (Weeks 7–8)          │
│ - Encrypted WebSocket node daemon for remote laptop control            │
│ - Dispatches desktop actions from Docker/Server to physical client     │
│ - Multi-device fleet management in Alpha Gateway                       │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Phase 1: System 1 Fast Reflex Harness (`alpha.system1`)

### 2.1 File Structure & Deliverables
```text
backend/packages/harness/alpha/system1/
├── __init__.py                # Public exports: get_system1_engine, DecisionType
├── models.py                  # Pydantic schemas: ChoiceRequest, ScoreRequest, NoulRequest
├── engine.py                  # Dual-engine: Jev API client + Local ONNX classifier
├── classifier.py              # Lightweight local CPU ONNX model runtime
└── middleware.py              # Middleware that hooks into LangGraph & DWE
```

### 2.2 Core Logic: `backend/packages/harness/alpha/system1/engine.py`
```python
"""System 1 fast decision engine supporting Jev and Local ONNX fallback."""

from __future__ import annotations
import logging
import os
from typing import Any
from alpha.system1.models import ChoiceResult, NoulResult, ScoreResult

logger = logging.getLogger(__name__)

class System1Engine:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("JEV_API_KEY")
        self.use_cloud = bool(self.api_key)
        if not self.use_cloud:
            logger.info("Jev API key not configured; activating Local Free ONNX System 1 Engine")
            from alpha.system1.classifier import LocalONNXClassifier
            self.local_classifier = LocalONNXClassifier()

    def choice(self, context: str, candidates: list[str]) -> ChoiceResult:
        if self.use_cloud:
            # Call Jev REST / gRPC endpoint
            return self._call_jev_choice(context, candidates)
        # 100% Free Local CPU ONNX inference (<10ms)
        return self.local_classifier.predict_choice(context, candidates)

    def noul(self, context: str, question: str) -> NoulResult:
        if self.use_cloud:
            return self._call_jev_noul(context, question)
        return self.local_classifier.predict_noul(context, question)

    def score(self, context: str, scale: tuple[float, float] = (0.0, 1.0)) -> ScoreResult:
        if self.use_cloud:
            return self._call_jev_score(context, scale)
        return self.local_classifier.predict_score(context, scale)
```

### 2.3 Registration & Catalog Wiring
- Register capability in `backend/packages/harness/alpha/capabilities/catalog.py`:
  ```python
  "system1_reflex": CapabilitySpec(
      module="alpha.system1.engine",
      target="System1Engine",
      description="Dual-process System 1 fast reflex decision harness (Jev + Local ONNX).",
      kind="engine",
  ),
  ```

---

## 3. Phase 2: OS Computer Use & Laptop Automation (`alpha.computer_use`)

### 3.1 File Structure & Deliverables
```text
backend/packages/harness/alpha/computer_use/
├── __init__.py                # Exports: LaptopController, get_laptop_controller
├── accessibility.py           # Windows UIAutomation / pywinauto tree inspector
├── dispatcher.py              # OS Mouse & Keyboard simulator (pyautogui/pynput)
├── screen.py                  # Ultra-fast mss screenshot & Set-of-Marks annotator
├── windows.py                 # Window management (focus, list, launch, close)
└── guard.py                   # Sentinel boundaries and panic-corner listener
```

### 3.2 Built-in Toolset: `backend/packages/harness/alpha/tools/builtins/os_computer_tool.py`
Add the following tools to `BUILTIN_TOOLS`:
1. `desktop_screenshot`: Captures full screen or active application window in <10ms.
2. `desktop_inspect_ui_tree`: Queries OS Accessibility Tree, returning exact click targets without consuming LLM vision tokens.
3. `desktop_mouse_action`: Dispatches clicks, movements, drags, and scrolling.
4. `desktop_keyboard_action`: Types text, presses keys, or runs key shortcuts.
5. `desktop_window_manage`: Lists open windows, brings an app to the front, or launches desktop software.

### 3.3 Zero-Token Accessibility Tree Implementation
```python
"""OS Accessibility Tree Reader for Zero-Token UI Grounding."""

from __future__ import annotations
import sys
from typing import Any

def inspect_active_window_elements(app_title_filter: str = "") -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    
    if sys.platform == "win32":
        from pywinauto import Desktop
        windows = Desktop(backend="uia").windows()
        for win in windows:
            title = win.window_text()
            if app_title_filter and app_title_filter.lower() not in title.lower():
                continue
            for child in win.descendants():
                try:
                    name = child.window_text().strip()
                    ctrl_type = child.friendly_class_name()
                    rect = child.rectangle()
                    if name and rect.width() > 0 and rect.height() > 0:
                        elements.append({
                            "name": name,
                            "type": ctrl_type,
                            "bbox": [rect.left, rect.top, rect.right, rect.bottom],
                            "center": [rect.mid_point().x, rect.mid_point().y],
                        })
                except Exception:
                    continue
    return elements
```

---

## 4. Phase 3: Meta Muse-Style Sentinel Safety Sandbox

### 4.1 Sentinel Pre-Execution Interceptor
Every tool call with OS-level side-effects routes through the **Sentinel Safety Guard**:
- **Panic Corner**: A background thread continuously monitors mouse coordinates. If the physical mouse hits `(0, 0)`, automation halts immediately.
- **Window Bounding Box Lock**: The agent cannot click coordinates outside the targeted window.
- **Two-Keys Protocol**: If the agent attempts a high-risk action (e.g., executing an unsigned script or modifying system settings), execution pauses and emits an interactive approval request on the Next.js frontend / Gateway WebSocket.

---

## 5. Phase 4: OpenClaw-Style Paired Device Node Bridge

### 5.1 Remote-to-Local WebSocket Protocol
```text
┌─────────────────────────────────┐                 ┌─────────────────────────────────┐
│     Alpha Gateway (Docker)      │                 │    Alpha Node Daemon (Laptop)   │
│                                 │                 │                                 │
│ - Dynamic Workflow Engine       │                 │ - Tray Icon Application         │
│ - LangGraph Orchestrator        │                 │ - Accessibility Tree Inspector  │
│ - Cognitive Memory              │                 │ - pyautogui / pynput Driver     │
│                                 │                 │ - Local Screen Capture (mss)    │
│ [WebSocket Hub /api/nodes/ws]   │◄───(TLS+HMAC)──►│ [Node Client Session]           │
└─────────────────────────────────┘                 └─────────────────────────────────┘
```

1. **Gateway Hub**: Mounts `/api/nodes/ws` on FastAPI Gateway.
2. **Laptop Client**: Runs `alpha-node --gateway wss://my-alpha.local --token <HMAC>` on the user's laptop.
3. **Transparent Proxying**: When the DWE executes a `NodeType.COMPUTER` or OS tool, the Gateway serializes the tool call over the WebSocket, the local daemon executes the click/keystroke locally, and sends back the result and screenshot.

---

## 6. Comprehensive Verification Plan

To ensure seamless production stability and adherence to Alpha's quality gates:

1. **System 1 Unit Tests (`backend/tests/test_system1_engine.py`)**:
   - Verify `choice`, `score`, and `noul` decisions.
   - Benchmark decision latency: must execute in under 50ms on CPU.
   - Validate automatic fallback when `$JEV_API_KEY` is omitted.
2. **OS Computer Use Tests (`backend/tests/test_os_computer_use.py`)**:
   - Mocked window manager and display driver tests.
   - Verify accessibility tree parsing on Windows/Linux.
   - Verify panic-corner emergency shutoff.
3. **Orphan Module Scanner (`backend/tests/test_no_orphan_modules.py`)**:
   - Verify all new modules (`alpha.system1.*`, `alpha.computer_use.*`) are imported or indexed in `alpha.capabilities.catalog`.
4. **Tool Schema Validation (`backend/scripts/check_tool_schemas.py`)**:
   - Verify all new `@tool` definitions strictly adhere to the `runtime: Runtime` required parameter rule.
5. **Feature Manifest Generation (`scripts/generate_feature_manifest.py`)**:
   - Re-index all new capabilities into `contracts/feature_manifest.json`.

---

## 7. Strategic Impact

With this roadmap executed:
- **Speed**: Agent loops will run up to 10x faster via the System 1 Jev reflex layer.
- **Capability**: Alpha expands from a CLI/code assistant into a full-fledged OS Laptop Agent capable of operating any desktop software.
- **Cost**: Operates completely free of third-party API fees using native accessibility trees and local ONNX classifiers.
- **Safety**: Meta Muse-style Sentinel guards prevent accidental damage to user environments.
