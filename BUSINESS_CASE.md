# Business Case: Multi-Agent Pharmacovigilance Signal Detection System

## Overview

Pharmaceutical companies and regulators are legally required to monitor drugs for adverse events (AEs) after they reach the market. Today, this monitoring relies heavily on manual review of massive, growing volumes of AE reports — a process that is slow, inconsistent, and struggles to keep pace with the data. This capstone proposes a multi-agent AI system that automates the pipeline from raw AE report ingestion through statistical signal detection, literature corroboration, and drafting a reviewer-ready safety summary — surfacing likely drug-event signals faster and with a documented evidence trail, while keeping a human reviewer as the final decision-maker.

## The Problem

- **Volume**: Post-market surveillance databases (e.g., the FDA's FAERS) receive hundreds of thousands of adverse event reports per quarter, covering thousands of drugs and event types.
- **Manual bottleneck**: Safety reviewers must triage this volume largely by hand — scanning reports, running statistical checks, and separately searching medical literature to see if a suspected drug-event pair has independent support.
- **Fragmented workflow**: AE data, statistical tooling, and literature evidence typically live in separate systems. A reviewer investigating one candidate signal has to manually stitch together evidence from all three before they can write up a finding.
- **Cost of delay or error**: A signal that is missed or caught late can mean continued patient harm, delayed label changes, regulatory penalties, or in the worst case a product recall. A false signal that consumes reviewer time without cause has its own cost in wasted capacity.

## Why This Is Hard

- **Volume vs. capacity**: The number of reports scales faster than the number of trained reviewers.
- **Signal vs. noise**: Most drug-event co-occurrences are coincidental. Distinguishing a true safety signal from statistical noise requires disciplined quantitative methods (e.g., disproportionality analysis), not just eyeballing counts.
- **Evidence fragmentation**: Confirming a candidate signal requires cross-referencing literature and existing drug labeling — a manual research task for every candidate pair a statistical pass flags.
- **Consistency**: Manual review quality varies by reviewer, workload, and time pressure, making it hard to guarantee consistent triage standards across a large caseload.

## The Solution

A pipeline of specialized agents, each responsible for one stage of the signal-detection workflow:

1. **Ingestion agent** — cleans and structures raw adverse event reports into a consistent format.
2. **Signal detection agent** — runs statistical disproportionality analysis (e.g., proportional reporting ratio) across the structured data to flag candidate drug-event pairs that occur more often than expected.
3. **Literature/context agent** — for each flagged candidate, searches relevant literature and reference sources for corroborating or contradicting evidence.
4. **Reporting agent** — synthesizes the statistical finding and literature context into a structured, evidence-backed summary for a human reviewer, including a confidence/severity assessment.

A human reviewer remains the final decision-maker: the system's output is a drafted, evidence-linked recommendation, not an autonomous action.

## Why Multi-Agent (vs. a Single Model or Traditional Pipeline)

- **Separation of concerns**: Statistical analysis, literature reasoning, and narrative synthesis are different kinds of tasks; dedicating an agent to each keeps each step focused and easier to validate than one model doing everything at once.
- **Auditability**: Because each agent's output is a discrete, inspectable artifact (structured data, a statistical score, a set of cited sources, a draft summary), a reviewer — or an auditor — can trace exactly how a conclusion was reached.
- **Extensibility**: Any single stage (e.g., the statistical method, or the literature source) can be upgraded or replaced without redesigning the whole system.

## Why It Matters / Impact

- **Faster time-to-signal**: Automating triage and evidence-gathering shortens the time between a signal emerging in the data and a reviewer seeing a substantiated write-up.
- **Reduced reviewer workload**: Reviewers spend their time evaluating pre-assembled evidence rather than manually assembling it.
- **Consistency and auditability**: A standardized, evidence-linked report format supports more consistent triage decisions and a clearer record for regulatory purposes.

## Scope & Assumptions for This Capstone

- **Synthetic data only**: This project uses a synthetically generated drug catalog, patient cohort, and AE report dataset — not real patient or FAERS data — since real pharmacovigilance data is sensitive and regulated. Synthetic data is acceptable here because the goal is to demonstrate the *approach* (pipeline design, agent collaboration, signal recovery), not to produce a submission-ready regulatory tool.
- **Known signals by design**: A small number of drug-event associations are deliberately injected into the synthetic data during generation, giving a ground truth against which the system's detection performance can be measured.
- **Out of scope**: This is not a regulatory-submission-ready or clinically validated system. It does not connect to real AE databases, does not make autonomous safety decisions, and is not intended for use with real patient data.

## Success Criteria

- The system correctly recovers the drug-event signals deliberately injected into the synthetic data.
- The false-positive rate on non-injected (noise) drug-event pairs stays within a reasonable, statistically justified range.
- The final reviewer-facing report is coherent, cites its supporting evidence, and would plausibly help a human reviewer make a faster, better-informed decision.
