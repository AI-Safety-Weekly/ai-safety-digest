# Author identity provenance

Checked 2026-10-09 using public Semantic Scholar Graph API responses and primary author/institutional sources. This records observed evidence, not a claim that any profile is complete or permanently stable. S2 profiles can be split or polluted. The temporary raw API responses are not retained with this report.

## Xin Chen (Cynthia Xin Chen)

- S2 author: https://www.semanticscholar.org/author/2403655825
- Primary identity and bibliography: https://www.xccyn.com/
- The author identifies herself as “Xin Chen, Cynthia,” says she can be called Cynthia or Chen Xin, describes ETH Zurich and prior CHAI work, and lists the paper below.
- Corroborating paper: https://arxiv.org/abs/2505.24445 — Learning Safety Constraints for Large Language Models.
- Exact S2 endpoint queried: https://api.semanticscholar.org/graph/v1/paper/ARXIV:2505.24445?fields=title,authors
- Observed response: paperId 35aafa6763752fb4238c4d5edbbfa796baa89d58; title Learning Safety Constraints for Large Language Models; authors Xin Chen (2403655825), Yarden As (2151001957), Andreas Krause (2257180836).

## Hadassah Harland (Haddie Harland)

- S2 author: https://www.semanticscholar.org/author/2215192039
- Primary institutional/research collective profile: https://araac.au/researchers/haddie_harland/
- Profile explicitly identifies “Hadassah Harland (Haddie)” with Deakin University and CSIRO Data61.
- Matching papers on that profile and in the S2 author-search response: AI apology: a critical review of apology in AI systems; AI apology: interactive multi-objective reinforcement learning for human-aligned AI.
- S2 search query: Hadassah Harland; observed one result, Hadassah Harland, 2215192039.

## Sebastian Farquhar (Seb Farquhar)

- S2 authors: https://www.semanticscholar.org/author/33859827 and https://www.semanticscholar.org/author/2274932640
- Primary identity: https://www.cs.ox.ac.uk/people/sebastian.farquhar/ — explicitly uses both Sebastian and Seb for the same researcher.
- Primary bibliography: https://sebastianfarquhar.com/publications/
- 33859827: S2 name Sebastian Farquhar, affiliation Oxford. Matching primary-bibliography titles include Discovering Agents; Path-Specific Objectives for Safer Agent Incentives; Detecting hallucinations in large language models using semantic entropy.
- 2274932640: S2 name Sebastian Farquhar. Matching primary-bibliography titles include MONA: Myopic Optimization with Non-myopic Approval Can Mitigate Multi-step Reward Hacking; Evaluating Frontier Models for Dangerous Capabilities; Holistic Safety and Responsibility Evaluations of Advanced AI Models.
- S2 search query: Sebastian Farquhar; observed two results. Both IDs contain corroborated publications by this researcher; one ID alone omits relevant work.

## Florian E. Dorner (correction from Florian Droner)

- S2 author: https://www.semanticscholar.org/author/2064453117
- Primary author page: https://flodorner.github.io/
- Page identifies Florian Dorner, MPI-IS/ETH Zurich, advised by Moritz Hardt and Fanny Yang.
- Matching titles in the primary bibliography and S2: Training on the Test Task Confounds Evaluation and Emergence; Limits to scalable evaluation at the frontier: LLM as Judge won't beat twice the data; Don't Label Twice: Quantity Beats Quality when Comparing Binary Classifiers on a Budget.
- S2 search query: Florian E. Dorner; observed one result, Florian E. Dorner, 2064453117.
- Evidence proves Dorner's profile; interpreting the original Droner entry as a typo is a separate configuration decision.

## Phillip Isola (correction from Philip Isola)

- S2 author: https://www.semanticscholar.org/author/2094770
- Primary institutional identity: https://sqi.mit.edu/about/people/phillip-isola
- Primary bibliography: https://web.mit.edu/phillipi/www/all_papers.html
- Exact matching title: Distilled Feature Fields Enable Few-Shot Language-Guided Manipulation; MIT bibliography lists William Shen, Ge Yang, Alan Yu, Jansen Wong, Leslie Kaelbling and Phillip Isola, CoRL 2023.
- S2 search query: Phillip Isola; observed 2094770 named Phillip Isola with this paper and many matching vision/representation-learning papers.
- S2 reported ten name-search results. This corroborated ID is not an exhaustive representation of his publications; additional split profiles exist.

## Roman V. Yampolskiy (correction from Roman Yampolsky)

