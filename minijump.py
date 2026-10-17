"""minijump — a fast AceJump / EasyMotion-style plugin for Sublime Text 4.

Same UX as ice9js/ace-jump-sublime (MIT, Copyright (c) 2015 Kuba Birecki),
reimplemented from scratch for performance and the modern ST4 API.

The document is never modified. Labels are drawn as minihtml *phantoms*
(``sublime.PhantomSet``) and targets are highlighted with native regions,
so a jump session costs:

  * zero buffer edits        → no undo entries, no re-parse, no re-highlight,
                               no dirty state, no linter re-runs, nothing to
                               save or restore afterwards,
  * zero syntax switching    → the original swapped every visible view's
                               syntax to a custom .tmLanguage on each jump,
                               forcing a full re-highlight of each file,
  * zero settings save/load  → the original saved/restored ~30 view settings
                               per jump,

while searching is kept tight:

  * the visible region's text is extracted once (a single native ``substr``
    call) and scanned with a cached Python regex, so cost is strictly
    bounded by what is on screen — a search can never scan the rest of the
    file, no matter how sparse the matches,
  * searching stops as soon as every label has a target, so cost per
    keystroke is constant no matter how dense the matches are,
  * all phantoms are submitted per view in a single ``PhantomSet.update()``
    call and all highlights in a single ``add_regions()`` call,
  * label HTML, per-view fonts/colors and other state are cached for the
    lifetime of the session.
"""

import re

import sublime
import sublime_plugin

if int(sublime.version()) < 4000:
    raise ImportError("minijump requires Sublime Text 4 (build 4000+).")

SETTINGS_FILE = "minijump.sublime-settings"
PHANTOM_KEY = "minijump_labels"
REGION_KEY = "minijump_targets"

DEFAULT_LABELS = "sadewcvgtbfhijklmnopqruxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Fallback monospace face when neither the view nor the preferences specify
# one (matches Sublime Text's own per-platform defaults).
DEFAULT_FONT_FACE = {
    "osx": "Menlo",
    "windows": "Consolas",
    "linux": "Monospace",
}

_session = None  # the active _Session, or None
_select = False  # select mode armed for the next jump

_prefs = None
_regex_cache = {}


def _preferences():
    global _prefs
    if _prefs is None:
        _prefs = sublime.load_settings("Preferences.sublime-settings")
    return _prefs


def _fmt_num(value):
    """Format a number for CSS, tolerating strings from settings files."""
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (int, float)):
        return f"{value:g}"
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _escape_html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _cleanup():
    """Tear down the active session: remove overlays, disarm select mode."""
    global _session, _select
    session = _session
    if session is None:
        return
    _session = None
    _select = False
    for view in session.rendered.values():
        if view.is_valid():
            view.erase_regions(REGION_KEY)
            phantom_set = session.phantom_sets.get(view.id())
            if phantom_set is not None:
                phantom_set.update([])
    session.rendered.clear()
    session.phantom_sets.clear()
    session.label_map = {}


