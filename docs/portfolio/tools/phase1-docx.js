// Phase 1 conception document (Word), IU formatting: A4, 2 cm margins, Arial 11 body,
// Arial 12 headings, 1.5 line spacing, justified, hyphenation, 6 pt paragraph spacing.
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, ImageRun, AlignmentType, HeadingLevel,
} = require("docx");

const REPO = "D:/CLAUDE FILES/sovereign-ai-access-gateway/docs/portfolio";
const OUT = `${REPO}/Surname-FirstName00000000DataEngineeringP1S.docx`;
const png = fs.readFileSync(`${REPO}/architecture-submission.png`);

const LINE = 360; // 1.5 line spacing
const AFTER = 120; // 6 pt
const body = (runs, extra = {}) =>
  new Paragraph({ alignment: AlignmentType.JUSTIFIED, spacing: { line: LINE, after: AFTER }, children: runs, ...extra });
const lead = (label, text) => body([new TextRun({ text: `${label} `, bold: true }), new TextRun(text)]);
const h = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(text)] });

const doc = new Document({
  creator: "Portfolio author",
  title: "Phase 1: Conception",
  features: { updateFields: false },
  hyphenation: { autoHyphenation: true, consecutiveHyphenLimit: 2, hyphenationZone: 357 },
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: "Arial", size: 24, bold: true },
        paragraph: { spacing: { before: 240, after: 120, line: LINE }, keepNext: true, outlineLevel: 0 } },
      { id: "Title", name: "Title", basedOn: "Normal", next: "Normal",
        run: { font: "Arial", size: 28, bold: true },
        paragraph: { spacing: { before: 0, after: 120 }, keepNext: true } },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 }, // A4
        margin: { top: 1134, right: 1134, bottom: 1134, left: 1134 }, // 2 cm
      },
    },
    children: [
      new Paragraph({ style: "Title", children: [new TextRun(
        "Sovereign AI Access Gateway: A Real-Time Streaming Data Backend for AI Governance, Security, and Operational Analytics")] }),
      new Paragraph({ spacing: { after: 60 }, children: [new TextRun({
        text: "DLMDSEDE02 Data Engineering · Portfolio · Phase 1: Conception (Task 2)", size: 22 })] }),
      new Paragraph({ spacing: { after: 240 }, children: [new TextRun({
        text: "Name: [Surname, First name] · Matriculation number: [00000000] · Date: [DD.MM.YYYY]", size: 22, color: "555555" })] }),

      h("Concept"),
      lead("Problem and objective:", "European organisations increasingly use AI systems and autonomous agents but must keep sensitive data away from unapproved models, endpoints and jurisdictions, and prove that policies were enforced. This project builds a real-time streaming data backend that ingests AI-governance events, processes and aggregates them, and delivers live security and operational metrics."),
      lead("Architecture:", "A FastAPI gateway authenticates callers, isolates tenants, detects sensitive data and prompt injection, and enforces policy-as-code. Each decision is stored with an event in a transactional outbox; a separate publisher delivers it to Apache Kafka, chosen for durable, partitioned, replayable buffering. Spark Structured Streaming, chosen for event-time windows, watermarks and checkpointed recovery, validates and enriches events and computes one-minute tumbling and five-minute sliding aggregations. PostgreSQL stores the results; a reporting API serves them to a React dashboard."),
      lead("Data and reliability:", "A producer generates over 1,000,000 timestamped events. At-least-once delivery, deduplication, idempotent writes, checkpointing, retries and a dead-letter topic provide reliability; partitioning supports scaling."),
      lead("Procedure and tools:", "Gateway first, then ingestion, processing and delivery, each verified by tests. Isolated Docker microservices, Docker Compose, versioned migrations and Git keep it reproducible and maintainable."),
      lead("Security and governance:", "JWT, role-based access control, tenant isolation, least-privilege accounts, data minimisation, retention controls and tamper-evident audit records."),
      lead("Advantages and disadvantages:", "Real-time, auditable visibility and reproducible deployment, at the cost of operational complexity and tuning windows, thresholds and resources."),

      h("Architecture draft"),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 60 }, keepNext: true, children: [
        new ImageRun({ type: "png", data: png, transformation: { width: 642, height: 396 },
          altText: { title: "Architecture", description: "Phase 1 architecture of the real-time streaming data backend", name: "architecture" } }),
      ] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: AFTER }, children: [new TextRun({
        text: "Figure 1: Architecture of the real-time streaming data backend (solid: synchronous request path; dashed: asynchronous event streaming).",
        size: 20, italics: true })] }),

      h("Explanatory remarks"),
      body([new TextRun("The architecture separates synchronous AI-security enforcement from asynchronous real-time analytics.")]),
      body([new TextRun("The gateway authenticates requests, detects sensitive data and prompt-injection risk, evaluates policy-as-code rules, and stores each decision with an outbox event in one transaction. A separate publisher delivers events to Kafka.")]),
      body([new TextRun("Kafka buffers and distributes governance events. Spark Structured Streaming validates, enriches, and aggregates the stream using one-minute tumbling and five-minute sliding windows. Aggregated metrics are stored in PostgreSQL and delivered through a real-time reporting API to the React dashboard.")]),
      body([new TextRun("Invalid events are routed to a dead-letter topic. Checkpointing enables recovery after processor failure. Tenant isolation, policy-as-code enforcement, data minimisation, and audit records support security and governance.")]),
    ],
  }],
});

Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(OUT, buf); console.log("wrote", OUT); });
