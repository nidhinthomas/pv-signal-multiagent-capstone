# Assignment Brief (verbatim)

> This document is a fixed reference — pasted verbatim from the capstone brief provided by the user. It is not edited as the project evolves. The build is checked against this document periodically (see `PLAN.md` → "Periodic rubric checks") and the final compliance mapping lives in `README.md`.

---

CAPSTONE PROJECT — AGENTIC AI IN THE PHARMACEUTICAL INDUSTRY

## Problem Statement

Pharmaceutical organizations operate across multiple functions such as Research & Development, Manufacturing, Quality, Regulatory Affairs, Supply Chain, Pharmacovigilance, Clinical Operations, Sales, Marketing, Finance, HR and other enterprise functions.

Each function contains business processes that involve data, documents, repetitive activities, decision-making, collaboration, compliance requirements and human approvals.

Your challenge is to identify one meaningful problem or opportunity from your own department/function and design and build a working Agentic AI solution that demonstrates how autonomous or semi-autonomous AI agents can plan, reason, use tools, collaborate with specialized agents and complete a business workflow while maintaining appropriate human oversight.

## Objective

Working as a team, select your own pharmaceutical use case and develop an Agentic AI prototype that:

- Addresses a clearly defined business problem.
- Uses VS Code as the development IDE.
- Uses Claude Code as the AI coding assistant.
- Accepts an appropriate user request, data, document or business input.
- Creates a plan to accomplish the requested task.
- Uses tools and/or enterprise information where required.
- Delegates specialized tasks to appropriate Sub-agents.
- Maintains appropriate state/context during execution.
- Produces an evidence-based business output.
- Incorporates human approval for important or high-impact decisions.
- Demonstrates responsible, secure and governed use of AI.

## Mandatory Agentic AI Components

Your solution must demonstrate the following components:

1. **CLAUDE.md** — Define project instructions, architecture, coding standards, business rules and constraints.
2. **Skills** — Create reusable capabilities required by your selected use case.
3. **Hooks** — Use appropriate hooks for automated validation, logging, testing or workflow controls.
4. **Sub-agents** — Create specialized agents with clearly defined responsibilities. Demonstrate coordination between agents rather than using a single LLM for the entire task.
5. **MCP** — Use Model Context Protocol (MCP) where appropriate to connect the Agentic AI system with approved tools, files, databases or other resources.
6. **State / Context / Memory** — Maintain the information required by the agents throughout the workflow so that the system can understand what has already happened and what should happen next.
7. **Guardrails** — Define what the AI system is allowed and not allowed to do. Prevent unsupported conclusions, unauthorized actions and unsafe use.
8. **AI Governance** — Address data privacy, security, access control, accountability, human oversight and responsible AI requirements.
9. **Human-in-the-Loop** — Identify at least one important stage where human review, validation or approval is required before proceeding.
10. **Evaluation** — Define appropriate tests and metrics to evaluate the quality, correctness, reliability and effectiveness of the Agentic AI system.
11. **Observability** — Capture important operational information such as agent execution, tool calls, errors, latency and token/model usage where applicable.
12. **Traceability** — Maintain an end-to-end record showing:
    `User Request → Agent Plan → Agent/Sub-agent → Skill → Tool/MCP → Evidence/Data → Action → Guardrail Check → Human Approval → Final Output`

## Three-Hour Challenge

- **0–30 Minutes — DEFINE**: Select the business problem, define users, expected outcome, data/documents required, success criteria and AI boundaries.
- **30–60 Minutes — DESIGN**: Design the Agentic AI architecture, workflow, Supervisor/Orchestrator, Sub-agents, Skills, Hooks, MCP/tools, state management, guardrails and human approval points.
- **60–140 Minutes — BUILD**: Using VS Code + Claude Code, develop a working prototype and integrate the mandatory Agentic AI components.
- **140–165 Minutes — TEST & EVALUATE**: Test the complete workflow, evaluate output quality, test guardrails and failures, and capture observability and traceability information.
- **165–180 Minutes — PRESENT**: Demonstrate the working solution and explain its business value, architecture, agentic behavior, governance and limitations.

## Expected Deliverables

Each group should present:

- Selected pharmaceutical business problem and objective.
- Working Agentic AI prototype.
- Architecture/workflow diagram.
- CLAUDE.md.
- Skills and Hooks.
- Supervisor/Orchestrator and Sub-agents.
- MCP/tool integration.
- State/context or memory mechanism.
- Guardrails and Human-in-the-Loop.
- AI Governance approach.
- Evaluation results.
- Observability and Traceability output.
- README/project documentation.
- Live end-to-end demonstration.

## Important Principle

The objective is not simply to build a chatbot.

Your solution should demonstrate genuine agentic behavior:

`UNDERSTAND → PLAN → DELEGATE → USE SKILLS → ACCESS TOOLS/MCP → EXECUTE → OBSERVE → VERIFY → RE-PLAN IF REQUIRED → APPLY GUARDRAILS → HUMAN APPROVAL → COMPLETE → EVALUATE → TRACE`

## Final Challenge

"Choose a real problem from your pharmaceutical function and demonstrate how Agentic AI can transform the existing process into an intelligent, tool-enabled, governed and human-supervised workflow."

---

## Notes on applicability (recorded once, not re-litigated per phase)

- The "Three-Hour Challenge" schedule above is treated as a rough shape, not a hard clock — this project is built multi-session, untimed, per an explicit decision with the user (see `PLAN.md` → Context). Nothing mandatory is skipped because of that; only the pacing differs.
- "Not everything will be applicable — like MCPs" (the user's own initial framing) was superseded during planning: MCP is implemented as a real, minimal server, not skipped. See `PLAN.md` → Context.
