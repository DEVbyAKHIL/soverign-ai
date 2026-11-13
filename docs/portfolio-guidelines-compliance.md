# IU Portfolio Guidelines compliance

Audit date: 2026-11-07. The official guideline and assignment PDFs were not in the repository; the requirements below are taken from the audit brief.

PebblePad and myCampus submissions are outside the repository, so those rows are **UNVERIFIABLE** unless a file in the repository proves them.

**Writing rule:** the personal and reflective texts (concept, reflection, abstract) must be written by the student. The repository can supply the facts, figures and diagrams they draw on; it should not supply the student's reflection.

| Area | Requirement | Status | Evidence | What is needed |
|---|---|---|---|---|
| Structure | Three phases: conception → development/reflection → finalisation | **FAIL** | No phase documents; the README uses engineering phases (1-8, S1-S3) | `docs/portfolio/phase-1-conception.md`, `phase-2-development.md`, `phase-3-finalisation.md`, linked from the README |
| Structure | Phase order kept | **UNVERIFIABLE** | Submission dates are not in the repository | A submission date in each phase document |
| Phase 1 | Aim of the work | PARTIAL | `README.md:1-5` | One paragraph in the phase 1 document |
| Phase 1 | Ideas and concept | PARTIAL | `docs/task2-gap-analysis.md`, `docs/refactor-plan.md` | Summarise in the phase 1 document |
| Phase 1 | Sustainability of the idea: positives and negatives | **FAIL** | Not written anywhere | A pros/cons section (e.g. reuse of the gateway telemetry vs single-node limits and resource cost) |
| Phase 1 | Planned procedure | PARTIAL | `docs/implementation-order.md` | Short form in the phase 1 document |
| Phase 1 | Methodology and tools, with reasons | PARTIAL | `docs/kafka.md`, `docs/spark-streaming.md`, `docs/architecture-after-refactor.md` §5-6 | `docs/portfolio/tools.md`: each tool, why, alternative considered |
| Phase 1 | Architecture draft **PNG** | **FAIL** | No `*.png`; Mermaid only (`DATA_FLOW.md`, `ARCHITECTURE.md`) | `docs/portfolio/architecture.png` |
| Phase 1 | Remarks explaining the architecture | PARTIAL | `DATA_FLOW.md` stage descriptions | Remarks directly under the PNG |
| Phase 1 | Concept text in PebblePad (about 200 words) | **UNVERIFIABLE** | Outside the repository | Student writes and uploads it |
| Phase 2 | What is implemented / what remains | PARTIAL | README status tables; `EVALUATION.md` | Phase 2 document |
| Phase 2 | Challenges, difficulties and how they were addressed | PARTIAL | `EVALUATION.md` run-1 diagnosis | Phase 2 document with at least 3 challenge → solution entries. Candidates from the record: the RocksDB `noexec /tmp` failure, the async-generator read-back bug, watermark skew from `maxOffsetsPerTrigger`, the Docker disk move |
| Phase 2 | Lessons learned, future improvements | **FAIL** | Not written | Student-written reflection |
| Phase 2 | `.txt` with the GitHub link | **FAIL** | No `.txt`; remote `main` lacks the streaming code | Push and merge first, then create the `.txt` |
| Phase 2 | Text in PebblePad | **UNVERIFIABLE** | Outside the repository | Student uploads it |
| Phase 3 | 50-word abstract in PebblePad | **UNVERIFIABLE** | Outside the repository | Student writes it (45-55 words) |
| Phase 3 | 2-page PDF abstract | **FAIL** | No PDF | Student-written PDF |
| Phase 3 | Final `.txt` with the GitHub link | **FAIL** | None | The `.txt` pointing at the final `main` |
| Phase 3 | All three parts uploaded again | **UNVERIFIABLE** | Outside the repository | Checklist item |
| Format | DIN A4 | **UNVERIFIABLE** | No PDF | 595 × 842 pt |
| Format | 2 cm margins | **UNVERIFIABLE** | No PDF | Template setting |
| Format | Arial 11 pt body, 12 pt headings, 10 pt footnotes | **UNVERIFIABLE** | No PDF | Check with `pdffonts` (Arial embedded) |
| Format | 1.5 line spacing, justified, hyphenation | **UNVERIFIABLE** | No PDF | Template setting plus a visual check |
| Format | Maximum 2 pages | **UNVERIFIABLE** | No PDF | Page count of 2 or fewer |
| Product | Clear product (software, data architecture, streaming backend, dashboard) | **PASS** | Running stack; `EVALUATION.md`; `frontend/src/pages/live/*` | - |
| Tools | Selection, parameters and use of tools documented | PARTIAL | `docs/kafka.md`, `docs/spark-streaming.md`, `docs/docker-compose.md`, `ci.yml` | `tools.md` with rationale for React, GitHub Actions, the test, security and monitoring tools |
| Reflection | Decisions, alternatives, problems, solutions, learning | PARTIAL | `EVALUATION.md`, `docs/aggregations.md`, `docs/task2-gap-analysis.md` §6 | "Alternatives considered" and "lessons" sections |

## Summary

**Score:** 33/100 for this section (PASS = 1, PARTIAL = 0.5, FAIL = 0; UNVERIFIABLE rows excluded).

The software product is strong. The portfolio wrapper around it is missing.

**Smallest set of work to make it submittable:**
1. Push and merge the streaming code.
2. Create the architecture PNG with remarks.
3. Write the three phase documents and `tools.md`.
4. Create the link `.txt` files.
5. Write the 2-page PDF to the format rules and the 50-word abstract.
