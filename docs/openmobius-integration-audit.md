# OpenMobius Integration Audit

Repository surveyed: `OpenMobius-skill`, `main` as of 2026-07-06.
Audited 2026-08-17. **No code, card text, or embedding was copied.**

---

## 1. Verdict first

The repository is a genuinely useful **terminology and pedagogy reference** and
is **not** a source of market truth. Three findings drive that split:

1. **No `LICENSE` file.** `ATTRIBUTION.md` states Apache-2.0 and links
   `./LICENSE`; that file is absent from the reviewed distribution. The grant
   cannot be verified from what was supplied.
2. **Every structural indicator output is future-aware.** Ten of eleven audited
   outputs are retrospective, delayed, or opaque. Details in
   [`openmobius-temporal-audit.md`](openmobius-temporal-audit.md).
3. **Cases are unreviewed machine extractions.** All 1,282 carry
   `review_status: "pending"`; all were produced by VLMs from YouTube videos.

Consequence: we take the vocabulary and write our own definitions. Nothing is
vendored.

## 2. Architecture

```
OpenMobius-skill/
├── knowledge_base/
│   ├── concepts/     726 JSON cards
│   ├── cases/       1282 JSON cards
│   ├── index.json, term_aliases.json, _merge_report.json
├── scripts/
│   ├── kb_klines.py        2,733 lines — market data client + SMC indicator
│   ├── kb_retrieve.py      RAG query interface
│   ├── build_index.py      Chroma index build
│   ├── kb_draw_annotation.py  PIL annotation on user images
│   ├── chart_render/       lightweight-charts + headless chromium
│   └── _lib/{embedder,retriever}.py
├── workflows/       analyze, annotate, klines, qna
└── platforms/, docs/
```

Total 2,051 files, 64 MB. It is a **Claude skill package**, not a library:
scripts are CLI entry points driven by markdown workflows.

## 3. Knowledge-base schema

**Concept card** (726):

```
global_card_id · global_canonical · type · school · aliases · definition
definition_per_source · identification_rules · common_mistakes
trading_implication · merge_notes · merge_strategy · merged_at
source_cards[] · illustrated_by_cases[] · related_concepts · _embedding
```

`_embedding` is a 768-dim vector stored inline in every card — the reason the
repository is 64 MB.

Composition: 655 Latin-script canonical names, **71 CJK**. Schools:
ICT 262 · Price Action 104 · SMC 84 · Indicator Based 74 · General 62 ·
缠论 (Chan Theory) 58 · Risk Management 44 · Order Flow 18 · others 19.

Upstream projects named in `source_cards`: `Education - ICT` (380),
`Education - ICT (Updated)` (206), `SMC-Strong-Weak-Supplement` (169),
`Teach-Wuyuan` (140), `SMC-Indicator-Supplement` (109), and seven more.
127 cards carry no source project.

**Case card** (1,282):

```
card_id · title · school · asset · timeframe · market_context · key_observation
analysis_steps[] · lessons · outcome · illustrates_concepts[] · related_concepts[]
sources[{video_id, segment_ids, time_range}] · primary_image · supporting_images[]
image_descriptions{} · model_used · extraction_confidence · confidence_reason
review_status · raw_response · _embedding
```

Assets: NQ1! 177 · EURUSD 130 · GOLD 119 · BTCUSDT 49 · XAUUSD 47 · ES1! 45 ·
ES 40 · GBPUSD 34 · NAS100 31 · ETH 31 · 58 unlabelled.

## 4. Case provenance — the critical limitation

Every case is a **vision-language-model extraction from a YouTube video**:

| Property | Value |
|---|---|
| `review_status: pending` | **1,282 / 1,282** |
| `extraction_confidence` | high 1,206 · medium 76 |
| Distinct source `video_id`s | 306 |
| Extracting models | qwen3-vl-plus 423 · claude-haiku-4.5 369 · claude-opus-4.7 221 · claude-opus-4-8 153 · claude-sonnet-4.6 116 |

Not one card has been human-reviewed. `raw_response` retains the model's
unparsed output. `image_descriptions` include promotional banner text captured
from the source frames.

This makes the corpus a record of **what was taught**, twice removed: a model's
reading of an instructor's narration of a chart they chose to show. Three
selection layers sit between it and the market, and each favours examples that
worked.

## 5. Retrieval system

