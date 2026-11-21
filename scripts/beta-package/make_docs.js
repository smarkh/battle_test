// Builds "1 - Read me first.docx" and "3 - Beta details and FAQ.docx" for the
// Battle Test beta package (beta-package/).
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell, WidthType,
  ShadingType, BorderStyle, AlignmentType, LevelFormat, Footer, ExternalHyperlink, PageNumber,
} = require("docx");

const OUT = process.argv[2];
const EMAIL = "battle_test@smarkiq.us";
const SITE = "battle.smarkiq.us";
const NAVY = "1B2A41", BRASS = "9A6B1F", GREY = "555D6B", RULE = "C9C3B6", TINT = "F4F1EA", HEAD = "E8E4DA";

const styles = {
  default: { document: { run: { font: "Calibri", size: 22 }, paragraph: { spacing: { after: 120, line: 276 } } } },
  paragraphStyles: [
    { id: "Title", name: "Title", basedOn: "Normal", run: { font: "Cambria", size: 44, bold: true, color: NAVY },
      paragraph: { spacing: { before: 0, after: 160 }, keepNext: true } },
    { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
      run: { font: "Cambria", size: 30, bold: true, color: NAVY },
      paragraph: { spacing: { before: 320, after: 120 }, keepNext: true, outlineLevel: 0 } },
    { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
      run: { font: "Cambria", size: 24, bold: true, color: NAVY },
      paragraph: { spacing: { before: 220, after: 80 }, keepNext: true, outlineLevel: 1 } },
  ],
};
const numbering = {
  config: [
    { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 540, hanging: 270 } } } }] },
    { reference: "steps", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 540, hanging: 300 } } } }] },
    { reference: "join", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 540, hanging: 300 } } } }] },
  ],
};

// "**bold** text" -> runs
function runs(text, base = {}) {
  const out = [];
  text.split(/(\*\*[^*]+\*\*)/).forEach((part) => {
    if (!part) return;
    if (part.startsWith("**")) out.push(new TextRun({ ...base, text: part.slice(2, -2), bold: true }));
    else out.push(new TextRun({ ...base, text: part }));
  });
  return out;
}
const P = (text, opts = {}) => new Paragraph({ children: runs(text, opts.run || {}), ...opts.para });
const H1 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(t)] });
const H2 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(t)] });
const B = (t) => new Paragraph({ numbering: { reference: "bullets", level: 0 }, children: runs(t), spacing: { after: 80 } });
const N = (t, ref = "steps") => new Paragraph({ numbering: { reference: ref, level: 0 }, children: runs(t), spacing: { after: 80 } });
const label = (t) => new Paragraph({ spacing: { after: 60 },
  children: [new TextRun({ text: t.toUpperCase(), font: "Arial", size: 17, bold: true, color: BRASS, characterSpacing: 30 })] });
const mail = () => new ExternalHyperlink({ link: `mailto:${EMAIL}`,
  children: [new TextRun({ text: EMAIL, style: "Hyperlink", color: "1F4E8C", underline: {} })] });

const border = { style: BorderStyle.SINGLE, size: 4, color: RULE };
const borders = { top: border, bottom: border, left: border, right: border };
function table(rows, widths, { header = true } = {}) {
  const total = widths.reduce((a, b) => a + b, 0);
  return new Table({
    width: { size: total, type: WidthType.DXA }, columnWidths: widths,
    rows: rows.map((row, i) => new TableRow({
      tableHeader: header && i === 0,
      children: row.map((cell, j) => new TableCell({
        borders, width: { size: widths[j], type: WidthType.DXA },
        shading: header && i === 0 ? { fill: HEAD, type: ShadingType.CLEAR, color: "auto" }
          : (!header && j === 0 ? { fill: TINT, type: ShadingType.CLEAR, color: "auto" } : undefined),
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        children: [new Paragraph({ spacing: { after: 0 },
          children: runs(cell, { size: 20, bold: (header && i === 0) || (!header && j === 0) ? true : undefined }) })],
      })),
    })),
  });
}
const box = (paras) => new Table({
  width: { size: 9360, type: WidthType.DXA }, columnWidths: [9360],
  rows: [new TableRow({ children: [new TableCell({
    width: { size: 9360, type: WidthType.DXA },
    borders: { top: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, bottom: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
      right: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, left: { style: BorderStyle.SINGLE, size: 18, color: BRASS } },
    shading: { fill: TINT, type: ShadingType.CLEAR, color: "auto" },
    margins: { top: 140, bottom: 140, left: 220, right: 220 },
    children: paras,
  })] })],
});
const space = () => new Paragraph({ spacing: { after: 60 }, children: [] });

