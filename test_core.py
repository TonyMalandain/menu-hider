#!/usr/bin/env python3
"""Tests for the writing logic, against a throwaway home and a fake /usr/share.

Nobody's real menu is touched. Run with:  python3 test_core.py

The point of these tests is the three write modes and their restores. A hide
that cannot be undone exactly is worse than no tool at all, because the parent
finds out months later.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from menuhider import core  # noqa: E402

PLAIN = """[Desktop Entry]
Type=Application
Name=Plain App
Exec=plainapp
Icon=utilities-terminal

[Desktop Action extra]
Name=Extra
Exec=plainapp --extra
"""


class FakeTarget:
    """A Target with the directories pointed somewhere harmless."""

    def __init__(self, root):
        self.name = "testuser"
        self.is_self = True
        self.root = root
        self.sys_dir = os.path.join(root, "usr", "share", "applications")
        self.override_dir = os.path.join(root, "home", ".local", "share",
                                         "applications")
        os.makedirs(self.sys_dir)

    def system_dirs(self):
        return [self.sys_dir]

    def chown(self, path):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="menuhider-test-")
        self.t = FakeTarget(self.root)

    def tearDown(self):
        shutil.rmtree(self.root)

    def put_system(self, name, text=PLAIN):
        path = os.path.join(self.t.sys_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def put_user(self, name, text=PLAIN):
        os.makedirs(self.t.override_dir, exist_ok=True)
        path = os.path.join(self.t.override_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def app(self, name):
        return core.collect(self.t)[name]

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()


class TestWhatGetsListed(Base):
    def test_a_plain_app_is_listed(self):
        self.put_system("a.desktop")
        self.assertIn("a.desktop", core.collect(self.t))

    def test_a_link_is_not_an_application(self):
        self.put_system("b.desktop", "[Desktop Entry]\nType=Link\nName=B\nURL=x\n")
        self.assertNotIn("b.desktop", core.collect(self.t))

    def test_nodisplay_from_the_vendor_is_not_our_business(self):
        self.put_system("c.desktop", PLAIN + "\n[Desktop Entry]\n")
        self.put_system("d.desktop",
                        "[Desktop Entry]\nType=Application\nName=D\nExec=d\n"
                        "NoDisplay=true\n")
        self.assertNotIn("d.desktop", core.collect(self.t))

    def test_hidden_true_is_skipped(self):
        self.put_system("e.desktop",
                        "[Desktop Entry]\nType=Application\nName=E\nExec=e\n"
                        "Hidden=true\n")
        self.assertNotIn("e.desktop", core.collect(self.t))

    def test_nodisplay_false_is_still_listed(self):
        """A regression guard. Epiphany's real file says NoDisplay=false, and a
        grep for the word 'NoDisplay' calls that hidden. Parsing does not."""
        self.put_system("f.desktop",
                        "[Desktop Entry]\nType=Application\nName=F\nExec=f\n"
                        "NoDisplay=false\nHidden=false\n")
        self.assertIn("f.desktop", core.collect(self.t))

    def test_not_shown_in_gnome_is_skipped(self):
        self.put_system("g.desktop",
                        "[Desktop Entry]\nType=Application\nName=G\nExec=g\n"
                        "NotShowIn=GNOME;\n")
        self.assertNotIn("g.desktop", core.collect(self.t))

    def test_only_shown_elsewhere_is_skipped(self):
        self.put_system("h.desktop",
                        "[Desktop Entry]\nType=Application\nName=H\nExec=h\n"
                        "OnlyShowIn=KDE;\n")
        self.assertNotIn("h.desktop", core.collect(self.t))

    def test_missing_tryexec_is_skipped(self):
        self.put_system("i.desktop",
                        "[Desktop Entry]\nType=Application\nName=I\nExec=i\n"
                        "TryExec=/no/such/binary\n")
        self.assertNotIn("i.desktop", core.collect(self.t))


class TestFlatpakIsExcluded(Base):
    def test_the_x_flatpak_key(self):
        self.put_system("j.desktop",
                        "[Desktop Entry]\nType=Application\nName=J\nExec=j\n"
                        "X-Flatpak=com.example.J\n")
        self.assertNotIn("j.desktop", core.collect(self.t))

    def test_renamedfrom_is_not_a_flatpak_marker(self):
        """org.gnome.TextEditor.desktop is a DNF package and carries
        X-Flatpak-RenamedFrom. A prefix test would wrongly drop it."""
        self.put_system("k.desktop",
                        "[Desktop Entry]\nType=Application\nName=K\nExec=k\n"
                        "X-Flatpak-RenamedFrom=gnome-k\n")
        self.assertIn("k.desktop", core.collect(self.t))

    def test_bare_flatpak_run_in_exec(self):
        self.put_system("l.desktop",
                        "[Desktop Entry]\nType=Application\nName=L\n"
                        "Exec=flatpak run com.example.L\n")
        self.assertNotIn("l.desktop", core.collect(self.t))

    def test_absolute_flatpak_run_in_exec(self):
        """The real Chrome file uses /usr/bin/flatpak run."""
        self.put_system("m.desktop",
                        "[Desktop Entry]\nType=Application\nName=M\n"
                        "Exec=/usr/bin/flatpak run com.example.M\n")
        self.assertNotIn("m.desktop", core.collect(self.t))

    def test_exec_with_a_leading_env_call(self):
        """The real Epiphany file starts its Exec with `env VAR=1`."""
        self.put_system("n.desktop",
                        "[Desktop Entry]\nType=Application\nName=N\n"
                        "Exec=env WEBKIT=1 /usr/bin/flatpak run com.example.N\n")
        self.assertNotIn("n.desktop", core.collect(self.t))


class TestCopyMode(Base):
    def setUp(self):
        super().setUp()
        self.put_system("a.desktop")

    def test_hide_writes_a_copy_and_stamps_it(self):
        core.hide(self.app("a.desktop"), self.t)
        out = os.path.join(self.t.override_dir, "a.desktop")
        self.assertTrue(os.path.isfile(out))
        app = self.app("a.desktop")
        self.assertTrue(app.hidden)
        self.assertTrue(app.managed)
        self.assertEqual(app.mode, "copy")

    def test_the_keys_land_in_desktop_entry_not_in_an_action(self):
        """The naive `echo NoDisplay=true >>` appends after the last group,
        which here is [Desktop Action extra], where the key does nothing."""
        core.hide(self.app("a.desktop"), self.t)
        text = self.read(os.path.join(self.t.override_dir, "a.desktop"))
        before_action = text.split("[Desktop Action extra]")[0]
        self.assertIn("NoDisplay=true", before_action)

    def test_the_action_group_survives(self):
        core.hide(self.app("a.desktop"), self.t)
        text = self.read(os.path.join(self.t.override_dir, "a.desktop"))
        self.assertIn("[Desktop Action extra]", text)
        self.assertIn("plainapp --extra", text)

    def test_show_deletes_the_copy(self):
        core.hide(self.app("a.desktop"), self.t)
        core.show(self.app("a.desktop"), self.t)
        self.assertFalse(
            os.path.exists(os.path.join(self.t.override_dir, "a.desktop")))
        self.assertFalse(self.app("a.desktop").hidden)

    def test_hiding_twice_is_harmless(self):
        core.hide(self.app("a.desktop"), self.t)
        core.hide(self.app("a.desktop"), self.t)
        core.show(self.app("a.desktop"), self.t)
        self.assertFalse(
            os.path.exists(os.path.join(self.t.override_dir, "a.desktop")))

    def test_every_new_directory_level_is_made(self):
        self.assertFalse(os.path.isdir(self.t.override_dir))
        core.hide(self.app("a.desktop"), self.t)
        self.assertTrue(os.path.isdir(self.t.override_dir))


class TestInplaceMode(Base):
    def setUp(self):
        super().setUp()
        self.put_system("a.desktop")
        self.custom = PLAIN.replace("Name=Plain App", "Name=My Own Name")
        self.user_file = self.put_user("a.desktop", self.custom)

    def test_mode_is_inplace_when_the_user_has_a_file(self):
        self.assertEqual(self.app("a.desktop").mode, "inplace")

    def test_the_users_own_edits_are_kept(self):
        core.hide(self.app("a.desktop"), self.t)
        self.assertIn("Name=My Own Name", self.read(self.user_file))

    def test_show_restores_the_file_exactly(self):
        core.hide(self.app("a.desktop"), self.t)
        core.show(self.app("a.desktop"), self.t)
        text = self.read(self.user_file)
        self.assertIn("Name=My Own Name", text)
        self.assertNotIn("NoDisplay", text)
        self.assertNotIn("X-MenuHider", text)
        self.assertTrue(os.path.isfile(self.user_file))

    def test_show_refuses_a_file_we_did_not_stamp(self):
        with self.assertRaises(PermissionError):
            core.show(self.app("a.desktop"), self.t)
        self.assertEqual(self.read(self.user_file), self.custom)


class TestSymlinkMode(Base):
    def setUp(self):
        super().setUp()
        self.put_system("a.desktop")
        portal = os.path.join(self.root, "home", ".local", "share",
                              "xdg-desktop-portal", "applications")
        os.makedirs(portal)
        self.portal_file = os.path.join(portal, "a.desktop")
        with open(self.portal_file, "w", encoding="utf-8") as fh:
            fh.write(PLAIN.replace("Plain App", "Web App"))
        os.makedirs(self.t.override_dir, exist_ok=True)
        self.link = os.path.join(self.t.override_dir, "a.desktop")
        self.rel = "../xdg-desktop-portal/applications/a.desktop"
        os.symlink(self.rel, self.link)

    def test_mode_is_symlink(self):
        self.assertEqual(self.app("a.desktop").mode, "symlink")

    def test_hide_replaces_the_link_with_a_real_file(self):
        core.hide(self.app("a.desktop"), self.t)
        self.assertFalse(os.path.islink(self.link))
        self.assertTrue(os.path.isfile(self.link))
        self.assertTrue(self.app("a.desktop").hidden)

    def test_the_file_the_link_pointed_at_is_untouched(self):
        original = self.read(self.portal_file)
        core.hide(self.app("a.desktop"), self.t)
        self.assertEqual(self.read(self.portal_file), original)
        self.assertNotIn("NoDisplay", self.read(self.portal_file))

    def test_where_the_link_pointed_is_recorded(self):
        core.hide(self.app("a.desktop"), self.t)
        self.assertIn(f"X-MenuHider-Link={self.rel}", self.read(self.link))

    def test_show_puts_the_link_back(self):
        core.hide(self.app("a.desktop"), self.t)
        core.show(self.app("a.desktop"), self.t)
        self.assertTrue(os.path.islink(self.link))
        self.assertEqual(os.readlink(self.link), self.rel)

    def test_show_complains_if_the_link_was_not_recorded(self):
        core.hide(self.app("a.desktop"), self.t)
        text = self.read(self.link).replace("X-MenuHider-Link=", "X-Gone=")
        with open(self.link, "w", encoding="utf-8") as fh:
            fh.write(text)
        with self.assertRaises(OSError):
            core.show(self.app("a.desktop"), self.t)


class TestUserOnlyApp(Base):
    """An app with no system file at all, e.g. a Chrome web app shortcut."""

    def setUp(self):
        super().setUp()
        self.user_file = self.put_user("z.desktop")

    def test_it_is_listed(self):
        self.assertIn("z.desktop", core.collect(self.t))

    def test_hide_and_show_keep_the_file(self):
        core.hide(self.app("z.desktop"), self.t)
        self.assertTrue(self.app("z.desktop").hidden)
        core.show(self.app("z.desktop"), self.t)
        self.assertTrue(os.path.isfile(self.user_file))
        self.assertNotIn("NoDisplay", self.read(self.user_file))


if __name__ == "__main__":
    unittest.main(verbosity=2)
