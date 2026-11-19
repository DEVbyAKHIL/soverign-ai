// Phase 3: two-page abstract (Word). IU format: A4, 2 cm margins, Arial 11 body, Arial 12
// headings, Arial 10 footnotes, 1.5 line spacing, justified, hyphenation, max 2 pages.
// Usage: node phase3-docx.js <output.docx> ["Full Name"] ["Matriculation no."] ["DD.MM.YYYY"]
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, AlignmentType, HeadingLevel, FootnoteReferenceRun,
} = require("docx");

const [out, name = "[Name]", matr = "[Matriculation number]", date = "[DD.MM.YYYY]"] = process.argv.slice(2);
const LINE = 360;
const AFTER = 120;
const p = (...runs) => new Paragraph({
  alignment: AlignmentType.JUSTIFIED, spacing: { line: LINE, after: AFTER },
  children: runs.map((r) => (typeof r === "string" ? new TextRun(r) : r)),
});
const h = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(text)] });

const doc = new Document({
  creator: name,
  title: "Phase 3: Abstract",
  hyphenation: { autoHyphenation: true, consecutiveHyphenLimit: 2, hyphenationZone: 357 },
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: "Arial", size: 24, bold: true },
        paragraph: { spacing: { before: 120, after: 60, line: LINE }, keepNext: true, outlineLevel: 0 } },
      { id: "Title", name: "Title", basedOn: "Normal", next: "Normal",
        run: { font: "Arial", size: 24, bold: true },
        paragraph: { spacing: { before: 0, after: 60, line: 300 }, keepNext: true } },
      { id: "FootnoteText", name: "footnote text", basedOn: "Normal",
        run: { font: "Arial", size: 20 }, paragraph: { spacing: { after: 0 } } },
    ],
  },
  footnotes: {
    1: { children: [new Paragraph({ style: "FootnoteText", children: [new TextRun(
      "Repository: https://github.com/DEVbyAKHIL/soverign-ai. Measurements, logs and the verification scripts are in EVALUATION.md and docs/evidence.")] })] },
  },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1134, right: 1134, bottom: 1134, left: 1134 } } },
    children: [
      new Paragraph({ style: "Title", children: [new TextRun(
        "Sovereign AI Access Gateway: A Real-Time Streaming Data Backend for AI Governance, Security, and Operational Analytics")] }),
      new Paragraph({ spacing: { after: 120 }, children: [new TextRun({
        text: `DLMDSEDE02 Data Engineering · Phase 3 abstract · ${name} · ${matr} · ${date}`, color: "444444" })] }),

      h("Problem and aim"),
      p("Organisations that let staff and AI agents use language models need two things at once: sensitive data must not reach models or countries it should not, and someone has to be able to show later that the rules were applied. I treated the second part as a data engineering problem. A security gateway already decides on every AI request, so I turned each decision into a timestamped event and built a real-time backend that ingests, processes, aggregates and delivers these events as live governance metrics."),

      h("What I built"),
      p("The gateway (FastAPI) authenticates the caller, checks for personal data and prompt injection and applies policy-as-code rules. The decision and its event are written to PostgreSQL in one transaction through an outbox table, so a crash can never lose or invent an event. A separate publisher sends the events to Apache Kafka, which buffers them in a six-partition topic. Spark Structured Streaming checks every event against a versioned JSON schema, routes invalid ones to a dead-letter topic, removes duplicates and computes eleven metrics, for example requests per minute in one-minute tumbling windows and deny rate or 99th-percentile latency in five-minute sliding windows. The results are upserted into PostgreSQL, and a read-only reporting API serves them to a React dashboard that refreshes every five seconds. All ten services run as separate Docker containers defined in one Compose file, and everything is versioned in Git."),

      h("Were the requirements met?"),
      p("Mostly, yes. A producer generates the required one million timestamped events, and a script checks the whole path. On a fresh clone of the repository, 1,000,000 events were produced, consumed and aggregated, and none were lost although I killed the Spark processor halfway through. Two concurrent producers raised the ingest rate from about 4,500 to 7,800 events per second.",
        new FootnoteReferenceRun(1),
        " The limits are real, though: everything runs on one machine with a single Kafka broker and Spark in local mode, so this shows local scaling, not cluster scaling."),

      h("What went wrong"),
      p("My first million-event run lost 8.4 % of the events. A per-batch read limit let quiet Kafka partitions run ahead in event time, and the watermark then discarded the busy partition's events as late. Removing the limit and widening the watermark fixed it. Spark also crashed at start-up because RocksDB could not load its native library from a temp folder without execute rights. Later a test run reported missing events that were only delayed, because my own script stopped waiting too early. Each time the lesson was the same: check the numbers before trusting either the system or the test."),

      h("Reliability, scalability, maintainability and security"),
      p("Reliability comes from the outbox, acknowledged Kafka writes with retries, Spark checkpoints, deduplication and idempotent writes. Kafka partitions and a stateless publisher allow scaling out; Spark is the next bottleneck. Separate services, about 750 automated tests and a CI pipeline keep the code maintainable. Every service has its own least-privilege database account, events carry no prompts or personal values, user IDs are keyed hashes, a retention job deletes old data, and dependencies and images are scanned. Production would still need TLS between services, encrypted storage and a trusted model registry."),

      h("Adding batch processing"),
      p("A batch path could reuse the same data: a nightly Spark job would read an archive of the valid topic, recompute daily and monthly reports and retrain the agent anomaly model. Because Spark runs the same code in batch and streaming mode, the validation and window logic would exist only once."),

      h("What I learned"),
      p("Technically, I learned event-time processing with watermarks, the outbox pattern for reliable delivery, and how to make a multi-service stack reproducible with Docker and Infrastructure as Code. Beyond that, I learned to plan work in small steps that can each be checked, to write down decisions so that others can follow them, and to stay patient when a problem only appears under load."),
    ],
  }],
});

Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(out, buf); console.log("wrote", out); });
