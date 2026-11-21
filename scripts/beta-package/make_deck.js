// Builds "2 - Battle Test overview.pptx": a self-explanatory deck for lawyers
// reading it on their own (nobody presents it).
const pptxgen = require("pptxgenjs");
const { applyTheme } = require(process.argv[3]);
const OUT = process.argv[2];

const EMAIL = "battle_test@smarkiq.us";
const THEME = {
  name: "Battle Test",
  headFontFace: "Cambria",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "1B2A41", lt1: "FFFFFF", dk2: "3A4556", lt2: "F2F3F5",
    accent1: "A8741F", accent2: "1B2A41", accent3: "2F5D8A", accent4: "8C2F2F", accent5: "6B7280", accent6: "D9C7A3",
    hlink: "1F4E8C", folHlink: "5B4A8C",
  },
};
const HEX = THEME.colors;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
pres.author = "Mark Hamilton";
pres.title = "Battle Test: overview and beta invitation";
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
const C = pres.SchemeColor;

const W = 13.333, M = 0.7;

pres.defineSlideMaster({
  title: "DARK", background: { color: C.text1 },
  objects: [{ placeholder: { options: { name: "title", type: "title", x: M, y: 2.3, w: 11.4, h: 2.0, fontFace: "Cambria",
    fontSize: 48, bold: true, color: C.background1, valign: "top", align: "left", margin: 0 }, text: "" } }],
});
pres.defineSlideMaster({
  title: "CONTENT", background: { color: C.background1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: M, y: 0.55, w: 11.9, h: 0.9, fontFace: "Cambria",
      fontSize: 34, bold: true, color: C.text1, valign: "top", margin: 0, align: "left" }, text: "" } },
    { text: { text: "Battle Test · beta overview", options: { x: M, y: 7.0, w: 6, h: 0.3, fontSize: 10,
      color: C.accent5, margin: 0 } } },
  ],
  slideNumber: { x: 12.2, y: 7.0, w: 0.5, h: 0.3, fontSize: 10, color: C.accent5, align: "right" },
});

// The same frame on navy, for the promises slide: its title is the layout's own
// placeholder, so no empty "Click to add title" box sits under it in PowerPoint.
pres.defineSlideMaster({
  title: "CONTENT_DARK", background: { color: C.text1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: M, y: 0.55, w: 11.9, h: 0.9, fontFace: "Cambria",
      fontSize: 34, bold: true, color: C.background1, valign: "top", margin: 0, align: "left" }, text: "" } },
    { text: { text: "Battle Test · beta overview", options: { x: M, y: 7.0, w: 6, h: 0.3, fontSize: 10,
      color: C.accent6, margin: 0 } } },
  ],
  slideNumber: { x: 12.2, y: 7.0, w: 0.5, h: 0.3, fontSize: 10, color: C.accent6, align: "right" },
});

// White line icons on a brass disc, as PNG data for addImage.
async function makeIcons(names) {
  const React = require("react");
  const ReactDOMServer = require("react-dom/server");
  const sharp = require("sharp");
  const fi = require("react-icons/fi");
  const out = {};
  for (const name of names) {
    const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(fi[name], { color: "#FFFFFF", size: 256 }));
    const buf = await sharp(Buffer.from(svg)).resize(256, 256).png().toBuffer();
    out[name] = "image/png;base64," + buf.toString("base64");
  }
  return out;
}

