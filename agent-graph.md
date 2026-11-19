# Agent graph

- Every result returns to the orchestrator. Workers never talk to each other.
- Solid arrows are handoffs. Dashed arrows are escalation routes, which the orchestrator takes when a node fails the same way twice.
- Blue nodes are read-only, green nodes write files, amber nodes are gates.
- The pool sizes (×2, ×1, ×2) are the standing pool; the orchestrator can start more of any type.

```mermaid
flowchart TD
  user([You])
  orchestrator["<b>orchestrator</b><br/>Claude Fable 5.2 · high"]
  user --> orchestrator

  subgraph understand["Understand (read-only)"]
    explorer["<b>explorer</b><br/>Claude Haiku 5.5 · high"]
    researcher["<b>researcher</b><br/>GPT-6.2 Sol Fast · high"]
  end
  subgraph decide["Decide (read-only)"]
    oracle["<b>oracle</b><br/>GPT-6.2 Sol Fast · xhigh"]
    oracle_lite["<b>oracle-lite</b><br/>Gemini 4 Carbon · high"]
    councillor_fable["<b>councillor-fable</b><br/>Claude Fable 5.2 · high"]
    councillor_astra["<b>councillor-astra</b><br/>GPT-6.2 Astra · high"]
    councillor_carbon["<b>councillor-carbon</b><br/>Gemini 4 Carbon · high"]
    council["<b>council</b><br/>Claude Sonnet 5.5 · medium"]
  end
  subgraph build["Build (every node runs a loop until its evaluation passes)"]
    impl_openai["<b>impl-openai ×3</b><br/>GPT-6.2 Sol Fast · high"]
    impl_claude["<b>impl-claude ×2</b><br/>Claude Opus 5.5 · high"]
    impl_hard["<b>impl-hard</b><br/>Claude Opus 5.5 · high"]
    designer["<b>designer</b><br/>GPT-6.2 Sol Fast · high"]
  end
  subgraph verify["Verify"]
    reviewer["<b>reviewer</b><br/>Claude Opus 5.5 · high"]
    evaluate{{"<b>orchestrator evaluation</b><br/>rerun each check, join, full suite"}}
  end
  done([Report to you])

  orchestrator -->|search, docs| explorer
  orchestrator -->|research question| researcher
  orchestrator -->|hard decision| oracle
  orchestrator -->|second opinion| oracle_lite
  orchestrator -->|council question| councillor_fable
  councillor_fable --> council
  orchestrator --> councillor_astra
  councillor_astra --> council
  orchestrator --> councillor_carbon
  councillor_carbon --> council
  orchestrator -->|loop item| impl_openai
  orchestrator --> impl_claude
  orchestrator --> impl_hard
  orchestrator -->|UI work| designer
  impl_openai -.->|same failure twice| impl_claude
  impl_claude -.-> impl_hard
  impl_hard -.->|diagnosis| oracle
  build -->|loop report| evaluate
  evaluate -->|risky change| reviewer
  reviewer -->|findings| evaluate
  evaluate -->|all nodes passed| done

  classDef hub fill:#1f2937,stroke:#111827,color:#ffffff
  classDef read fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
  classDef write fill:#dcfce7,stroke:#16a34a,color:#14532d
  classDef gate fill:#fef3c7,stroke:#d97706,color:#78350f
  class orchestrator hub
  class explorer,researcher,oracle,oracle_lite,council,councillor_fable,councillor_astra,councillor_carbon read
  class impl_openai,impl_claude,impl_hard,designer write
  class evaluate,reviewer gate
```
