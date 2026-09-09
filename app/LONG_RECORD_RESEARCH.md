# Long Patient Records in SmartDoc

## Recommendation

SmartDoc should use model-aware direct summarisation for records that fit the local model, with explicit protection against input truncation. The immediate failure was caused by an application ceiling of 32,768 tokens and a character-based estimate, rather than proof that the installed model could not accept the record. The revised default ceiling is 131,072 tokens, with automatic selection of smaller windows for shorter records. The installed Gemma 4 12B reports a native capacity of 262,144 tokens, consistent with Google's model card.[^2]

This recommendation addresses the current failure without introducing an intermediate summary that can discard a medication change, negation, allergy or follow-up plan. It also preserves the existing local workflow: five concurrent transcription and identifier-detection tasks, one consistent pseudonym mapping, one combined summary, and restoration of identifiers after generation. It does not require another model download or an external service.

The recommendation is conditional on measured fit, available memory and clinical review. A context window is an input capacity, not a completeness guarantee. The app still targets a summary of at most 200 words, which necessarily selects from the record. Accepting all source text, generating valid citations, and producing a clinically adequate summary are three different outcomes and must be assessed separately.

For records beyond the direct limit, the next candidate is structured extraction of clinical facts with source references, followed by retrieval and reconciliation against the original passages. That is a development proposal, not an implemented fallback or a validated replacement. The evidence does not justify silently switching to ordinary chunk summaries whenever an input is too large.

## The capacity problem

There are four distinct limits in the current workflow. Upload validation limits a batch to ten documents and 64 MB, and limits an individual PDF to 500 pages. Transcription converts supported content to text. Identifier replacement may expand short names and dates into longer placeholders. Finally, the summariser must fit the source, instructions, evidence labels, formatting overhead and generated answer into the selected context window. Increasing the context changes only this final capacity.

The previous sizing helper selected from windows up to 32,768 tokens and rejected records whose estimated size exceeded that ceiling. It did not ask the installed model for its actual context capacity. Since characters are not tokens, the estimate could reject an input that the model's tokenizer would have accepted. It also obscured the difference between an app configuration limit and a model limit.

The revised code asks the local runtime for model metadata and uses the smaller of the configured ceiling and the native model capacity. A UTF-8 byte heuristic chooses a starting size and includes an output allowance. Crucially, this heuristic is not an admission test: even an estimate above the configured maximum is submitted at that maximum so that the actual tokenizer can decide. A genuine context overflow triggers a retry at a larger permitted size, retaining the original source.

The request explicitly disables both input truncation and context shifting. The verified Ollama 0.32.1 implementation exposes these request controls; its default behavior must not be assumed to preserve every source token. The app therefore checks for a compatible runtime and fails clearly on unsupported versions.[^3] This is a serving safeguard, separate from whether the model attends to every relevant passage.

The default 131,072-token ceiling is an engineering operating point, not a research-derived optimum. It is four times the previous configured maximum and below the model's advertised ceiling. The setting can be raised to 262,144 tokens on a suitable workstation, but that upper setting has not been validated here for memory use, latency or clinical quality. There is no reliable conversion from that token limit to a fixed number of pages.

## Findings from the supplied thesis

The supplied thesis is directly relevant because it studies Swedish patient journals, transcription, pseudonymisation and local open-weight summarisation. Its final comparison includes direct, retrieval-augmented and hierarchical approaches. However, the experimental context was fixed at 4,096 tokens, with 1,024 output tokens and an estimated source allowance of approximately 2,717 tokens. That is materially different from the larger context available in the installed model.[^1]

In the thesis's direct condition, complete records were used when they fitted. Otherwise, only a prefix of complete source units was included. That experimental truncation policy should not become the application's normal fallback. It can systematically exclude later follow-up, changed medication status or a correction to an earlier note. The present implementation instead preserves all source units in the prompt or reports that the full record cannot fit.

The tested hierarchical condition contained several potential loss points. Input chunks were approximately 1,300 estimated tokens, with a 700-token intermediate response budget. Strict parsing could discard a chunk's extracted cards. The deduplication key omitted several clinically meaningful fields, including polarity, certainty, temporality, subject and unit. The synthesis input could then be shortened by repeatedly removing its trailing portion to fit the final budget. These choices make it difficult to attribute a poor result to hierarchy alone.[^1]

