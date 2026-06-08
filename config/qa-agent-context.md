# QA Agent — Product Knowledge Base

Persistent knowledge for `qa-impact-agent`. Read at the start of every analysis.
Updated by Seed Mode when rules are confirmed.

---

## Product Overview

**Product:** <Your product name>
**Jira project:** <YOUR-PROJECT-KEY>
**GitLab projects:** `frontend-repo`, `admin-repo`, `backend-repo` (adjust to your setup)

**Currencies / Geos:**
| Currency | Geo |
|---|---|
| — | — |

**Environments:**
| Stend | Frontend | Admin |
|---|---|---|
| Master | https://your-product.example.com | https://admin.your-product.example.com |
| qa1–qaN | https://qa{N}.your-product.example.com | https://admin-qa{N}.your-product.example.com |

**Test accounts:**
| Email | Password | Geo/Role |
|---|---|---|
| qa-account-1@example.com | <password> | <geo> |
| qa-account-2@example.com | <password> | <geo> |

---

## Domain Risk Classification

### Critical — always Critical regardless of diff size

Business impact overrides code size. A 2-line change here is more dangerous
than a 200-line change in Medium territory.

- Registration flow (signup steps, phone/email verification, geo routing)
- Login / authentication (sign-in, session creation, token issuance)
- Cashier (cashier component, modal, entry point)
- Deposit flow (payment method selection, initiation, confirmation, callback)
- Bonus acquisition (claiming, eligibility check at claim time)
- Bonus lifecycle (activation, wagering progress, expiry, cancellation, forfeit)
- Promo code activation (input, validation, server-side application)

### High — always High regardless of diff size

- Bonus wagering mechanics (calculation, progress updates, completion triggers)
- User balance display and calculation
- Withdrawal flow (initiation, limits, confirmation, status tracking)
- KYC / identity verification (document upload, verification status, gate logic)
- Session management (token refresh, expiry, logout, multi-tab behavior)
- Payment method configuration / availability
- User account settings affecting financial operations

### Medium — default for most feature work

- Game lobby / loading / state
- Navigation and routing
- Notifications (popups, banners, alerts)
- User preferences (non-financial)
- Analytics and tracking integrations

### Low

- UI-only changes with no business logic
- Copy / text changes
- Color, spacing, animation changes (outside critical flows)
- Loading skeleton / placeholder states

### Analytics-Only (auto-Light mode)

When ALL changed files are in these paths — use Light mode, no diff needed:
`event_tracker`, `analytics`, `tracking`, `amplitude`, `customer_io`,
`posthog`, `sentry`, `metrics`

---

## Regression Baseline

These checks apply after **any Critical or High risk task**, regardless of scope.
Escalate any baseline item to P0 if it shares a module with the changed code.

| Check | How to verify | Why |
|---|---|---|
| Login | Sign in with test account, confirm session created | Auth touches many downstream flows |
| Balance display | Open main page, check balance widget shows correct value | Balance calculation is a shared dependency |
| Bonus status | Open account → bonuses section, verify active bonuses visible | Bonus state reads from shared store |
| Cashier entry | Open cashier modal — confirm it loads without error (don't deposit) | Cashier entry point must always be reachable |

**Pre-test smoke (run before any P0):**
```
□ Confirm deploy: MR merged + correct stend updated?
□ Hard refresh (Ctrl+Shift+R) to clear cached JS/CSS
□ Correct test account for the geo being tested?
□ App loads without JS console errors on entry page?
```

---

## Historically Unstable Areas

Extra scrutiny regardless of Jira scope.

| Area | Instability | Notes | Review date |
|---|---|---|---|
| Bonus system | HIGH | Multiple production incidents. Activation, wagering, lifecycle — always Deep Scan. | — |
| Promo code activation | MEDIUM-HIGH | Edge cases: already used, expired, wrong geo, ineligible state. | — |

---

## Technical Facts

Confirmed technical details that affect risk assessment and test design.
Add entries here as your team confirms product-specific facts via Seed Mode.

<!-- Example:
- **[Fact name].** Explanation and impact on test design.
  *Confirmed: YYYY-MM-DD, TASK-KEY*
-->

---

## Hidden Dependency Evidence Policy

The agent must follow this policy for Step 6 (Hidden Dependencies):

- Every dependency claim must reference a **specific file, function, or pattern from the diff**.
- If evidence cannot be found in the code → classify as `[UNKNOWN]`, not `[HYPOTHESIS]`.
- Inferred dependencies without code evidence are not allowed in the Hidden Dependencies section.

---

## Contradictory Data Policy

When Jira and MR code contradict each other:

- Do NOT resolve the contradiction silently.
- Do NOT pick a side without code evidence.
- Mark as `[UNKNOWN]` in the Impact Map.
- Add to `## Unknowns & Questions → For Developer`.

---

## UI Analysis Principle

Evaluate UI changes by **business impact**, not visual perfection.

Do NOT spend tokens on: screenshots, pixel-perfect review, design validation, typography,
spacing, colors.

DO flag UI changes that affect: deposit completion, bonus claiming, cashier entry,
registration completion, promo code activation.

---

## Agent Quality Metrics

Track these after each analysis session to measure agent performance over time.
Update manually after testing is complete.

| Date | Task | Risk level set | Risk level correct? | P0 items | P0 caught bugs | Hidden deps relevant? | Mode used |
|---|---|---|---|---|---|---|---|
| YYYY-MM-DD | TASK-KEY | — | — | — | — | — | — |

**How to update:** after a task is closed, fill in "Risk level correct?" (yes/no/partial),
"P0 caught bugs" (how many P0 items actually found issues), "Hidden deps relevant?" (yes/no/partial).

---

## Seed Mode Rules

Rules confirmed by your team. Each entry: date, rule, context.
Add entries here as your team confirms product-specific rules via Seed Mode.

<!-- Example:
- [YYYY-MM-DD] <Confirmed rule about your product>. Context: TASK-KEY.
-->

---

## Seed Mode: Pending Questions

Questions asked by the agent, awaiting user confirmation.
Orchestrator moves confirmed answers to `## Seed Mode Rules` above.

<!-- Format: -->
<!-- PENDING [YYYY-MM-DD] <question>. Triggered by: <TASK-KEY> -->
<!-- DECLINED [YYYY-MM-DD] <question> — user said not relevant -->