function kicker(slide, text, dark = false) {
  slide.addText(text.toUpperCase(), { x: M, y: dark ? 1.6 : 0.25, w: 10, h: 0.3, fontSize: 12, bold: true,
    color: C.accent1, charSpacing: 3, margin: 0, isTextBox: true, objectName: "Kicker" });
}
function card(slide, x, y, w, h, title, body, opts = {}) {
  slide.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: opts.fill || C.background2 },
    line: { color: opts.fill || C.background2 }, objectName: "Card " + title });
  let top = y + 0.25;
  if (opts.num !== undefined) {
    slide.addShape(pres.shapes.OVAL, { x: x + 0.3, y: top, w: 0.6, h: 0.6, fill: { color: C.accent1 },
      line: { color: C.accent1 }, objectName: "Number " + opts.num });
    slide.addText(String(opts.num), { x: x + 0.3, y: top, w: 0.6, h: 0.6, fontSize: 20, bold: true, fontFace: "Cambria",
      color: C.background1, align: "center", valign: "middle", margin: 0, isTextBox: true });
    top += 0.8;
  }
  slide.addText(title, { x: x + 0.3, y: top, w: w - 0.6, h: 0.5, fontSize: 20, bold: true, fontFace: "Cambria",
    color: opts.titleColor || C.text1, margin: 0, valign: "top", isTextBox: true });
  slide.addText(body, { x: x + 0.3, y: top + 0.55, w: w - 0.6, h: h - (top - y) - 0.75, fontSize: 15,
    color: opts.bodyColor || C.text2, margin: 0, valign: "top", isTextBox: true, paraSpaceAfter: 6 });
}

