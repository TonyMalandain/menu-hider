"""The window: a grid of app icons, each with a switch.

Shaped after the Main Menu app (LibreMenuEditor): a scrolling grid of tiles,
icon on top, title under it. One switch per tile turns the entry on or off.
The search box and the filter buttons narrow the view.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import core  # noqa: E402

CSS = """
.app-tile {
  padding: 12px 6px;
  border-radius: 12px;
  min-width: 108px;
}
.app-tile:hover { background: alpha(currentColor, .06); }
.app-tile.off .tile-icon,
.app-tile.off .tile-name { opacity: .35; }
.tile-name { font-size: .9em; }
"""

FILTERS = ("all", "visible", "hidden")
FILTER_LABELS = {"all": "All", "visible": "In the menu", "hidden": "Hidden"}


class Tile(Gtk.Box):
    def __init__(self, app, on_toggle):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.app = app
        self._on_toggle = on_toggle
        self.add_css_class("app-tile")

        icon = Gtk.Image.new_from_gicon(app.icon)
        icon.set_pixel_size(48)
        icon.add_css_class("tile-icon")
        self.append(icon)

        name = Gtk.Label(label=app.name)
        name.set_wrap(True)
        name.set_justify(Gtk.Justification.CENTER)
        name.set_lines(2)
        name.set_ellipsize(3)  # Pango.EllipsizeMode.END
        name.set_max_width_chars(14)
        name.add_css_class("tile-name")
        self.append(name)

        self.switch = Gtk.Switch()
        self.switch.set_halign(Gtk.Align.CENTER)
        # The switch reads as "is in the menu", so ON means not hidden.
        self.switch.set_active(not app.hidden)
        self.switch.connect("state-set", self._toggled)
        self.append(self.switch)

        tip = app.comment or app.app_id
        self.set_tooltip_text(f"{app.app_id}\n{tip}" if app.comment else tip)
        self._sync()

    def _toggled(self, _switch, wants_visible):
        self._on_toggle(self, wants_visible)
        return True  # we set the state ourselves once the write succeeded

    def set_visible_state(self, visible):
        self.switch.set_state(visible)
        self.switch.set_active(visible)
        self._sync()

    def _sync(self):
        if self.switch.get_active():
            self.remove_css_class("off")
        else:
            self.add_css_class("off")

    @property
    def is_hidden(self):
        return not self.switch.get_active()

    def matches(self, needle, mode):
        if mode == "visible" and self.is_hidden:
            return False
        if mode == "hidden" and not self.is_hidden:
            return False
        if not needle:
            return True
        return needle in f"{self.app.name} {self.app.app_id}".lower()


class Window(Adw.ApplicationWindow):
    def __init__(self, application, target):
        super().__init__(application=application, title="Menu Hider")
        self.target = target
        self.set_default_size(860, 640)
        self._needle = ""
        self._mode = "all"

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.WindowTitle(title="Menu Hider", subtitle=f"menu of {target.name}")
        )

        self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic")
        self.search_btn.set_tooltip_text("Search")
        header.pack_end(self.search_btn)

        menu = Gio.Menu()
        menu.append("Show every app again", "win.show-all")
        menu.append("Reload", "win.reload")
        more = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(more)
        view.add_top_bar(header)

        # Filter buttons, in their own row so the grid keeps full width.
        filter_row = Gtk.Box(spacing=0)
        filter_row.set_halign(Gtk.Align.CENTER)
        filter_row.add_css_class("linked")
        self._filter_buttons = {}
        first = None
        for key in FILTERS:
            btn = Gtk.ToggleButton(label=FILTER_LABELS[key])
            if first is None:
                first = btn
                btn.set_active(True)
            else:
                btn.set_group(first)
            btn.connect("toggled", self._filter_clicked, key)
            filter_row.append(btn)
            self._filter_buttons[key] = btn

        self.counter = Gtk.Label()
        self.counter.add_css_class("dim-label")
        self.counter.add_css_class("caption")

        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        bar.set_margin_top(6)
        bar.set_margin_bottom(6)
        bar.append(filter_row)
        bar.append(self.counter)
        view.add_top_bar(bar)

        self.search = Gtk.SearchEntry(placeholder_text="Search apps")
        self.search.connect("search-changed", self._search_changed)
        search_bar = Gtk.SearchBar()
        search_bar.set_child(self.search)
        search_bar.connect_entry(self.search)
        search_bar.set_key_capture_widget(self)
        self.search_btn.bind_property(
            "active", search_bar, "search-mode-enabled",
            2 | 4,  # SYNC_CREATE | BIDIRECTIONAL
        )
        view.add_top_bar(search_bar)

        self.flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            column_spacing=6,
            row_spacing=6,
            min_children_per_line=3,
            max_children_per_line=10,
        )
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_margin_start(12)
        self.flow.set_margin_end(12)
        self.flow.set_margin_bottom(12)
        self.flow.set_filter_func(self._filter_child)

        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_child(self.flow)

        self.empty = Adw.StatusPage(
            icon_name="system-search-symbolic",
            title="Nothing here",
            description="No app matches this view.",
        )
        self.stack = Gtk.Stack()
        self.stack.add_named(scroller, "grid")
        self.stack.add_named(self.empty, "empty")
        view.set_content(self.stack)

        for name, fn in (("show-all", self._show_all), ("reload", self._reload)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", fn)
            self.add_action(action)

        self.tiles = []
        self.reload()

    # ---- data ----------------------------------------------------------

    def reload(self):
        child = self.flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.flow.remove(child)
            child = nxt

        apps = core.collect(self.target)
        self.tiles = []
        for app in sorted(apps.values(), key=lambda a: a.name.casefold()):
            tile = Tile(app, self._toggle)
            self.flow.append(tile)
            self.tiles.append(tile)
        self._refilter()

    def _reload(self, *_a):
        self.reload()
        self._toast("Reloaded the app list.")

    # ---- writing -------------------------------------------------------

    def _toggle(self, tile, wants_visible):
        try:
            if wants_visible:
                core.show(tile.app, self.target)
            else:
                core.hide(tile.app, self.target)
        except Exception as exc:  # noqa: BLE001 - shown to the user verbatim
            self._toast(str(exc))
            tile.set_visible_state(not wants_visible)
            return
        core.refresh(self.target)
        # Re-read this one entry so `managed` and `mode` stay true.
        fresh = core.collect(self.target).get(tile.app.app_id)
        if fresh is not None:
            tile.app = fresh
        tile.set_visible_state(wants_visible)
        self._refilter()

    def _show_all(self, *_a):
        failed = 0
        for tile in self.tiles:
            if not tile.is_hidden:
                continue
            try:
                core.show(tile.app, self.target)
            except Exception:  # noqa: BLE001
                failed += 1
                continue
            tile.set_visible_state(True)
        core.refresh(self.target)
        self.reload()
        if failed:
            self._toast(f"{failed} entries were not written by this tool; left alone.")
        else:
            self._toast("Every app is in the menu again.")

    # ---- view ----------------------------------------------------------

    def _search_changed(self, entry):
        self._needle = entry.get_text().strip().casefold()
        self._refilter()

    def _filter_clicked(self, button, key):
        if button.get_active():
            self._mode = key
            self._refilter()

    def _filter_child(self, child):
        return child.get_child().matches(self._needle, self._mode)

    def _refilter(self):
        self.flow.invalidate_filter()
        shown = sum(1 for t in self.tiles if t.matches(self._needle, self._mode))
        hidden = sum(1 for t in self.tiles if t.is_hidden)
        self.counter.set_label(
            f"{len(self.tiles)} apps · {hidden} hidden · showing {shown}"
        )
        self.stack.set_visible_child_name("grid" if shown else "empty")

    def _toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=4))


class App(Adw.Application):
    def __init__(self, target):
        super().__init__(application_id="dev.local.MenuHider",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.target = target

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def do_activate(self):
        win = self.get_active_window() or Window(self, self.target)
        win.present()


def run(target, argv):
    GLib.set_application_name("Menu Hider")
    return App(target).run(argv)