The thesis did not retain the intermediate map responses, limiting diagnosis of exactly where evidence disappeared. This matters when interpreting the reported outcome: the Gemma and Qwen hierarchical conditions produced no parseable procedural claims in the reported evaluation. Their recall was zero and F1 was undefined, rather than a measured zero F1 for every aspect of summary quality. The third model had a procedural F1 of 0.038. This is strong evidence against copying that particular implementation unchanged, but not proof that every staged method fails.[^1]

The direct and retrieval conditions were much closer. For the procedural evaluation, Gemma's F1 values were 0.627 and 0.629; Qwen's were 0.656 and 0.675. These differences do not establish a universal winner. There were six held-out procedural graphs, five real cases and two seeds, and the methods differed in what information reached the final prompt. The research configuration is not a substitute for a representative clinical deployment evaluation.[^1]

The real-case context-stress gate was also not met. Only two of the five real packets exceeded the nominal budget, and mean excluded content was 16.03%; the predefined requirement was at least three packets and 25% mean exclusion. Consequently, this comparison provides limited evidence about the situation where most real records greatly exceed a model's usable context. It should not be presented as a decisive benchmark of long-record architectures.[^1]

Retrieval used multilingual E5 embeddings, six clinical query categories and a diversity-aware selection strategy, with source order restored before generation. This is more structured than one generic similarity query, but relevant information can still be missed if it is poorly represented by the retrieval queries. The thesis also observes that including source material does not ensure that the final summary includes its facts. Its most useful lesson for SmartDoc is to audit each stage's information coverage and failures separately.[^1]

## Evidence from other research

**MedAlign** provides a relevant demonstration that input capacity can matter for EHR tasks. Its clinician-generated dataset contains 983 instructions, with expert reference responses for a subset. The reported GPT-4 comparison improved correctness from 51.8% with a 2,048-token context to 60.1% with 32,000 tokens, an 8.3 percentage-point difference. This supports testing larger direct input, but it does not predict the gain for Swedish Gemma summaries: the models, tasks and records differ, and substantial errors remained.[^4]

**Lost in the Middle** shows why simply fitting a record is insufficient. In multi-document question answering and key-value retrieval, relevant information was often used less reliably when located in the middle of the context than near its boundaries. The study is not a clinical evaluation of Gemma 4. Its practical implication here is an evaluation design: move the same critical fact through early, middle and late positions and measure whether its meaning survives.[^5]

**RULER** broadens testing beyond a single hidden-fact retrieval task to include multiple retrieval requirements, aggregation and multi-hop reasoning. Its results distinguish advertised context length from effective performance across increasing lengths. For SmartDoc, a model-card capacity should therefore be treated as an upper input boundary, while the usable operating range must also be supported by task-specific quality and runtime measurements.[^6]

**LongHealth**, in the 2024 preprint version reviewed here, uses 20 fictional patient cases and 400 questions. It tests information extraction across long records, distracting material and recognition that requested information is missing. The evaluated models struggled particularly with missing information. The benchmark supports including explicit absence-of-evidence tests; an omitted mention of allergy is not equivalent to a documented negative allergy history. Its older models and question-answering format limit direct comparison with this app.[^7]

**Kruse and colleagues' longitudinal clinical study** evaluates open models, retrieval and prompting on MIMIC-III and EHRShot-derived tasks. Longer contexts improved integration without consistently improving clinical reasoning, and temporal progression remained difficult. Retrieval reduced hallucinations in some settings but did not resolve the general problem. The work is more clinically aligned than generic document benchmarks, yet it does not validate Gemma 4 on Swedish journals; its limited human evaluation also constrains strong claims about clinical superiority.[^8]

**SummN** provides a counterpoint to the thesis's poor hierarchical result. Its staged coarse-to-fine summarisation improved benchmark results on long dialogues and documents, including meeting and government-report datasets. This establishes that a well-designed staged method can be useful. Those nonclinical results do not establish preservation of medication status, dose, negation or chronology, so ordinary map/reduce cannot inherit a clinical safety claim from them.[^9]

**Summary of a Haystack** examines query-focused summarisation with known supporting insights and source citations. It exposes trade-offs between evidence selection, citation quality and insight coverage; retrieval can improve some dimensions while leaving relevant insights out. Its task differs from patient summarisation, but the evaluation distinction is valuable: a cited answer can still omit important evidence, and an existing citation can still be interpreted incorrectly.[^10]