class _Session(object):
    """All state for one active jump. Only one session exists at a time."""

    __slots__ = (
        "_html_cache",
        "anchors",
        "awaiting_label",
        "bold",
        "case_sensitive",
        "change_counts",
        "command",
        "current_buffer_only",
        "font_face",
        "font_size",
        "head_char",
        "kind",
        "label_bg",
        "label_fg",
        "label_map",
        "labels",
        "needs_head_char",
        "offset",
        "padding",
        "phantom_sets",
        "radius",
        "rendered",
        "scope",
        "window",
    )

    def __init__(self, window, command, kind, needs_head_char, current_buffer_only):
        settings = sublime.load_settings(SETTINGS_FILE)

        self.window = window
        self.command = command
        self.kind = kind
        self.needs_head_char = needs_head_char
        self.current_buffer_only = bool(current_buffer_only)

        labels = settings.get("labels", DEFAULT_LABELS)
        if isinstance(labels, (list, tuple)):
            labels = "".join(str(label) for label in labels)
        self.labels = "".join(dict.fromkeys(labels or DEFAULT_LABELS))

        self.case_sensitive = bool(settings.get("search_case_sensitivity", True))

        self.scope = settings.get("labels_scope", "invalid") or ""
        self.bold = bool(settings.get("label_bold", True))
        self.padding = max(0, _to_int(settings.get("label_padding") or 0, 0))
        self.radius = max(0, _to_int(settings.get("label_border_radius") or 0, 0))
        try:
            self.offset = float(settings.get("label_offset_em") or 0.6)
        except (TypeError, ValueError):
            self.offset = 0.6
        self.font_face = settings.get("label_font_face") or ""
        self.font_size = settings.get("label_font_size") or 0
        self.label_fg = settings.get("label_foreground") or ""
        self.label_bg = settings.get("label_background") or ""

        self.head_char = ""
        self.awaiting_label = False
        self.label_map = {}  # label -> (view, point)
        self.phantom_sets = {}  # view id -> PhantomSet
        # View objects are unhashable in Sublime Text 4. Keep stable ids as
        # keys and retain the view object as the value.
        self.rendered = {}  # view id -> view currently carrying overlays
        self.change_counts = {}  # view id -> change_count at render time
        self.anchors = {}  # view id -> selection anchor at run time
        self._html_cache = {}  # view id -> cached <span style=...>

    # ------------------------------------------------------------ label html

    def label_html(self, view, label):
        style = self._html_cache.get(view.id())
        if style is None:
            style = self._label_style(view)
            self._html_cache[view.id()] = style
        return f'<span style="{style}">{_escape_html(label)}</span>'

    def _label_style(self, view):
        bg, fg = self._label_colors(view)
        parts = [
            f"background-color:{bg}",
            f"color:{fg}",
        ]
        face = (
            self.font_face
            or view.settings().get("font_face")
            or _preferences().get("font_face")
            or DEFAULT_FONT_FACE.get(sublime.platform(), "monospace")
        )
        face = str(face).replace("'", "")
        if face:
            parts.append(f"font-family:'{face}'")
        size = (
            self.font_size
            or view.settings().get("font_size")
            or _preferences().get("font_size", 10)
        )
        # Sublime's font_size is in points; use pt so labels match the
        # view's text metrics exactly (a px-sized label would misalign).
        parts.append(f"font-size:{_fmt_num(size)}pt")
        if self.bold:
            parts.append("font-weight:bold")
        if self.padding:
            parts.append("padding:0 %dpx" % self.padding)  # noqa: UP031
        if self.radius:
            parts.append("border-radius:%dpx" % self.radius)  # noqa: UP031
        # Pull the label back over the target character (monospace advance
        # widths are ~0.5–0.62em; 0.6em is right for most fonts).
        parts.append(f"position:relative;left:-{_fmt_num(self.offset)}em")
        parts.append("white-space:nowrap")
        return ";".join(parts)

    def _label_colors(self, view):
        """Resolve label colors: explicit settings > labels_scope styling in
        the color scheme > the scheme's accent color."""
        fg = self.label_fg
        bg = self.label_bg
        if fg and bg:
            return bg, fg
        scoped = {}
        if self.scope:
            try:
                scoped = view.style_for_scope(self.scope) or {}
            except Exception:
                scoped = {}
        default = {}
        try:
            default = view.style() or {}
        except Exception:
            pass
        if not bg:
            bg = scoped.get("background") or ""
            if not bg or bg == default.get("background"):
                bg = "var(--accent)"
        if not fg:
            fg = scoped.get("foreground") or ""
            if not fg or fg == default.get("foreground"):
                fg = "color(var(--foreground) min-contrast(var(--accent) 4.5))"
        return bg, fg