- S2 author: https://www.semanticscholar.org/author/1976753
- Primary institutional identity: https://faculty.cse.louisville.edu/roman/
- Primary page identifies Roman V. Yampolskiy, University of Louisville, and his AI-safety research and Artificial Intelligence Safety and Security book.
- S2 search query: Roman Yampolskiy; observed result 1976753 named Roman V Yampolskiy with affiliation University of Louisville.
- Observed S2 titles include Artificial Intelligence Safety and Security; Safety Engineering for Artificial General Intelligence; Impossibility Results in AI: A Survey; Uncontrollability of Artificial Intelligence.
- Multiple other Roman Yampolskiy profiles appeared in S2; this ID is corroborated, not exhaustive.

## Shai Shalev-Shwartz (correction from Shai Shalev-Schwartz)

- S2 author: https://www.semanticscholar.org/author/1389955537
- Primary institutional identity: https://www.cs.huji.ac.il/~shais/
- Primary bibliography: https://www.cs.huji.ac.il/~shais/publications.html
- Bibliography identifies Understanding Machine Learning: From Theory to Algorithms, Shai Shalev-Shwartz and Shai Ben-David, Cambridge University Press, 2014, which matches the S2 corpus.
- S2 search query: Shai Shalev-Shwartz; observed 1389955537 with abbreviated display name S. Shalev-Shwartz and extensive machine-learning corpus including this book.
- Use Shalev-Shwartz spelling from the author's own university page.

## David Duvenaud

- S2 author: https://www.semanticscholar.org/author/1704657
- Primary institutional identity and bibliography: https://www.cs.toronto.edu/~duvenaud/
- Exact matching title: Neural Ordinary Differential Equations. Both the primary bibliography and S2 corpus contain it. S2 also contains Latent Ordinary Differential Equations for Irregularly-Sampled Time Series.
- S2 search query: David Duvenaud; observed 1704657 with abbreviated display name D. Duvenaud. A second result 2429706985 had no papers and was not selected.
- Do not retain the nickname davidad for Duvenaud: https://davidad.org/ explicitly identifies davidad as David A. Dalrymple. Independent primary corroboration: https://esp.mit.edu/teach/teachers/davidad/bio.html

## Lookup reproducibility and limitations

The author queries used https://api.semanticscholar.org/graph/v1/author/search with query set to the names stated above, fields=name,url,affiliations,papers.title, and limit=5. The Sebastian query returned both matching profiles. Name searches alone were not treated as adequate; the identity and overlapping publications above supply corroboration. Subsequent exact-paper calls returned HTTP 429 and were stopped. No paid APIs or new accounts were used.

## Thiago Viana (University of Gloucestershire)

- S2 author: https://www.semanticscholar.org/author/2114852779
- Primary institutional identity: https://www.glos.ac.uk/staff/profile/thiago-viana/
- University paper repository: https://eprints.glos.ac.uk/14476/ links the author to ORCID 0000-0001-9380-4611 and the 2024 paper Towards an End-to-End Personal Fine-Tuning Framework for AI Value Alignment.
- Exact S2 paper response: https://api.semanticscholar.org/graph/v1/paper/DOI:10.3390/electronics13204044?fields=title,authors,externalIds identifies Thiago Viana as 2114852779 on that paper.
- This is the approved replacement for the unidentified Tiago Vhana entry. It is not the similarly named Prosus researcher.
- The profile also indexes uncertain banking and Nitinol papers. Relevant newly included papers require paper-level author corroboration; the whole profile is not assumed to be clean.

## C. Henrik Åslund (configured as Carl Henrik Rolf Åslund)

- S2 author: https://www.semanticscholar.org/author/103767116
- Primary paper: https://arxiv.org/abs/2306.14816 names C Henrik Åslund on Experiments with Detecting and Mitigating AI Deception.
- Exact S2 response: https://api.semanticscholar.org/graph/v1/paper/DOI:10.48550/arXiv.2306.14816?fields=title,authors,externalIds returns paperId f536bef81ca3a65bc4e8df95961044d473606dc8, the exact title and arXiv ID, and C. Åslund with authorId 103767116 alongside Ismail Sahbane and Francis Rhys Ward.
- S2 author external IDs include DBLP C. Henrik Åslund; its bibliography includes Virtuously Safe Reinforcement Learning.
- The profile also contains a possibly unrelated Swedish law thesis. Paper-level identity checks remain required for relevant new inclusions.

## Scope and coverage limitations

Both David Duvenaud and David Dalrymple remain configured in their existing tiers. Gavan Muler is set aside until an identifiable researcher is supplied. All other existing configured names retain their scope and tiers. Supported spelling corrections and aliases above are recorded explicitly in the immutable migration policy. No profile is represented as exhaustive; fragmented profiles and merged namesakes remain source-quality limitations rather than proof of complete author bibliography coverage.
