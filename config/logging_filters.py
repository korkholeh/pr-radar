"""Root-logger filter masking GitHub tokens out of every log record."""

import logging

from config.security import mask_secrets


class SecretMaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {key: self._mask_value(value) for key, value in record.args.items()}
            else:
                record.args = tuple(self._mask_value(arg) for arg in record.args)
        if record.exc_text:
            record.exc_text = mask_secrets(record.exc_text)
        elif record.exc_info:
            # Pre-format now so the masked text is what every handler's Formatter caches
            # and reuses; formatting later would bypass this filter entirely.
            record.exc_text = mask_secrets(logging.Formatter().formatException(record.exc_info))
        return True

    @staticmethod
    def _mask_value(value: object) -> object:
        return mask_secrets(value) if isinstance(value, str) else value
