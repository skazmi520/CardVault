# CardVault User Guide

I built CardVault to run my card business, and a few friends asked to use it, so this is the guide I wish I'd had. It's written for anyone who buys, sells, trades and grades Pokémon cards and wants to know, at any moment, what they own, what it cost, what it's worth and what they've actually made.

CardVault runs privately on your own computer and opens in your web browser. Nothing goes to a cloud service, and nobody sees your inventory but you.

**Contents**

1. [What it's for](#1-what-its-for)
2. [The ideas everything rests on](#2-the-ideas-everything-rests-on)
3. [Getting set up and importing your cards](#3-getting-set-up)
4. [The screens, one by one](#4-the-screens-one-by-one)
5. [Booking deals: worked examples](#5-booking-deals-worked-examples)
6. [A day at a show](#6-a-day-at-a-show)
7. [Between shows](#7-between-shows)
8. [What every number means](#8-what-every-number-means)
9. [Your data, backups and troubleshooting](#9-your-data-backups-and-troubleshooting)

---

## 1. What it's for

If you flip cards, the hard part isn't the buying and selling. It's knowing afterwards whether you made money. Trades make that worse: you give three cards and some cash for one card, and a month later nobody remembers what anything really cost.

CardVault fixes that by recording every transaction the moment it happens and doing the math for you:

- **Inventory:** every slab and raw card you hold, with what you paid and what it's worth today.
- **Deals:** every buy, sale and trade, including cash in either direction, with the profit or loss on each card that left.
- **Grading:** raw cards waiting to go out, cards at PSA (or BGS, CGC, TAG), what you guessed they'd grade, and what they came back as.
- **Reports:** realized gains by year (for taxes), sell lists, printable sheets and CSV exports.

It's built for a fast, messy environment like a show floor. A deal takes under a minute to enter, and the app refuses to save anything that doesn't add up, so mistakes get caught at the table instead of discovered months later.

---

## 2. The ideas everything rests on

Read this section once. Every screen makes sense after it.

### Basis and market are different numbers

- **Basis** (cost basis) is what a card cost you.
- **Market** is what it's worth today, which you set yourself when you reprice.

The app never mixes them up. If you buy a card for $850 that's worth $1,000, it knows both. If you later sell it for $950, it tells you that you made $100, not that you lost $50 against market.

### Every transaction is a deal

A deal has three parts: cards that left, cards that arrived, and cash in either direction.

- A **purchase** is a deal with only cards arriving.
- A **sale** is a deal with only cards leaving.
- A **trade** has both, with or without cash.

When a deal saves, every card that left gets its sale proceeds and realized gain, and every card that arrived gets its cost basis. Nothing else is needed.

### What you typed is the record

This is the most important rule in the app: **a value you type is stored exactly as you typed it.** The app only calculates a number for a field you left blank, and it always shows you that it did. Calculated amounts appear as *split $300* in italics with a dashed outline. Amounts you typed appear as plain **basis $300**.

The app never fills anything in from market value. A blank market stays blank, and a blank price is split from a total you typed, never guessed from market.

Every deal also keeps an exact copy of what you typed, so it can be checked or replayed later.

### Realized vs unrealized

- **Unrealized gain** is a paper gain: market minus basis on cards you still hold. It moves every time you reprice.
- **Realized gain** is locked in: what a card fetched minus what it cost, booked the moment it leaves in a deal.

### Trades realize gains (and that's correct)

When you trade a card away, its gain is realized right then, just like a sale. The card you got back starts with a basis equal to what you took it in at.

Example: you graded a card for $100 total and it's now worth $1,000. You trade it for a card you take in at $1,200. The app books **$1,100 realized** on the card you gave, and the new card's basis is **$1,200**.

This matches how trades are taxed: a trade of collectibles counts as selling one card and buying another. It also means your total cost basis grows as you roll gains into new cards, so **cost basis is not the same as the cash you've put in.** A long run of profitable trades can leave you with a large basis even if you've only put in modest cash. Your total profit is always realized plus unrealized, however the trades fall.

### Raw cards and grading

Raw cards you plan to grade count at what you paid until they come back. When one returns, the grading fee is added to its basis: a $60 raw card graded for $80 becomes a slab with a $140 basis. The grading fee is part of what the card cost you.

### Personal collection

Mark any card as **Personal collection (PC)** if you're keeping it. PC cards stay in your total holdings but drop out of the stock figures, sell lists and the "clear basis" tally, because keepers aren't inventory.

### Removed, lost and cracked

Three ways a card leaves your inventory without a sale:

- **Removed:** a reclassification. You pulled it into a binder or entered it by mistake. No gain or loss; you still have the value.
- **Lost:** gone with no money back (misplaced, stolen, damaged, or moved in a deal you never recorded). Its full basis books as a realized loss.
- **Cracked:** you broke it out of the slab to regrade it. It becomes a raw card with the same basis. Not a sale, no gain.

Every one of these can be undone with **Restore to inventory**.

### Deals don't change by accident

Once saved, a deal only changes through its **Edit deal** button, which saves a new revision and archives the old one. Or you can **Void** it, which puts everything back exactly as it was. Nothing else can quietly rewrite a deal.

---

## 3. Getting set up

### Install (Mac)

You need a Mac on macOS 11 or newer and an internet connection. Open **Terminal** (press ⌘ + Space, type *Terminal*, press Return), paste this one line and press Return:

```bash
curl -fsSL https://raw.githubusercontent.com/skazmi520/CardVault/main/v2/setup-mac.sh | bash
```

What it does:

1. Installs Python from python.org if your Mac doesn't have a recent one. macOS asks for your password once; nothing shows while you type it, which is normal.
2. Downloads CardVault into a **CardVault** folder in your home folder. You don't need git.
3. Gives CardVault its own private Python environment.
4. Creates an empty database in `~/.cardvaultmac`.
5. Puts **CardVault v2** in the Applications folder in your home folder and opens it.

From then on, open it from Spotlight (⌘ + Space, type *CardVault*) or Launchpad. To keep it in your Dock, right-click its Dock icon while it's open and choose Options, then Keep in Dock. It runs at `http://127.0.0.1:5177` in your browser.

**Updating:** run the same command again. It replaces the program and keeps all your data.

### Your first hour

The database starts empty. If you already track your cards somewhere else, bring them in with **Import** (next section). Otherwise add them one at a time with **+ Add card** on the Collection screen. Either way, type what you paid if you know it. If you don't, leave it blank: the card then counts in your inventory but stays out of every profit number, which is better than pretending it cost nothing.

After that, record everything new as a deal (section 5), and reprice your inventory every couple of weeks.

### Bringing in cards you already track

If your cards live in a spreadsheet, Collectr, or anything that can export a CSV, the **Import** screen brings them all in at once. Open **Collection** and press **Import**.

1. **Pick Slabs or Raw cards** and press **Download template**. You get a spreadsheet file with the right column headings.
2. **Fill it in**, one card per row. Open it in Excel, Numbers or Google Sheets, or paste in your existing data under the matching headings. Save or export it as CSV.
3. **Choose the file** (or paste its contents) and press **Preview**. Every row is checked before anything is saved. Problems show in red with the reason, and cards whose cert is already in your collection are skipped.
4. **Fix any problems and preview again.** Import stays switched off until every row is clean.
5. **Press Import.** All the cards go in together, or none do if something fails.

**The slab template:**

| Column | Needed? | What to put | Example |
|---|---|---|---|
| Card name | Yes | The card | Charizard ex |
| Set | No | The set | Obsidian Flames |
| Card number | No | The number on the card | 223 |
| Year | No | Release year | 2023 |
| Grader | Yes | PSA, BGS, CGC or TAG | PSA |
| Grade | Yes | The grade on the label | 10 |
| Cert | No | Cert number. Used to stop duplicates. | 12345678 |
| Cost | No | What it cost you, including any grading fee. Blank means unknown. | 450 |
| Market value | No | What it's worth today | 600 |
| Date acquired | No | YYYY-MM-DD or M/D/YYYY. Blank means today. | 2025-11-03 |
| Personal collection | No | yes if it's a keeper | yes |
| Notes | No | Anything | bought at a local show |

**The raw template** has the same columns without Grader, Grade and Cert, plus **Grading status**: Not Slated, Slated or At Grading (blank means Not Slated).

The `samples` folder in the download has a filled-in example of each.

**Rules worth knowing:**

- Money columns take plain amounts, like `450`, `$450` or `1,250.50`. Shorthand like `1.2k` is refused, so a typo can't import the wrong number.
- A blank Cost is stored as unknown, never as $0.
- A blank Market value stays blank. The app counts that card at its cost until you price it.
- The same cert twice in a file is flagged, and a cert already in your collection is skipped, so importing the same file twice won't double your inventory. Raw cards have no cert, so don't import the same raw list twice.

---

## 4. The screens, one by one

### The top bar

Always visible:

- **Tabs:** Dashboard, Collection, Deals, Grading, Reports, and **More** (Evaluator, Stock Check, PSA Import).
- **Search box** ("Search cards, certs, deals"). Press ⌘K to jump to it. Enter takes you to Collection with that search applied.
- **New deal** button, the one you'll press most at a show.
- **Light/dark theme toggle.**

If the app ever finds an inconsistency in your data at startup, a **Data integrity** banner appears at the top of every page. Treat the totals as unreliable until it's resolved (section 9).

### Dashboard

Your home page and the answer to "how am I doing?"

**The hero (top):**

- **Stock value:** the market value of everything you hold for sale, slabs and raw cards, excluding PC. A card with no market typed counts at what you paid.
- **The change pill:** how much your whole holding (including PC) moved since the last snapshot.
- **Counts:** slabs, raw cards, and how many are at PSA.
- **Notes underneath:** your PC value, shown separately ("Plus $X personal collection, not in these figures"), and how many slabs are counted at cost because they have no market yet.

**The chart** tracks your total holding over time. A snapshot is saved each day you open the dashboard, so it fills in as you use the app.

**The strip:**

- **Cost basis:** what your stock cost you.
- **Unrealized:** market minus basis on what you hold, your paper gain.
- **Realized in (this year):** profit locked in this calendar year, counted by the date each card left. This is the number for taxes.
- **Win rate:** the share of cards that left at a profit, out of all cards that didn't break even, across all time.

**Recent deals:** your last six deals. Each shows Trade, Sold or Bought, who with, the cash, and the gain or loss. Click one to open it.

**Capital:**

- **Money at PSA:** the cost of the raw cards out at grading, how long the oldest has been out, and when the next batch is due back.
- **Cards that clear basis at 85%:** how many slabs you could sell at 85% of market and still profit, and the total profit. **Print sheet** gives you a table-ready list.
- **Collection by grader:** your slabs split by PSA, BGS, CGC and TAG, by value.

**Attention:**

- **Stale prices:** slabs not repriced in 30 days or more.
- **Unpriced cards:** slabs with no market at all.

Both open Stock Check, filtered so you can fix them.

**Movers:** your five slabs with the biggest price change at their last reprice.

### Collection

Every card you hold, in one sortable table.

**Tabs:** Slabs (the default), PSA, BGS, TAG, CGC, Raw, Personal, All.

**Filters:**

- **Search box:** name, set, cert, card number or ID. Press / to jump to it.
- **Set** and **type** (slabs, raw, or both).
- **Grade.**
- **Status:** active, at grading, lost, or removed.
- **Stale only:** cards not repriced in 30 days.

**Columns** (click a header to sort):

- **ID:** the app's own number for the card.
- **Card:** name, number, set and cert.
- **Grade:** plus chips for PC, LOST, REMOVED or AT GRADING.
- **Basis** and **Market.**
- **Gain:** market minus basis.
- **Repriced:** how long ago you last set the market. It turns a warning colour after 30 days.

**Repricing fast:**

- Click any market price, or press Enter on a selected row, and type the new value.
- **Tab** saves and moves to the next card, so you can run down the whole list without touching the mouse.
- **Esc** cancels.
- Keys: ↑/↓ move, Enter prices, E opens the card, / searches, Esc closes.

**The card drawer** (click a row) shows everything about one card:

- **Market** (click to reprice) and **Basis**, with where the basis came from:
  - **typed:** you entered it.
  - **derived by the app:** split from a deal total, or carried through grading or cracking.
  - **legacy:** imported data.
  - **unknown:** no cost recorded.

  If a deal set the basis, it shows **deal #N** and is locked. Click it to go to the deal; a deal's numbers only change through **Edit deal**.
- **Details:** set, number, year, grader, grade, cert, acquired date and notes. Click any one to edit it.
- **History:** where the card came from, its last repricings, and every change ever made to it, with who made it (you, the app, an import).
- **Sell…:** a quick one-card cash sale. Type the price, an optional buyer and the payment method, then press **Book sale**. It books a real deal underneath, so your totals, Show Day and reports all stay right. If the price is wildly off market (over 3x or under a third), it asks you to confirm, which catches fat fingers.
- **The … menu:**
  - **Mark as personal collection** (or take it out).
  - **Crack to raw** (slabs only): moves it to Grading as a raw card, keeping its basis.
  - **Remove from inventory**, or **Mark lost** (pick a reason).
  - **Restore to inventory**, for a removed or lost card.

**+ Add card** is for single cards that never came through a deal: something you already owned, or a grading return you didn't track. Leave basis blank if you don't know it.

**Import** brings in many cards at once from a CSV file (see [Bringing in cards you already track](#bringing-in-cards-you-already-track)).

**Export** downloads your whole collection as a CSV.

### Deals

Every deal you've ever recorded, newest first, grouped by day. Each day header shows the number of deals, net cash and realized gain. Each row shows:

- the time;
- the lead card, with "+ N more" and "→ what came back";
- Trade, Sold or Bought, who with, where and how they paid;
- the cash and the realized gain.

The **filter box** matches card names, people, places and payment methods.

**Show day**, at the top, covers today only: number of deals, cash in, cash out, net cash and realized today. It's the running scoreboard for the day.

**Export** downloads your full deal history as a CSV.

### New deal

The screen you'll use most. It has two sides, **Leaving** (your cards) and **Arriving** (their cards), plus cash on each side. Section 5 walks through every kind of deal; this is the layout.

**Top fields:**

- **Counterparty:** a name or table number.
- **Location.**
- **Payment:** cash, venmo, zelle, paypal, trade or mixed.
- **When:** defaults to now.
- **Notes.**

**Leaving (your cards):**

- Search your inventory by name, set or cert. Same-name cards show their set, number and cert so you pick the right copy.
- Each card shows its market and basis.
- **Cash I pay** sits below.
- Cards at grading can't be picked; they're at PSA, not in your hand.

**Arriving (their cards):** press **+ Add card** for each one and fill in:

- **Card name**, **set**, and **Slab or Raw**.
- For slabs: **grader**, **grade**, **cert** and **year**.
- **market $:** what the card is worth. Optional, but it powers your unrealized numbers and splits.
- **took in at $:** what you're paying for it. **This becomes its cost basis, exactly as typed.**

**Cash I receive** sits on this side.

**My cards went for:** the total value your cards are going out at. You only need it when you leave some *took in at* boxes blank. The app then splits the remainder across those cards in proportion to their market. The 85/88/90/100% chips fill it in from your cards' market.

**Split per card going out:** tick this to set what each of your cards goes out at individually. Normally the app splits the proceeds across your cards by market.

**The totals bar** updates as you type:

- **Out fetched:** what your cards are going out at.
- **In at:** what you're taking cards in at.
- **Cash.**
- **Realized on this deal:** your profit or loss on the cards leaving.
- **Built in on arriving:** the gap between market and what you're paying on the cards coming in.
- **% of market:** the dial.

Underneath, one plain sentence says exactly what will book, for example "Books: 2 cards out fetched $700 · 1 card in at $600 · cash +$100". Each card shows a pill with what it will book.

**When the app won't save:** the sentence reads "Won't book yet:" and says why in plain words, such as a card with no price and nothing to split from, prices that don't add up to the cash on a purchase, or a card with no name. Fix what it says. It's stopping a mistake, not being fussy.

**Money boxes are strict.** Type plain amounts like `3500`, `$3,500` or `3500.50`. Shorthand like `3.5k`, spaces inside the number, or a letter O instead of a zero are refused, so a typo can't book the wrong number. Negative numbers are refused too: use **Cash I pay** and **Cash I receive** instead.

**Safety nets:**

- Leaving the page with anything typed asks before throwing it away.
- Save can't fire twice.
- A card booking at more than 3x its market asks you to confirm.

### The deal page

Open any deal from the Deals list or the dashboard.

**The strip** shows cash, the basis of what left and what arrived, realized gain, and what % of market each side traded at, using the market each card had at the time.

**Left and Arrived** list each card. For arriving cards, the source of the price is shown: **typed**, **split from ask** (calculated), or **unknown** for very old data.

**As entered** shows exactly what you typed on the deal screen, so you can always check what you entered against what the app worked out.

**Edit deal** opens the deal screen with everything you typed. Change it and press **Save revision**: the old version is archived and the new one books under the same deal number.

- Changes you made since, such as a reprice or a cert added, are kept unless you changed that same value in the edit.
- If the edit would undo something you did later, it lists exactly what and asks you to confirm.
- Very old deals, from before the entry log existed, can't be edited; void and rebook them instead.

**Void deal** reverses the whole deal: cards that left come back, cards that arrived are removed, and the cash is reversed.

- A copy is archived, but a void can't be undone. To bring it back, book it again.
- If a card from the deal has since been sold, graded or lost, the void is blocked until you sort that card out first.

**Photos:** attach pictures of the deal (cards, a handwritten note, a payment screenshot). They're documentation only.

### Evaluator (More menu)

A scratchpad for negotiating. **Nothing here is saved to your books** until you convert it.

- **I Give:** add cards from your inventory, or manual lines, and set the % of market they go out at (80/85/90/100% or your own).
- **I Get:** add their cards with a market and the % you're taking them at (70 to 100%, or your own). Every line can be overridden with a specific value.
- **Cash** on either side.
- **The totals:** Giving, Getting, Cash, **Profit over basis** (what your cards are fetching versus what they cost you, the number to negotiate on), and whether the deal is even, in your favour or against you.

It remembers what you entered on that device, so you can walk away mid-negotiation and come back. When it's agreed, **Convert to Deal** opens the deal screen with everything filled in, ready to check and save.

### Grading (Raw & Grading)

Your raw cards and the grading pipeline.

**The strip:** how many cards are Slated, At grading and Not slated, with their total cost, when the next batch is due back, and how many guesses are riding.

**Tabs:** All, Slated, At grading, Not slated. Each row shows:

- **Status:** Not Slated, Slated or At Grading. Change it from the row.
  - Slated means it's going in the next submission.
  - At Grading means it's at the grader. It's then stamped with a submitted date and can't be picked on the deal screen, because you can't hand it across a table.
- **Target:** which grader it's going to.
- **Guess:** the grade you think it'll get.
- **Bucket:** **Banker** for a measured bet you're confident in, **Casino** for a fun gamble.
- **Back by:** when you expect it back.
- **Returned…:** record the result when it comes back.

**Guesses and buckets lock** the moment a card goes to At Grading. That's deliberate: what you predicted before sending is the honest prediction, and you can't rewrite it after a card comes back a 7.

**Bulk update:** tick cards (or use **Select all slated** or **Select all at grading**) and a bar appears. Set the status, the back-by date or the grader for all of them at once. Use it the day you ship a submission.

**When a card comes back, press Returned…** and enter the grader, grade, cert, grading cost and date. The preview shows the new basis (raw cost plus grading cost). **Add to collection** turns it into a slab.

**Grading eye** scores your guesses against what actually came back:

- exact hits;
- within one grade;
- your bias: positive means you guess high, negative means you guess low;
- how many guesses are still riding.

Banker and Casino are scored separately, so you learn how good your eye really is on the cards you were serious about.

### PSA Import (More menu)

The fast way to handle a whole PSA order instead of entering returns one by one.

1. Download the order CSV from your PSA account. Choose the file, or paste its contents. The order number fills in from the file name.
2. Enter the **grading cost per card**, for orders that have been graded.
3. Press **Preview.** The app matches each cert to one of your raw cards, using name, card number and year.
4. **Tick each match you've checked.** Nothing is ticked for you, on purpose: a wrong cert on the wrong card is painful to unpick. Certs already in your collection are skipped automatically.
5. Press **Apply.**

What applying does:

- **Graded rows:** the cards become slabs, with grade, cert and raw cost plus fee as the basis.
- **Rows with no grade yet** (an order still in progress): the cards are marked At Grading with the cert noted, ready for the final import.
- **Cards PSA refused to grade:** no grade is recorded. The card goes back to Not Slated, with PSA's reason in its notes.

It's all or nothing: if anything fails, nothing changes.

### Stock Check (More menu)

Physical inventory, done at your case or desk. It lists every slab, sorted by grader.

- **Tap a row** to tick it off as you find the card. Your progress is saved in the browser, so you can stop and pick up where you left off.
- **Progress:** checked, still to find, stale prices and unpriced.
- **Filters:** grader tabs, search, unchecked only, and price age (30+ days, or no market).
- **Reprice as you go:** click a market price, type the new one, and press Tab to move to the next card.
- **The ✓ button** next to a price means "still right": it marks the price as checked today without changing it, so the card stops showing as stale.
- **Copy a cert:** one click on a cert number selects it. Press ⌘C, then paste it into CardLadder, PSA or wherever you check prices.
- **Print list** gives you a paper checklist.
- **Start over** clears the ticks.

### Reports

**Realized gains**, the headline: every card that left your inventory, grouped by month.

- **Year switcher:** pick a year for taxes.
- **Search:** find a specific card.
- **Totals:** cards moved, basis, proceeds and realized gain.
- **Each row:** dates in and out, basis, proceeds, gain, and a link to the deal.
- Lost cards are included as losses. Cards with an unknown basis are listed but kept out of the totals.

**Exports:**

- **Realized gains CSV:** for your accountant.
- **Collection CSV:** everything you hold.
- **Deal history CSV:** one row per card per deal.
- **Sell list:** pick a % of market (80 to 100%). You get every slab you'd still profit on at that price, as a CSV or a printable sheet with a blank "Sold ✓" column for working a table with a pen.
- **Printable inventory sheet:** your holdings, with a blank Sold Price column and write-in rows for new pickups.
- **Google Sheets zip:** one CSV per grader, plus SOLD and RAW. Drop them into Google Drive to look up your inventory from your phone when the laptop's closed.

---

## 5. Booking deals: worked examples

The numbers below are made up. The rule behind all of them:

> **What your cards fetched = cash you received − cash you paid + what you took their cards in at.**
>
> That amount is split across your cards (by market, unless you set it per card), and each card's realized gain is its share minus its basis. Each card you take in gets a basis equal to its *took in at*.

### Buying a slab for cash

They have a PSA 10 worth $1,000, and you pay $850.

- **Arriving:** add the card, market $1,000, took in at $850.
- **Leaving:** nothing. **Cash I pay:** $850.
- **Result:** the card's basis is $850, with $150 built in. No realized gain; you haven't sold anything.

### Buying a lot

Three slabs for $900 cash total, with markets of $600, $400 and $200.

- **Arriving:** add all three with their markets, and leave *took in at* blank.
- **Cash I pay:** $900.
- **Result:** the app splits $900 by market: $450, $300 and $150. Each shows as *split $…* in italics so you know it was calculated.
- If you agreed a price on one of them, type it. The others split the rest.

### Selling for cash

You sell a card with a $300 basis for $500.

- **Leaving:** pick the card. **Cash I receive:** $500.
- **Result:** proceeds $500, realized gain +$200.
- For one card you can also use **Sell…** in the Collection drawer, which books the same deal.

### A straight trade

You give two cards: A (basis $300, market $500) and B (basis $200, market $300). You take one card worth $700 at $600 and get $100 cash.

- **Leaving:** cards A and B.
- **Arriving:** the new card, market $700, took in at $600.
- **Cash I receive:** $100.
- **Result:**
  - Your cards fetched $600 + $100 = **$700**, split 5:3 by market: A $437.50, B $262.50.
  - Realized: A +$137.50, B +$62.50, so **+$200** in total.
  - The new card's basis is **$600**.

### A trade where you pay cash

You give a card (basis $1,000, market $1,500) and pay $500 for a card worth $2,200 that you take in at $2,000.

- Your card fetched $2,000 − $500 = **$1,500**, so realized **+$500**.
- The new card's basis is **$2,000.**

### A big trade where you only know their markets

You give $9,000 of cards and take in six cards. You agreed the overall deal but not a price per card.

- Type your total in **My cards went for:** $9,000. The 100% chip fills it from your market.
- Add the six cards with their **markets** only, and leave *took in at* blank.
- The app splits $9,000 across the six by market, and each one's basis is shown as calculated.
- Any card you did agree a price on, type it. The rest split what's left.

### If the app won't save

It's telling you something doesn't add up. Common ones:

- **"No price typed":** you left *took in at* blank and gave the app nothing to split from. Type the prices, or fill in My cards went for.
- **"You paid $X but the cards' prices add up to $Y":** on a purchase, typed prices must match the cash, or leave some blank.
- **"Nothing came back":** you're giving cards away with no cash and nothing in. For a genuine giveaway, tick split per card and type 0 on each card.
- **"Every arriving card needs a name."**

---

## 6. A day at a show

This is how I run it.

### Before the show

- **Reprice the night before.** Stock Check with the "Not repriced in 30+ days" filter, or the Collection with Tab-to-next. Your market numbers are what you'll quote and negotiate from.
- **Print a sell sheet** (Reports, or Print sheet on the dashboard) at the % you're willing to go to. It's your floor for every card in the case.
- **Know your cash.** If you're there to buy, decide how much.

### Setting up

- Laptop behind the case, CardVault open on **New deal.**
- A quick Stock Check while you lay out the case, so you know everything made it.

### During the day

- **Enter every deal when it happens,** before the next customer. It takes under a minute: counterparty (a name or table number), cards, cash, Save.
- **Use the Evaluator** for anything being negotiated: load both sides, play with percentages, and convert when you shake hands.
- **Check Show Day** on the Deals tab whenever you want the running total: cash in, cash out, net, and realized today.
- **Made a typo?** Open the deal and use **Edit deal.** Booked something that didn't happen? **Void** it.
- **Too busy to enter one properly?** Jot it down and enter it with the right time later. The When field takes any date and time.

### End of the day

- **Look at Show Day and the dashboard:** what you actually made, not what it felt like.
- **Raw cards you picked up to grade:** set them to Slated with a target grader, and record your **Guess** and **Bucket** while the card's fresh in your mind.
- **Attach photos** to any deal where you'd want a record (a big trade, a payment screenshot).

---

## 7. Between shows

### Repricing

Every couple of weeks, open **Stock Check** with the "Not repriced in 30+ days" filter. Click each cert to copy it, look the card up, and type the new market or press ✓ if it hasn't moved. The **Movers** panel on the dashboard then shows what's changing.

### Grading

1. **Building a submission:** mark cards Slated with a target grader, and set your guess and bucket.
2. **The day you ship:** tick them on the Grading screen, bulk-update them to **At Grading**, and set a **Back by** date. Your guesses lock and the dashboard starts showing Money at PSA.
3. **When the order comes back:** run **PSA Import** with the order CSV and the cost per card, or press **Returned…** on each card for other graders.
4. **Afterwards:** check the **Grading eye** panel. Over time it tells you whether your eye runs high or low.

### Selling down

The **Sell list** in Reports shows every slab you'd still profit on at, say, 85% of market. It's the fastest way to see what you can move for cash, or to build a list for a big buyer.

### Taxes

Reports, Realized gains, pick the year, and press **Export.** That's the file your accountant needs: every card that left, what it cost, what it fetched and the gain, with trades booked as sales the way the tax rules treat them.

---

## 8. What every number means

| Number | What it is |
|---|---|
| **Basis** | What a card cost you: typed when you bought it, or calculated from a deal total, plus any grading fee. |
| **Market** | What a card is worth today, as you last set it. The app never changes it on its own. |
| **Stock value** | The market of everything you hold for sale (slabs and raw, not PC). A card with no market counts at its basis. |
| **Cost basis (dashboard)** | Total basis of your stock (not PC). Not the same as cash invested, because trades roll gains into new cards' basis. |
| **Unrealized** | Market minus basis on what you hold. A paper gain until you sell. |
| **Realized** | Profit locked in when cards leave: what each fetched minus its basis. Counted by the date the card left. Lost cards count as losses. |
| **Proceeds** | What a card that left fetched: its share of cash received − cash paid + what the other side's cards were taken in at. |
| **Built in** | Market minus what you paid on cards you just took in: the margin you bought. |
| **Win rate** | Share of cards that left at a profit, out of all that didn't break even. |
| **% of market** | What a side of a deal traded at compared with its market, using the market each card had at the time. |
| **Money at PSA** | Cost of the raw cards currently out at grading. |
| **Clear basis at 85%** | Slabs you'd still profit on at 85% of market, and the total profit. |
| **Stale** | A slab whose market hasn't been set or checked in 30 days or more. |
| **Typed / derived** | Typed means you entered the value; derived means the app calculated it from a total you typed. |

---

## 9. Your data, backups and troubleshooting

### Where things live

- **The program:** the **CardVault** folder in your home folder.
- **Your data:** `~/.cardvaultmac`, a hidden folder in your home folder. This holds your database, deal photos and the server log.

### Backups

The app backs up your database automatically once a day:

- **On your computer:** `~/.cardvaultmac/backups`, the last 7 days.
- **In iCloud Drive:** a **CardVault Backups** folder, the last 30 days, if iCloud Drive is on.

The backups are silent, so peek at the folder now and then to make sure they're there. To restore one, quit the app, copy the backup over `~/.cardvaultmac/cardvault_v2.db`, delete any `cardvault_v2.db-wal` and `cardvault_v2.db-shm` files next to it, and reopen.

### Updating

Run the install command again. Your data is never touched by an update.

### Troubleshooting

| Problem | What to do |
|---|---|
| The page won't load | Open the CardVault app again. If it still fails, the last lines of `~/.cardvaultmac/v2_server.log` say why. |
| CardVault says it can't find Python | Run the install command again. |
| A **Data integrity** banner appears | Something in the stored numbers doesn't add up. Don't trust the totals it names until it's fixed. The banner lists what failed; send it to whoever helps you with the app. |
| A deal won't save | Read the "Won't book yet" sentence; it says exactly what's missing. See section 5. |
| A card can't be picked on the deal screen | It's marked At Grading. Change its status on the Grading screen if it's actually in your hand. |

### Removing CardVault

Drag **CardVault v2** from the Applications folder in your home folder to the Trash, and delete the **CardVault** folder. Your data stays in `~/.cardvaultmac` until you delete that too, which permanently erases your cards and deals.

---

That's it. Questions? Find me at the show.
