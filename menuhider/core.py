"""Find the GNOME menu entries that do not come from Flatpak, and hide them.

Hiding is per user and reversible. We never block execution. We only add
`NoDisplay=true` to a copy of the `.desktop` file in the user's own
`~/.local/share/applications`, which shadows the system one.

Three write modes exist, and the difference is the whole safety story:

  copy    - the user had no file of that name. We copy the system file,
            add the keys, and stamp it. Restoring deletes our copy.
  inplace - the user ALREADY had a file of that name (their own
            customisation). We do not overwrite it. We add the keys to it
            and stamp it. Restoring removes only those keys.
  symlink - the user's file is a SYMLINK, so writing to it would destroy the
            link. We replace the link with a real file, and we record where
            the link pointed. Restoring puts the link back. GNOME web apps
            are symlinks into ~/.local/share/xdg-desktop-portal/applications,
            and they are exactly the kind of entry a parent wants to hide, so
            refusing them was not an option.

Anything in the user's directory without our stamp was not put there by us,
so we never delete it. On this machine that is not academic: 9 of the 15
files in ~/.local/share/applications already carry NoDisplay, written by
Chrome and Epiphany.
"""

import os
import pwd
import shutil
import subprocess

from gi.repository import Gio, GLib

ENTRY = GLib.KEY_FILE_DESKTOP_GROUP  # "Desktop Entry"

# Our stamp. Presence of MARK means "this file is ours to undo".
MARK = "X-MenuHider"
MARK_MODE = "X-MenuHider-Mode"
MARK_LINK = "X-MenuHider-Link"  # only in symlink mode: where the link pointed
MARK_KEYS = (MARK, MARK_MODE, MARK_LINK)

# The desktop environment we filter OnlyShowIn / NotShowIn against.
DESKTOP = "GNOME"


class Target:
    """The user whose menu we are editing, and where their files live."""

    def __init__(self, username=None):
        if username is None:
            username = pwd.getpwuid(os.getuid()).pw_name
        self.pw = pwd.getpwnam(username)
        self.name = self.pw.pw_name
        self.is_self = self.pw.pw_uid == os.getuid()
        if not self.is_self and os.geteuid() != 0:
            raise PermissionError(
                f"editing the menu of '{self.name}' needs root; "
                f"re-run with: sudo -E {os.path.basename(_argv0())} --user {self.name}"
            )
        self.override_dir = os.path.join(
            self.pw.pw_dir, ".local", "share", "applications"
        )

    def system_dirs(self):
        """Shared `applications` directories, with Flatpak exports removed.

        Flatpak apps are excluded on purpose: malcontent already filters
        those, so listing them here would only invite double work.
        """
        dirs = []
        for base in list(GLib.get_system_data_dirs()):
            if "/flatpak/exports/" in base.rstrip("/") + "/":
                continue
            path = os.path.join(base, "applications")
            if os.path.isdir(path) and path != self.override_dir:
                dirs.append(path)
        return dirs

    def chown(self, path):
        """Give a file we wrote back to the target user.

        lchown, not chown: a restored symlink must not have its target's
        ownership changed.
        """
        if not self.is_self:
            os.lchown(path, self.pw.pw_uid, self.pw.pw_gid)


def _argv0():
    import sys

    return sys.argv[0]


def _load(path):
    kf = GLib.KeyFile()
    try:
        kf.load_from_file(path, GLib.KeyFileFlags.KEEP_COMMENTS
                          | GLib.KeyFileFlags.KEEP_TRANSLATIONS)
    except GLib.Error:
        return None
    return kf


def _bool(kf, key):
    try:
        return kf.get_boolean(ENTRY, key)
    except GLib.Error:
        return False


def _str(kf, key):
    try:
        return kf.get_string(ENTRY, key)
    except GLib.Error:
        return None