class MinijumpCommand(sublime_plugin.WindowCommand):
    """Base class for the jump commands.

    Subclasses set `kind`, `needs_head_char` and `caption`.
    """

    kind = ""
    needs_head_char = True
    caption = "minijump"

    # ---------------------------------------------------------------- setup

    def run(self, current_buffer_only=False, **kwargs):
        global _session
        if _session is not None:
            return
        view = self.window.active_view()
        if view is None:
            return

        session = _Session(
            self.window, self, self.kind, self.needs_head_char, current_buffer_only
        )
        _session = session
        self._session = session

        self.window.show_input_panel(
            self.caption, "", self._on_done, self._on_change, self._on_cancel
        )

        if not self.needs_head_char:
            # Opening the panel shrinks the viewport; let the layout settle
            # before measuring the visible region.
            sublime.set_timeout(self.refresh, 0)

    def is_enabled(self, current_buffer_only=False, **kwargs):
        return _session is None

    # ------------------------------------------------------------- callbacks

    def _on_change(self, text):
        session = self._session
        if session is None or _session is not session:
            return

        if session.awaiting_label:
            if text:
                self._jump(text[-1])
            return

        if session.needs_head_char:
            if len(text) == 1:
                session.head_char = text
                self._render()
            elif len(text) >= 2:
                if text[0] != session.head_char:
                    session.head_char = text[0]
                    self._render()
                # Input panels report the complete input, not just the last
                # key. Use the latest character so repeated edits and IME
                # input cannot leave the session looking stuck.
                self._jump(text[-1])
            else:
                session.head_char = ""
                self._render()
        else:
            if len(text) == 1:
                self._jump(text)
            elif not text:
                self._render()

    def _on_done(self, text):
        """Enter: after a head char, switch to a label panel; else cancel."""
        session = self._session
        if session is None or _session is not session:
            return

        # Make the common `query -> Enter -> label` workflow explicit. The
        # original implementation kept the head character in the same input
        # panel, which made Enter appear to do nothing to new users.
        if session.needs_head_char and not session.awaiting_label and len(text) == 1:
            session.awaiting_label = True
            self.window.show_input_panel(
                "Label", "", self._on_done, self._on_change, self._on_cancel
            )
            self._status("minijump: type a label")
            return

        _cleanup()

    def _on_cancel(self):
        _cleanup()

    # -------------------------------------------------------------- rendering

    def refresh(self):
        """Re-render (used by the select toggle); no-op without a head char."""
        session = self._session
        if session is None or _session is not session:
            return
        if session.needs_head_char and not session.head_char:
            return
        self._render()

    def _render(self):
        """Search the visible region and label the first targets found."""
        session = self._session
        if session is None or _session is not session:
            return
        views = self._views(session)
        for view in views:
            if view.id() not in session.anchors:
                sels = view.sel()
                session.anchors[view.id()] = sels[0].begin() if sels else 0
        self._show_labels(self._find_targets(views, session))

    def _find_targets(self, views, session):
        """Find up to one target per label, as [(view, point)] in view order."""
        if session.needs_head_char and not session.head_char:
            return []
        targets = []
        for view in views:
            if not view.is_valid():
                continue
            remaining = len(session.labels) - len(targets)
            if remaining <= 0:
                break
            region = view.visible_region()
            if session.kind == "line":
                points = _line_scan(view, region, remaining)
            else:
                points = _substr_scan(
                    view,
                    region,
                    r"\b" + re.escape(session.head_char),
                    remaining,
                    session,
                )
            targets.extend((view, point) for point in points)
        return targets

    def _show_labels(self, targets):
        session = self._session

        session.label_map = {}
        per_view = {}  # view id -> [(point, label)]
        views_by_id = {}
        for label, (view, point) in zip(session.labels, targets):
            session.label_map[label] = (view, point)
            per_view.setdefault(view.id(), []).append((point, label))
            views_by_id[view.id()] = view

        for view_id, view in list(session.rendered.items()):
            if view_id not in per_view:
                if view.is_valid():
                    view.erase_regions(REGION_KEY)
                    phantom_set = session.phantom_sets.pop(view.id(), None)
                    if phantom_set is not None:
                        phantom_set.update([])
                session.rendered.pop(view_id, None)

        for view_id, pairs in per_view.items():
            view = views_by_id[view_id]
            if not view.is_valid():
                continue
            phantoms = [
                sublime.Phantom(
                    sublime.Region(point),
                    session.label_html(view, label),
                    sublime.LAYOUT_INLINE,
                )
                for point, label in pairs
            ]
            regions = [sublime.Region(point, point + 1) for point, _ in pairs]

            phantom_set = session.phantom_sets.get(view.id())
            if phantom_set is None:
                phantom_set = sublime.PhantomSet(view, PHANTOM_KEY)
                session.phantom_sets[view.id()] = phantom_set
            phantom_set.update(phantoms)
            view.add_regions(
                REGION_KEY, regions, session.scope, flags=sublime.DRAW_NO_OUTLINE
            )
            session.change_counts[view.id()] = view.change_count()
            session.rendered[view_id] = view

        if not targets:
            hint = f" for {session.head_char!r}" if session.head_char else ""
            self._status(f"minijump: no matches{hint}")
        else:
            views = len(per_view)
            self._status(
                "minijump: %d targets in %d view%s — type a label"  # noqa: UP031
                % (len(targets), views, "" if views == 1 else "s")
            )

    def _views(self, session):
        """Views to label: all visible panes, or just the active view for
        select mode / current_buffer_only."""
        if _select or session.current_buffer_only:
            view = self.window.active_view()
            return [view] if view is not None else []
        window = session.window
        if not window.is_valid():
            return []
        seen = set()
        views = []
        for group in range(window.num_groups()):
            view = window.active_view_in_group(group)
            if view is None or view.id() in seen:
                continue
            seen.add(view.id())
            views.append(view)
        return views

    # ------------------------------------------------------------------ jump

    def _jump(self, label):
        session = self._session
        if session is None or _session is not session:
            return False
        target = session.label_map.get(label)
        if target is None:
            # Accept the other case when it is unambiguous, so pressing `b`
            # can select a displayed `B`.
            target = session.label_map.get(label.swapcase())
        if target is None:
            self._status(
                f"minijump: no visible label {label!r} — type a label "
                "or press Esc to cancel"
            )
            return False

        view, point = target
        if not view.is_valid():
            _cleanup()
            return True
        if view.change_count() != session.change_counts.get(view.id()):
            self._status("minijump: document changed — jump aborted")
            _cleanup()
            return True

        select = _select
        anchor = session.anchors.get(view.id(), point)

        _cleanup()
        self.window.run_command("hide_panel", {"cancel": True})

        self.window.focus_view(view)
        selections = view.sel()
        selections.clear()
        if select and anchor != point:
            selections.add(sublime.Region(anchor, point))
        else:
            selections.add(sublime.Region(point))
        view.show(point)
        return True

    def _status(self, message):
        if self.window.is_valid():
            self.window.status_message(message)


