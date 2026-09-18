# MetaGPT & ChatDev Multi-Agent SDLC Architectures

> **Classification:** Multi-Agent Systems & Software Engineering Automation  
> **Status:** Comparative Technical Reference & Blueprint  
> **Target System:** Alpha Multi-Agent Orchestration Layer  
> **Key References:** MetaGPT (Wu et al., 2023), ChatDev (Qian et al., 2023)  

---

## 1. Executive Summary: Monolithic LLMs vs. Multi-Agent SDLC

When a monolithic LLM agent is tasked with building or maintaining complex software, it struggles with **cognitive load aggregation**. The model must simultaneously reason about product requirements, database schemas, frontend interactions, API contracts, deployment configurations, and unit tests. This cognitive overload leads to hallucinated endpoints, missing dependencies, incomplete implementations, and context degradation.

To resolve this bottleneck, multi-agent frameworks introduce **Software Development Life Cycle (SDLC) Decomposition**:
- **MetaGPT** structures multi-agent collaboration around **Standardized Operating Procedures (SOPs)** and an asynchronous **Publish/Subscribe Message Bus**, producing formal document artifacts (PRD, System Design, Data Schemas, API Specs) before code emission.
- **ChatDev** models software development as a **Chat Chain** of communicative agent pairs engaged in structured conversational dialogues across waterfall phases (Designing, Coding, Testing, Documenting).

This paper deconstructs the architectural mechanics of both paradigms and outlines Alpha's hybrid multi-agent orchestration model.

```
+-----------------------------------------------------------------------------+
|                           MetaGPT SDLC Cascade                              |
+-----------------------------------------------------------------------------+
|                                                                             |
|  [User Prompt]                                                              |
|        |                                                                    |
|        v                                                                    |
|  [Product Manager Agent] ===> Artifact: PRD (Product Requirements Doc)     |
|        |                                                                    |
|        v                                                                    |
|  [Architect Agent]       ===> Artifact: System Design & Class Diagram       |
|        |                                                                    |
|        v                                                                    |
|  [Project Manager Agent] ===> Artifact: Task Breakdown & API Spec          |
|        |                                                                    |
|        v                                                                    |
|  [Engineer Agent]        ===> Artifact: Modular Python/TypeScript Code      |
|        |                                                                    |
|        v                                                                    |
|  [QA Engineer Agent]     ===> Artifact: Pytest/Jest Test Suite & Coverage   |
|                                                                             |
+-----------------------------------------------------------------------------+
```

---

## 2. MetaGPT: Standard Operating Procedures (SOPs) & Pub/Sub Architecture

MetaGPT's core philosophy is: **"Code = SOP(Team)"**. Human software engineering organizations succeed not because individual engineers hold the entire codebase in their heads, but because standardized documentation and interfaces coordinate human activities.

### 2.1 The Role Hierarchy and Document Artifacts

MetaGPT defines five primary roles, each consuming specific upstream artifacts and producing structured markdown/UML deliverables:

1. **Product Manager (`ProductManager`):**
   - **Input:** User natural language request.
   - **Output Artifact:** PRD containing Competitive Analysis, User Stories, MoSCoW Feature Priorities, and UI Design drafts.
2. **Architect (`Architect`):**
   - **Input:** PRD artifact.
   - **Output Artifact:** System Design document containing Architecture Style, Data Structures & Interfaces, Mermaid Class Diagrams, and Sequence Diagrams.
3. **Project Manager (`ProjectManager`):**
   - **Input:** PRD and System Design artifacts.
   - **Output Artifact:** Task Decomposition Matrix, file dependency graph, and exact implementation sequence.
4. **Engineer (`Engineer`):**
   - **Input:** Assigned file tasks, interface contracts, and class diagrams.
   - **Output Artifact:** Complete source code files conforming strictly to the API specification.
5. **QA Engineer (`QaEngineer`):**
   - **Input:** Implemented source code and task definitions.
   - **Output Artifact:** Unit and integration test suites, execution logs, and defect tickets.

### 2.2 The Publish/Subscribe Event Bus

MetaGPT avoids chaotic peer-to-peer agent messaging through a decoupled **Message Pool (Blackboard)**:

```python
# Conceptual Architecture of MetaGPT Message Bus
class MessagePool:
    def __init__(self):
        self.messages: List[Message] = []
        
    def publish(self, message: Message):
        self.messages.append(message)
        
    def subscribe(self, role: str, required_topics: List[str]) -> List[Message]:
        return [
            msg for msg in self.messages
            if msg.topic in required_topics and role not in msg.consumed_by
        ]
```

- Each role subscribes only to the message topics relevant to its responsibilities.
- For example, an `Engineer` agent does not process user raw queries; it subscribes exclusively to `Task` and `SystemDesign` messages.
- This decoupling drastically reduces context window consumption and eliminates circular conversational rabbit holes.

---

