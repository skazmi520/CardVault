# CardVault User Guide

Welcome. CardVault is the app I built to run my card operation, and a few friends asked to use it, so here's the guide. It's a local web app. Your data lives on your machine, nothing goes to a cloud, nobody can see your inventory but you.

The whole app is built around one idea: what you paid (basis) and what a card is worth (market) are different numbers and should never get mixed up. Every profit number you see comes from keeping those two honest. If you buy a card for 850 that's worth 1000, the app knows both, and when you sell it for 950 it tells you that you made 100, not that you lost 50.

## Getting started

You need a Mac with python3. Then:

1. Clone or download this repo
2. Open Terminal in the repo folder and run `./v2/install.sh`
3. That installs two python packages, creates your database, and builds a dock app called CardVault v2

Double click CardVault v2.app (drag it to your dock if you want) and the app opens at http://127.0.0.1:5177 in your browser. You can also run `python3 -m v2.app` from the repo folder if you prefer a terminal.

Fresh installs start with an empty database. Start entering cards through Deals (more on that below) or hit me up if you have a spreadsheet you want help importing. If you're bringing a spreadsheet, shape it like the files in the `samples/` folder, one CSV for slabs and one for raw cards, and the import goes smooth. Dollar signs and commas in the money columns are fine. The Grading Fee column is only for cards you graded yourself, leave it blank on slabs you bought.

### Optional keys

Two features need API keys, both optional. Put them in `~/.cardvaultmac/v2.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
PSA_API_TOKEN=...
```

The Anthropic key powers slab photo reading (a few cents per photo, it reads the label and fills in the card for you). The PSA token verifies cert numbers against PSA's registry, though fair warning, PSA cut their free tier to 1 lookup per day so don't plan around it.

### Phone access

If you run Tailscale, add `CARDVAULT_HOST=0.0.0.0` to that same v2.env file and you can open the app from your phone at your laptop's Tailscale address, port 5177. I use this constantly at shows.

## The tabs

### Dashboard

Your home page. Big tiles up top show total market value, total cost basis, unrealized gain, and realized gain for the year. Below that, smaller tiles for card counts, cash on hand, win rate, average profit per card, and how much money you have riding at PSA.

Needs Attention shows cards that haven't been repriced in 30 days or have no price at all. Click those tiles, they take you straight to a filtered list so you can fix it. Recent Deals, a value chart over time, top movers since your last repricing pass, your cash pool, sell list candidates, and what's out at grading fill in the rest.

The Cash Pool panel is worth setting up on day one. Add a deposit entry for whatever cash you're starting with and every deal after that adjusts the balance automatically.

### Collection

Every card you own, slabs and raw, in one sortable table. The search box finds names, sets, and cert numbers. Keyboard shortcuts make repricing fast: arrow keys move, Enter opens the price editor, Tab saves and jumps to the next card, E opens the full card editor.

Click any price to change it. Double click a row to edit the whole card. The edit window also has a Sell button for quick one card cash sales (it books a proper deal under the hood so your stats stay right) and a Crack to raw button for when you're breaking a card out of its slab to regrade it.

There's also a Personal Collection checkbox in the card editor. Check it for cards you're keeping. PC cards stay in your net worth and stock checks but stop showing up in sell lists, because your keepers aren't inventory.

### Reports

Realized gains by year with a searchable table of everything you've ever sold, where it went, and what you made. Export buttons give you CSVs of realized gains, deal history, your full collection, and a sell list at whatever percent of market you're working.

The Google Sheets section builds a zip of CSVs laid out one sheet per grading company plus SOLD and RAW. I drop those into Drive so I can look up my inventory from my phone when the laptop's off.

Printable sheets live here too. The sell sheet and inventory sheet are formatted for paper with blank columns for working a table with a pen.

### Deals

This is the heart of the app. Every transaction is a deal: cards you gave, cards you got, and cash in either direction. A pure purchase is a deal with only cards in. A pure sale is only cards out. A trade has both. The app computes your realized gain on everything that left and sets the cost basis on everything that arrived.

On the New Deal screen, search your inventory to add cards you're giving. For cards you're getting, type them in with a market value and what you paid. If you're buying a lot at a percentage, enter the side total and the app splits it across the cards pro rata. Cash has two boxes, cash I pay and cash I receive, so you never have to think about negative numbers.

If the two sides don't balance within 5% the app warns you before saving. It'll also warn you if you're about to book a giveaway as a sale (been there).

One habit worth building: if you got a card at a discount because of margin on cards you traded away, the recorded basis is the negotiated value. Your true edge shows up in the realized gains on what you gave. The books stay right either way.

Each deal has its own page where you can attach photos and void the whole thing if you fat fingered something. Voiding puts the cards back exactly as they were.

### Evaluator

A scratchpad for negotiating. Load up both sides of a potential trade, set what percent of market each side is trading at (I take cards in around 80% and give mine at full value, adjust to your style), and it shows you the balance live. When the deal is agreed, one click converts it into a real deal with everything prefilled. Numbers persist on your device until you clear them, so you can walk away mid negotiation and come back.

### Raw & Grading

Your ungraded cards and the grading pipeline. Each card has a grading status (Not Slated, Slated, At Grading) and a target company.

Two features here I built for myself and recommend to everyone. First, My Guess: before you submit a card, record what grade you think it gets. Second, the Bucket: mark each submission Banker (a measured bet you're confident in) or Casino (a gamble for fun). Both lock the moment the card goes to At Grading, so you can't quietly rewrite history when a card comes back a 7. The Grading Eye panel at the bottom scores your predictions over time and tells you whether your eye runs optimistic or conservative. Keep the buckets honest and you'll learn exactly how good you are.

Cards at grading get a Back By date so the dashboard can show you when your money lands. When a card returns, hit Record grading return, enter the real grade and cert, and the card moves to your graded collection with the grading cost rolled into its basis.

### Stock Check

Physical inventory mode. The list shows every slab with a checkbox. Go through your case, tap each card as you verify it's actually there, and the progress counter tracks you. Checks save on the device you're using, so you can do it from your phone at the case.

You can reprice inline while you're at it, click a price, type the new one, Tab to the next card. There's a filter for cards that haven't been priced in 30 days, which turns a stock check into a repricing pass at the same time. Print list gives you a paper version.

### Slab Photos

Photograph your slabs, upload them here, and the app reads the labels and creates or updates cards. It costs a few cents per photo with an Anthropic key. Review each extraction before it writes anything, nothing saves without your confirmation. Sideways photos are fine, it figures it out.

### Backfill

A cleanup tool. It lists graded cards missing their cert, set, card number, or year, and lets you fix them by dropping a slab photo on the row. Useful after importing old data. If your collection is complete this page just congratulates you.

## A day at a show

How I actually use it. Morning: stock check on my phone while setting up the case. During the day: every buy, sale, and trade goes in as a deal right when it happens, takes under a minute on the phone. The Deals tab has a Show Day view with running totals for the day. Evaluator for anything being negotiated. Evening: check the dashboard, see what the day actually made, and enter grade guesses on anything raw I picked up.

That's it. Questions, find me at the show.