const footer = (text) => new Footer({ children: [new Paragraph({ children: [
  new TextRun({ text: text + "  ·  Page ", font: "Arial", size: 16, color: GREY }),
  new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 16, color: GREY }),
] })] });
const page = { size: { width: 12240, height: 15840 }, margin: { top: 1260, bottom: 1260, left: 1440, right: 1440 } };

function save(doc, name) {
  return Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(path.join(OUT, name), buf); console.log("saved", name); });
}

// ------------------------------------------------------------ 1 - Read me first
const readme = new Document({
  creator: "Mark Hamilton", title: "Battle Test: an invitation to a free beta", styles, numbering,
  sections: [{ properties: { page }, footers: { default: footer("Battle Test · beta invitation") }, children: [
    label("Battle Test · Adversarial case review"),
    new Paragraph({ style: "Title", children: [new TextRun("An invitation to try Battle Test, free")] }),
    P("Someone in your group passed these documents on. They're from Mark Hamilton, who built Battle Test. I'm looking for a few litigators to try it over the next two months and tell me honestly what it's worth.", { run: { size: 23 } }),

    H1("What it is"),
    P("Battle Test helps you find where a lawsuit is weak before you file it. You give it the facts of a case, or a complaint you've written. Two AI models then argue it:"),
    B("one, for the plaintiff, drafts the complaint and a motion for summary judgment;"),
    B("the other, for the defendant, writes the strongest opposition it can, and the plaintiff's model can reply."),
    P("Every statute, rule and constitution citation is checked in code against a copy of current law, and problems are marked in the draft. The result is a starting point for a lawyer to review and build on, never a document to file. It covers Utah, California, Texas and Alaska, plus federal law."),

    H1("The beta"),
    table([
      ["Cases", "15, over 60 days"],
      ["Cost", "Free. No commitment."],
      ["Case facts", "**Fictional or fully anonymised only.** Please don't enter real client information during the beta."],
      ["Where", `${SITE}, in a web browser. Nothing to install.`],
      ["Time per case", "About 6 minutes"],
      ["In return", "Your honest feedback, by email"],
    ], [2200, 7160], { header: false }),

    H1("How to join"),
    new Paragraph({ children: [new TextRun("Email "), mail(), new TextRun(" with:")] }),
    N("your name;", "join"),
    N("the email address you'd like to sign in with;", "join"),
    N("the state or states you practise in;", "join"),
    N("the kinds of cases you'd like to try it on (for example contracts, construction, landlord–tenant).", "join"),
    P("I'll set up your account and email you a private link to choose your password. The link works for 72 hours."),

    H1("The attached documents"),
    table([
      ["File", "What it is"],
      ["1 - Read me first", "This page"],
      ["2 - Battle Test overview", "A short slide deck: how it works, what it can and can't do, and the beta"],
      ["3 - Beta details and FAQ", "The details: how your information is handled, known limits, and answers to common questions"],
      ["4 - Sample result (fictional case)", "A real, unedited result on an invented roofing dispute, as you'd download it"],
    ], [3400, 5960]),
    space(),
    P("Thank you for taking a look.", { para: { spacing: { before: 200, after: 40 } } }),
    P("**Mark Hamilton**", { para: { spacing: { after: 0 } } }),
    new Paragraph({ children: [mail()] }),
    space(),
    P("Battle Test produces drafts for review by a licensed attorney. It is not a law firm and does not give legal advice.",
      { run: { size: 18, color: GREY, italics: true } }),
  ] }],
});

