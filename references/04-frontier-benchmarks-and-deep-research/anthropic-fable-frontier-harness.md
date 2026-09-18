Yes. After checking Anthropic’s latest public material, the most important finding is that **Fable 5.1 and Mythos 5.1 are not two separate underlying model architectures**. Anthropic says they are the **same underlying model with different safeguard configurations**; Fable 5.1 is generally available, while Mythos 5.1 is restricted through trusted-access programs for higher-risk cybersecurity and life-sciences work. ([Anthropic][1])

So, for your own harness, you should not try to reproduce “Fable architecture” or “Mythos architecture” as if they were exposed internal neural architectures. What is publicly observable is the **agent/harness architecture surrounding the models**. Anthropic's public engineering work gives unusually useful pieces of that architecture.

# Anthropic Frontier Agent Harness

I would synthesize the public Fable/Mythos, Claude Code, Cowork, Managed Agents, long-running harness, multi-agent research, Skills, context engineering, and containment work into this architecture:

```text
                              USER / EVENT
                                   │
                                   ▼
                      ┌─────────────────────────┐
                      │      AGENT GATEWAY      │
                      │                         │
                      │ identity                │
                      │ authentication         │
                      │ session                 │
                      │ request normalization  │
                      └────────────┬────────────┘
                                   │
                                   ▼
                      ┌─────────────────────────┐
                      │     EXECUTIVE CLAUDE    │
                      │                         │
                      │ understand objective    │
                      │ determine success       │
                      │ estimate complexity     │
                      │ choose effort           │
                      │ decide autonomy level   │
                      └────────────┬────────────┘
                                   │
                                   ▼
                      ┌─────────────────────────┐
                      │    CONTEXT ENGINE       │
                      │                         │
                      │ current state           │
                      │ relevant memory         │
                      │ project files           │
                      │ skills                  │
                      │ tools                   │
                      │ previous attempts       │
                      │ constraints             │
                      └────────────┬────────────┘
                                   │
              ┌────────────────────┼─────────────────────┐
              ▼                    ▼                     ▼
      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
      │ PLAN ENGINE  │      │ MEMORY ENGINE│      │ MODEL ROUTER │
      │              │      │              │      │              │
      │ decomposition│      │ episodic     │      │ Fable        │
      │ sequencing   │      │ semantic     │      │ Opus         │
      │ dependencies │      │ procedural   │      │ Sonnet       │
      │ checkpoints  │      │ project      │      │ Haiku        │
      └──────┬───────┘      └──────────────┘      └──────────────┘
             │
             ▼
       ┌───────────────────────────────────┐
       │          AGENT RUNTIME             │
       │                                    │
       │ observe → think → act → observe    │
       │            → verify → continue     │
       └────────────────┬──────────────────┘
                        │
            ┌───────────┼───────────────┐
            ▼           ▼               ▼
      ┌──────────┐ ┌──────────┐ ┌──────────────┐
      │ TOOLS    │ │ SKILLS   │ │ SUBAGENTS    │
      │          │ │          │ │              │
      │ shell    │ │ coding   │ │ researcher   │
      │ browser  │ │ research │ │ coder        │
      │ files    │ │ design   │ │ analyst      │
      │ APIs     │ │ science  │ │ verifier     │
      │ MCP      │ │ docs     │ │ specialist   │
      └────┬─────┘ └────┬─────┘ └──────┬───────┘
           └─────────────┼──────────────┘
                         ▼
              ┌──────────────────────┐
              │ COMPUTER / WORLD     │
              │                      │
              │ terminal             │
              │ browser              │
              │ desktop applications │
              │ files                │
              │ Git                  │
              │ cloud                │
              │ databases            │
              └───────────┬──────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │   OBSERVATION LAYER    │
              │                        │
              │ command result         │
              │ file state             │
              │ DOM                    │
              │ screenshot             │
              │ accessibility          │
              │ test output            │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │      VERIFICATION      │
              │                        │
              │ tests                  │
              │ critic                 │
              │ visual comparison      │
              │ state checks            │
              │ requirements            │
              └────────────┬───────────┘
                           │
                    ┌──────┴──────┐
                    ▼             ▼
                   PASS          FAIL
                    │             │
                    │             ▼
                    │       RECOVERY ENGINE
                    │             │
                    │      retry / repair
                    │      alternate tool
                    │      replan / restart
                    │             │
                    └─────────────┘
                           │
                           ▼
                  ┌────────────────────┐
                  │ CHECKPOINT / STATE │
                  │                    │
                  │ progress           │
                  │ artifacts          │
                  │ next steps         │
                  │ decisions          │
                  │ failures           │
                  └─────────┬──────────┘
                            │
                            ▼
                  CONTINUE / COMPLETE
```