ChromaDB + `sentence-transformers` with `nomic-ai/nomic-embed-text-v1.5`
(Apache-2.0, downloaded at install; weights not redistributed). Optional
remote OpenAI-compatible embedding endpoint.

We import **no embeddings and no index**. Our retrieval is deterministic
lexical rank over 18 concepts — reproducibility matters more than recall at
this size, because a hypothesis family that reorders between runs cannot be
pre-registered.

## 6. Structural indicator

`scripts/kb_klines.py` implements: `calc_atr`, `find_swings`, `find_fvgs`,
`find_order_blocks`, `find_sweeps`, `find_displacements`,
`find_volume_anomalies`, `analyze_structure` (HH/HL/LH/LL → BOS/CHoCH).

Premium/discount bands, equal highs/lows and trailing extremes are **not
computed locally** — they arrive as opaque `objects` from a remote service.

Full temporal findings: [`openmobius-temporal-audit.md`](openmobius-temporal-audit.md).

## 7. External API usage

```
MOBIUS_API_BASE = https://api.mobiusquant.ai   (default, env-overridable)
```

Endpoints: `health`, `symbols_search`, `symbols_builtin`, `markets`, `klines`,
`indicators`. The header comment states no credentials are required.

**Not integrated.** An unauthenticated third-party market-data service of
unstated provenance cannot satisfy the Phase 9 ingestion gate: it is not a
named provider with documented adjustments, and its indicator outputs are
precisely the opaque ones the temporal audit bars.

## 8. Chart generation

`lightweight-charts` (Apache-2.0, TradingView) in headless Chromium via
Playwright, plus PIL annotation of user-supplied images. Rendering patterns
worth reusing conceptually: FVG/OB rectangles, liquidity lines, BOS/CHoCH
markers, entry/stop/target, pivot labels, premium/discount bands.

Reuse is of the **visual grammar only**. Any future panel renders our validated
features, never OpenMobius calculations.

## 9. Dependencies

`chromadb>=0.5` · `sentence-transformers>=3.0` · `numpy>=1.24` · `einops>=0.7` ·
`Pillow>=10` · `playwright>=1.40` · `openai>=1.50` (optional).

All Apache-2.0/BSD/MIT. **None added to this project** — we import no runtime
dependency from the survey.

## 10. Licence and attribution status

| Item | Finding |
|---|---|
| `LICENSE` file | **ABSENT** from the distribution |
| Declared licence | Apache-2.0, per `ATTRIBUTION.md` |
| Card counts in `ATTRIBUTION.md` | "380 concept cards and 584 case cards" |
| Actual counts | **726 concepts, 1,282 cases** |
| Card content | claimed original paraphrase of public educational material |
| Third-party sources | 306 YouTube videos; named upstream course projects |

Two documentation discrepancies — a missing licence file and stale counts —
in the one file that governs redistribution.

**Our position.** Apache-2.0 would permit vendoring with attribution, but the
grant is unverifiable from what was supplied, and the underlying material is
derived from named third parties' course content. So we take the position the
licence uncertainty requires:

- **No card text, no embeddings, no code, no case data copied.**
- Only canonical names, aliases and structural observations retained — facts
  about terminology, not expressive content.
- All 18 definitions in our ontology are **our own paraphrase** of widely
  published ICT/SMC terminology.
- Attribution preserved in `KnowledgeSource`, with the licence state recorded
  verbatim including the missing file.

This is the minimum-metadata path the brief calls for when a dataset is
unsuitable for direct redistribution.

## 11. Data-provenance limitations

1. Cases are unreviewed machine extractions from third-party video.
2. Case outcomes are narrative, not measured, and selected for teaching value.
3. Concepts are merged across projects; `merge_strategy` is often
   `single_source_passthrough`, so "curated" sometimes means "one source, copied".
4. 71 concepts and a substantial share of cases are Chinese-language; 58 cases
   are Chan Theory, unrelated to ICT.
5. Market data comes from an unauthenticated third-party API of unstated
   provenance.
6. Indicator outputs carry `age_bars` relative to the end of the array passed,
   so a stored record silently misstates age.

## 12. What we took

| Taken | Not taken |
|---|---|
| Canonical term names | Card definitions |
| Alias sets | `identification_rules`, `trading_implication` |
| Category structure | Case text, images, outcomes |
| Concept relationships | Embeddings and the Chroma index |
| Visual grammar (conceptual) | Indicator code and outputs |
| Knowledge that these terms exist and are taught together | Any claim they work |
