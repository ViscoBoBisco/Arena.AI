-- The Reliquary (1/2) — page_text: prose the client renders but never stores.
-- Clock 1, patch none. `page_text` is (ID, Text, NextPageID, VerifiedBuild) — nothing else.
-- Line breaks inside Text are `$B`; a page with NextPageID = 0 ends the book.
-- Entry range: 900400-900499 (docs/04 §4).

DELETE FROM page_text WHERE ID BETWEEN 900400 AND 900429;

INSERT INTO page_text (ID, Text, NextPageID, VerifiedBuild) VALUES
  -- A three-page journal. The chain is the whole mechanism: the client asks for 900400,
  -- draws a "next page" arrow because NextPageID is not 0, then asks for 900401.
  (900400,
   'Field Journal of the Ashen Watch, first leaf.$B$B"The vault below answers to a key that was never cut.$B$B'
   'We counted nine doors and eight keys. The ninth door is the one we were sent to open."',
   900401, 12340),
  (900401,
   'Second leaf.$B$B"Keeper Vael argues the door is a lock and the key is a question.$B$B'
   'I have stopped arguing with him. The door hums when he speaks to it."',
   900402, 12340),
  (900402,
   'Third leaf, unsigned.$B$B"If you are reading this, the Watch did not return. Take the ledger from the '
   'shelf behind you. It is worth more than the door."',
   0, 12340),

  -- A single-page plaque: same table, no chain, no arrow.
  (900410,
   'THE RELIQUARY$B$BBooks read here are ordinary objects. Nothing about this room required a patch, '
   'a model, or a client file: the text is sent to you a page at a time.', 0, 12340),

  -- A two-page ledger, to show a chain that is not three long.
  (900420,
   'Ledger of the Ninth Door$B$BEntries 1-88: doors counted, keys filed, nothing opened.', 900421, 12340),
  (900421,
   'Entries 89-91:$B$B"Nine people at the door. Eight keys. The ninth person was the key.$B$B'
   'We did not open it. It opened us."', 0, 12340);