Across these sources, no reviewed result establishes a best architecture for Gemma 4 12B, Swedish OCR-derived longitudinal records, restored identifiers and a 200-word final summary on this workstation. The evidence supports a measured, staged engineering decision: remove the artificial capacity restriction first, preserve the original evidence, and benchmark more complex alternatives against that stronger baseline.

## Architecture comparison

| Approach | Fit for SmartDoc | Principal limitation | Decision |
|---|---|---|---|
| Adaptive direct context | Keeps every source unit available; minimal workflow change | Memory, latency and omissions can increase with length | Implement first |
| Retrieval-only summarisation | Can focus a bounded prompt on selected clinical topics | Relevant facts can be excluded before generation | Evaluate for focused questions or a verified fallback |
| Ordinary chunk summaries, then synthesis | Can process input beyond one window | Early compression can remove facts that cannot be recovered later | Do not add as an automatic default |
| Structured fact extraction plus source retrieval | Can preserve explicit fields and link synthesis to original evidence | Extraction omissions, reconciliation complexity and extra calls | Preferred future candidate beyond direct capacity |
| Different or remotely hosted model | May offer other capacity/performance options | Requires a new quality, hardware and data-handling assessment | Not needed for the present capacity defect |

The first choice is the smallest architectural change that addresses the observed failure. It avoids making a retrieval index, selecting a new embedding model or adding a second generative compression stage before the larger direct baseline has been measured. It also keeps the present sentence-to-source traceability intact.

Retrieval has a stronger initial use case for a focused question, such as the latest documented medication change, than for a general summary expected to cover all clinically salient topics. General summarisation requires either reliable coverage across categories or a mechanism to detect missing topics. A similarity-ranked list alone is not a coverage certificate. This is an engineering judgment informed by the studies above, not a comparative clinical result for SmartDoc.

## Implemented behavior

The new context planner reads the selected model's native capacity and the installed Ollama version. It chooses from progressively larger windows, beginning with 8,192 tokens for small requests and ending at the configured maximum. The planner includes instructions, source labels, the response schema and a 2,048-token generation allowance in its sizing estimate. These are operational allowances, not exact token counts.

If Ollama reports actual context overflow, the same complete source prompt is retried in the next permitted window. If the estimate was overly conservative, a record can succeed at the cap instead of being rejected merely by its character count. Allocation errors and unrelated runtime failures do not trigger progressively larger memory requests. If no permitted window fits, the job stops without returning a partial summary.

Existing output checks remain active. Summary sentences must cite valid evidence IDs; incomplete generation and malformed output trigger the bounded formatting retry and then fail if unresolved. Numerical discrepancies against cited passages are exposed for review. These checks establish output structure and some traceability, not that the cited passage entails the sentence or that every important fact was selected.

Run telemetry now records the selected context, configured and model limits, estimated total size, actual runtime token counts when returned, retries, runtime version and explicit truncation protections. These measurements support diagnosis without storing an additional copy of patient text in technical logs. The source remains available through the existing pseudonymised evidence view.

The change does not alter encryption or restoration. Identifier detection still reads the original text locally, all detections are merged before a single mapping is assigned, and the temporary encrypted mapping is removed after restoration or handled failure. A larger input window neither improves identifier-detection recall by itself nor changes which local files are retained by the existing app.

## A future path beyond direct capacity

For substantially larger longitudinal records, evaluate a structured extraction stage instead of compressing each chunk into free prose. Each extracted item should retain the patient or subject, clinical concept, value, unit, polarity, certainty, temporal status, source location and the original supporting text. Medication items should distinguish started, continued, changed, held and stopped states. A historical negative should not cancel a later positive without explicit temporal reasoning.

Chunk boundaries should follow documents, pages and clinical sections where possible. Use overlap for boundary-spanning statements, while preserving stable source offsets so overlapping detections can be reconciled without inventing duplicates. Every chunk must have an explicit success or failure status. A parse failure must lead to a retry or visible job failure, not an empty list that appears to be successful processing.

Deduplication must preserve clinically meaningful differences. Two items with the same drug and dose can describe different dates or opposite statuses. Two measurements with the same number can have different units or subjects. Retain contradictory candidates and their evidence until a reconciliation stage can explain the relationship; do not discard one merely to save prompt space.

