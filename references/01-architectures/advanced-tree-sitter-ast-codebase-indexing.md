# Advanced Tree-Sitter AST Codebase Indexing, Symbol Graphs, and PageRank Retrieval

> **Classification:** Codebase Intelligence & Context Retrieval  
> **Status:** Production Architecture Specification & Algorithmic Blueprint  
> **Target System:** Alpha Structural Code Intelligence Engine  
> **Core Technologies:** Tree-Sitter CST, Concrete Syntax Trees, Symbol Dependency Graphs, Personalized PageRank  

---

## 1. Executive Summary: The Failure of Naive RAG on Source Code

Standard Retrieval-Augmented Generation (RAG) pipelines break down when applied to complex software engineering repositories. Chunking code files into fixed character windows (e.g., 500-token chunks with 50-token overlaps) creates severe architectural pathologies:

```
+-------------------------------------------------------------------------+
|                  The Pathologies of Naive Code RAG                      |
+-------------------------------------------------------------------------+
|                                                                         |
|  1. Scope Splitting       --> Functions split across arbitrary chunks   |
|  2. Loss of Call Graphs   --> RAG cannot see caller/callee links        |
|  3. Namespace Blindness   --> Cannot resolve imports or class hierarchy |
|  4. Low Semantic Density  --> Boilerplate matches user queries falsely  |
|  5. Context Window Waste  --> Fills prompt with duplicate snippets      |
|                                                                         |
+-------------------------------------------------------------------------+
```

To provide an agent with true codebase comprehension within tight token budgets, modern frontier coding agents (such as Aider, Cursor, and Alpha) abandon naive text embeddings in favor of **Deterministic Structural Code Intelligence**:
- **Tree-Sitter Multi-Language Concrete Syntax Trees (CST):** Incremental parsing of polyglot source code into syntax-aware symbol hierarchies.
- **Symbol Dependency Graph:** Directed graph mapping definitions, call sites, inheritance chains, and imports across every file in the repository.
- **Personalized PageRank (PPR):** Graph-centrality algorithms that calculate the topological importance of every symbol, dynamically biased toward the files relevant to the active user task.
- **Compact Repo Maps:** Packing an entire 100,000-line codebase into an ultra-dense, 1,500-token syntax outline that fits comfortably inside the agent's context window.

```
+-------------------------------------------------------------------------+
|                 Alpha Structural Code Indexing Pipeline                 |
+-------------------------------------------------------------------------+
|                                                                         |
|  [Repository Files] (Python, TypeScript, Rust, Go, C++, etc.)           |
|        |                                                                |
|        v                                                                |
|  [Tree-Sitter Incremental Parser]                                       |
|        | Extracts: Classes, Functions, Interfaces, Imports, Call Sites  |
|        v                                                                |
|  [Symbol Dependency Graph Builder]                                      |
|        | Nodes: Symbols & Files | Edges: Calls, Imports, Inherits       |
|        v                                                                |
|  [Personalized PageRank (PPR) Engine]                                   |
|        | Computes global centrality biased by active editor file        |
|        v                                                                |
|  [Context-Budget Repo Map Optimizer]                                    |
|        | Selects top-N symbols within strict token budget (e.g. 1.5k)   |
|        v                                                                |
|  [Injected into LLM System Prompt as Structural Repo Map]               |
|                                                                         |
+-------------------------------------------------------------------------+
```

---

## 2. Tree-Sitter Concrete Syntax Trees & S-Expression Queries

**Tree-Sitter** is an incremental parsing library that builds concrete syntax trees (CST) for any programming language in milliseconds. Crucially, Tree-Sitter updates ASTs incrementally as the user or agent modifies files, eliminating the overhead of full-file re-parsing.

### 2.1 S-Expression Syntax Queries
Tree-Sitter provides a declarative pattern-matching language (`.scm` query files) to extract semantic symbols:

```scheme
;; Extract TypeScript Class & Method Definitions
(class_declaration
  name: (type_identifier) @class.name
  body: (class_body
    (method_definition
      name: (property_identifier) @method.name
      parameters: (formal_parameters) @method.params)))

;; Extract Function Calls
(call_expression
  function: [
    (identifier) @call.direct
    (member_expression
      property: (property_identifier) @call.method)
  ])
```

Alpha maintains curated Tree-Sitter query files for Python, TypeScript, JavaScript, Rust, Go, C/C++, Java, and SQL, guaranteeing universal polyglot indexing across the entire workspace.

---

## 3. Constructing the Symbol Dependency Graph

Alpha transforms raw AST nodes into a directed property graph $\mathcal{G} = (\mathcal{V}, \mathcal{E})$:

### 3.1 Graph Entities and Edges
- **Vertices $\mathcal{V}$:**
  - File Nodes: Representing source code files (`src/core/router.ts`).
  - Symbol Nodes: Classes, interfaces, functions, methods, global constants.
