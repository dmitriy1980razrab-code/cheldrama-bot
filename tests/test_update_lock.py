from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.update_lock import UpdateAlreadyRunning, update_lock


class UpdateLockTests(unittest.TestCase):
    def test_rejects_second_simultaneous_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sync.lock"
            with update_lock(path):
                with self.assertRaises(UpdateAlreadyRunning):
                    with update_lock(path):
                        pass

    def test_releases_lock_after_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sync.lock"
            with self.assertRaises(RuntimeError):
                with update_lock(path):
                    raise RuntimeError("test")
            with update_lock(path):
                pass