Before final synthesis, retrieve the original passages supporting candidate facts, including neighboring context and relevant later changes. If the complete fact inventory still exceeds the final budget, retain a reviewable structured companion document and perform explicit staged reconciliation. Do not repeatedly drop the last portion of the inventory. The short final summary should make unresolved conflicts visible.

This candidate should be compared against direct generation on exactly the same cases. For cases that exceed the direct cap, evaluate factual coverage against clinician-selected reference facts rather than treating the compressed method's ability to finish as proof of improvement. Additional extraction and verification calls have a latency cost that should be measured along with any gain in coverage.

## Performance and operating limits

Page concurrency and summary capacity solve different problems. Five concurrent OCR or identifier tasks can shorten preparation, while the final combined summary is one generation over all selected source text. Increasing page workers does not expand that generation's input window. The existing five-page setting therefore remains useful, but is not the remedy for the reported context error.

Ollama documents that parallel requests increase context-memory requirements and that larger context windows require more memory.[^11] The exact cost depends on the model architecture, attention implementation, cache types and offloading. Gemma's hybrid attention means a generic memory estimate should not be presented as a measured figure for this machine. A larger configured window can also cause a model reload between short identifier requests and long summarisation.

The operating policy should be to keep smaller windows for short inputs, admit long summaries only within measured machine capacity, and measure end-to-end latency rather than tokens per second alone. If simultaneous long summaries cause memory pressure, a separate limit on long-generation concurrency is preferable to reducing the five-page OCR limit. That scheduling enhancement is not part of the current change.

Neither 128K nor 256K should be described as unlimited input. Very large batches can still exceed the combined context budget, consume excessive memory or time out. The 200-word output limit also becomes more selective as source length grows. A future longer, structured clinical overview is a separate product choice and should not be introduced silently by changing the input limit.

## Verification and evaluation

The automated regression suite checks model-capacity discovery, unsupported runtime versions, invalid limits, preservation of every source unit, retries after actual tokenizer overflow, success despite an overestimated size, failure at the hard cap and immediate propagation of unrelated memory errors. Existing privacy, export, transcription, concurrency and updater tests remain part of the regression checks.

A local negative test submitted 6,316 actual prompt tokens with a deliberately small context request. Ollama reported an effective 2,048-token context and returned HTTP 400 with an explicit context-size error. With truncation and shifting disabled, the runtime rejected the input instead of generating from a shortened prompt. This verifies the relevant serving behavior on the installed version; it does not measure summary quality.

A local synthetic Swedish record contained 202,639 characters across 643 evidence units. The successful request used a 98,304-token window and reported 65,646 actual prompt tokens, exceeding the previous 32,768-token maximum. All source units were preserved in order. The 53-word summary retained the inserted penicillin allergy with prior anaphylaxis, the discontinued metformin dose and status, and the planned diabetes follow-up from early, middle and final source positions.

This run took 552.56 seconds for the summarisation call, including loading and one output-format retry; transcription and identifier detection were not included. The initial generation reached its 2,048-token output limit, while the shorter retry completed with 317 generated tokens. It was a single run using the existing model and five-slot Ollama configuration, not a throughput average or a measurement of a full ten-document workflow.

The result also invented a count of 199 prior follow-ups. That count was unsupported by its cited passages and the existing numeric discrepancy check flagged it. Therefore the outcome establishes larger-input feasibility and retention of three selected facts, but it does not establish clinical correctness. The warning is evidence that review remains necessary, not a claim that every hallucination will be detected. The test did not exercise the full 131,072-token ceiling or the optional 262,144-token setting.

| Local check | Observed result |
|---|---|
| Automated regression checks | 74 passed, including 13 new context tests |
| Synthetic source size | 202,639 characters; 643 source units |
| Actual prompt tokens on successful attempt | 65,646 |
| Allocated context | 98,304 tokens; configured cap 131,072 |
| Source preservation | Every evidence unit retained in source order |
| Selected beginning/middle/end facts | All three represented with supporting citations |
| Output completion | One formatting retry; final generation completed |
| Factual limitation | Unsupported visit count, flagged by numeric review check |
| Measured summarisation time | 552.56 seconds for this run |


