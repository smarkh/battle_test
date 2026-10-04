# Plan — Profitability: Selling battle_test to Lawyers

This is a planning model, not a forecast. Every number rests on an
assumption that's written down here, so it can be replaced with real data
as it comes in. It builds on the hosting costs in
`plans/aws-bedrock-plan.md` (Bedrock models plus an AWS app server).

It isn't financial, tax or legal advice. An accountant should review the
tax and entity side, and a lawyer the terms of service and the
regulatory side, before charging anyone.

## Summary

- **The unit economics are strong.** At a blended ~$98 per subscriber per
  month, running costs per subscriber are only about **$5.50** (models
  ~$2.40, card fees ~$3.10). That's a **~94% gross margin**, or **~87%**
  once Cloudflare Access costs $7/user above 50 users.
- **Fixed costs are small:** ~$650/month of business overhead (insurance,
  accounting, legal documents, tools), plus ~$30/month of servers.
  **Break-even is about 8 paying subscribers** before marketing spend.
- **Model costs barely matter.** Even premium models with twice the usage
  reduce profit at 100 subscribers by only ~$1,000/month. **Price,
  customer acquisition cost and churn** decide the outcome.
- **Scenarios over 24 months** (after marketing spend):

  | Scenario | Subscribers at month 12 | Monthly profit at month 12 | Cumulative profit at month 24 |
  |---|---|---|---|
  | Conservative | ~37 | ~$300 | ~$2,000 |
  | Base | ~100 | ~$5,000 | ~$118,000 |
  | Optimistic | ~270 | ~$17,000 | ~$414,000 |

- **These exclude** your own pay, taxes, SOC 2 compliance (likely needed
  for firms beyond solos), and support and development time. See "What's
  not included".
- **The biggest risk isn't cost. It's whether the product is good enough
  to keep customers.** The current law-selection quality (see
  `plans/plan.md` 3b) would drive high churn. The prerequisites below
  come before charging anyone.

## Market and pricing

### What lawyers already pay (2026)

| Tool | Price per user per month | Notes |
|---|---|---|
| Paxton AI | $49–99 (solo); $499 individual plan | Research and drafting |
| CoCounsel (Thomson Reuters) | $75–500 | Five plans |
| Spellbook | ~$180 | Contract drafting |
| Lexis+ AI (Protégé) | ~$200–400 | Annual, sales-led |
| Harvey | ~$500–1,500 | Enterprise, unpublished |

- A solo or small firm spends **~$847/month** on practice software in
  total (a survey of 500 practitioners, early 2026).
- So **$79–149/month** for a focused tool is within normal budgets.

### Positioning

battle_test does one job deeply: it **stress-tests a lawsuit position**
before filing, by drafting the complaint and summary judgment motion,
writing the opponent's best response, and checking every statute and rule
citation against current law. It's narrower than CoCounsel or Harvey, so
it should price **below the broad platforms**, as a specialist add-on to
whatever research tool the lawyer already uses.

### Proposed pricing (a starting point to test)

| Plan | Price | Includes | Who it's for |
|---|---|---|---|
| **Solo** | $79/month | 30 cases/month | Solo practitioners |
| **Pro** | $149/month | 100 cases/month | Busy litigators |
| **Firm** | $129/seat/month (minimum 3 seats) | 60 cases/seat, pooled | Small firms |
| Pay per case (optional) | ~$15/case | No subscription | Occasional users. It can bring in people who later subscribe |

- **Case limits** protect against cost blow-ups and leave room for an
  upgrade path. Overage could be $2–3/case.
- **Annual plans** at ~2 months free improve cash flow and reduce churn.
- **A free trial** of 3–5 cases lets lawyers judge quality on their own
  matter types.

**Blended assumption used below:** 70% Solo, 20% Pro, 10% Firm seats gives
**$98 per subscriber per month**, with **~23 cases per subscriber per
month**. Most subscribers use well under their limit.

## Costs

### Variable (per subscriber per month)

| Item | Cost | Basis |
|---|---|---|
| Models (Bedrock) | ~$2.40 | 23 cases × $0.104 (recommended mix, `plans/aws-bedrock-plan.md`) |
| Card processing | ~$3.14 | 2.9% + $0.30 per charge |
| Cloudflare Access | $0 → **$7** | Free up to 50 users, then $7/user for **all** users. AWS-native sign-in (Cognito) would avoid it, at the cost of some development work |
| **Total** | **~$5.50** (≤50 users) / **~$12.50** (>50) | |

