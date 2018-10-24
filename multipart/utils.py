from typing import Union

import sys

FILE_SYSTEM_ENCODING = sys.getfilesystemencoding()


def str_or_decode_bytes_system(str_or_bytes: Union[str, bytes]) -> str:
    if isinstance(str_or_bytes, bytes):
        return str_or_bytes.decode(FILE_SYSTEM_ENCODING)
    else:
        return str_or_bytes
