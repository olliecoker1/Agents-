# Beast Sanctuary — project guide for Claude

I'm a beginner building a Roblox game called **Beast Sanctuary** with **Rojo** and **Luau**.
I test in Roblox Studio and on my phone. Explain things simply, keep code modular, keep answers short.

> Note: this repo also contains an unrelated older project (`data/`, `report/`, `scan/`). Leave it alone.
> The game lives in `src/` and `default.project.json`.

## Concept
A cute, bright, cosy fantasy game. Players tame wild fantasy beasts (baby phoenix, moss griffin, sea unicorn and so on) and keep them in their own sanctuary, where they earn coins per second, even while offline (capped at 8 hours). Beasts have an element, stats and moves. Players build a team of 3 and fight bosses up a boss tower, and later fight other players' saved defence teams on a ranked ladder. Battles are fully automatic. Beasts never die: a beaten beast "faints" with a funny nap animation.

**Core loop:** Tame beasts, grow the sanctuary, strengthen a team of 3, climb the tower, repeat.

## Rules
- Mobile-first: one-tap controls, big simple UI.
- Everything data-driven: beasts, elements, moves, bosses, floors, prices and odds live in ModuleScript data tables. All balance numbers live in one Config module.
- Free players can obtain every beast. Paid items speed things up or add comfort and cosmetics. No paid raw power that free players can't also earn.
- Everything must be original: no copied names, designs, art, moves or assets from any other game or franchise.

## Server vs client
The server owns taming results, coins, battles, rewards, purchases and saving. The client only handles input and visuals. Validate everything the client sends (rate-limit requests, never trust client-sent numbers).

## Elements (6)
Fire, Water, Nature, Storm, Light, Shadow.
Counters (x1.5 damage when strong, x0.67 when weak):
- Fire beats Nature
- Nature beats Water
- Water beats Fire
- Storm beats Water and is weak to Nature
- Light and Shadow are strong against each other

## Rarity
Common, Uncommon, Rare, Epic, Legendary, Mythic. Rarer beasts have bigger stats, more moves and better coin rates.

Tame-odds per attempt: Common 60%, Uncommon 25%, Rare 10%, Epic 4%, Legendary 0.9%, Mythic 0.1%.

Variants:
| Variant | Chance | Stats | Coins |
|---|---|---|---|
| Normal | 90% | x1 | x1 |
| Golden Aura | 8% | x1.3 | x3 |
| Glowing | 1.7% | x1.6 | x6 |
| Rainbow | 0.3% | x2 | x15 |

## Beast stats
HP, Attack, Speed, an element, 1 passive and 2 moves (rarer beasts may have a 3rd). A "power rating" is one number shown per beast: a formula in Config combining HP, Attack and Speed.

## Starter beast list (30, 5 per element)
Stats in a data table. Claude may suggest names but must ask before adding more.
- Fire: baby phoenix, ember fox, lava tortoise, spark salamander, flare pup
- Water: sea unicorn, bubble drake, koi dragon, tide otter, pearl turtle
- Nature: moss griffin, bloom deer, thorn wolf cub, leaf bunny, vine serpent
- Storm: thunder stallion, cloud eagle, gust cat, spark hare, tempest owl
- Light: star unicorn, halo owl, dawn lion cub, glimmer fawn, sun finch
- Shadow: moon cat, dusk wyvern, shade fox, night bat, ink panther cub

## Taming
Player finds a wild beast, picks a treat (each beast prefers certain treats; the right treat raises tame odds), then plays a tap-timing bar. Rarer beasts move the bar faster. A miss means the beast runs off. The server decides the final result.

## Sanctuary
Each player has a plot with pens. Placed beasts earn coins per second (stat/rarity/variant based). Offline earnings are worked out on join, capped at 8 hours, and shown in a "Welcome back, you earned X!" pop-up. Coins buy more pens, treats and upgrades.