### Fixed (per month)

| Item | Cost | Notes |
|---|---|---|
| AWS app server and disk | ~$30 | Up to ~100 users. ~$100 at 250, ~$250 at 500 (a bigger server, redundancy, backups, monitoring) |
| Insurance: tech errors & omissions plus cyber | ~$250 | **An assumption. Get quotes.** Essential when selling to lawyers |
| Accounting, bookkeeping, business software | ~$100 | |
| Legal documents: terms of service, privacy policy, data processing agreement | ~$250 | ~$3,000 one-time, spread over year 1 |
| Domain, email, support tools | ~$50 | |
| **Total business fixed** | **~$650** | Plus infrastructure |

## Profit at different sizes (steady state, per month, before marketing)

| Paying subscribers | Revenue | Model + payment costs | Cloudflare Access | Infrastructure | Business fixed | **Operating profit** | Margin |
|---|---|---|---|---|---|---|---|
| 10 | $980 | $55 | $0 | $30 | $650 | **$245** | 25% |
| 25 | $2,450 | $138 | $0 | $30 | $650 | **$1,632** | 67% |
| 50 | $4,900 | $277 | $0 | $30 | $650 | **$3,943** | 80% |
| 100 | $9,800 | $553 | $700 | $30 | $650 | **$7,867** | 80% |
| 250 | $24,500 | $1,384 | $1,750 | $100 | $650 | **$20,616** | 84% |
| 500 | $49,000 | $2,767 | $3,500 | $250 | $650 | **$41,833** | 85% |

- **Break-even before marketing:** ~8 subscribers.
- **The jump at 51 users:** Cloudflare Access starts charging for
  everyone. Before passing 50, decide between paying it, moving to
  AWS-native sign-in, or one Access seat per firm.

## 24-month scenarios (including marketing spend)

Assumptions differ by scenario: new paying subscribers per month, monthly
churn (the share who cancel), and **CAC** (customer acquisition cost:
marketing spend per new paying subscriber). Small-firm legal tech
benchmarks suggest CAC ~$300 and monthly churn 1–3.5%. A new, unknown
product should plan for worse.

**Conservative:** 4 new/month, 5% churn, $600 CAC. Lifetime value ~$1,850,
3.1× CAC.

| Month | Subscribers | Monthly revenue | Monthly profit | Cumulative profit |
|---|---|---|---|---|
| 3 | 11 | $1,118 | –$2,025 | –$7,094 |
| 6 | 21 | $2,077 | –$1,120 | –$11,329 |
| 12 | 37 | $3,604 | $320 | –$12,794 |
| 18 | 48 | $4,726 | $1,379 | –$7,009 |
| 24 | 57 | $5,551 | $1,761 | $2,213 |

First profitable month: 11. **Peak cash needed: ~$13,000** (around month
12).

**Base:** 10 new/month, 3% churn, $300 CAC. Lifetime value ~$3,080, 10×
CAC.

| Month | Subscribers | Monthly revenue | Monthly profit | Cumulative profit |
|---|---|---|---|---|
| 3 | 29 | $2,853 | –$988 | –$5,602 |
| 6 | 56 | $5,456 | $1,078 | –$3,994 |
| 12 | 102 | $10,001 | $4,972 | $16,631 |
| 18 | 141 | $13,787 | $8,274 | $58,312 |
| 24 | 173 | $16,940 | $11,024 | $117,823 |

First profitable month: 5. **Peak cash needed: ~$6,000.**

**Optimistic:** 25 new/month, 2% churn, $200 CAC. Lifetime value ~$4,620,
23× CAC.

| Month | Subscribers | Monthly revenue | Monthly profit | Cumulative profit |
|---|---|---|---|---|
| 3 | 74 | $7,204 | $603 | –$3,869 |
| 6 | 143 | $13,984 | $6,446 | $9,705 |
| 12 | 269 | $26,372 | $17,099 | $86,678 |
| 18 | 381 | $37,346 | $26,669 | $223,333 |
| 24 | 480 | $47,067 | $35,147 | $413,521 |

First profitable month: 3. **Peak cash needed: ~$4,000.**

## What moves the result (sensitivity at 100 subscribers)

