from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.technical_log import create_technical_logger, record_error


class TechnicalLogTests(unittest.TestCase):
    def test_log_excludes_exception_message_and_user_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "technical.log"
            logger = create_technical_logger(path)
            try:
                record_error(logger, "http.chat", RuntimeError("Секретный текст пользователя"))
                for handler in logger.handlers:
                    handler.flush()

                content = path.read_text(encoding="utf-8")
                self.assertIn("location=http.chat", content)
                self.assertIn("error_type=RuntimeError", content)
                self.assertNotIn("Секретный", content)
            finally:
                for handler in list(logger.handlers):
                    handler.close()
                    logger.removeHandler(handler)
