# Demonstration Guide — "Operation Ledger"

This walkthrough takes about 15 minutes and exercises every module. It follows a **synthetic** money-laundering ring planted in `datasets/synthetic_transactions.csv`, alongside 14,000+ normal transactions and other planted patterns.

**The ring (fictitious):** `dataset_card.json → featured_ring`

| Role | Name | Account |
|---|---|---|
| Controller | Victor Hale | AC4003294304 (Harbor Federal) |
| Alias account | V. Hale | AC4003302223 (Atlas Savings) |
| Shell companies | Bluewater Trading LLC, Orion Logistics Ltd, Kestrel Imports Inc, Silverline Consulting LLC | AC4003310142, AC4003318061, AC4003325980, AC4003333899 |

**The story in the data:**
1. **8–12 Sep 2025:** five people deposit ten amounts of $8.6k–$9.9k each into Victor's account. This is smurfing below the $10k reporting threshold, and some deposits share the ring's IP address.
2. **15–18 Sep 2025:** $84,000 moves Victor → Bluewater → Orion → Kestrel → Silverline → V. Hale → Victor, losing about 1–3% per hop. This is a 6-hop layering cycle.
3. **3 Nov 2025:** $46,000 is passed through Bluewater → Silverline → Victor within 7 hours, a rapid pass-through.
4. All ring accounts transact from IP `203.0.113.250`.

Sign in at http://localhost:5180 as **`investigator@fraudlens.local`** / `FraudLens-Demo-2026`.

> With Docker and `FL_SEED_DEMO=true`, steps 1–2 have already run at start-up, so you can still do them to see the flow (a re-import is reported as all duplicates). On a fresh database, start at step 1.

---

### 1. Import transaction data
**Data Import** → download `messy_bank_export.xlsx` from *Synthetic test datasets*, then drop it into the upload area.
- The mapping dialog auto-maps headers such as `From A/C`, `Amt`, `Value Date` and `Remitter`. Badges show *exact* or *fuzzy*, and you can change any mapping before clicking **Import**.
- The result always reports **6 invalid rows**: a negative amount, `n/a`, 31 February, `yesterday`, a missing sender and a self-transfer.
- On an **empty** database it also reports **15 duplicates** (repeated rows in the file) and warnings for missing beneficiary names.
- On the **demo-seeded** database every valid row is reported as a duplicate. The export re-uses the synthetic transactions under new `BX-…` IDs, so they match existing transactions (same accounts and amount within 60 s) and are skipped instead of double-counted.
- Every rejected row is listed with its reason. The original file's SHA-256 is shown in *Import history*.

Then import `synthetic_transactions.csv`. Its ground-truth columns `is_fraud` and `fraud_pattern` are mapped automatically to the *synthetic-only* fields.

### 2. Run fraud detection
**Detection Center** → **Run detection** (about 3 s). Check the following:
- The stat tiles separate **rule indicators** from **model predictions**.
- *How risk is scored* explains why a model prediction alone can never flag a transaction.
- *Evaluation against ground truth* reports precision and recall per decision rule and per pattern. Its caveat notes that the generator and detectors share authors.

### 3. Identify suspicious transactions
In the alerts table (sorted by score), the top alert is **"6-hop circular flow of $84,000.00 returned to origin"** (critical, 95%). Click it. The drawer shows:
- the reasons;
- the flow path *Victor Hale → Bluewater → Orion → Kestrel → Silverline → V. Hale → Victor Hale*;
- the six transactions and the involved accounts;
- review actions (Acknowledge, Escalate, Dismiss).

Also try **Transactions** → *Flagged = Flagged*, *Indicator = Circular*, and click a row to see why it was scored.

### 4. Open a suspicious account
In the alert drawer, click **Victor Hale**. The account profile shows:
- automated risk ≈ 99.5 (critical), clearly labelled as an indicator;
- monthly bars showing activity only in September and November 2025: a dormant-looking account with sudden large flows;
- *Where money came from*: the alias account V. Hale, Silverline Consulting, and the five depositors;
- alerts where Victor is the primary account: smurfing and the 6-hop circular flow. His indicator list also shows *account network*, *rapid movement* and *unusual behaviour* from alerts he is involved in.

