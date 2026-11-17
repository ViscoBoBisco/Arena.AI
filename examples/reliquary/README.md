# The Reliquary — books, plaques and ledgers as a content system

Prose is the cheapest content there is, and 3.3.5a will render an arbitrary amount of it from one
128-row table. `page_text` is `(ID, Text, NextPageID, VerifiedBuild)`; `NextPageID` is the *only*
thing that makes the client draw a "next page" arrow, so a chain of rows is a book and a row with
`NextPageID = 0` is a plaque.

| | |
|---|---|
| **Patch cost** | none |
| **Reused client assets** | `GameObjectDisplayInfo` lecterns, `PageTextMaterial.dbc` paper, `Languages.dbc` |
| **Clock** | 1 — `page_text` rows are read by the server and narrated per page |

| Element | Reused client asset |
|---|---|
| Lectern, plaque, shelf | existing `GameObjectDisplayInfo` rows (pick ones that fit the fiction) |
| Page paper texture | a stock `PageTextMaterial.dbc` row (`Data2 = 1` here) |
| Language gating | `Data1` → `Languages.dbc`, `0` = universal |
| The text itself | yours, runtime — no DBC row, no MPQ entry |

## Files

```
sql/01_pages.sql      three page_text chains: a 3-page journal, a 1-page plaque, a 2-page ledger
sql/02_lecterns.sql   three type-9 game objects reading those pages, plus spawns
sql/99_rollback.sql   removes both, text first
```

## Install

```bash
mysql -u acore -p acore_world < sql/01_pages.sql
mysql -u acore -p acore_world < sql/02_lecterns.sql
```

Restart the worldserver (or `.reload page_text` if your fork has it; a restart is the honest answer).
Then `.gobject add 900401` next to yourself, or walk to the anteroom coordinates in the SQL.

## What the player sees

Click the journal: a paper window with the first leaf, and an arrow. The arrow asks for 900401, which
asks for 900402, which has no successor — so the arrow disappears on the last page. The plaque opens
straight into one page with no arrow at all. Nothing was installed on the client.

## Acceptance

| Check | How |
|---|---|
| The page opens | click the lectern → paper window with the journal's first leaf |
| The chain works | the arrow advances page 1 → 2 → 3 and then vanishes |
| A single page has no arrow | the plaque opens with no arrow, ever |
| Line breaks work | `$B` produced the paragraph breaks; `|cff…|r` would colour text |
| Text is live | edit a `page_text.Text` row, restart, read it again — no client cache involved |
| Language gating is real | set `Data1 = 1` (Common) and read it with a character who lacks the language |
| Rollback is clean | `99_rollback.sql`, then the lecterns are gone and the pages are unreachable |

## Where this stops being free

| Variant | Cost | Why |
|---|---|---|
| Lecterns/plaques (this example) | none | text is server-sent per page |
| A **readable item** (`item_template.PageText`) | **DBC rows** | the *item* must exist in the client's `Item.dbc` — name, icon and all |
| Mail delivery of the same text | none | mail bodies and subjects are server-sent strings |
| A book with a custom paper texture | assets | that is new art, which this project does not do |

The item case is the interesting one: a player reading `item_template` lore thinks they have built a
patch-free book, and they have not, because the client has no idea what item 900401 *is*. The object
case sidesteps it entirely — a lectern needs only a display ID that already exists.

## Why exactly this layer

`page_text` is clock 1 and patch none: the row is read by the server and narrated per request. It is
also the best proof that "content" is not limited to items, quests and spawns — any prose you can
write, you can ship, in every locale you are willing to translate (see `page_text_locale`).

Compare: [docs/09 §7](../../docs/09-alteration-catalog.md) puts text delivery in `content`;
[docs/10 §2.2](../../docs/10-novel-alterations.md) treats this table as a CMS, including the trick of
generating chains per player rather than writing them by hand.