async function main() {
const ICONS = await makeIcons(["FiSearch", "FiBookOpen", "FiUsers", "FiClock", "FiZap", "FiTag"]);
let s;

// 1 Cover --------------------------------------------------------------------
s = pres.addSlide({ masterName: "DARK" });
kicker(s, "Battle Test · Adversarial case review", true);
s.addText("Find where a lawsuit is weak before you file it", { placeholder: "title" });
s.addText("Two AI models argue your case, and every statute and rule they cite is checked against current law.",
  { x: M, y: 4.45, w: 10.5, h: 0.9, fontSize: 20, color: C.accent6, margin: 0, isTextBox: true });
s.addText("An invitation to a free 60-day beta · November 2026", { x: M, y: 6.5, w: 10, h: 0.4, fontSize: 14,
  color: C.background1, margin: 0, isTextBox: true });
s.addNotes("Battle Test is a tool for litigators. This deck explains what it does, what it can and can't promise, and how to join the free beta. Fuller details are in “3 - Beta details and FAQ”.");

// 2 What it is ---------------------------------------------------------------
s = pres.addSlide({ masterName: "CONTENT" });
kicker(s, "What it is");
s.addText("A starting point, not a filing", { placeholder: "title" });
s.addText("A lawsuit position drafted for you, together with the other side’s best response to it, for a lawyer to review and build on.",
  { x: M, y: 1.8, w: 7.4, h: 2.2, fontSize: 28, fontFace: "Cambria", color: C.text1, margin: 0, valign: "top", isTextBox: true });
s.addText([
  { text: "Fewer references, all of them right, beats more references with some wrong.", options: { breakLine: true } },
  { text: " ", options: { breakLine: true, fontSize: 8 } },
  { text: "When the AI isn’t sure a section of law belongs, it leaves it out of the draft and lists it separately for you to judge." },
], { x: M, y: 4.4, w: 7.4, h: 1.6, fontSize: 17, color: C.text2, margin: 0, valign: "top", isTextBox: true });
s.addShape(pres.shapes.RECTANGLE, { x: 8.8, y: 1.8, w: 3.8, h: 4.4, fill: { color: C.text1 }, line: { color: C.text1 }, objectName: "Panel" });
s.addText([
  { text: "It plays", options: { breakLine: true, fontSize: 16, color: C.accent6 } },
  { text: "two opposing lawyers", options: { breakLine: true, fontSize: 26, bold: true, fontFace: "Cambria", color: C.background1 } },
  { text: " ", options: { breakLine: true, fontSize: 14 } },
  { text: "never a judge", options: { breakLine: true, fontSize: 26, bold: true, fontFace: "Cambria", color: C.background1 } },
  { text: " ", options: { breakLine: true, fontSize: 14 } },
  { text: "It shows the weak points. It never declares a winner.", options: { fontSize: 15, color: C.accent6 } },
], { x: 9.1, y: 2.1, w: 3.2, h: 3.8, margin: 0, valign: "top", isTextBox: true });

// 3 How a case runs ----------------------------------------------------------
s = pres.addSlide({ masterName: "CONTENT" });
kicker(s, "How a case runs");
s.addText("Two models argue it out, in about 6 minutes", { placeholder: "title" });
const steps = [
  ["Complaint", "Drafted from your case facts, or your own complaint is used as written", "Plaintiff’s model"],
  ["Motion", "For summary judgment, built on the complaint and the state’s rule", "Plaintiff’s model"],
  ["Opposition", "The other side’s best response, reading what the plaintiff cited", "Defendant’s model"],
  ["Reply", "Optional: the plaintiff answers the opposition", "Plaintiff’s model"],
];
const sw = 2.65, gap = 0.42;
steps.forEach(([t, b, who], i) => {
  const x = M + i * (sw + gap);
  s.addText(who.toUpperCase(), { x, y: 1.95, w: sw, h: 0.3, fontSize: 11, bold: true, charSpacing: 2,
    color: who.startsWith("Defendant") ? C.accent4 : C.accent3, margin: 0, isTextBox: true });
  card(s, x, 2.35, sw, 3.0, t, b, { num: i + 1 });
  if (i < steps.length - 1) s.addShape(pres.shapes.ISOSCELES_TRIANGLE, { x: x + sw + 0.06, y: 3.72, w: 0.3, h: 0.26,
    rotate: 90, fill: { color: C.accent1 }, line: { color: C.accent1 }, objectName: "Arrow " + (i + 1) });
});
s.addText("After every draft, ordinary software checks each statute, rule and constitution citation against a copy of current law.",
  { x: M, y: 5.75, w: 11.9, h: 0.6, fontSize: 16, color: C.text2, margin: 0, isTextBox: true });

// 4 Real law only ------------------------------------------------------------
s = pres.addSlide({ masterName: "CONTENT" });
kicker(s, "Real law only");
s.addText("AI misremembers citations, so it isn’t trusted", { placeholder: "title" });
[
  ["Find", "Before drafting, it searches a copy of the law and shows the model the text of the relevant sections."],
  ["Cite only that", "The model may cite only what it was shown. Anything else becomes a [CITATION NEEDED] placeholder."],
  ["Check", "Software confirms every citation exists, is in force, and is the right state’s law. A second model flags any that may not say what the draft claims."],
].forEach(([t, b], i) => card(s, M + i * 4.1, 1.9, 3.75, 3.3, t, b, { num: i + 1 }));
s.addText("Problems are marked in the draft, never silently removed, so you see exactly what was written.",
  { x: M, y: 5.5, w: 11.9, h: 0.5, fontSize: 16, color: C.text2, margin: 0, isTextBox: true });

// 5 What you get -------------------------------------------------------------
s = pres.addSlide({ masterName: "CONTENT" });
kicker(s, "What you get");
s.addText("The documents, the checks, and the law behind them", { placeholder: "title" });
[
  ["The briefs", "Complaint, motion, opposition and reply, one tab each"],
  ["Citation check", "Every citation, with its status and the checker’s note"],
  ["Possible references", "Law the research found but wasn’t sure of, left out of the drafts for you to judge"],
  ["Authorities shown", "The text of each section the models saw, linked to its official source"],
].forEach(([t, b], i) => card(s, M + (i % 2) * 6.05, 1.85 + Math.floor(i / 2) * 2.05, 5.8, 1.8, t, b));
s.addText("Download as", { x: M, y: 6.15, w: 1.8, h: 0.45, fontSize: 16, color: C.text2, margin: 0, valign: "middle", isTextBox: true });
["Word", "PDF", "Markdown"].forEach((t, i) => {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 2.55 + i * 1.75, y: 6.15, w: 1.55, h: 0.45, rectRadius: 0.22,
    fill: { color: C.text1 }, line: { color: C.text1 }, objectName: "Download " + t });
  s.addText(t, { x: 2.55 + i * 1.75, y: 6.15, w: 1.55, h: 0.45, fontSize: 14, bold: true, color: C.background1,
    align: "center", valign: "middle", margin: 0, isTextBox: true });
});
s.addText("See the attached “4 - Sample result” for a real example.", { x: 8.0, y: 6.15, w: 4.6, h: 0.45,
  fontSize: 14, italic: true, color: C.accent5, margin: 0, valign: "middle", isTextBox: true });