// ------------------------------------------------------- 3 - Beta details and FAQ
const details = new Document({
  creator: "Mark Hamilton", title: "Battle Test beta: details and FAQ", styles, numbering,
  sections: [{ properties: { page }, footers: { default: footer("Battle Test · beta details and FAQ") }, children: [
    label("Battle Test · Beta details"),
    new Paragraph({ style: "Title", children: [new TextRun("Beta details and FAQ")] }),
    P("Everything a tester needs to know: how a case runs, what it can and can't promise, how your information is handled, the beta's terms, and the feedback that helps most. November 2026."),

    H1("How a case runs"),
    N("**You start a case.** Pick the state, then either fill in a short case form or paste or upload a complaint you've written (Word, PDF or text). Choose one round or two."),
    N("**It researches the law.** Each side searches a local copy of the law for the statutes and rules it needs."),
    N("**The plaintiff's model drafts** the complaint (or uses yours as written) and a motion for summary judgment."),
    N("**The defendant's model writes the opposition,** the other side's best response."),
    N("**Optionally, the plaintiff replies.**"),
    N("**Every citation is checked,** and the results are shown with the documents."),
    P("A case takes about 6 minutes. You can close the page and come back. Nothing is declared a winner: it plays two opposing lawyers, never a judge."),

    H1("How it keeps to real law"),
    P("AI models misremember citations, so Battle Test doesn't take their word for any of them:"),
    B("**Find.** Before drafting, it searches the law and shows the models the text of the relevant sections."),
    B("**Cite only that.** A model may cite only what it was shown. Anything else becomes a [CITATION NEEDED] placeholder."),
    B("**Check.** After drafting, ordinary software checks that every statute, rule and constitution citation exists, is in force, and is the right state's law. A second model then reads each cited section beside the paragraph citing it, and marks the ones it doubts."),
    B("**Leave out what doesn't fit.** Before drafting, the same checker sets aside sections that don't suit this kind of case. Law the research found but wasn't sure of is listed separately as “possible references” for you to judge."),
    P("Problems are marked in the draft, never silently removed, so you see exactly what was written."),

    H1("What you get"),
    B("Each document in its own tab: complaint, motion, opposition and reply."),
    B("A citation check: every citation with its status, and the checker's note on any it doubts."),
    B("Possible references, and the text of every section of law each model was shown, with a link to its official source."),
    B("Downloads as Word, PDF or Markdown."),
    P("The attached “4 - Sample result (fictional case)” is a real example."),

    H1("What it can and can't promise"),
    box([
      P("**It can promise:** every statute and rule it cites exists and is in force as of the law date printed on the result.", { para: { spacing: { after: 80 } } }),
      P("**It can't promise** that nothing is missing, that each citation says what the draft claims (the checker's marks are warnings, not guarantees), or anything about case law, which it doesn't check yet.", { para: { spacing: { after: 0 } } }),
    ]),
    space(),
    P("Treat a result the way you'd treat a first draft from a junior associate: useful, and in need of review."),

    H1("Coverage"),
    P("State statutes, constitutions and court rules for **Utah, California, Texas and Alaska**, plus **federal** law: about 397,000 sections from Open US Law (Vaquill AI, CC BY 4.0), checked weekly for updates. Every result states the date its law is current as of. **Case law isn't covered yet.** The models are told not to cite cases, and anything that looks like a case citation is marked unchecked."),

    H1("Known limits"),
    P("These figures come from tests on seven invented cases (construction, a lease deposit, an unpaid invoice and a used-car sale), three runs each, scored against lists of the authorities a competent brief should cite. Those lists haven't yet been reviewed by a lawyer, so treat the numbers as indicative."),
    table([
      ["Limit", "What to expect"],
      ["Missing law", "The drafts cited roughly 6 in 10 of the key statutes and rules. Search can miss a statute worded differently from the question. Expect gaps."],
      ["Off-topic law", "None cited in the tests on this model. Sections that don't fit the case are set aside before drafting."],
      ["Misread law", "A real section can be cited for something it doesn't quite say. The checker marks many of these, not all."],
      ["Cautious checker", "It questions about 1 citation in 4, and some of those are fine. Its note says why."],
      ["Invented facts", "A draft can add facts that aren't in your case. Check them against your file."],
      ["Case law", "Not covered yet. Gaps where it belongs are marked [CITATION NEEDED]."],
    ], [2200, 7160]),

    H1("Your information"),
    H2("Where it goes"),
    B("The website, the law search and the citation checks run on a server I operate."),
    B(`The AI drafting runs on **Amazon Bedrock** in the US West (Oregon) region, using the gpt-oss 120B model. Amazon's published terms say Bedrock doesn't store prompts or use them to train models, and doesn't share them with model providers.`),
    B("Traffic reaches the site through **Cloudflare**, which handles it in transit."),
    P("Because the beta uses fictional or anonymised facts only, no client information should be involved. Using real client material later depends on a review of exactly this, by a lawyer advising the project."),
    H2("Who can see it"),
    B("Only invited email addresses can reach the site at all: you enter your email, receive a 6-digit code, then sign in with your own password."),
    B("Each user sees only their own cases. The admin pages show when a case was run, its state and settings, never its content."),
    B("I can technically reach the stored files, as the operator. I won't open a case's content unless you ask me to look at it, or to fix a fault."),
    H2("How long it's kept"),
    B("Cases are deleted automatically 90 days after they're run. You can delete any case sooner. Downloads you save are yours."),
    B("Uploaded files are used only to extract their text. The file itself isn't kept."),

    H1("Beta terms"),
    B("15 cases over 60 days, free, with no obligation to continue. A run that fails isn't counted."),
    B("Fictional or fully anonymised facts only."),
    B("At most 3 of your cases can be waiting or running at once."),
    B("After 60 days, or 15 cases, you can still sign in, read and download your cases, but not start new ones."),
    B("The output is a draft for your professional review, not legal advice. You remain responsible for anything you file."),

    H1("The feedback that helps most"),
    table([
      ["Feedback", "What to tell me"],
      ["Wrong law", "A cited section that doesn't apply, or doesn't say what the draft claims. Name the section and paragraph."],
      ["Missing law", "The statute or rule you'd have cited first, that it didn't"],
      ["The opposition", "Did it find the weakness opposing counsel would?"],
      ["Time saved", "Was it a useful start? What stopped you using it?"],
      ["Features", "What would make it part of how you work? Which of those would decide whether you keep using it?"],
      ["What it was worth", "Roughly how many hours it saved, and what you'd pay: per case or monthly"],
    ], [2200, 7160]),
    space(),
    new Paragraph({ children: [new TextRun("Send it to "), mail(), new TextRun(", with the case's name so I can find it.")] }),

    H1("Questions"),
    H2("Doesn't AI make up citations?"),
    P("It can. That's why every statute, rule and constitution citation is checked in code, and why the models may cite only law they were shown. Problems are marked, not hidden."),
    H2("Can I use it on a real matter?"),
    P("Not during the beta. Please use invented or fully anonymised facts. A good test is a past matter you know well, with the names and details changed."),
    H2("Does it decide who would win?"),
    P("No. It plays both sides' lawyers, never a judge. Its value is in showing the weak points, and the other side's best answer to them."),
    H2("What about professional-conduct rules on AI?"),
    P("Rules and guidance on generative AI (for example, ABA Formal Opinion 512) apply to how you use any tool like this. The beta's fictional-cases rule keeps client confidences out of it entirely. Questions are welcome by email."),
    H2("Which states are next?"),
    P("Tell me the ones you'd need. Requests decide what's added next."),
    H2("What will it cost after the beta?"),
    P("No price is set. What testers say it's worth is part of what the beta is for."),
    H2("Who do I contact?"),
    new Paragraph({ children: [new TextRun("Mark Hamilton, "), mail(), new TextRun(", for joining, problems and feedback.")] }),
  ] }],
});

Promise.all([save(readme, "1 - Read me first.docx"), save(details, "3 - Beta details and FAQ.docx")]);
