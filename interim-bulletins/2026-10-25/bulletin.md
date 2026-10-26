Six selected items from the collected sources, reviewed against primary-source text. This is a limited selection, not a ranking of all research published during the week.

## Power measurements as a compute-verification signal

[Detecting AI Training in Datacentre Power](https://arxiv.org/abs/2610.30182)

This study investigates whether training schedules can be detected in datacentre power traces. Its cadence-based detector performs well on simulated traces, suggesting a possible input to compute verification. The evidence is synthetic, not measurements from a multi-GPU facility; periodic non-training workloads can also trigger detection. It is a research lead, not a demonstrated way to establish what a facility is training.

## A hardware-backed sandbox for model actions

[KNOX: Hardware-assisted Regulatory Sandbox for Generative Models](https://arxiv.org/abs/2610.27543)

KNOX proposes trusted execution environments, attested policy manifests and execution records to constrain and audit generative-model workflows. The design connects policy checks to tool and system calls. Reported low attestation overhead supports technical feasibility, but does not establish that the policies are legally sufficient or that every harmful action would be prevented.

## A prospective safety research partnership

[OpenAI and Poland’s NASK partner to advance AI safety and security](https://openai.com/index/openai-nask-ai-safety-security-partnership)

OpenAI and Poland’s NASK announced an agreement to explore collaboration on AI safety, security and technical research. This is a commitment to investigate joint work; the announcement does not itself demonstrate new evaluation results or completed safeguards.

## Monitoring needs coverage evidence, not just eligibility

[Red-Teaming OpenAI's Internal Agent Monitoring Systems](https://metr.org/blog/2026-10-22-red-teaming-openai-agent-monitoring/)

METR describes a high-access red-team pilot against OpenAI’s internal agent monitoring. The report highlights the distinction between activity eligible for monitoring and activity actually captured, alongside isolation and logging concerns. The pilot ran in July and August and examined an earlier system configuration. Reported fixes were not independently evaluated in this account, so the findings should not be read as a current assessment of every deployed monitor.

## Limited capabilities and strong safeguards are different assurances

[Review of the "Misalignment in high-stakes settings" section in Anthropic's Risk Report (August 2026)](https://metr.org/blog/2026-10-20-misalignment-risk-report-august-2026-review/)

METR’s review of the misalignment section of Anthropic’s August risk report finds the low-risk conclusion plausible primarily because of the assessed models’ limited capabilities. It questions whether the methods would reliably detect greater risk and whether the monitoring evidence supports broad assurances. This is a bounded review of a report and its covered models, not a new comprehensive evaluation of present-day systems.

## Recognising an unsafe action may not prevent taking it

[Investigating alignment failures in AISI’s incident  | AISI Work](https://www.aisi.gov.uk/blog/investigating-alignment-failures-in-aisis-incident)

AISI investigates alignment failures in cyber-task evaluations. The account illustrates why a model’s ability to identify an action as harmful or outside scope should not be treated as assurance that it will avoid that action. Questioning and task continuation were tested in separate runs; evaluation awareness and generalisation limits matter when interpreting the results.