That is the basic **Fable-style long-running harness**.

Anthropic says Fable 5.1 is explicitly designed for jobs lasting hours and spanning multiple applications, including browser work, Slack/Cowork workflows, unattended managed agents, long coding sessions, deep research, and complex knowledge work. It plans, uses tools, recovers from failures, keeps the user updated, writes tests, and uses vision to inspect results against goals. ([Anthropic][2])

---

# 1. The most important Fable feature: long-running execution

A normal agent does:

```text
request
 ↓
context
 ↓
answer
```

A Fable-class harness does:

```text
goal
 ↓
initialize
 ↓
plan
 ↓
execute
 ↓
checkpoint
 ↓
new session
 ↓
restore state
 ↓
continue
 ↓
verify
 ↓
finish
```

Anthropic's long-running research explicitly found that agents lose coherence as context fills and recommends **context resets with structured handoff state**, rather than depending on one ever-growing context. ([Anthropic][3])

So your architecture should have:

```text
Session 1
   ↓
checkpoint
   ↓
Session 2
   ↓
checkpoint
   ↓
Session 3
   ↓
...
```

Each new session gets:

```text
project state
+
completed work
+
known failures
+
next tasks
+
important decisions
+
artifacts
```

---

# 2. Context Engine

This should be a first-class subsystem.

```text
                  CONTEXT ENGINE
                       │
       ┌───────────────┼────────────────┐
       ▼               ▼                ▼
   retrieval       compression       state load
       │               │                │
       ▼               ▼                ▼
 relevant docs     summaries       current project
 tool results      notes            active goal
 memories          decisions        environment
```

Anthropic explicitly discusses **compaction, structured note-taking, and multi-agent architectures** as techniques for extending effective context over long horizons. ([Anthropic][4])

I would go further:

```text
Raw context
   ↓
relevance scorer
   ↓
deduplication
   ↓
state extraction
   ↓
memory retrieval
   ↓
context composer
   ↓
model
```

This should be one of your strongest components.

---

# 3. Structured Handoff System

Instead of handing the next agent a giant transcript:

```json
{
  "objective": "...",
  "completed": [],
  "in_progress": [],
  "blocked": [],
  "decisions": [],
  "known_failures": [],
  "next_actions": [],
  "artifacts": [],
  "verification": []
}
```

That becomes the persistent contract between sessions.

This is directly aligned with Anthropic's long-running harness work. ([Anthropic][4])

---

# 4. Fable's agent architecture should be hierarchical

Anthropic publicly describes multi-agent research as an **orchestrator-worker** architecture: a lead agent develops the strategy and delegates specialized work to parallel subagents.

So:

```text
                    LEAD AGENT
                        │
         ┌──────────────┼───────────────┐
         ▼              ▼               ▼
      Research        Coding          Analysis
       worker          worker          worker
         │              │               │
         └──────────────┼───────────────┘
                        ▼
                    SYNTHESIS
                        │
                        ▼
                    VERIFIER
```

For an even higher-end version:

```text
                 EXECUTIVE
                     │
                ORCHESTRATOR
                     │
              ┌──────┼──────┐
              ▼      ▼      ▼
           manager manager manager
              │      │      │
           workers workers workers
              └──────┼──────┘
                     ▼
                 REVIEWER
```

---

# 5. Agent Teams

Anthropic's public engineering work goes further than simple subagents. Its agent-team experiment used many Claude instances operating in parallel on a shared codebase. Anthropic reports that 16 agents worked across nearly 2,000 sessions to build a large Rust-based C compiler. ([Anthropic][5])

For your architecture:

```text
                     TEAM MANAGER
                          │
             ┌────────────┼────────────┐
             ▼            ▼            ▼
         Agent A       Agent B       Agent C
         parser        backend        tests
             │            │            │
             └────────────┼────────────┘
                          ▼
                     SHARED STATE
                          │
                          ▼
                      INTEGRATOR
```

Add:

```text
file ownership
task locking
conflict detection
shared build system
shared test suite
agent messaging
handoff protocol
```

---

# 6. Skills architecture

Anthropic's Skills system is another major component.

A skill should be:

```text
Skill
 ├── instructions
 ├── tools
 ├── resources
 ├── examples
 ├── permissions
 ├── tests
 └── metadata
```

Examples:

```text
coding
research
spreadsheet
presentation
PDF
frontend
science
database
security
design
```

The key principle is **progressive disclosure**:

```text
agent sees skill summary
        ↓
decides skill is useful
        ↓
loads detailed instructions
        ↓
loads relevant resources
        ↓
executes
```

That prevents wasting context on every available capability. Anthropic publicly describes Skills as reusable customized instructions and resources that can equip Claude for different workflows.

---

# 7. Tool architecture

A high-end Claude-style harness should make tools modular:

```text
Tool Registry
 ├── shell
 ├── filesystem
 ├── browser
 ├── git
 ├── github
 ├── database
 ├── search
 ├── HTTP
 ├── MCP
 ├── computer
 └── custom tools
```

Every tool should declare:

```json
{
  "name": "shell",
  "risk": "medium",
  "permissions": [],
  "timeout": 300,
  "supports_streaming": true,
  "supports_async": true
}
```

---

# 8. Claude Code architecture

Your coding subsystem should look like:

```text
User request
     │
     ▼
Repository understanding
     │
     ▼
Task decomposition
     │
     ▼
Implementation
     │
     ├── code navigation
     ├── search
     ├── shell
     ├── tests
     ├── git
     └── browser
     │
     ▼
Verification
     │
     ▼
Review
     │
     ▼
Fix
     │
     ▼
Final verification
```

Anthropic's long-running coding work emphasizes keeping the agent making incremental progress across sessions and leaving artifacts that enable the next session to continue coherently. ([Anthropic][4])

---

# 9. Visual verification

Fable 5.1's public description explicitly calls out vision for checking coding outputs against original design/goal and understanding diagrams, charts, tables, PDFs and other visual information. ([Anthropic][2])

Your architecture should therefore do:

```text
Generate
   ↓
Render
   ↓
Screenshot
   ↓
Vision model
   ↓
Compare against specification
   ↓
identify mismatch
   ↓
fix
   ↓
render again
```

For frontend:

```text
design
 ↓
implementation
 ↓
browser
 ↓
screenshot
 ↓
visual critic
 ↓
CSS/layout correction
```

This is much stronger than simply running unit tests.

---

# 10. Mythos architecture

This part is particularly interesting.

Anthropic says:

> **Fable 5.1 and Mythos 5.1 are the same underlying model.**

The primary difference is the **safety configuration**. Mythos has a higher-capability/high-risk deployment posture with restricted access, while Fable has safeguards intended for broader use. ([Anthropic][1])

So model architecture should be:

```text
                SAME INTELLIGENCE
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
       FABLE                     MYTHOS
          │                         │
 broader deployment          restricted deployment
 safety stack                specialized safeguards
```

This is an excellent lesson for your own architecture:

**Separate intelligence from deployment policy.**

Do not bake every restriction into the core agent.

Instead:

```text
MODEL
 ↓
CAPABILITY LAYER
 ↓
POLICY PROFILE
 ↓
ENVIRONMENT
```

You can then have:

```text
safe-user
developer
research
enterprise
trusted-agent
sandbox
```

profiles.

---

# 11. The Mythos-style capability gateway

For high-capability tools:

```text
Agent
 ↓
Capability request
 ↓
Risk classifier
 ↓
Policy profile
 ↓
Environment restriction
 ↓
tool
```

This is far better than:

```text
Agent
 ↓
all tools
```

Anthropic's current containment work stresses that as model capability increases, the critical question is how to **cap blast radius through containment**, including sandboxes, VMs and egress controls. ([Anthropic][6])

---

# 12. Security architecture

A serious harness should have:

```text
                    AGENT
                      │
                      ▼
                 ACTION PLAN
                      │
                      ▼
               RISK CLASSIFIER
                      │
                      ▼
               POLICY ENGINE
                      │
             ┌────────┴─────────┐
             ▼                  ▼
          allowed             blocked
             │
             ▼
        CAPABILITY TOKEN
             │
             ▼
          SANDBOX
             │
       ┌─────┼──────┐
       ▼     ▼      ▼
   process files network
```