## 3. ChatDev: The "Chat Chain" Model & Conversational Pairs

In contrast to MetaGPT's document-centric pipeline, **ChatDev** organizes development into a sequence of communicative dialogues between complementary agent pairs:

```
+---------------------------------------------------------------------------+
|                          ChatDev Chat Chain                               |
+---------------------------------------------------------------------------+
|                                                                           |
|   Phase 1: DESIGNING                                                      |
|   [CEO] <======================= Dialog =======================> [CPO]    |
|   Goal: Decide programming language, architecture, and core modalities   |
|                                                                           |
|   Phase 2: CODING                                                         |
|   [CTO] <======================= Dialog =======================> [Programmer]
|   Goal: Generate directory structure, write core algorithms, implement APIs |
|                                                                           |
|   Phase 3: TESTING                                                        |
|   [Programmer] <================ Dialog =======================> [Reviewer]|
|   [Programmer] <================ Dialog =======================> [Tester]  |
|   Goal: Static code review, runtime execution, capture stderr, bug repair |
|                                                                           |
|   Phase 4: DOCUMENTING                                                    |
|   [CTO] <======================= Dialog =======================> [Designer]|
|   Goal: Generate manual, API docs, system specifications, and environment |
|                                                                           |
+---------------------------------------------------------------------------+
```

### 3.1 Conversational De-biasing & Multi-Turn Consensus
In each phase, two agents engage in a structured multi-turn conversation:
- **Role Inversion / Prompting:** One agent serves as the *Instructor* (e.g., Code Reviewer) while the other serves as the *Assistant* (e.g., Programmer).
- **Self-Correction Loop:** When code fails to compile or tests fail, the Tester agent captures the execution traceback and provides it to the Programmer. The Programmer reviews the specific diff and amends the code.
- **Consensus Termination:** The conversation proceeds until an explicit `<TERMINATE>` token is reached, indicating both agents have agreed that the phase goals are met.

---

## 4. Deep Comparative Analysis

| Feature Dimension | MetaGPT | ChatDev | Alpha Architecture |
| :--- | :--- | :--- | :--- |
| **Primary Coordination Model** | Asynchronous Pub/Sub Message Bus | Sequential Chat Chain (Waterfall) | Hybrid Actor-Graph + Event Bus |
| **Inter-Agent Communication** | Formal Document Artifacts (PRD, UML) | Conversational Dialogue Pairs | Typed Structured Artifacts & Tool Events |
| **Token Efficiency** | High (Agents process only filtered inputs)| Moderate (Dialogues accumulate context)| Ultra-High (AST filtering & selective routing)|
| **Executable Testing** | Pytest execution via QA role | Sandboxed subshell runtime execution | Containerized Docker/SWEEnv execution |
| **Code Modularity** | Multi-file, structured repositories | Small-to-medium single/multi-file scripts | Enterprise multi-tier full-stack codebases |
| **Human Intervention** | PRD review & gate approval | Interactive chat injection | Bidirectional human-in-the-loop steering |

---

## 5. Architectural Blueprint: Alpha's SDLC Harness

Alpha synthesizes the structural rigor of MetaGPT's SOPs with the rapid conversational verification of ChatDev's review pairs:

```
+----------------------------------------------------------------------------+
|                   Alpha Hybrid SDLC Orchestration Flow                     |
+----------------------------------------------------------------------------+
|                                                                            |
|  [Planner Subagent]                                                        |
|         | Generates Implementation Plan & Architecture Spec                |
|         v                                                                  |
|  [Human Verification Gate] (Interactive UI Approval Modal)                 |
|         | Approved                                                         |
|         v                                                                  |
|  [Coder Subagent]                                                          |
|         | Implements AST Changes & File Diffs                              |
|         v                                                                  |
|  [Reviewer / Critic Subagent]                                              |
|         | AST static verification, security audits, style checks           |
|         +---> [Defects Found?]                                             |
|                   |                                                        |
|                   +---> YES: Direct Feedback Loop to Coder                 |
|                   +---> NO:  Proceed to Test Runner                        |
|                                     |                                      |
|  [QA Test Runner Subagent] <--------+                                      |
|         | Executes pytest / npm test in sandbox                            |
|         v                                                                  |
|  [Commit & Deliver Stage]                                                  |
|                                                                            |
+----------------------------------------------------------------------------+
```

### 5.1 Key Implementation Directives for Alpha
1. **Artifact Gate Enforcement:** No code modification may proceed without an approved plan artifact containing explicit verification criteria.
2. **Dedicated Critic/Reviewer Agent:** Large refactors are evaluated by a distinct subagent configured with adversarial prompts to detect edge cases, boundary violations, and performance regressions.
3. **Decoupled Execution Sandbox:** QA tests execute in an isolated environment, streaming only structured JSON test summaries (test name, status, failure traceback) rather than unfiltered raw stdout.

---
*Reference Document authored for Alpha Autonomous Agent Architecture.*