# ------------------------------------------------------------- find helpers


def _substr_scan(view, region, pattern, limit, session):
    """Find up to `limit` matches of `pattern` in `region`.

    The region's text is extracted once with a single native `substr` call
    and scanned with a cached Python regex, so cost is strictly bounded by
    the region's size — never by the size of the file. `re.ASCII` makes
    ``\\b``/``\\w`` behave exactly like the editor's regex engine.

    Returns the matching points.
    """
    flags = re.ASCII
    if not session.case_sensitive:
        flags |= re.IGNORECASE
    compiled = _regex_cache.get((pattern, flags))
    if compiled is None:
        compiled = re.compile(pattern, flags)
        _regex_cache[(pattern, flags)] = compiled

    base = region.begin()
    out = []
    for match in compiled.finditer(view.substr(region)):
        out.append(base + match.start())
        if len(out) >= limit:
            break
    return out


def _line_scan(view, region, limit):
    """Find up to `limit` non-empty lines of `region`. No regex involved —
    line positions are computed directly.

    Returns the first non-whitespace point of each line.
    """
    size = view.size()
    if size == 0 or region.empty():
        return []
    row, _ = view.rowcol(region.begin())
    last_row, _ = view.rowcol(min(region.end(), size) - 1)
    out = []
    while len(out) < limit and row <= last_row:
        line = view.line(view.text_point(row, 0))
        text = view.substr(line)
        if text.strip():
            # Target the first non-whitespace character of the line.
            out.append(line.begin() + len(text) - len(text.lstrip()))
        row += 1
    return out


# ------------------------------------------------------------- jump commands


class MinijumpWordCommand(MinijumpCommand):
    """Jump to the beginning of a word starting with the given character."""

    kind = "word"
    caption = "Head char"


class MinijumpLineCommand(MinijumpCommand):
    """Label all non-empty visible lines and jump to one of them."""

    kind = "line"
    needs_head_char = False
    caption = "Line label"


# ------------------------------------------------------------- select mode


class MinijumpSelectCommand(sublime_plugin.WindowCommand):
    """Toggle select mode: the next jump selects from the cursor to the
    target."""

    def run(self, **kwargs):
        global _select
        _select = not _select
        self.window.status_message("minijump: select %s" % ("ON" if _select else "OFF"))
        session = _session
        if session is not None and session.window.id() == self.window.id():
            session.command.refresh()


# ------------------------------------------------------------------ lifecycle


def plugin_loaded():
    pass


def plugin_unloaded():
    _cleanup()