Anthropic says containment has become a major part of how it secures Claude across products. ([Anthropic][6])

---

# 13. Prompt-injection defense

Your harness should treat:

```text
web pages
emails
PDFs
GitHub issues
README files
documents
tool responses
database content
```

as **untrusted data**.

Use:

```text
trusted policy
   >
user objective
   >
agent state
   >
tool instructions
   >
external content
```

rather than allowing external content to redefine the goal.

---

# 14. Agent should not directly control secrets

Use:

```text
Agent
 ↓
Credential Broker
 ↓
policy
 ↓
temporary scoped credential
 ↓
tool
```

instead of:

```text
Agent
 ↓
global API keys
```

This becomes particularly important for an autonomous Fable/Mythos-style agent running unattended.

---

# 15. The Anthropic “brain and hands” separation

One of Anthropic's most important current architectural ideas is **decoupling the brain from the hands** for managed agents: harness assumptions become stale as models improve, so the environment/tool interfaces should remain stable while the underlying agent/harness can evolve. ([Anthropic][7])

Your architecture should therefore have:

```text
                 BRAIN
                   │
             Agent protocol
                   │
                   ▼
                  HANDS
                   │
       ┌───────────┼──────────┐
       ▼           ▼          ▼
    browser      shell     computer
```

Never couple the reasoning engine directly to a particular UI implementation.

---

# 16. The long-running runtime

This becomes:

```text
                   JOB
                    │
                    ▼
              INITIALIZATION
                    │
                    ▼
                 SESSION
                    │
          ┌─────────┴──────────┐
          ▼                    ▼
        execute             checkpoint
          │                    │
          └─────────┬──────────┘
                    ▼
              context limit?
                /       \
              no        yes
              │          │
              │          ▼
              │     NEW SESSION
              │          │
              │     restore state
              │          │
              └──────────┘
```

This is one of the clearest patterns from Anthropic's long-running work. ([Anthropic][3])

---

# 17. Context reset vs compaction

Use both.

### Compaction

```text
same agent
 ↓
summarize
 ↓
continue
```

### Context reset

```text
old agent
 ↓
persist structured state
 ↓
terminate
 ↓
new agent
 ↓
restore
 ↓
continue
```

Anthropic specifically distinguishes these and found full context resets useful for preventing coherence degradation and “context anxiety” on long tasks. ([Anthropic][3])

---

# 18. Adaptive effort

Fable 5.1's public material explicitly discusses different effort levels and cost/performance tradeoffs. ([Anthropic][8])

Your harness should therefore support:

```text
EFFORT 0
deterministic

EFFORT 1
fast reasoning

EFFORT 2
normal reasoning

EFFORT 3
deep reasoning

EFFORT 4
multi-agent

EFFORT 5
deep research + verification
```

The supervisor chooses the level dynamically.

---

# 19. Verification loop

Do not trust the model's own claim that something works.

Use:

```text
                OUTPUT
                   │
        ┌──────────┼───────────┐
        ▼          ▼           ▼
       tests      critic      vision
        │          │           │
        └──────────┼───────────┘
                   ▼
               verifier
                   │
             ┌─────┴──────┐
             ▼            ▼
           PASS          FAIL
             │            │
             │            ▼
             │         repair
             │            │
             └────────────┘
```

This is especially important for Fable-style autonomous coding.

---

# 20. Failure recovery

A Fable-class agent should not fail because one tool call failed.

Use:

```text
Tool failure
 ↓
diagnose
 ↓
retry?
 ↓
alternate tool?
 ↓
alternate method?
 ↓
new agent?
 ↓
replan?
 ↓
restore checkpoint?
```

Example:

```text
browser login failed
 ↓
inspect state
 ↓
retry
 ↓
if still failed
 ↓
restart browser
 ↓
if still failed
 ↓
use alternate authentication flow
 ↓
if impossible
 ↓
checkpoint + escalate
```

---

# 21. Continuous updates

Fable 5.1 explicitly emphasizes keeping the user updated while it works. ([Anthropic][2])

Your event system should therefore emit:

```text
started
planning
delegating
working
blocked
recovered
verification
checkpoint
completed
```

The UI can turn these into concise progress updates without forcing the model to waste context generating status messages every few seconds.

---