def _has(kf, key):
    """Exact key test. PyGObject strips KeyFile.has_key, so we read the keys."""
    try:
        keys = kf.get_keys(ENTRY)
    except GLib.Error:
        return False
    if isinstance(keys, tuple):  # some bindings return (keys, length)
        keys = keys[0]
    return key in keys


def _list(kf, key):
    try:
        return [s for s in kf.get_string_list(ENTRY, key) if s]
    except GLib.Error:
        return []


def _is_flatpak(kf, path):
    """Three independent tests, because one is not enough.

    `X-Flatpak-RenamedFrom` is NOT a Flatpak marker. A plain DNF package can
    carry it: /usr/share/applications/org.gnome.TextEditor.desktop does. So we
    test the exact key `X-Flatpak`, never a prefix, never a grep.
    """
    if "/flatpak/exports/" in path:
        return True
    if _has(kf, "X-Flatpak"):
        return True
    # A Flatpak export can sit in the user's own applications directory, where
    # the path test does not reach, and its Exec can be an absolute path, where
    # a bare "flatpak run" test does not reach either. Both happen on this
    # machine (com.google.Chrome, org.gnome.Epiphany), and only the X-Flatpak
    # key above catches them. This Exec test is the third net, not the first.
    words = (_str(kf, "Exec") or "").split()
    for i, word in enumerate(words[:-1]):
        if os.path.basename(word) == "flatpak" and words[i + 1] == "run":
            return True
    return False


def _shows_in_gnome(kf):
    only = _list(kf, "OnlyShowIn")
    if only and DESKTOP not in only:
        return False
    return DESKTOP not in _list(kf, "NotShowIn")


def _tryexec_ok(kf):
    want = _str(kf, "TryExec")
    if not want:
        return True
    if os.path.isabs(want):
        return os.access(want, os.X_OK)
    return shutil.which(want) is not None


class App:
    """One candidate menu entry."""

    def __init__(self, app_id, system_path, user_path, base_kf, user_kf):
        self.app_id = app_id
        self.system_path = system_path
        self.user_path = user_path
        self._base_kf = base_kf
        self._user_kf = user_kf

    # ---- what the user sees -------------------------------------------

    @property
    def base_path(self):
        """The file our decision is read from: the system one if there is one."""
        return self.system_path or self.user_path

    @property
    def name(self):
        kf = self._user_kf or self._base_kf
        return (_str(kf, "Name") or self.app_id.removesuffix(".desktop"))

    @property
    def comment(self):
        kf = self._user_kf or self._base_kf
        return _str(kf, "Comment") or ""

    @property
    def icon(self):
        info = Gio.DesktopAppInfo.new_from_filename(self.base_path)
        if info is not None:
            gicon = info.get_icon()
            if gicon is not None:
                return gicon
        return Gio.ThemedIcon.new("application-x-executable")

    # ---- state ---------------------------------------------------------

    @property
    def managed(self):
        """True when WE added the keys, so we know how to take them away."""
        if self._user_kf is None:
            return False
        return _bool(self._user_kf, MARK)

    @property
    def mode(self):
        """How the hide was written, or how it would be written."""
        if self.managed:
            return _str(self._user_kf, MARK_MODE) or "copy"
        if not self.user_path:
            return "copy"
        return "symlink" if os.path.islink(self.user_path) else "inplace"

    @property
    def hidden(self):
        kf = self._user_kf or self._base_kf
        return _bool(kf, "NoDisplay")