The next quality evaluation should include held-out Swedish records across short, medium and long token ranges, with clinician-selected critical facts. Reposition an unchanged allergy, discontinued medication, dosage, laboratory unit, uncertainty and follow-up plan near the beginning, middle and end. Include later corrections, duplicated notes, contradictions, missing information, OCR corruption and facts split across document boundaries.

Measure critical-fact recall, unsupported claims, negation and temporal-status errors, dosage/unit errors, citation support, identifier restoration and processing failures. Report these alongside actual input tokens, latency, peak memory and retries. Assess individual high-consequence errors as well as aggregate scores. A fluent summary and a perfect JSON success rate should not conceal missed clinical facts.

The synthetic capacity check is a regression and feasibility test. It is not a clinical accuracy estimate, does not establish the maximum safe context on other machines, and does not reproduce the thesis's held-out evaluation. A production quality claim requires representative records and review beyond these engineering checks.

## Sources

[^1]: Aleksander Amid. *A Stage-Wise Evaluation of AI-Driven Transcription Summarization of Swedish EHR Patient Journals Using Open-Weight Large Language Models*. Master's thesis, Biomedical Engineering, Chalmers University of Technology, 2026. Supplied file: `AI_Driven_Summarization_of_Swedish_Electronic_Health_Record_Patient_Journals_Usi (10).pdf`, 97 PDF pages. Relevant PDF pages: 46-47, 58-59, 63-64 and 67-71. Page references use PDF page positions, not printed chapter pagination. Private supplied copy; no public URL asserted.

[^2]: Google DeepMind. [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4). Official model documentation, accessed 9 September 2026. Used for the 12B model's native context capacity and architecture, not as clinical validation.

[^3]: Ollama contributors. [API request types, version 0.32.1](https://raw.githubusercontent.com/ollama/ollama/v0.32.1/api/types.go) and [request routing, version 0.32.1](https://raw.githubusercontent.com/ollama/ollama/v0.32.1/server/routes.go). Version-pinned primary source, accessed 9 September 2026. Defines and forwards the explicit truncate and shift controls.

[^4]: Scott L. Fleming et al. [MedAlign: A Clinician-Generated Dataset for Instruction Following with Electronic Medical Records](https://arxiv.org/abs/2308.14089). arXiv:2308.14089, version 2, 24 December 2023. Context comparison in the full paper, PDF p. 8.

[^5]: Nelson F. Liu et al. [Lost in the Middle: How Language Models Use Long Contexts](https://arxiv.org/abs/2307.03172). Transactions of the Association for Computational Linguistics, 2024; preprint first released 2023. Evidence on positional sensitivity in long-context tasks.

[^6]: Cheng-Ping Hsieh et al. [RULER: What's the Real Context Size of Your Long-Context Language Models?](https://arxiv.org/abs/2404.06654). COLM 2024; arXiv:2404.06654. Version 3 full text reviewed for effective-context evaluation.

[^7]: Lisa Adams et al. [LongHealth: A Question Answering Benchmark with Long Clinical Documents](https://arxiv.org/abs/2401.14490). arXiv:2401.14490, version 1, 25 January 2024. Findings here refer to this preprint version rather than an assumed later journal revision.

[^8]: Maya Kruse et al. [Large Language Models with Temporal Reasoning for Longitudinal Clinical Summarization and Prediction](https://aclanthology.org/2025.findings-emnlp.1128/). Findings of EMNLP, Association for Computational Linguistics, November 2025, pp. 20715-20735. Full paper includes evaluation and human-review limitations.

[^9]: Yusen Zhang et al. [SummN: A Multi-Stage Summarization Framework for Long Input Dialogues and Documents](https://aclanthology.org/2022.acl-long.112/). ACL, Association for Computational Linguistics, May 2022, pp. 1592-1604. Nonclinical staged-summarisation evidence.

[^10]: Philippe Laban, Alexander R. Fabbri, Caiming Xiong and Chien-Sheng Wu. [Summary of a Haystack: A Challenge to Long-Context LLMs and RAG Systems](https://aclanthology.org/2024.emnlp-main.552/). EMNLP, Association for Computational Linguistics, November 2024, pp. 9885-9903. Evidence on summary coverage and citation evaluation.

[^11]: Ollama. [Context length](https://docs.ollama.com/context-length) and [Concurrent requests](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests). Official serving documentation, accessed 9 September 2026. Used for context configuration and parallel-memory trade-offs.
