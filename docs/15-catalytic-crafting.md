# 15 — Catalytic crafting: let ingredients change the result

A profession recipe can be more than a fixed list of reagents. A player might spend extra common materials to make a larger batch, or add a rare catalyst—such as a Moonstone—to create a predictable magical variant. This is a strong fit for the Content Foundry **if we model it as a server-authoritative recipe augmentation**, rather than letting the addon decide what was made.

The machine-readable design is [`designs/catalytic-crafting.json`](../designs/catalytic-crafting.json). It is explicitly `DESIGN_ONLY_NOT_A_FOUNDRY_SPEC`. It contains no asserted item/spell/enchant IDs and does not claim an executable profession recipe or module exists.

## What the inspected core already does

Source was inspected at public AzerothCore revision [`6ffb62abb867dabb258de6813ff5d06d6b011543`](https://github.com/azerothcore/azerothcore-wotlk/tree/6ffb62abb867dabb258de6813ff5d06d6b011543), not pinned as Campaign Party's core.

- `Spell::CheckCast` checks the fixed reagent IDs/counts from the recipe spell; `Spell::TakeReagents` consumes them. `Spell::cast` calls registered `OnCast` handlers and then removes the normal reagents before effect handling, explicitly so the crafted item can use the freed inventory slot.
- `Spell::DoCreateItem` uses the spell effect's output quantity, caps it to the item's stack size, then processes the core's existing gem-perfection and profession-specialization extra-item behavior before storing the result.
- The `SpellScript` interface exposes `OnCheckCast`, `OnCast`, `OnEffectHitTarget`, `PreventHitDefaultEffect`, `SetEffectValue`, and a `CreateItem` helper.
- `PlayerScript::OnPlayerCreateItem(Player*, Item*, uint32 count)` runs after `DoCreateItem` has stored the output. Its callback signature does **not** identify the recipe spell on its own.

These are useful extension points, not proof that an augmentation implementation already exists. Existing specialization bonuses must remain intact and must not accidentally multiply a second time.

## A safe design for the player's choices

A crafting UI can present choices such as:

- **Standard:** the learned stock recipe and its ordinary result.
- **Batch boost:** pay extra base ingredients for a deterministic larger quantity, with a maximum batch and stack/inventory checks.
- **Catalyst imprint:** add an explicit rare material and select a deterministic result, for example a Moonlit equipment variant.
- **Mastery formula:** a vocation or regional discovery unlocks an augmentation; it does not grant the recipe or profession skill client-side.

The UI sends only `{recipe_spell_id, augmentation_key, request_id}`. The server revalidates the learned recipe, skill, active crafter/permissions, material counts, output definition, and request idempotency. It returns a receipt stating exactly which materials were consumed and what output was created. A replay must not consume the catalyst or create a second item.

For a magical output, the safest first version is usually a **different server item template with the same locally verified display** and a known stock effect/enchant. That keeps the effect attached to a well-defined output, rather than trying to infer a recipe from an item after it has been created. If a custom item identity is needed, its `Item.dbc` row must match the server and may need client DBC distribution. If the effect itself is new, its spell data and visual need their own client/server compatibility review. Do not invent a Moonstone ID or assume a stock spell is appropriate.

### Where the client patch might be avoided

- A batch boost may change only the server-side output count, using an existing item identity.
- A catalyst may attach a compatible, verified stock enchant or use an already-existing item variant.
- A sidecar vocation UI can be an addon and server module without creating a new profession skill line.

### Where a patch can become necessary

- A new item identity generally needs a matching client `Item.dbc` row to display and classify correctly.
- A new profession skill line needs DBC skill-line/ability data and verified client presentation.
- A new spell, proc visual, icon, or enchant display may need DBC rows when no suitable stock record exists.

No new art is required for any of these approaches; reusing a stock display is mandatory. DBC changes must be lossless, client/server-consistent, conflict-checked against the actual baseline, and distributed without replacing an unknown custom MPQ.

## The sharp edge: ingredient and output atomicity

The ordering matters. Base reagents are consumed before the craft effect creates the output. An augmentation must define what happens if the optional catalyst is checked but the cast is interrupted, if output storage fails, if the item variant is invalid, or if a replayed request arrives. Consuming a rare stone in `OnCast` and hoping a later output succeeds is not a safe transaction design. AzerothCore's standard spell flow does not, by itself, provide an escrow transaction for optional catalysts or a joint atomic commit of base reagents, catalyst and output.

The implementation must choose and test a clear policy, for example:

1. Associate an augmentation intent with a one-use server-side craft context after cast validation. "Reserve" here means a module-owned reservation/hold policy; it is **not** a claim that SpellScript exposes a stock inventory escrow primitive.
2. Bind that context to the character, exact recipe spell and request/cast identity; make it idempotent and expire/clean it on every failure path.
3. Select/intercept the exact output **before it is stored** (for example in a validated effect path). `OnPlayerCreateItem` is too late to choose or replace the result; use it only for successful-storage confirmation/receipt if appropriate.
4. Commit the optional cost exactly once after output success, or compensate/refund it (the failed craft's rollback) on failure. Separately account for the native base-reagent consumption order; if the required guarantee cannot be made around it, use a custom crafting service that owns the full cost/output operation rather than pretending the native cast is atomic.
5. Use an inventory-full test, missing ingredient test, spell cancel/interruption test, restart/replay test and output-template-missing test before calling it qualified.

A custom crafting service invoked through a gossip/addon intent is an alternative if the native SpellScript path cannot give the needed transaction guarantee. It must still call AzerothCore inventory and skill authorities; do not create a parallel bag or profession database. A static DBC recipe variant is another option, but it creates one visible recipe per variant, needs verified spell/reagent records and is less flexible.

## Creative consequences

Once materials can select deterministic outcomes, the system can support:

1. **Material-efficient bulk orders:** common reagents buy a bounded batch-size increase; no lottery and no hidden fees.
2. **Elemental imprints:** Moonstone, Emberglass or Stormsalt select different stock-spell or enchant variants sharing one verified appearance.
3. **Regional provenance:** an item records which region's material was used; clues and gathering routes can unlock recipes without forcing a level corridor.
4. **Party commissions:** selected characters contribute ingredients to a craft order through an explicit Campaign Party escrow contract. No direct addon access to another character's bags.
5. **Profession/vocation bridges:** a sidecar Cartographer discovers a formula, while an existing qualified crafter actually makes it.
6. **Economy feedback:** optional catalysts create meaningful demand curves that can be modeled against AHBot, while Campaign Party's Material Exchange and economy remain authoritative.
7. **Build-your-own relic:** one base gear template, several deterministic catalyst paths, and level-banded stats through verified stock scaling data.

This turns crafting into a preparation and choice system, not just a chance to roll a bonus. It also gives the profession story a reason to cross the whole world: recipes become discoveries, ingredients carry provenance, and crafted equipment becomes a record of the player's choices.

## First implementation slice

Start small: one existing craft recipe, one deterministic batch augmentation, one rare-catalyst variant, one verified reused item display, and an auditable craft receipt. Do **not** begin by making every recipe dynamic.

Acceptance conditions are in the design JSON; the core negative cases are: insufficient optional materials, wrong/unknown recipe, wrong profession skill, invalid output, duplicate request ID, inventory full, cancellation, module restart and output-creation failure. The normal recipe must continue to work unchanged, and the existing specialization bonus must compose exactly once.

## Evidence and status

| Check | Status |
|---|---|
| Upstream recipe reagent consumption and output code inspected at a recorded public revision | Source-path inspection only |
| Reagent/catalyst IDs verified against target client and server data | `NOT_ASSESSED` |
| Generic Foundry compiler emits executable augmented profession recipes | Not implemented; current recipe output is descriptive |
| C++ module compiled against a pinned AzerothCore revision | `ARENA-COMPILE` pending |
| SQL/material transaction applied and rolled back on disposable database | `ARENA-DB` pending |
| Playerbots, Material Exchange and Campaign Party integration | `CAMPAIGN-ADMISSION` / `CAMPAIGN-LIVE` pending |

The safest next engineering step is a synthetic recipe-augmentation contract and fault model first. Only after it demonstrates exactly-once spending, output/result consistency, refund behavior and existing-craft preservation should a module bind the contract to AzerothCore spell hooks or a crafting service.