### 5. Explore its financial network
Click **Network**. The graph is focused on Victor at a depth of 2. Shell companies show as purple diamonds and accounts as risk-coloured squares; red edges carry flagged transfers.
- Click a shell company's account to inspect it, then **Expand from here**.
- Open the **Cycles** tab and click the 6-hop flow. The ring is highlighted and everything else fades.
- Open **Path**: *From* "Amanda Brown" (a depositor) → *To* "Bluewater Trading LLC". The shortest money path is Amanda Brown → Victor Hale → Bluewater. Then try *Victor Hale → V. Hale*: the shortest route is 3 hops via Bluewater → Silverline (the November pass-through), shorter than the 6-hop September cycle.
- Open **Clusters** to list connected groups of risky transactions.
- **Export** JSON, GraphML or PNG.

### 6. Follow the flow of money
On Victor's profile, open **Follow the money** with 2 hops and a minimum of $1,000. Upstream lists the depositors. Downstream lists Bluewater, followed by Orion *via Bluewater*.

### 7. Examine the investigation timeline
Click **Timeline** on the account, or on the case after step 8.
- The event map shows the 8–12 Sep deposits, the 15–18 Sep cycle and the detection events.
- Click a transaction for its details. Double-click an event to **correlate** everything within ± 2 days that shares accounts.
- Filter by date or event type, and export CSV or JSON.

### 8. Create an investigation case
Back on the 6-hop alert, click **Open case**:
- Title: *Operation Ledger – layering through shell companies*
- Priority: high
- Assign: Priya Raman

The case opens with **6 accounts**: Victor as *subject*, and the alias and four shells as *related*. It also has the 6 cycle transactions. To add the smurfing deposits as well, open the smurfing alert on Victor and use **Open case → Existing case**.

### 9. Attach supporting evidence
In the case, open **Evidence**:
- **Preserve transaction records** stores a system-generated CSV of the case transactions as evidence (`EV-…`), with its SHA-256.
- **Attach file** accepts any PDF or PNG. For example, take a screenshot of the network graph, category *Screenshot*. The upload is rejected if the content doesn't match the extension; try renaming a `.txt` to `.pdf`.
- Open an item to see its custody chain (uploaded, accessed …), then click **Verify integrity**. The stored file is re-hashed, and a *verified* event is appended. The *hash chain intact* badge confirms that the log has not been altered.

Add a **Note**, such as "All shell transfers originate from IP 203.0.113.250". Add a **Finding** (*Confirmed*), such as "Funds layered through four shell companies and returned to the controller", based on the evidence above.

### 10. Generate a PDF investigation report
Open **Reports** in the case → **Generate PDF report** → **Download**. The PDF contains:

| Section | Contents |
|---|---|
| 1–4 | ID, summary, objectives, accounts, suspicious transactions |
| 5 | Methodology with the run parameters; rule indicators and model predictions listed separately |
| 6–7 | Network figure and timeline figure |
| 8 | Evidence with SHA-256, integrity and custody status |
| 9 | Investigator observations |
| 10–11 | Automated summary vs. **investigator conclusions** |
| 12 | Limitations |

Also try **JSON** and **CSV** exports.

### Finishing the workflow
1. Move the case to **Under investigation**, then to **Pending review**. As an investigator, you cannot close it.
2. Sign out and sign in as `reviewer@fraudlens.local`. Open the case and **Close case** with a comment.
3. As the reviewer, go to **Settings → Audit log** to see every action recorded.

### Other things to try
- **Entities:** *Relationship suggestions* lists "V. Hale ↔ Victor Hale" (initial + shared IP) and 25 people holding second accounts (exact name + shared IP + repeated transfers). Verify a link, then **Merge…**. Name-only matches stay at ≤ 35% confidence and can't be merged until verified.
- **Investigator scope:** sign in as `investigator2@fraudlens.local`. Priya's case is not visible, and its evidence cannot be downloaded.
- **Settings (admin):** lower `velocity_min_count` or `flag_threshold`, re-run detection, and compare the evaluation.