def collect(target):
    """Return the non-Flatpak entries that belong in the menu, by id.

    An entry qualifies when, reading the file the system provides:
      - it is Type=Application
      - it is not Hidden
      - it is not already NoDisplay by someone other than us
      - GNOME is allowed to show it
      - its TryExec resolves
    """
    base = {}  # app_id -> (path, keyfile)
    for d in target.system_dirs():
        for entry in sorted(os.listdir(d)):
            if not entry.endswith(".desktop") or entry in base:
                continue
            path = os.path.join(d, entry)
            kf = _load(path)
            if kf is not None:
                base[entry] = (path, kf)

    user = {}
    if os.path.isdir(target.override_dir):
        for entry in sorted(os.listdir(target.override_dir)):
            if not entry.endswith(".desktop"):
                continue
            path = os.path.join(target.override_dir, entry)
            kf = _load(path)
            if kf is not None:
                user[entry] = (path, kf)

    apps = {}
    for app_id in sorted(set(base) | set(user)):
        sys_path, sys_kf = base.get(app_id, (None, None))
        usr_path, usr_kf = user.get(app_id, (None, None))
        decide_kf = sys_kf or usr_kf
        decide_path = sys_path or usr_path

        if _str(decide_kf, "Type") != "Application":
            continue
        if _is_flatpak(decide_kf, decide_path):
            continue
        if _bool(decide_kf, "Hidden"):
            continue
        if not _shows_in_gnome(decide_kf):
            continue
        if not _tryexec_ok(decide_kf):
            continue

        app = App(app_id, sys_path, usr_path, decide_kf, usr_kf)
        # Already invisible, and not by our hand: somebody else's business.
        # This is what spares the 9 Chrome and Epiphany files.
        if app.hidden and not app.managed:
            continue
        apps[app_id] = app
    return apps


def _mkdirs(leaf, target):
    """Create missing directories and give EVERY new level to the target user.

    os.makedirs alone would leave ~/.local and ~/.local/share owned by root
    for a child account that has never had them, which breaks that account in
    ways that have nothing to do with menus.
    """
    missing = []
    path = leaf
    while not os.path.isdir(path):
        missing.append(path)
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    for path in reversed(missing):
        os.mkdir(path, 0o755)
        target.chown(path)


def _write(kf, path, target):
    _mkdirs(os.path.dirname(path), target)
    data, _ = kf.to_data()
    tmp = path + ".menuhider-new"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
    os.replace(tmp, path)
    os.chmod(path, 0o644)
    target.chown(path)


def hide(app, target):
    """Make one entry invisible. Returns the path written."""
    if app.hidden and app.managed:
        return app.user_path

    mode = app.mode
    if mode == "copy":
        kf = _load(app.system_path)
        out = os.path.join(target.override_dir, app.app_id)
    else:
        kf = _load(app.user_path)
        out = app.user_path
    if kf is None:
        raise OSError(f"cannot read the desktop file for {app.app_id}")

    kf.set_boolean(ENTRY, "NoDisplay", True)
    kf.set_boolean(ENTRY, MARK, True)
    kf.set_string(ENTRY, MARK_MODE, mode)
    if mode == "symlink":
        # Read the link BEFORE we overwrite it, or the way back is lost.
        kf.set_string(ENTRY, MARK_LINK, os.readlink(out))
    _write(kf, out, target)
    return out


def show(app, target):
    """Undo our own hide. Refuses to touch a file we did not stamp."""
    if not app.user_path:
        return None
    if not app.managed:
        raise PermissionError(
            f"{app.user_path} was not written by this tool; refusing to change it"
        )

    if app.mode == "copy":
        os.unlink(app.user_path)
        return None

    kf = _load(app.user_path)
    if kf is None:
        raise OSError(f"cannot read {app.user_path}")

    if app.mode == "symlink":
        link = _str(kf, MARK_LINK)
        if not link:
            raise OSError(
                f"{app.user_path} says it replaced a symlink but does not say "
                f"which one; restore it by hand and delete the file"
            )
        os.unlink(app.user_path)
        os.symlink(link, app.user_path)
        target.chown(app.user_path)
        return app.user_path

    for key in ("NoDisplay",) + MARK_KEYS:
        try:
            kf.remove_key(ENTRY, key)
        except GLib.Error:
            pass
    _write(kf, app.user_path, target)
    return app.user_path


def refresh(target):
    """Rebuild the desktop database so the shell notices at once."""
    tool = shutil.which("update-desktop-database")
    if tool and os.path.isdir(target.override_dir):
        subprocess.run([tool, target.override_dir],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False)