# 22. Event-driven architecture

Make everything an event:

```text
GoalCreated
PlanCreated
TaskCreated
AgentSpawned
ToolCalled
ToolCompleted
ObservationReceived
ArtifactCreated
VerificationStarted
VerificationPassed
VerificationFailed
RecoveryStarted
CheckpointCreated
SessionClosed
SessionResumed
TaskCompleted
ProjectCompleted
```

Store them durably.

This gives you:

**replay + observability + debugging + evaluation + training data.**

---

# 23. Persistent project state

For serious long-running work:

```text
/project
│
├── objective.json
├── requirements.md
├── architecture.md
├── state.json
├── decisions.md
├── tasks.json
├── failures.json
├── verification.json
├── artifacts/
├── checkpoints/
└── sessions/
```

A new agent can inspect this and continue.

---

# 24. Research architecture

Anthropic's public research system already demonstrates that specialized agents can search different aspects in parallel and that dynamic multi-step search is preferable to simple static retrieval for complex questions.

For your system:

```text
Research Director
       │
 ┌─────┼────────────────┐
 ▼     ▼                ▼
Search Primary       Counter-
agent  source        evidence
       agent          agent
 │       │              │
 └───────┼──────────────┘
         ▼
    evidence graph
         │
         ▼
      synthesis
         │
         ▼
    fact checker
         │
         ▼
       report
```

---

# 25. Scientific / knowledge-work mode

For Mythos/Fable-class capability:

```text
Question
 ↓
hypotheses
 ↓
literature search
 ↓
data extraction
 ↓
experiment design
 ↓
simulation / code
 ↓
analysis
 ↓
counterexample search
 ↓
verification
 ↓
conclusion
```

The important distinction is that the agent isn't merely generating prose. It operates a **research loop**.

---

# 26. Final Anthropic-inspired architecture

This is the version I would actually recommend for your own high-end harness:

```text
┌──────────────────────────────────────────────────────────────────┐
│                       AGENT OPERATING SYSTEM                     │
│                                                                  │
│                       USER / EVENT BUS                           │
│                              │                                   │
│                              ▼                                   │
│                     IDENTITY / SESSION                          │
│                              │                                   │
│                              ▼                                   │
│                         EXECUTIVE                                │
│                              │                                   │
│        ┌─────────────────────┼─────────────────────┐             │
│        ▼                     ▼                     ▼             │
│    CONTEXT ENGINE         WORLD STATE           MEMORY           │
│        │                     │                     │             │
│        └─────────────────────┼─────────────────────┘             │
│                              ▼                                   │
│                        PLANNING CORE                             │
│                              │                                   │
│                              ▼                                   │
│                       SUPERVISOR CORE                            │
│                              │                                   │
│              ┌───────────────┼────────────────┐                 │
│              ▼               ▼                ▼                 │
│          AGENT TEAMS      MODEL ROUTER      TASK GRAPH          │
│              │               │                │                  │
│     ┌────────┼────────┐      │       ┌────────┼────────┐        │
│     ▼        ▼        ▼      ▼       ▼        ▼        ▼        │
│  Research   Code    Design  Models  A       B        C          │
│     │        │        │      │       │        │        │         │
│     └────────┼────────┴──────┘       └────────┼────────┘         │
│              ▼                               │                  │
│          SKILL SYSTEM                         │                  │
│              │                                │                  │
│              ▼                                │                  │
│          TOOL FABRIC                          │                  │
│              │                                │                  │
│     MCP / APIs / shell / browser             │                  │
│              │                                │                  │
│              ▼                                │                  │
│       COMPUTER ENVIRONMENT                    │                  │
│       DOM / A11y / GUI / vision              │                  │
│              │                                │                  │
│              ▼                                │                  │
│        POLICY KERNEL                          │                  │
│        risk / permissions                     │                  │
│        credential broker                      │                  │
│              │                                │                  │
│              ▼                                │                  │
│        SECURE RUNTIME                         │                  │
│        sandbox / VM / network                 │                  │
│              │                                │                  │
│              ▼                                │                  │
│        REAL ENVIRONMENT                       │                  │
│              │                                │                  │
│              ▼                                │                  │
│         OBSERVATION                           │                  │
│              │                                │                  │
│              ▼                                │                  │
│         VERIFICATION                          │                  │
│              │                                │                  │
│        ┌─────┴───────────┐                    │                  │
│        ▼                 ▼                    │                  │
│       PASS              FAIL                  │                  │
│        │                 │                    │                  │
│        │                 ▼                    │                  │
│        │             RECOVERY                 │                  │
│        │                 │                    │                  │
│        └─────────────────┼────────────────────┘                  │
│                          ▼                                       │
│                   CHECKPOINT STATE                               │
│                          │                                       │
│             ┌────────────┴──────────────┐                        │
│             ▼                           ▼                        │
│         NEW SESSION                 COMPLETE                      │
│             │                                                    │
│             ▼                                                    │
│       RESTORE CONTEXT                                           │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

# 27. Then add the AVO-style evolution layer

This is where your architecture can go beyond the standard Claude/Fable design:

```text
                    PRODUCTION AGENT
                           │
                     trajectories
                           │
                           ▼
                    FAILURE MINER
                           │
                           ▼
                  IMPROVEMENT HYPOTHESIS
                           │
                           ▼
                     CANDIDATE
                           │
                           ▼
                 ISOLATED EVALUATION
                           │
                    ┌──────┴──────┐
                    ▼             ▼
                 BETTER         WORSE
                    │             │
                    ▼             ▼
                 CANARY         DISCARD
                    │
                    ▼
                 PROMOTE
                    │
                    ▼
             NEW PRODUCTION AGENT