- **Edges $\mathcal{E}$:**
  - $\text{DEFINES}(File, Symbol)$: A file defines a symbol.
  - $\text{CALLS}(Symbol_A, Symbol_B)$: Function $A$ invokes Function $B$.
  - $\text{IMPORTS}(File_A, Symbol_B)$: File $A$ imports symbol from File $B$.
  - $\text{INHERITS}(Class_A, Class_B)$: Class $A$ subclasses Class $B$.

```
+-------------------------------------------------------------------------+
|                    Sample Symbol Dependency Subgraph                    |
+-------------------------------------------------------------------------+
|                                                                         |
|  [src/api/auth.ts] ---- DEFINES ----> [loginHandler()]                  |
|                                            |                            |
|                                          CALLS                          |
|                                            v                            |
|  [src/db/users.ts] ---- DEFINES ----> [findUserByEmail()]               |
|         ^                                  |                            |
|         |                                INHERITS                       |
|         |                                  v                            |
|  [src/db/base.ts]  ---- DEFINES ----> [BaseRepository]                 |
|                                                                         |
+-------------------------------------------------------------------------+
```

---

## 4. Personalized PageRank (PPR) for Code Relevance

In a large software project, not all symbols are equally significant. A central utility function or core router is vastly more informative than a localized helper script.

### 4.1 Mathematical Formulation of PPR
Let $\mathbf{P}$ be the normalized transition probability matrix of the code dependency graph $\mathcal{G}$, and $\mathbf{v}$ be a personalization vector. The Personalized PageRank vector $\mathbf{r}$ satisfies:

$$\mathbf{r} = (1 - d) \cdot \mathbf{v} + d \cdot \mathbf{P}^T \mathbf{r}$$

Where:
- $d \in (0, 1)$ is the damping factor (typically $0.85$).
- $\mathbf{v}$ is the **Teleportation / Personalization Vector**:
  - If the user is currently editing `src/auth/login.ts`, the personalization mass is concentrated on the symbols belonging to `src/auth/login.ts` and its immediate query matches.
  - The algorithm distributes probability mass across the call graph, elevating symbols that are functionally connected to the active development task.

### 4.2 Power Iteration Algorithm

```python
def compute_personalized_pagerank(graph, seed_nodes, damping=0.85, max_iter=30, tol=1e-6):
    """
    Computes Personalized PageRank biased toward seed_nodes.
    """
    num_nodes = len(graph.nodes)
    if num_nodes == 0:
        return {}
        
    # Initialize personalization vector
    v = {node: 0.0 for node in graph.nodes}
    for seed in seed_nodes:
        if seed in v:
            v[seed] = 1.0 / len(seed_nodes)
            
    # Initialize rank vector
    r = {node: 1.0 / num_nodes for node in graph.nodes}
    
    for _ in range(max_iter):
        next_r = {node: (1 - damping) * v.get(node, 0.0) for node in graph.nodes}
        for node in graph.nodes:
            out_degree = len(graph.successors(node))
            if out_degree > 0:
                share = damping * r[node] / out_degree
                for successor in graph.successors(node):
                    next_r[successor] += share
            else:
                # Dangling node redistribution
                for target in graph.nodes:
                    next_r[target] += damping * r[node] * v.get(target, 1.0 / num_nodes)
                    
        # Check convergence
        diff = sum(abs(next_r[n] - r[n]) for n in graph.nodes)
        r = next_r
        if diff < tol:
            break
            
    return r
```

---

## 5. Context-Budget Repo Map Packing

Once symbols are ranked by PPR score, Alpha constructs the **Compact Repository Map**:
1. The budget optimizer defines a token ceiling (e.g., $B = 1,500$ tokens).
2. It greedily includes the highest-ranked files and their salient symbol definitions (classes, method signatures, parameter types), omitting implementation bodies.
3. The resulting map renders a clean structural representation of the repository architecture.

### 5.1 Sample Rendered Repo Map Snippet

```markdown
alpha/backend/
  core/
    config.py:
      class Settings(BaseSettings):
        database_url: str
        api_key: SecretStr
    orchestrator.py:
      class AgentOrchestrator:
        async def dispatch_task(self, task: AgentTask) -> TaskResult:
        async def stream_events(self) -> AsyncIterator[AgentEvent]:
  database/
    models.py:
      class User(BaseModel):
        id: UUID
        email: str
        is_active: bool
```

This ultra-compact representation allows the LLM to understand exact class names, method signatures, and module locations without consuming more than 2% of the context window.

---

## 6. Comparative Retrieval Performance

| Retrieval Architecture | Context Tokens Used | Call-Graph Preserved? | False-Positive Noise | Indexing Latency |
| :--- | :--- | :--- | :--- | :--- |
| **Naive Chunk RAG (Embeddings)** | 12,000+ tokens | No (Text fragments) | High (Boilerplate hits) | High (Vector embed) |
| **Full File Concatenation** | 120,000+ tokens | Yes (Full code) | None (Exact) | Zero (Disk read) |
| **Alpha Tree-Sitter + PPR Map** | **1,500 tokens** | **Yes (Graph traversal)** | **Near Zero** | **< 100ms incremental**|

---
*Reference Document authored for Alpha Autonomous Agent Architecture.*