// 6 Citation labels ----------------------------------------------------------
s = pres.addSlide({ masterName: "CONTENT" });
kicker(s, "The citation check");
s.addText("Every citation gets a label", { placeholder: "title" });
const head = (t) => ({ text: t, options: { bold: true, color: HEX.lt1, fill: { color: HEX.dk1 } } });
const rows = [
  [head("Label"), head("What it means"), head("Checked by")],
  ["In force", "Exists, is in force, and its text was shown to the model", "Software"],
  ["In force, not shown", "Real and current, but the model never saw its text", "Software"],
  ["Not found", "Not found, not in force, or another state’s law. Marked in the draft", "Software"],
  ["Ambiguous", "Matches more than one section", "Software"],
  ["Unchecked", "Looks like a citation it can’t read, such as case law", "Software"],
  ["May not support this", "The checker doubts the section says what the paragraph claims. Marked in the draft. A warning, not a guarantee", "A second AI model"],
];
s.addTable(rows, { x: M, y: 1.75, w: 11.9, colW: [2.8, 6.8, 2.3], fontSize: 14, color: HEX.dk2, fontFace: "Calibri",
  border: { type: "solid", pt: 0.75, color: "C9CDD4" }, valign: "middle", rowH: 0.55, margin: 0.08 });

// 7 Promises (dark) -----------------------------------------------------------
s = pres.addSlide({ masterName: "CONTENT_DARK" });
kicker(s, "What it can honestly claim");
s.addText("One promise, and what it won’t promise", { placeholder: "title" });
card(s, M, 1.9, 5.8, 2.4, "It can promise", "Every statute and rule it cites exists and is in force as of the law date printed on the result.",
  { fill: C.text2, titleColor: C.accent6, bodyColor: C.background1 });
card(s, M + 6.1, 1.9, 5.8, 2.4, "It can’t promise", "That nothing is missing. That every citation says what the draft claims. Anything about case law, which it doesn’t check yet.",
  { fill: C.text2, titleColor: C.accent6, bodyColor: C.background1 });
s.addText("Treat a result as you would a junior associate’s first draft: useful, and in need of review.",
  { x: M, y: 4.8, w: 11.9, h: 0.5, fontSize: 17, italic: true, color: C.accent6, margin: 0, isTextBox: true });

// 8 Coverage -----------------------------------------------------------------
s = pres.addSlide({ masterName: "CONTENT" });
kicker(s, "Coverage");
s.addText("Four states, plus federal law", { placeholder: "title" });
[["UT", "Utah"], ["CA", "California"], ["TX", "Texas"], ["AK", "Alaska"], ["US", "Federal"]].forEach(([c, n], i) => {
  const x = M + i * 2.42;
  s.addShape(pres.shapes.RECTANGLE, { x, y: 1.9, w: 2.2, h: 1.9, fill: { color: i === 4 ? HEX.dk1 : HEX.lt2 },
    line: { color: i === 4 ? HEX.dk1 : HEX.lt2 }, objectName: "Tile " + c });
  s.addText(c, { x, y: 2.05, w: 2.2, h: 1.0, fontSize: 44, bold: true, fontFace: "Cambria",
    color: i === 4 ? C.accent6 : C.accent3, align: "center", margin: 0, isTextBox: true });
  s.addText(n, { x, y: 3.05, w: 2.2, h: 0.5, fontSize: 16, color: i === 4 ? C.background1 : C.text2, align: "center", margin: 0, isTextBox: true });
});
s.addText([
  { text: "Statutes, constitutions and court rules: about 397,000 sections, checked weekly for updates. Every result states the date its law is current as of.", options: { breakLine: true } },
  { text: " ", options: { breakLine: true, fontSize: 8 } },
  { text: "Case law isn’t covered yet.", options: { bold: true } },
], { x: M, y: 4.2, w: 11.9, h: 1.4, fontSize: 17, color: C.text2, margin: 0, valign: "top", isTextBox: true });
s.addText("Law data: Open US Law by Vaquill AI, CC BY 4.0.", { x: M, y: 6.3, w: 8, h: 0.35, fontSize: 11, color: C.accent5, margin: 0, isTextBox: true });