```

Now you have:

**Anthropic/Fable-style long-running harness + Claude Code-style coding agent + Managed-Agent brain/hands separation + Mythos-style capability policy + AVO-style evolutionary outer loop.**

That is considerably more powerful architecturally than simply building a “Claude clone.”

---

## The most important design decision

Separate your system into these three things:

```text
BRAIN
What should be done?

HANDS
How can it interact with the environment?

GOVERNOR
What is it allowed to do?
```

Then surround them with:

```text
MEMORY
STATE
PLANNING
MULTI-AGENT
VERIFICATION
RECOVERY
EVOLUTION
```

That gives you a clean architecture:

```text
                 BRAIN
                   │
        ┌──────────┼──────────┐
        ▼          ▼          ▼
      MEMORY     PLANNER    WORLD MODEL
        │          │          │
        └──────────┼──────────┘
                   ▼
                AGENTS
                   │
                   ▼
                SKILLS
                   │
                   ▼
                 HANDS
                   │
                   ▼
              GOVERNOR
                   │
                   ▼
              ENVIRONMENT
                   │
                   ▼
              VERIFICATION
                   │
                   ▼
               MEMORY
                   │
                   ▼
              EVOLUTION
```

That is the **best public-information-based architecture to derive from Fable 5.1 + Mythos 5.1 + Claude Code + Cowork + Managed Agents**.

Anthropic's own public material strongly supports the core pieces: long-running state across sessions, context engineering, multi-agent orchestration, agent teams, reusable Skills, visual verification, model/hand separation, and containment. ([Anthropic][3])

And importantly, **Mythos 5.1 is not “a second architecture” you need to reproduce**; it is the same underlying model as Fable 5.1 with a different safety/deployment configuration. ([Anthropic][1])

[1]: https://www.anthropic.com/claude/mythos?utm_source=chatgpt.com "Claude Mythos \ Anthropic"
[2]: https://www.anthropic.com/claude/fable?utm_source=chatgpt.com "Claude Fable \ Anthropic"
[3]: https://www.anthropic.com/engineering/harness-design-long-running-apps?utm_source=chatgpt.com "Harness design for long-running application development \ Anthropic"
[4]: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents?utm_source=chatgpt.com "Effective context engineering for AI agents \ Anthropic"
[5]: https://www.anthropic.com/engineering/building-c-compiler?utm_source=chatgpt.com "Building a C compiler with a team of parallel Claudes"
[6]: https://www.anthropic.com/engineering/how-we-contain-claude?utm_source=chatgpt.com "How we contain Claude across products \ Anthropic"
[7]: https://www.anthropic.com/engineering/managed-agents?utm_source=chatgpt.com "Scaling Managed Agents: Decoupling the brain from the hands \ Anthropic"
[8]: https://www.anthropic.com/claude-fable-and-mythos-5-1?frmapp=yes&utm_source=chatgpt.com "Introducing Claude Fable 5.1 and Claude Mythos 5.1 \ Anthropic"
