# minijump

A sublime text package that enables you to jump the cursor to any
word or line currently visible on screen.

Note: This package is AI generated.

This package is heavily inspired by [AceJump](https://github.com/ice9js/ace-jump-sublime)

## Differences to AceJump:

- Removed the following features (as I dont personally use them):
  - Character mode
  - Within Line mode
  - Multiple cursors mode
  - Jump-after mode
  - Batching
- Labels are phantoms — the file is never touched (see table above).
- Line mode labels are visible immediately; jump lands on the first non-whitespace character of the line (the original required pressing Enter first and jumped to column 0).
- Typing an invalid label keeps the panel open with a hint instead of killing the session.
- Jumps are aborted cleanly if the document changed mid-session.
- `save_files_after_jump` and `view_settings` are gone — they existed only to work around the original's buffer edits.
- Uses a more performant approach available in Sublime Text 4:
  - The document is never modified. Labels are drawn as minihtml _phantoms_ (`sublime.PhantomSet`) and targets are highlighted with native regions, so a jump session costs:
    - zero buffer edits → no undo entries, no re-parse, no re-highlight, no dirty state, no linter re-runs, nothing to save or restore afterwards.
    - zero syntax switching → the original swapped every visible view's syntax to a custom .tmLanguage on each jump, forcing a full re-highlight of each file.
    - zero settings save/load → the original saved/restored ~30 view settings per jump.

  - Searching is kept tight:
    - the visible region's text is extracted once (a single native `substr` call) and scanned with a cached Python regex, so cost is strictly bounded by what is on screen — a search can never scan the rest of the file, no matter how sparse the matches.
    - searching stops as soon as every label has a target, so cost per keystroke is constant no matter how dense the matches are.
    - all phantoms are submitted per view in a single `PhantomSet.update()` call and all highlights in a single `add_regions()` call.
    - label HTML, per-view fonts/colors and other state are cached for the lifetime of the session.

## Installation

1. `Preferences → Browse Packages…`
2. Copy the `minijump` folder into it.
3. Restart Sublime Text (or run `Preferences → Package Control → Reload Packages`).

## Usage

| Mode | Keys (Win/Linux) | Keys (macOS)  | Flow                                   |
| ---- | ---------------- | ------------- | -------------------------------------- |
| Word | `Shift+Enter`    | `Shift+Enter` | type head char → type label            |
| Line | `Ctrl+Shift+.`   | `Cmd+Shift+.` | labels appear immediately → type label |

- **Word mode** jumps to the beginning of a word starting with the typed character (alphanumerics and `_`).
- **Line mode** labels every non-empty visible line; you jump to the first non-whitespace character of the chosen line.

No `Enter` is needed after a label. Each jump labels at most one target per label character (52 by default); targets beyond that are not shown.

### Select mode

Press `Alt+;` (macOS: `Ctrl+;`) before (or during) a jump to arm select mode: the next jump selects from the cursor to the target (current file only). Toggling it while labels are visible re-renders them immediately.

### Options

Every command also accepts `"current_buffer_only": true` to restrict the jump to the active buffer:

```jsonc
{
  "keys": ["shift+enter"],
  "command": "minijump_word",
  "args": { "current_buffer_only": true },
}
```

## Settings

`Preferences → Package Settings → minijump → Settings – User`:

| Setting                                   | Default               | Description                                                                                                                        |
| ----------------------------------------- | --------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `labels`                                  | `a-z A-Z`             | Label characters, in order. Unique chars; string or array.                                                                         |
| `search_case_sensitivity`                 | `true`                | Case-sensitive search in word mode.                                                                                                |
| `label_foreground` / `label_background`   | `#f7f7f7` / `#ec5f67` | Label colors (any CSS color). Empty background = scheme accent; empty foreground = contrasting color.                              |
| `target_foreground` / `target_background` | `#f7f7f7` / `#ec5f67` | Target-character highlight colors. Empty background disables the highlight.                                                        |

All colors are set explicitly; nothing is derived from scopes.

Sublime can only color highlights (`add_regions`) through the color scheme, so for the target highlight minijump writes a tiny color-scheme override per scheme in use to `Packages/User/minijump/<Scheme>.sublime-color-scheme`. It adds a single rule for the private `minijump.target` scope and is merged with your scheme by Sublime (your own overrides are untouched). The files are only rewritten when the target colors or your color scheme change; setting `target_background` to `""` empties them (they are never deleted while in use, as Sublime fails to reload a scheme whose override file disappears). They are left in place if you uninstall minijump; delete `Packages/User/minijump/` (with Sublime closed) to remove them.
