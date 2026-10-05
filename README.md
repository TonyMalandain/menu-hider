# Menu Hider

Hide app menu entries for one user. Works on the apps that `malcontent`
(Parental Controls) does not cover.

`malcontent` only filters Flatpak apps. Anything installed with `dnf` stays in
the menu. This tool covers that gap.

**This hides the icon. It does not block the app.** A child who knows the
command can still start the app from a terminal. That is the agreed scope.

## What it needs

Already on Fedora 44:

- `python3-gobject-base`
- `gtk4`
- `libadwaita`

## Run it

Open the window for your own menu:

```
./menu-hider
```

Open the window for a child's menu (needs root, because it writes in their
home):

```
sudo -E ./menu-hider --user CHILD
```

`sudo -E` keeps `WAYLAND_DISPLAY` and `XDG_RUNTIME_DIR`, so the window can
appear. Without `-E` you get a blank error about no display.

## The window

A grid of icons, title under each one, and a switch.

- Switch **on** = the app is in the menu.
- Switch **off** = the app is hidden.
- Three filter buttons: **All**, **In the menu**, **Hidden**.
- The search button narrows the grid by name or by id.
- The menu button has **Show every app again**.

Every change is written at once. There is no Save button.

## Text mode

Every action also works without the window. Use this to set up a new account
in one line.

| Command | What it does |
| --- | --- |
| `menu-hider --list` | print every entry and its state |
| `menu-hider --list --only hidden` | print the hidden ones |
| `menu-hider --hide ID ID ...` | hide these |
| `menu-hider --show ID ID ...` | put these back |
| `menu-hider --show-all` | put back everything this tool hid |
| `menu-hider --user NAME ...` | do it for another user (needs root) |

The `ID` is the first column of `--list`. The `.desktop` ending is optional.

Example:

```
sudo -E ./menu-hider --user CHILD --hide org.gnome.Terminal virt-manager rstudio
```

## How it hides an entry

It writes a copy of the `.desktop` file into the user's own
`~/.local/share/applications`, with `NoDisplay=true` added. That copy shadows
the system one. The system file is never changed.

The key is set with a proper key-file parser, not appended to the end of the
file. This matters. Many `.desktop` files end with a `[Desktop Action ...]`
group, so a plain `echo "NoDisplay=true" >> file` puts the key in the wrong
group, where it does nothing. The test suite has a case for this.

## It will not break what it did not make

Every file this tool writes is stamped with `X-MenuHider=true`. It will only
undo a file carrying that stamp. If you already had your own `.desktop` file
of the same name, it is **not** replaced; the key is added to it, and undoing
removes only that key.

This is not a theory. On this machine, 9 of the 15 files already in
`~/.local/share/applications` carry `NoDisplay`, written by Chrome and by
Epiphany. The tool leaves them alone and does not even list them, because an
entry already hidden by somebody else is not a menu entry.

Three write modes, picked automatically:

| Mode | When | Undo |
| --- | --- | --- |
| `copy` | the user had no file of that name | delete our copy |
| `inplace` | the user already had their own file | remove only our keys |
| `symlink` | the user's file is a symlink | put the link back |

The `symlink` mode exists for GNOME web apps. They are symlinks into
`~/.local/share/xdg-desktop-portal/applications`, and writing to the link
would have destroyed it. Where the link pointed is recorded in the file, so
undoing restores it exactly.

## What is not listed

- Flatpak apps. `malcontent` already handles those.
- Entries that are not `Type=Application`.
- Entries the vendor already marked `NoDisplay` or `Hidden`.
- Entries that GNOME is told not to show (`NotShowIn=GNOME`, or an
  `OnlyShowIn` without GNOME).
- Entries whose `TryExec` program is not installed.

Flatpak is detected three ways, because one is not enough:

1. the file sits under `.../flatpak/exports/...`
2. the file has the exact key `X-Flatpak`
3. the `Exec` line runs `flatpak run`

Test 2 is the one that earns its keep. On this machine, `com.google.Chrome`
and `org.gnome.Epiphany` have Flatpak copies in
`~/.local/share/applications`, which test 1 does not reach, and their `Exec`
is `/usr/bin/flatpak run`, which a bare `flatpak run` check misses.

And `X-Flatpak-RenamedFrom` is **not** a Flatpak marker. The plain `dnf`
package `org.gnome.TextEditor.desktop` carries it. So the key is matched
exactly, never as a prefix.

## Tests

```
python3 test_core.py
```

31 tests. They run against a throwaway home and a fake `/usr/share`, so no
real menu is touched.

## Add it to your own menu

Optional. Not done for you.

The launcher runs `menu-hider`, so the program must be on your `PATH` first.
On Fedora, `~/.local/bin` is on the `PATH` already.

```
mkdir -p ~/.local/bin
ln -s "$PWD/menu-hider" ~/.local/bin/menu-hider
cp ./menu-hider.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications
```

Run `ln -sf` instead if you move the folder later.

## Known limits

- Hiding an app does not remove it from an "Open With" list, if the app claims
  that file type.
- The child's GNOME Shell picks up the change by itself. If it does not, the
  child logs out and back in.
- `--user` needs root. There is no way around it: the files live in the other
  user's home.
- The root path (`--user` on somebody else) has **not** been watched working
  yet. Everything else here has.
## License

Apache 2.0. See `LICENSE`.
