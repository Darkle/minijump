# minijump

A sublime text package for jumping around words in your editor

AceJump for **Sublime Text 4**, rebuilt for speed. Jump the cursor to any
word or line currently visible on screen — across all open panes —
without touching the mouse.

Same workflow as the original
[ace-jump-sublime](https://github.com/ice9js/ace-jump-sublime) (MIT,
© 2015 Kuba Birecki), but reimplemented from scratch on top of the ST4
phantom API so that a jump **never modifies your document**.

## Why it's fast

The original plugin edited the buffer to show labels (then undid the edits),
switched every visible view's syntax to a custom `.tmLanguage` (forcing a full
re-highlight on every jump), and saved/restored ~30 view settings per jump.

minijump does none of that:

| Cost                         | Original                         | minijump                                                                     |
| ---------------------------- | -------------------------------- | ---------------------------------------------------------------------------- |
| Buffer edits / undo entries  | one edit per label               | **zero** — labels are phantoms                                               |
| Re-parse & re-highlight      | every jump (syntax switch)       | **zero**                                                                     |
| Settings save/restore        | ~30 settings × every view        | **zero**                                                                     |
| Linter re-runs / dirty state | yes                              | **zero**                                                                     |
| Search                       | Python `find()` loop over buffer | one `substr` + cached Python regex, strictly bounded to the visible region   |
| Line mode search             | regex over visible text          | **no regex at all** — line positions computed directly                       |
| Match density                | cost grows with matches          | **constant per keystroke** — search stops once every label has a target      |
| Label rendering              | buffer text replacement          | one `PhantomSet.update()` per view, one `add_regions()` per view             |

Everything is bounded by the number of labels and the visible region, so
performance depends on what's on screen — never on file size. Cursor jumps
land instantly, and Esc / the jump itself leaves no trace (no undo entries,
no dirty files, no linter noise).

## Installation

1. `Preferences → Browse Packages…`
2. Copy the `minijump` folder into it.
3. Restart Sublime Text (or run `Preferences → Package Control → Reload Packages`).

If you have the original `AceJump` package installed, remove it first — both
use the same default key bindings. Key bindings you wrote for the original
(`ace_jump_word`, `ace_jump_line`, `ace_jump_select`) need to be renamed to
`minijump_word`, `minijump_line`, `minijump_select`.

## Usage

| Mode | Keys (Win/Linux) | Keys (macOS)  | Flow                                   |
| ---- | ---------------- | ------------- | -------------------------------------- |
| Word | `Ctrl+Shift+;`   | `Cmd+Shift+;` | type head char → type label            |
| Line | `Ctrl+Shift+.`   | `Cmd+Shift+.` | labels appear immediately → type label |

- **Word mode** jumps to the beginning of a word starting with the typed
  character (alphanumerics and `_`).
- **Line mode** labels every non-empty visible line; you jump to the first
  non-whitespace character of the chosen line.

No `Enter` is needed after a label. Each jump labels at most one target per
label character (52 by default); targets beyond that are not shown.

### Select mode

Press `Alt+;` (macOS: `Ctrl+;`) before (or during) a jump to arm select mode:
the next jump selects from the cursor to the target (current file only).
Toggling it while labels are visible re-renders them immediately.

### Options

Every command also accepts `"current_buffer_only": true` to restrict the jump
to the active buffer:

```jsonc
{ "keys": ["ctrl+shift+;"], "command": "minijump_word", "args": { "current_buffer_only": true } }
```

## Settings

`Preferences → Package Settings → minijump → Settings – User`:

| Setting                                 | Default    | Description                                                                                                                        |
| --------------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `labels`                                | `a-z A-Z`  | Label characters, in order. Unique chars; string or array.                                                                         |
| `labels_scope`                          | `invalid`  | Scope used for label colors + target highlight. `""` disables the highlight.                                                       |
| `search_case_sensitivity`               | `true`     | Case-sensitive search in word mode.                                                                                                |
| `label_foreground` / `label_background` | `""`       | Explicit label colors (CSS); empty = derive from `labels_scope`/scheme.                                                            |
| `label_font_face` / `label_font_size`   | `""` / `0` | Label font; empty/0 = match the view's font.                                                                                       |
| `label_bold`                            | `true`     | Bold labels.                                                                                                                       |
| `label_padding`                         | `1`        | Horizontal padding (px) around each label.                                                                                         |
| `label_border_radius`                   | `2`        | Corner radius (px) of each label.                                                                                                  |
| `label_offset_em`                       | `0.6`      | Pull-back of the label over the target char, in em. Most monospace fonts are ~0.6em per character; use `0.5` for e.g. Ubuntu Mono. |

Colors come from the selected color scheme: if it styles `labels_scope`, those
colors are used (alpha included); otherwise the scheme's accent color is used
with a contrasting foreground, so labels stay readable in any theme.

## Differences from the original

- Labels are phantoms — the file is never touched (see table above).
- Line mode labels are visible immediately; jump lands on the first
  non-whitespace character of the line (the original required pressing Enter
  first and jumped to column 0).
- Typing an invalid label keeps the panel open with a hint instead of
  killing the session.
- Jumps are aborted cleanly if the document changed mid-session.
- `save_files_after_jump` and `view_settings` are gone — they existed only to
  work around the original's buffer edits.
- Character mode, within-line mode, the add-cursor and jump-after modifiers,
  and Enter-to-cycle batches are gone — minijump keeps only word mode, line
  mode and select mode.
- Known issues of the original select keybinding are fixed: it re-renders
  live and works reliably.

## License

MIT — see [LICENSE](LICENSE). Includes the copyright of the original
ace-jump-sublime author (Kuba Birecki).