| Change from base | Price per subscriber | Cases/user/mo | Model cost/case | Monthly operating profit |
|---|---|---|---|---|
| **Base** | $98 | 23 | $0.104 | **$7,867** |
| Lower prices (Solo $49, everything ~40% less) | $60 | 23 | $0.104 | $4,177 |
| Higher prices (Solo $129, everything ~50% more) | $147 | 23 | $0.104 | $12,624 |
| Users run twice as many cases | $98 | 46 | $0.104 | $7,627 |
| Premium models (Claude Sonnet for everything) | $98 | 23 | $0.279 | $7,464 |
| Premium models and twice the cases | $98 | 46 | $0.279 | $6,822 |
| Budget models (Qwen3 32B) | $98 | 23 | $0.014 | $8,074 |

**What this shows:**
- **Price matters about 10× more than model choice.** That argues for
  spending on the best models if they improve quality, because better
  quality supports a higher price and lower churn.
- **Churn and CAC** (the scenario tables) swing outcomes far more than
  any cost line.

## What's not included (deliberately)

- **Your time and pay:** development, support, sales, demos. At 100+
  subscribers, support alone could be part-time work.
- **Taxes and entity costs:** an LLC or corporation, state fees, and
  income tax on profit.
- **SOC 2 or a similar security audit:** mid-size firms and anything
  beyond solos will often ask for it. Expect roughly **$15,000–40,000/year**
  (compliance tooling plus an auditor). It probably becomes necessary
  around the point of selling to firms, and would take ~$1,500–3,000/month
  off the profit lines above.
- **Product development costs:** case-law checking (step 4), the 3a check,
  upload and export, and so on.
- **Price changes** by AWS, Bedrock, Cloudflare or Stripe.

## Prerequisites before charging anyone

1. **Quality good enough to keep customers.** Today the models often pick
   the wrong law (UCC sales law for construction contracts) and miss
   key statutes. Lawyers will notice immediately, and churn will follow.
   That needs:
   - the 3b search and selection fixes, with semantic search
   - the 3a "does it say that?" check
   - case-law checking (step 4)
   - better models, which Bedrock makes affordable

   Measure progress with the evaluation set. **Lawyer-reviewed** expected
   lists are essential here.
2. **Security and confidentiality:**
   - Cloudflare Access (or equivalent) is in place.
   - The lawyer has signed off on hosting (Bedrock and AWS storage).
   - A clear data processing agreement covers what's stored, where, for
     how long, and who can see it.
   - Expect prospective customers to ask about the ABA's guidance on
     generative AI (Formal Opinion 512) and their own confidentiality
     duties.
3. **Terms of service:**
   - Drafts are for attorney review, not legal advice. The app already
     says this, and the terms must too.
   - Limitation of liability.
   - Customers are responsible for verifying citations, especially case
     law with KeyCite or Shepard's.
4. **Insurance:** tech errors & omissions and cyber cover, before the first
   paying customer.
5. **Billing:** Stripe subscriptions, case limits per plan, a trial, and
   per-user monthly caps. That's code work.
6. **States:** only Utah, California and Texas are supported. Market to
   lawyers in those states first, and add states as demand shows (each
   needs evaluation cases).

## Go-to-market (a suggested order)

1. **Validate before building more:**
   - Show the current output to **10–20 litigators** in Utah, California
     and Texas.
   - Ask what they'd pay, and how often they'd use it.
   - Find what's missing. Case law is likely first.
2. **Run a paid pilot:** 5–10 lawyers at a founding discount (e.g. $39/month
   for life), in exchange for feedback and testimonials.
3. **Choose channels with low CAC first:**
   - local and state bar associations: CLE talks and sponsored sections
   - litigation-focused online communities and LinkedIn content showing
     real (fictional-case) outputs
   - referrals from pilot users
4. **Then** paid acquisition, once churn is known to be low enough that
   CAC pays back within ~6 months.

## Metrics to track from the first customer

- **Monthly recurring revenue (MRR)** and paying subscriber count
- **Monthly churn,** with reasons why people cancel
- **CAC** by channel
- **Cases per subscriber per month** against plan limits
- **Actual model cost per case** (logged per case once the Bedrock client
  records usage)
- **Quality signals:** citation-check results per case, user-reported
  problems, and evaluation-set scores over time

## How to update this plan

The numbers come from a small calculation using the assumptions above.
When real figures arrive (actual CAC, churn, usage, insurance quotes, a
chosen price), replace the assumptions and rerun the calculation. Ask
Claude to "recompute the profitability tables with …". The structure
stays the same.
