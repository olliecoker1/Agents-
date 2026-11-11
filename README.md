# Beast Sanctuary

A cosy Roblox beast-taming game, built with Rojo + Luau. Design rules live in `CLAUDE.md`.

## Folder layout
| Folder | Ends up in Studio as | Runs on |
|---|---|---|
| `src/server` | `ServerScriptService.Server` | Server only |
| `src/client` | `StarterPlayer.StarterPlayerScripts.Client` | Each player's device |
| `src/shared` | `ReplicatedStorage.Shared` | Both (ModuleScripts) |

File names tell Rojo what to make:
- `Name.server.luau` → Script
- `Name.client.luau` → LocalScript
- `Name.luau` → ModuleScript

## Daily commands
Open a terminal **in this folder**, then:

```bash
rojo serve
```

Leave it running. In Roblox Studio:
1. Open a place (a new Baseplate is fine).
2. **Plugins** tab → **Rojo** → **Connect** (default `localhost:34872`).
3. Press **Play**. The **Output** window (View → Output) should show:
   `[Beast Sanctuary] Server is running! Rojo sync works.`

Edits you save in `src/` appear in Studio instantly while connected.

To build a place file without Studio:

```bash
rojo build -o BeastSanctuary.rbxlx
```

## Saving your work
```bash
git add -A
git commit -m "Describe what you changed"
```