## Battle engine (important)
One pure function `simulateBattle(teamA, teamB, seed)` that runs on the server and returns an ordered list of events (who acts, which move, damage, faints, winner). The client replays the events as an animation and never decides the outcome.
Rules:
- Teams of 3.
- The highest Speed acts first.
- Each beast uses its moves in rotation (or the one best against the enemy's element).
- Damage formula lives in Config.
- A fainted beast is skipped.
- The battle ends when one team is all fainted or after 50 turns (the defender wins a timeout).
- Use the seed so a battle can be replayed identically.
- Write simple tests for it.

## Modes
- **Boss tower:** floors with a boss (element and weakness), rewards (coins, treats, beast shards, rare beasts on milestone floors). Floors are data.
- **Saved-team ladder:** a player saves a defence team (a snapshot). Others fight a copy of it, even when the owner is offline. Ranked with a rating (Elo-style), weekly reward tiers, and anti-abuse limits (opponent choice by rating, a daily attempt count).

## Technical rules
- Rojo layout: `src/server`, `src/client`, `src/shared`.
- DataStore saving with retry and session locking. Never lose player data. Include a data version number so fields can be added later without breaking old saves.
- Purchases use ProcessReceipt correctly and never lose a purchase or grant an item twice.
- Any paid random item or luck boost must show the base odds and boosted odds on screen and use PolicyService to block restricted regions. No crates or mystery boxes at launch.

## Economy pacing targets (tune in Config)
- First pen upgrade at about 2 minutes; each later upgrade takes roughly 2-3x longer.
- A Rare tame every few minutes of active play.
- Tower floors 1-5 beatable with starter beasts; later floors need better teams.

## First 5 minutes (new-player flow)
- Spawn in the sanctuary valley. A wild baby beast sits right in front of the player with an arrow: "Tap to tame!"
- First tame is guaranteed. The second tame is rigged to be a Golden Aura, with a gold flash, chime and "RARE!" banner.
- Prompt to place it in a pen. Coins start ticking up, with a line that beasts earn while you're away.
- By about 2 minutes the first upgrade is affordable and glowing.
- At about 3.5 minutes the Beast Book opens showing 1/30 with silhouettes, and a daily quest and a "first boss" teaser.
- No shop pop-ups and no text walls in the first 5 minutes.

## Monetization (Phase 8 only)
Starter Pack 49 R$ (once per player), 2x Coins 149, Extra Tower Attempts (small, with a free daily amount for everyone), +Team Slot/Pens 199, VIP (cosmetic aura, name tag) 499, Lucky Treat (short tame-luck boost, odds shown) 29-99, Server Luck Boost 199, Sanctuary Club subscription 49-99/month. Keep battle strength out of paid items.

## DO NOT BUILD (until I say so)
Live real-time PvP, trading, crates or mystery boxes, rebirth, guilds, more than 6 elements, more than 3-beast teams, custom 3D models (use simple Parts and shapes), anything not in the current phase.

## How Claude must work
- Small steps. After each, tell me exactly what to test in Studio or on my phone.
- Stop and ask me before: changing project structure, adding a feature not in the current phase, or any decision that would be hard to undo.
- Short comments so I can learn from the code.
- If unclear, ask one short question instead of guessing.

## Phases
- **Phase 0 — Project setup (no Studio needed):** Rojo project (`default.project.json`, `src/server`, `src/client`, `src/shared`), `.gitignore`, README with exact commands, git commit, install instructions for Rojo + Studio plugin. *Done when:* `rojo serve` runs, Studio connects, and a test print from a server script shows in Output.
- **Phase 1 — Data tables and taming:** Elements (with counter table), Rarities, Variants, Beasts (30, placeholder stats), Treats, Config modules. Wild beast spawning in the starting area, treat choice, tap-timing bar. Server validates the attempt and rolls rarity and variant by weighted odds. Tutorial override (first tame guaranteed, second Golden Aura). Coloured Parts as placeholder beasts. Debug command to force any rarity/variant. *Done when:* taming works on phone and in Studio, rarities look about right over 50 attempts, second tame is Golden, spam-tapping doesn't break anything.
- **Phase 2 — Sanctuary and idle income:** Plot with pens, place/remove beasts, coins per second, offline earnings (8-hour cap) on join with pop-up, clean mobile coin HUD. *Done when:* coins tick up, rejoining shows correct offline earnings, removing a beast stops its income.
- **Phase 3 — Saving (do not skip):** Safe DataStore saving for coins, owned beasts (variants, levels), placed beasts, saved team, upgrades, Beast Book, data version, last-seen time. Retry, session locking, autosave every 60s, save on leave and shutdown. Test that data survives rejoining. *Done when:* everything is exactly as left after leaving and rejoining, including after closing Studio mid-session.
- **Phase 4 — Upgrades and first-5-minutes flow:** Pen upgrades, treats, more pens. The guided first 5 minutes above. Analytics logging (first tame, first Golden, first placement, first upgrade, 5 minutes played, day-2 return). *Done when:* a brand-new account can play 5 minutes with no help, and the log shows each step.
- **PLAYTEST GATE:** 3-5 real people play 5 minutes unaided. Note confusion/boredom, fix the biggest problem first.
- **Phase 5 — Battle engine:** `simulateBattle` as above, tests (counter damage, speed order, faint handling, determinism), client replay with placeholder animations, HP bars, damage numbers, test arena command. *Done when:* same seed = same result, counters visibly matter, full 3v3 watchable on phone.
- **Phase 6 — Boss tower:** Floors 1-30 as data, team picker, milestone rewards, daily free attempts, Team Power on screen, floors 1-5 winnable with starters. *Done when:* can climb a few floors, counter teams do better, rewards save, daily limit works.
- **Phase 7 — Saved-team ladder:** Save defence snapshot, opponents by rating, fight copy with same engine, update both ratings, daily limits, anti-abuse, weekly reward tiers and leaderboard. *Done when:* two test accounts fight each other's teams, ratings update correctly, owner's data is never changed by someone else's fight.
- **Phase 8 — Monetization:** Products above, correct ProcessReceipt, odds display, PolicyService, subscription. Review all purchase code together. *Done when:* test purchases work, persist after rejoin, odds shown before every paid random item, free players can still do everything.
- **Phase 9 — Polish and launch prep:** Mobile UI pass, sound and juice, settings menu, exploit review, publishing checklist. *Done when:* smooth on a low-end phone and five real players have tried it.

## Current phase
Phase 0.
