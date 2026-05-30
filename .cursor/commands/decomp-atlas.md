---
description: Index similar matched functions and gather examples for prompt construction.
---

Use Atlas-style example discovery before long AI loops.

## Procedure

1. Search existing matched examples in the local project knowledge.
2. Extract 2-5 structurally similar examples.
3. Summarize per example:
   - why it is similar
   - key codegen patterns worth reusing
   - pitfalls to avoid
4. Feed results into `prompts/<fn>/prompt.md` under "Similar examples".

If there are no high-quality local examples, report that explicitly and proceed without fabricated analogies.
