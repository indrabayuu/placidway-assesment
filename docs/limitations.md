# Limitations and next steps

The agent answers from seven approved English PlacidWay pages. It cannot verify live availability, use unapproved sources, book care, or assess individual medical suitability. Prices can change and are not personalized totals; extracted pages may omit ambiguous rows. Video evidence covers visible listings only.

The current refactor has not passed its DeepEval gate: 51/60 single-turn, 6/12 conversation, 8/11 language, and 7/8 refactor checks passed. The runtime review can reject supported answers, and it missed a claim that a starting price was guaranteed as a final total. Without runtime checks, the offline pass count fell from 65/83 to 62/83. See [evaluation details](evaluation.md).

Thinking and provisional answers are streamed before review, so rejected text may already have appeared. The main model and reviewer share a provider and can make correlated errors. Language quality varies; the finite regression set is not an independent holdout. The ablation also showed a clear latency reduction without review.

The public demo is still the previous release and uses HTTP; HTTPS and seven-day availability have not been verified. The service runs one API worker, and users sharing a login secret share conversation history.

Priorities: fix review false rejections and citation/price associations, add independently reviewed holdout cases, validate new source versions, and complete HTTPS, backup/restore, monitoring, and availability checks before claiming release readiness.