// 9 Your information ---------------------------------------------------------
s = pres.addSlide({ masterName: "CONTENT" });
kicker(s, "Your information");
s.addText("Where it goes, who sees it, when it’s gone", { placeholder: "title" });
[
  ["Where it goes", "The website, law search and checks run on a server I operate. The AI drafting runs on Amazon Bedrock in Oregon, which per Amazon’s terms doesn’t store prompts or train on them."],
  ["Who sees it", "Only invited email addresses can reach the site. Each user sees only their own cases. Admin pages never show case content."],
  ["When it’s gone", "Cases are deleted automatically after 90 days. You can delete any case sooner. Uploaded files aren’t kept."],
].forEach(([t, b], i) => card(s, M + i * 4.1, 1.85, 3.75, 3.2, t, b));
s.addText("During the beta: fictional or fully anonymised facts only, so no client information is involved.",
  { x: M, y: 5.4, w: 11.9, h: 0.5, fontSize: 16, bold: true, color: C.accent4, margin: 0, isTextBox: true });

// 10 Known limits -------------------------------------------------------------
s = pres.addSlide({ masterName: "CONTENT" });
kicker(s, "Known limits");
s.addText("What to expect, from tests on seven invented cases", { placeholder: "title" });
[["~6 in 10", "of the key statutes and rules a good brief would cite were cited"], ["0", "off-topic laws cited"],
 ["~1 in 4", "citations questioned by the checker, some of them fine"]].forEach(([n, l], i) => {
  const x = M + i * 4.1;
  s.addText(n, { x, y: 1.8, w: 3.8, h: 1.0, fontSize: 50, bold: true, fontFace: "Cambria", color: C.accent1, margin: 0, isTextBox: true });
  s.addText(l, { x, y: 2.85, w: 3.6, h: 0.8, fontSize: 15, color: C.text2, margin: 0, valign: "top", isTextBox: true });
});
s.addText([
  { text: "Missing law: search can miss a statute worded differently from the question. Expect gaps.", options: { bullet: true, breakLine: true } },
  { text: "Misread law: a real section can be cited for something it doesn’t quite say.", options: { bullet: true, breakLine: true } },
  { text: "Invented facts: a draft can add facts that aren’t in your case.", options: { bullet: true, breakLine: true } },
  { text: "Case law: not covered yet.", options: { bullet: true } },
], { x: M, y: 4.0, w: 11.9, h: 2.0, fontSize: 16, color: C.text2, margin: 0, valign: "top", paraSpaceAfter: 6, isTextBox: true });
s.addText("Three runs per case; the expected-authority lists are still to be reviewed by a lawyer, so treat the figures as indicative.",
  { x: M, y: 6.35, w: 11.9, h: 0.35, fontSize: 11, color: C.accent5, margin: 0, isTextBox: true });

// 11 The beta -----------------------------------------------------------------
s = pres.addSlide({ masterName: "CONTENT" });
kicker(s, "The beta");
s.addText("Free, for 60 days", { placeholder: "title" });
[["15", "cases"], ["60", "days"], ["$0", "cost, no commitment"]].forEach(([n, l], i) => {
  const x = M + i * 4.1;
  s.addShape(pres.shapes.RECTANGLE, { x, y: 1.85, w: 3.75, h: 2.0, fill: { color: HEX.lt2 }, line: { color: HEX.lt2 }, objectName: "Stat " + l });
  s.addText(n, { x: x + 0.3, y: 1.95, w: 3.2, h: 1.1, fontSize: 60, bold: true, fontFace: "Cambria", color: C.text1, margin: 0, isTextBox: true });
  s.addText(l, { x: x + 0.3, y: 3.1, w: 3.2, h: 0.5, fontSize: 17, color: C.text2, margin: 0, isTextBox: true });
});
s.addText([
  { text: "Fictional or fully anonymised facts only. No real client information during the beta.", options: { bullet: true, breakLine: true, bold: true } },
  { text: "In a web browser at battle.smarkiq.us. Nothing to install.", options: { bullet: true, breakLine: true } },
  { text: "Sign in with a code emailed to you, then your own password.", options: { bullet: true, breakLine: true } },
  { text: "After the beta you can still read and download your cases.", options: { bullet: true } },
], { x: M, y: 4.2, w: 11.9, h: 2.2, fontSize: 17, color: C.text2, margin: 0, valign: "top", paraSpaceAfter: 6, isTextBox: true });

