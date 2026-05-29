# AI Safety Digest — week of 2026-05-28

_high: 4 · medium: 4 · low: 12 · 20 papers total_

## High relevance — read these { #high-relevance }

### <span class="tier-pill tier-pill-high">High</span> [CRaFT: Circuit-Guided Refusal Feature Selection via Cross-Layer Transcoders](https://arxiv.org/abs/2604.01604)
Su-Hyeon Kim, Hyundong Jin, Yejin Lee, Yo-Sub Han · 2026-05-28 · `interpretability` `alignment` `robustness`

CRaFT uses cross-layer transcoders to build sparse feature circuit graphs that identify causal refusal features in LLMs, enabling more effective steering-based jailbreak attacks by targeting mechanistically important features rather than highly activating ones.

<details><summary>Why?</summary>

This paper directly advances mechanistic interpretability of LLM safety behavior (refusal circuits) and demonstrates a significant improvement in jailbreak attack success, which is directly relevant to both understanding and stress-testing alignment mechanisms.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2604.01604" data-title="CRaFT: Circuit-Guided Refusal Feature Selection via Cross-Layer Transcoders" data-tier="High">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-high">High</span> [Securing Retrieval-Augmented Generation: A Taxonomy of Attacks, Defenses, and Future Directions](https://arxiv.org/abs/2604.08304)
Yuming Xu, Mingtao Zhang, Zhuohan Ge, Haoyang Li, Nicole Hu, … (+5) · 2026-05-28 · `robustness` `misuse` `evals` `multi_agent`

Presents SLOT, a taxonomy organizing RAG security literature along four axes (Surface, Layer, Objective, Target), mapping attacks and defenses onto a six-stage knowledge-access pipeline and identifying structural gaps.

<details><summary>Why?</summary>

Tracked safety author Yingjie Zhang is on this paper; it directly addresses security vulnerabilities in RAG systems (prompt injection, knowledge poisoning, confidentiality), which are increasingly deployed in frontier AI applications and relevant to robustness and misuse concerns.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2604.08304" data-title="Securing Retrieval-Augmented Generation: A Taxonomy of Attacks, Defenses, and Future Directions" data-tier="High">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-high">High</span> [Verifiable Process Rewards for Agentic Reasoning](https://arxiv.org/abs/2605.10325)
Huining Yuan, Zelai Xu, Huaijie Wang, Xiangmin Yi, Jiaxuan Gao, … (+4) · 2026-05-28 · `alignment` `multi_agent` `other`

Proposes Verifiable Process Rewards (VPR), a framework using dense turn-level supervision from symbolic/algorithmic oracles to improve credit assignment in long-horizon RL for LLM agents, with theoretical and empirical validation.

<details><summary>Why?</summary>

Tracked safety authors Yi Wu and Xin Zhang are on this paper. It directly addresses alignment-relevant challenges in training agentic LLMs via scalable, verifiable reward signals — a core concern in safe and reliable AI agent development.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2605.10325" data-title="Verifiable Process Rewards for Agentic Reasoning" data-tier="High">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-high">High</span> [The Alignment Floor: When Persona Customization Is Safe](https://arxiv.org/abs/2605.27382)
Xing Zhang, Guanghui Wang, Yanwei Cui, Wei Qiu, Ziyuan Li, … (+2) · 2026-05-28 · `alignment` `evals` `robustness`

Empirically measures how persona prompts affect sycophancy across alignment-strong vs. weak models (1,800 runs), discovering a stable "alignment floor" in strongly-aligned models and proposing a "Skeptic" persona defense that cuts sycophancy to 5% on weak models.

<details><summary>Why?</summary>

Tracked author Xin Zhang is on this paper. The paper directly addresses the alignment-customization tradeoff, quantifying how persona prompts can compromise or preserve alignment, with concrete design principles for safe persona deployment.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2605.27382" data-title="The Alignment Floor: When Persona Customization Is Safe" data-tier="High">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


## Medium relevance — worth a skim { #medium-relevance }

### <span class="tier-pill tier-pill-medium">Medium</span> [Reasoning on the Manifold: Bidirectional Consistency for Self-Verification in Diffusion Language Models](https://arxiv.org/abs/2604.16565)
Jiaoyang Ruan, Xin Gao, Yinda Chen, Hengyu Zeng, Liang Du, … (+3) · 2026-05-28 · `alignment` `other`

Proposes Bidirectional Manifold Consistency (BMC), a training-free metric for self-verification in diffusion LLMs that uses forward-masking/backward-reconstruction cycles to discriminate valid reasoning traces and provide alignment rewards.

<details><summary>Why?</summary>

Tracked author Jie Fu is on this paper. The alignment and self-verification angles are safety-adjacent (scalable oversight, self-evaluation), but the paper is primarily a capability/methodology contribution for diffusion LLMs rather than a direct safety advance.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2604.16565" data-title="Reasoning on the Manifold: Bidirectional Consistency for Self-Verification in Diffusion Language Models" data-tier="Medium">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-medium">Medium</span> [DataClawBench: An Agent Benchmark for Exploratory Real-World Financial Data Analysis](https://arxiv.org/abs/2605.02503)
Qiaohong Zhang, Weihao Ye, Jialong Chen, Yi Luo, BoYuan Li, … (+5) · 2026-05-28 · `evals` `capability_evals`

Introduces DataClawBench, a benchmark of 492 multi-step financial data analysis tasks to evaluate LLM agents on exploratory, noisy, real-world data with intermediate milestone annotations.

<details><summary>Why?</summary>

Tracked safety authors Jianyu Chen and Cynthia Xin Chen are on this paper. While it is primarily a capability benchmark for data analysis agents rather than a core safety paper, it evaluates agent reliability and reasoning failures in realistic settings, which is adjacent to agent safety and evals research.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2605.02503" data-title="DataClawBench: An Agent Benchmark for Exploratory Real-World Financial Data Analysis" data-tier="Medium">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-medium">Medium</span> [Detecting and Mitigating the Correct-Answer Extinction Window in Test-Time Reinforcement Learning with Majority Voting](https://arxiv.org/abs/2605.19444)
Hongxiang Lin, Zhirui Kuai, Erpeng Xue, Lei Wang · 2026-05-28 · `alignment` `evals`

Identifies a "Correct-Answer Extinction Window" failure mode in test-time RL with majority voting and proposes TTRL-Guard (FRS, MPS, RCSU) to mitigate reward signal corruption, achieving +54% relative improvement on AIME 2025.

<details><summary>Why?</summary>

Tracked author Liwei Wang is on this paper. While primarily a capability/training improvement for mathematical reasoning, it touches on failure modes in RL training signals (reward corruption/misinterpretation) that are relevant to alignment and reliable evaluation of learned behaviors.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2605.19444" data-title="Detecting and Mitigating the Correct-Answer Extinction Window in Test-Time Reinforcement Learning with Majority Voting" data-tier="Medium">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-medium">Medium</span> [Informing AI Policy Assessment using Large-Scale Simulation of Interventions](https://arxiv.org/abs/2605.27395)
Julia Barnett, Kimon Kieslich, Natali Helberger, Nicholas Diakopoulos · 2026-05-28 · `governance`

Introduces a methodology combining participatory evaluation, expert cost assessment, and LLM-based harm mitigation scoring with genetic algorithm simulation to help policymakers prioritize AI policy combinations.

<details><summary>Why?</summary>

Directly addresses AI governance by offering a practical tool for AI policy prioritization, but is primarily a methodology/process paper rather than a foundational safety research contribution — more useful for policy practitioners than safety researchers.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2605.27395" data-title="Informing AI Policy Assessment using Large-Scale Simulation of Interventions" data-tier="Medium">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


## Low relevance — context only { #low-relevance }

### <span class="tier-pill tier-pill-low">Low</span> [Compositional Consistency-Guided Decoding for Three-Way Logical Question Answering](https://arxiv.org/abs/2604.06196)
Tianyi Huang, Ming Hou, Jiaheng Su, Yutong Zhang, Ziling Zhang · 2026-05-28 · _no tag_

Proposes CGD-PD, a training-free decoding method that enforces negation consistency in three-way logical QA, improving accuracy by 4-7 points on the FOLIO benchmark for GPT and Claude models.

<details><summary>Why?</summary>

Despite a tracked author signal, this paper is primarily about improving logical reasoning consistency in LLMs on a formal benchmark — it does not directly address AI safety topics. The tracked author "Yingjie Zhang" does not appear among the listed authors (Tianyi Huang, Ming Hou, Jiaheng Su, Yutong Zhang, Ziling Zhang), suggesting a possible name collision rather than a true safety-relevant contribution.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2604.06196" data-title="Compositional Consistency-Guided Decoding for Three-Way Logical Question Answering" data-tier="Low">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-low">Low</span> [Prompt Optimization Is a Coin Flip: Diagnosing When It Helps in Compound AI Systems](https://arxiv.org/abs/2604.14585)
Xing Zhang, Guanghui Wang, Yanwei Cui, Wei Qiu, Ziyuan Li, … (+2) · 2026-05-28 · _no tag_

Analyzes when prompt optimization in compound AI systems (e.g., TextGrad, DSPy) helps vs. hurts, finding interaction effects between agent prompts are never significant and optimization only helps when tasks have exploitable output structure.

<details><summary>Why?</summary>

Despite a tracked-author signal, the paper is primarily about ML engineering—diagnosing the effectiveness of prompt optimization methods—with no substantive AI safety angle. It does not address alignment, interpretability, misuse, evals of dangerous capabilities, or other core safety topics.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2604.14585" data-title="Prompt Optimization Is a Coin Flip: Diagnosing When It Helps in Compound AI Systems" data-tier="Low">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-low">Low</span> [S2MAM: Semi-supervised Meta Additive Model for Robust Estimation and Variable Selection](https://arxiv.org/abs/2604.19072)
Xuelin Zhang, Hong Chen, Yingjie Wang, Tieliang Gong, Bin Gu · 2026-05-28 · _no tag_

Proposes a semi-supervised meta additive model (S²MAM) using bilevel optimization for variable selection and robust estimation under noisy/redundant inputs, with theoretical convergence and generalization bounds.

<details><summary>Why?</summary>

Despite a tracked-author signal, the paper is squarely about semi-supervised learning methodology (manifold regularization, graph Laplacian, variable selection) with no connection to AI safety topics. The tracked author "Xin Zhang" does not appear among the listed authors, suggesting a name collision rather than a genuine safety-researcher contribution.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2604.19072" data-title="S2MAM: Semi-supervised Meta Additive Model for Robust Estimation and Variable Selection" data-tier="Low">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-low">Low</span> [DiagramBank: A Quality-Audited Dataset of Scientific Schematic Diagrams with Multi-Level Document Context](https://arxiv.org/abs/2604.20857)
Ling Yue, Tingwen Zhang, Jiaying Wang, Zhen Xu, Shaowu Pan · 2026-05-28 · _no tag_

Introduces DiagramBank, a dataset of 57,100 scientific schematic diagrams from AI/ML venues with document context, for use in scientific document understanding and diagram retrieval tasks.

<details><summary>Why?</summary>

Despite a tracked author (Jindong Wang), this paper is a dataset contribution for scientific figure understanding with no direct safety relevance — it is a general ML resource paper about diagram extraction and curation.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2604.20857" data-title="DiagramBank: A Quality-Audited Dataset of Scientific Schematic Diagrams with Multi-Level Document Context" data-tier="Low">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-low">Low</span> [MAVEN A Multi-Agent Framework for Multicultural Text-to-Video Generation](https://arxiv.org/abs/2605.16716)
Shuowei Li, Yuming Zhao, Parth Bhalerao, Oana Ignat · 2026-05-28 · _no tag_

MAVEN is a multi-agent prompt refinement framework for improving cultural fidelity in text-to-video generation, with a new benchmark of 243 culturally grounded prompts across three cultures.

<details><summary>Why?</summary>

Despite a tracked author (Sharon Li) and multi-agent framing, this paper is primarily about improving cultural representation in T2V generation — a capability/application paper with no meaningful AI safety angle. The multi-agent aspect is purely a prompt engineering technique, not a safety-relevant dynamic.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2605.16716" data-title="MAVEN A Multi-Agent Framework for Multicultural Text-to-Video Generation" data-tier="Low">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-low">Low</span> [One LR Doesn't Fit All: Heavy-Tail Guided Layerwise Learning Rates for LLMs](https://arxiv.org/abs/2605.22297)
Di He, Songjun Tu, Keyu Wang, Lu Yin, Shiwei Liu · 2026-05-28 · _no tag_

Proposes layerwise learning rate assignment for LLMs guided by Heavy-Tailed Self-Regularization theory, achieving up to 1.5x training speedup and improved zero-shot accuracy on 1B–3B models.

<details><summary>Why?</summary>

Despite Di He being a tracked author, this paper is squarely focused on LLM training optimization (learning rate scheduling) with no safety-relevant content. It is a general ML efficiency/capability paper without alignment, interpretability, or safety angles.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2605.22297" data-title="One LR Doesn&#x27;t Fit All: Heavy-Tail Guided Layerwise Learning Rates for LLMs" data-tier="Low">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-low">Low</span> [Case-Aware Medical Image Classification with Multimodal Knowledge Graphs and Reliability-Guided Refinement](https://arxiv.org/abs/2605.22547)
Yiming Xu, Yixuan Liu, Yuhang Zhang, Ling Zheng, Yihan Wang, … (+1) · 2026-05-28 · _no tag_

Proposes a case-aware medical image classification framework using multimodal knowledge graphs and graph attention networks to retrieve similar historical cases and inject case-based features into visual representations.

<details><summary>Why?</summary>

Despite a tracked author signal, this paper is focused on medical image classification using knowledge graphs — a general ML/medical application with no substantive AI safety angle. The interpretability mentioned is domain-specific clinical case retrieval, not mechanistic model interpretability relevant to safety research.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2605.22547" data-title="Case-Aware Medical Image Classification with Multimodal Knowledge Graphs and Reliability-Guided Refinement" data-tier="Low">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-low">Low</span> [KT4EQG: Personalized Exercise Question Generation via Knowledge Tracing](https://arxiv.org/abs/2605.23933)
Xinyi Gao, Qiucheng Wu, Lu Ding, Q. Vera Liao, Kaizhi Qian, … (+3) · 2026-05-28 · _no tag_

KT4EQG is an educational question generation framework that uses knowledge tracing models to personalize exercise questions for individual students, maximizing knowledge mastery improvement.

<details><summary>Why?</summary>

This paper is focused on educational technology (personalized question generation) and has no meaningful connection to AI safety research. The tracked author "Yingjie Zhang" appears to be a different person from the safety researcher "Yang Zhang" listed as an author — and even so, the topic is clearly outside AI safety.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2605.23933" data-title="KT4EQG: Personalized Exercise Question Generation via Knowledge Tracing" data-tier="Low">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-low">Low</span> [CollectionLoRA: Collecting 50 Effects in 1 LoRA via Multi-Teacher On-Policy Distillation](https://arxiv.org/abs/2605.25378)
Fangtai Wu, Hailong Guo, Shijie Huang, Jiayi Song, Yubo Huang, … (+5) · 2026-05-28 · _no tag_

CollectionLoRA distills up to 50 visual-effect LoRAs plus few-step generation into a single LoRA for diffusion models, reducing deployment overhead and parameter interference.

<details><summary>Why?</summary>

Despite a tracked author (Yaodong Yu), this paper is entirely about efficient image editing with diffusion models and has no safety-relevant content — it addresses deployment efficiency and style fidelity, not alignment, interpretability, misuse, or any other AI-safety topic.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2605.25378" data-title="CollectionLoRA: Collecting 50 Effects in 1 LoRA via Multi-Teacher On-Policy Distillation" data-tier="Low">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-low">Low</span> [SetupX: Can LLM Agents Learn from Past Failures in Functionality-Correct Code Repository Setup?](https://arxiv.org/abs/2605.26186)
Zihang Zhou, Ziqian Ren, Yukai Wu, Yingjie Xiong, Wei Zhou, … (+5) · 2026-05-28 · _no tag_

SetupX is an LLM agent framework for automatically configuring code repository execution environments, using experiential learning and Docker snapshots to achieve 92% pass rate on repo setup benchmarks.

<details><summary>Why?</summary>

Despite a tracked author (Yi Wu), this paper is primarily a software engineering / developer tools paper focused on repository environment setup. It has no meaningful connection to AI safety topics such as alignment, interpretability, robustness, or misuse.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2605.26186" data-title="SetupX: Can LLM Agents Learn from Past Failures in Functionality-Correct Code Repository Setup?" data-tier="Low">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-low">Low</span> [EEG-FM-Audit: A Systematic Evaluation and Analysis Pipeline for EEG Foundation Models](https://arxiv.org/abs/2605.26910)
Xianheng Wang, Yige Yang, Damien Coyle · 2026-05-28 · _no tag_

Proposes EEG-FM-Audit, a pipeline for evaluating EEG foundation models via benchmarking, ablation studies, and neurophysiological probing across four EEG-FMs and five supervised baselines.

<details><summary>Why?</summary>

Despite tracked authors Xiting Wang and Yaodong Yang, this paper is focused on EEG signal decoding and neuroscience foundation models — not AI safety. The interpretability work concerns neurophysiological features, not safety-relevant model internals.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2605.26910" data-title="EEG-FM-Audit: A Systematic Evaluation and Analysis Pipeline for EEG Foundation Models" data-tier="Low">📝 Disagree with this tier? Tell the bot.</a></div>

</details>


### <span class="tier-pill tier-pill-low">Low</span> [EvoSpec: Evolving Speculative Decoding via Real-Time Vocabulary and Parameter AdaptationTarget](https://arxiv.org/abs/2605.27390)
Shuyu Zhang, Lingfeng Pan, Qicheng Wang, Yaqi Shi, Yueyang Tan, … (+4) · 2026-05-28 · _no tag_

EvoSpec proposes a framework for faster LLM inference via dynamic vocabulary and parameter adaptation in speculative decoding, achieving 1.13x speedup over FR-Spec on specialized domains.

<details><summary>Why?</summary>

Despite tracked safety authors, this paper is purely about LLM inference efficiency (speculative decoding speed), with no meaningful connection to AI safety topics like alignment, interpretability, robustness, or misuse.

<div class="feedback"><a class="tier-feedback" href="#" data-url="https://arxiv.org/abs/2605.27390" data-title="EvoSpec: Evolving Speculative Decoding via Real-Time Vocabulary and Parameter AdaptationTarget" data-tier="Low">📝 Disagree with this tier? Tell the bot.</a></div>

</details>