// 12 Feedback -----------------------------------------------------------------
s = pres.addSlide({ masterName: "CONTENT" });
kicker(s, "Beta · Your feedback");
s.addText("The feedback that helps most", { placeholder: "title" });
[
  ["FiSearch", "Wrong law", "A cited section that doesn’t apply, or doesn’t say what the draft claims. Name the section."],
  ["FiBookOpen", "Missing law", "The statute or rule you’d have cited first, that it didn’t"],
  ["FiUsers", "The opposition", "Did it find the weakness opposing counsel would?"],
  ["FiClock", "Time saved", "Was it a useful start, and what stopped you using it?"],
  ["FiZap", "Features you’d want", "What’s missing that would make it part of how you work?"],
  ["FiTag", "What it was worth", "Hours it saved on the case, and what you’d pay: per case or monthly"],
].forEach(([icon, t, b], i) => {
  const x = M + (i % 3) * 4.1, y = 1.75 + Math.floor(i / 3) * 2.2, w = 3.75, h = 1.95;
  const key = i === 5;  // the price question: the one the beta most needs answered
  s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: key ? HEX.dk1 : HEX.lt2 },
    line: { color: key ? HEX.dk1 : HEX.lt2 }, objectName: "Feedback " + t });
  s.addShape(pres.shapes.OVAL, { x: x + 0.3, y: y + 0.3, w: 0.62, h: 0.62, fill: { color: C.accent1 },
    line: { color: C.accent1 }, objectName: "Icon disc " + t });
  s.addImage({ data: ICONS[icon], x: x + 0.44, y: y + 0.44, w: 0.34, h: 0.34, altText: t + " icon" });
  s.addText(t, { x: x + 1.1, y: y + 0.3, w: w - 1.3, h: 0.62, fontSize: 19, bold: true, fontFace: "Cambria",
    color: key ? C.background1 : C.text1, valign: "middle", margin: 0, isTextBox: true });
  s.addText(b, { x: x + 0.3, y: y + 1.05, w: w - 0.6, h: h - 1.2, fontSize: 14,
    color: key ? C.accent6 : C.text2, valign: "top", margin: 0, isTextBox: true });
});
s.addText(`Send feedback to ${EMAIL}. Include the case’s name so it can be found.`,
  { x: M, y: 6.3, w: 11.9, h: 0.4, fontSize: 15, color: C.text2, margin: 0, isTextBox: true });
s.addNotes("Wrong law matters most: one wrong reference costs more trust than one missing. On what it was worth, a figure for hours saved and what you'd pay is far more useful than useful-or-not.");

// 13 Join (dark) -------------------------------------------------------------
s = pres.addSlide({ masterName: "DARK" });
kicker(s, "How to join", true);
s.addText("Would you like to try it?", { placeholder: "title" });
s.addText([
  { text: "Email ", options: {} },
  { text: EMAIL, options: { bold: true, color: HEX.accent6, hyperlink: { url: "mailto:" + EMAIL } } },
  { text: " with your name, the email you’ll sign in with, the state you practise in, and the kinds of cases you’d try. You’ll get a private setup link by email.", options: {} },
], { x: M, y: 3.6, w: 11.0, h: 1.4, fontSize: 20, color: HEX.lt1, margin: 0, valign: "top", isTextBox: true });
s.addText("Mark Hamilton · Battle Test · Drafts for attorney review, not legal advice.", { x: M, y: 6.5, w: 11, h: 0.4,
  fontSize: 13, color: HEX.accent6, margin: 0, isTextBox: true });

await pres.writeFile({ fileName: OUT });
await applyTheme(OUT, THEME);
console.log("saved", OUT);
}

main().catch((e) => { console.error(e); process.exit(1); });
