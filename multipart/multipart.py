from io import BytesIO
from multipart.decoders import Base64Decoder, QuotedPrintableDecoder
from multipart.exceptions import (
    FileError,
    FormParserError,
    MultipartParseError,
    QuerystringParseError
)
from numbers import Number
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

import logging
import os
import re
import shutil
import sys
import tempfile

_missing = object()

# States for the querystring parser.
STATE_BEFORE_FIELD = 0
STATE_FIELD_NAME = 1
STATE_FIELD_DATA = 2

# States for the multipart parser
STATE_START = 0
STATE_START_BOUNDARY = 1
STATE_HEADER_FIELD_START = 2
STATE_HEADER_FIELD = 3
STATE_HEADER_VALUE_START = 4
STATE_HEADER_VALUE = 5
STATE_HEADER_VALUE_ALMOST_DONE = 6
STATE_HEADERS_ALMOST_DONE = 7
STATE_PART_DATA_START = 8
STATE_PART_DATA = 9
STATE_PART_DATA_END = 10
STATE_END = 11

STATES = [
    "START",
    "START_BOUNDARY", "HEADER_FEILD_START", "HEADER_FIELD", "HEADER_VALUE_START",
    "HEADER_VALUE",
    "HEADER_VALUE_ALMOST_DONE", "HEADRES_ALMOST_DONE", "PART_DATA_START",
    "PART_DATA", "PART_DATA_END", "END"
]

# Flags for the multipart parser.
FLAG_PART_BOUNDARY = 1
FLAG_LAST_BOUNDARY = 2

MAX_INT = 2 ** 10000

# Get constants.  Since iterating over a str on Python 2 gives you a 1-length
# string, but iterating over a bytes object on Python 3 gives you an integer,
# we need to save these constants.
CR = b'\r'[0]
LF = b'\n'[0]
COLON = b':'[0]
SPACE = b' '[0]
HYPHEN = b'-'[0]
AMPERSAND = b'&'[0]
SEMICOLON = b';'[0]
LOWER_A = b'a'[0]
LOWER_Z = b'z'[0]
NULL = b'\x00'[0]

# Lower-casing a character is different, because of the difference between
# str on Py2, and bytes on Py3.  Same with getting the ordinal value of a byte,
# and joining a list of bytes together.
# These functions abstract that.


def lower_char(c):
    return c | 0x20


def ord_char(c):
    return c


def join_bytes(b):
    return bytes(list(b))


# These are regexes for parsing header values.
SPECIAL_CHARS = re.escape(b'()<>@,;:\\"/[]?={} \t')
QUOTED_STR = br'"(?:\\.|[^"])*"'
VALUE_STR = br'(?:[^' + SPECIAL_CHARS + br']+|' + QUOTED_STR + br')'
OPTION_RE_STR = (
    br'(?:;|^)\s*([^' + SPECIAL_CHARS + br']+)\s*=\s*(' + VALUE_STR + br')'
)
OPTION_RE = re.compile(OPTION_RE_STR)
QUOTE = b'"'[0]


def _parse_options_header_bytes(value: bytes) -> Tuple[bytes, Dict]:
    if b';' not in value:
        return value.lower().strip(), {}
    else:
        # Split at the first semicolon, to get our value and then options.
        ctype, rest = value.split(b';', 1)
        options = {}

        # Parse the options.
        for match in OPTION_RE.finditer(rest):
            key = match.group(1).lower()
            value = match.group(2)
            if value[0] == QUOTE and value[-1] == QUOTE:
                # Unquote the value.
                value = value[1:-1]
                value = value.replace(b'\\\\', b'\\').replace(b'\\"', b'"')

            options[key] = value

        return ctype, options


def parse_options_header(value: Union[None, str, bytes]) -> Tuple[bytes, Dict]:
    """
    Parses a Content-Type header into a value in the following format:
        (content_type, {parameters})
    """
    if not value:
        return b'', {}

    return _parse_options_header_bytes(
        value.encode('utf8') if isinstance(value, str) else value
    )


class Field(object):
    """A Field object represents a (parsed) form field.  It represents a single
    field with a corresponding name and value.

    The name that a :class:`Field` will be instantiated with is the same name
    that would be found in the following HTML::

        <input name="name_goes_here" type="text"/>

    This class defines two methods, :meth:`on_data` and :meth:`on_end`, that
    will be called when data is written to the Field, and when the Field is
    finalized, respectively.

    :param name: the name of the form field
    """
    def __init__(self, field_name):
        self.field_name = field_name
        self._value = []

        # We cache the joined version of _value for speed.
        self._cache = _missing

    @classmethod
    def from_value(cls, name, value: Optional[bytes]):
        """
        Create an instance of a :class:`Field`, and set the corresponding
        value - either None or an actual value.  This method will also
        finalize the Field itself.
        """
        f = cls(name)
        if value is None:
            f.set_none()
        else:
            f.write(value)
        f.finalize()
        return f

    def write(self, data: bytes) -> int:
        """Write some data into the form field.

        :param data: a bytestring
        """
        return self.on_data(data)

    def on_data(self, data) -> int:
        """This method is a callback that will be called whenever data is
        written to the Field.

        :param data: a bytestring
        """
        self._value.append(data)
        self._cache = _missing
        return len(data)

    def on_end(self) -> None:
        """This method is called whenever the Field is finalized.
        """
        if self._cache is _missing:
            self._cache = b''.join(self._value)

    def finalize(self) -> None:
        """Finalize the form field.
        """
        self.on_end()

    def close(self) -> None:
        """Close the Field object.  This will free any underlying cache.
        """
        # Free our value array.
        if self._cache is _missing:
            self._cache = b''.join(self._value)

        del self._value

    def set_none(self) -> None:
        """Some fields in a querystring can possibly have a value of None - for
        example, the string "foo&bar=&baz=asdf" will have a field with the
        name "foo" and value None, one with name "bar" and value "", and one
        with name "baz" and value "asdf".  Since the write() interface doesn't
        support writing None, this function will set the field value to None.
        """
        self._cache = None

    @property
    def value(self) -> bytes:
        """This property returns the value of the form field."""
        if self._cache is _missing:
            self._cache = b''.join(self._value)

        return self._cache

    def __eq__(self, other) -> bool:
        if isinstance(other, Field):
            return (
                self.field_name == other.field_name and
                self.value == other.value
            )
        else:
            return NotImplemented

    def __repr__(self) -> str:
        if len(self.value) > 97:
            # We get the repr, and then insert three dots before the final
            # quote.
            v = repr(self.value[:97])[:-1] + "...'"
        else:
            v = repr(self.value)

        return "%s(field_name=%r, value=%s)" % (
            self.__class__.__name__,
            self.field_name,
            v
        )


def empty_dict_if_none(dict_or_none):
    return {} if dict_or_none is None else dict_or_none


FILE_SYSTEM_ENCODING = sys.getfilesystemencoding()


def str_or_decode_bytes_system(str_or_bytes: Union[str, bytes]) -> str:
    if isinstance(str_or_bytes, bytes):
        return str_or_bytes.decode(FILE_SYSTEM_ENCODING)
    else:
        return str_or_bytes


class File(object):
    """This class represents an uploaded file.  It handles writing file data to
    either an in-memory file or a temporary file on-disk, if the optional
    threshold is passed.

    There are some options that can be passed to the File to change behavior
    of the class.  Valid options are as follows:

    .. list-table::
       :widths: 15 5 5 30
       :header-rows: 1

       * - Name
         - Type
         - Default
         - Description
       * - UPLOAD_DIR
         - `str`
         - None
         - The directory to store uploaded files in.  If this is None, a
           temporary file will be created in the system's standard location.
       * - UPLOAD_DELETE_TMP
         - `bool`
         - True
         - Delete automatically created TMP file
       * - UPLOAD_KEEP_FILENAME
         - `bool`
         - False
         - Whether or not to keep the filename of the uploaded file.  If True,
           then the filename will be converted to a safe representation (e.g.
           by removing any invalid path segments), and then saved with the
           same name).  Otherwise, a temporary name will be used.
       * - UPLOAD_KEEP_EXTENSIONS
         - `bool`
         - False
         - Whether or not to keep the uploaded file's extension.  If False, the
           file will be saved with the default temporary extension (usually
           ".tmp").  Otherwise, the file's extension will be maintained.  Note
           that this will properly combine with the UPLOAD_KEEP_FILENAME
           setting.
       * - MAX_MEMORY_FILE_SIZE
         - `int`
         - 1 MiB
         - The maximum number of bytes of a File to keep in memory.  By
           default, the contents of a File are kept into memory until a certain
           limit is reached, after which the contents of the File are written
           to a temporary file.  This behavior can be disabled by setting this
           value to an appropriately large value (or, for example, infinity,
           such as `float('inf')`.

    :param file_name: The name of the file that this :class:`File` represents

    :param field_name: The field name that uploaded this file.  Note that this
                       can be None, if, for example, the file was uploaded
                       with Content-Type application/octet-stream

    :param config: The configuration for this File.  See above for valid
                   configuration keys and their corresponding values.
    """
    def __init__(self, file_name, field_name=None, config=None):
        # Save configuration, set other variables default.
        self.logger = logging.getLogger(__name__)
        self._config = empty_dict_if_none(config)
        self.in_memory = True
        self.size = 0
        self.file_object = BytesIO()

        # Save the provided field/file name.
        self.field_name = field_name
        self.file_name = file_name

        # Our actual file name is None by default, since, depending on our
        # config, we may not actually use the provided name.
        self.actual_file_name: Optional[bytes] = None

        # Split the extension from the filename.
        if file_name is not None:
            base, ext = os.path.splitext(file_name)
            self._file_base = base
            self._ext: Union[str, bytes] = ext

    def flush_to_disk(self):
        """If the file is already on-disk, do nothing.  Otherwise, copy from
        the in-memory buffer to a disk file, and then reassign our internal
        file object to this new disk file.

        Note that if you attempt to flush a file that is already on-disk, a
        warning will be logged to this module's logger.
        """
        if not self.in_memory:
            self.logger.warning(
                "Trying to flush to disk when we're not in memory"
            )
            return

        # Go back to the start of our file.
        self.file_object.seek(0)

        # Open a new file.
        new_file = self._get_disk_file()

        # Copy the file objects.
        shutil.copyfileobj(self.file_object, new_file)

        # Seek to the new position in our new file.
        new_file.seek(self.size)

        # Reassign the fileobject.
        old_fileobj = self.file_object
        self.file_object = new_file

        # We're no longer in memory.
        self.in_memory = False

        # Close the old file object.
        old_fileobj.close()

    def _get_disk_file(self):
        """This function is responsible for getting a file object on-disk for us.
        """
        self.logger.info("Opening a file on disk")

        file_dir = self._config.get('UPLOAD_DIR')
        keep_filename = self._config.get('UPLOAD_KEEP_FILENAME', False)
        keep_extensions = self._config.get('UPLOAD_KEEP_EXTENSIONS', False)
        delete_tmp = self._config.get('UPLOAD_DELETE_TMP', True)

        # If we have a directory and are to keep the filename...
        if file_dir is not None and keep_filename:
            self.logger.info("Saving with filename in: %r", file_dir)

            # Build our filename.
            # TODO: what happens if we don't have a filename?
            fname = (self._file_base + self._ext
                     if keep_extensions
                     else self._file_base)

            path = os.path.join(file_dir, fname)
            try:
                self.logger.info("Opening file: %r", path)
                tmp_file = open(path, 'w+b')
            except (IOError, OSError) as e:

                self.logger.exception("Error opening temporary file")
                raise FileError("Error opening temporary file: %r" % path)
        else:
            tmp_file, fname = get_temp_file_details(file_dir,
                                                    delete_tmp,
                                                    keep_extensions,
                                                    self)

        self.actual_file_name = fname
        return tmp_file

    def write(self, data: bytes) -> int:
        """ Write some data to the File. """
        return self.on_data(data)

    def on_data(self, data) -> int:
        """This method is a callback that will be called whenever data is
        written to the File.

        :param data: a bytestring
        """
        pos = self.file_object.tell()
        bwritten = self.file_object.write(data)
        # true file objects write  returns None
        if bwritten is None:
            bwritten = self.file_object.tell() - pos

        # If the bytes written isn't the same as the length, just return.
        if bwritten != len(data):
            self.logger.warning("bwritten != len(data) (%d != %d)", bwritten,
                                len(data))
            return bwritten

        # Keep track of how many bytes we've written.
        self.size += bwritten

        # If we're in-memory and are over our limit, we create a file.
        if (self.in_memory and
                self._config.get('MAX_MEMORY_FILE_SIZE') is not None and
                (self.size >
                 self._config.get('MAX_MEMORY_FILE_SIZE'))):
            self.logger.info("Flushing to disk")
            self.flush_to_disk()

        # Return the number of bytes written.
        return bwritten

    def on_end(self) -> None:
        """This method is called whenever the Field is finalized.
        """
        # Flush the underlying file object
        self.file_object.flush()

    def finalize(self) -> None:
        """Finalize the form file.  This will not close the underlying file,
        but simply signal that we are finished writing to the File.
        """
        self.on_end()

    def close(self) -> None:
        """Close the File object.  This will actually close the underlying
        file object (whether it's a :class:`io.BytesIO` or an actual file
        object).
        """
        self.file_object.close()

    def __repr__(self) -> str:
        return "%s(file_name=%r, field_name=%r)" % (
            self.__class__.__name__,
            self.file_name,
            self.field_name
        )


def get_temp_file_details(
        file_dir: Union[None, str, bytes],
        delete_tmp: bool,
        keep_extensions: bool,
        f: File
) -> Tuple[Any, bytes]:  # Any should be _TemporaryFileWrapper, but it's private
    options: Dict[str, Any] = {
        'delete': delete_tmp
    }
    if keep_extensions:
        options['suffix'] = str_or_decode_bytes_system(f._ext)

    if file_dir is not None:
        options['dir'] = str_or_decode_bytes_system(file_dir)

    # Create a temporary (named) file with the appropriate settings.
    f.logger.info("Creating a temporary file with options: %r", options)

    try:
        tmp_file = tempfile.NamedTemporaryFile(**options)
    except (IOError, OSError):
        f.logger.exception("Error creating named temporary file")
        raise FileError("Error creating named temporary file")
    else:
        fname: Union[str, bytes] = tmp_file.name

        return tmp_file, (fname.encode(sys.getfilesystemencoding())
                          if isinstance(fname, str)
                          else fname)


class BaseParser(object):
    """This class is the base class for all parsers.  It contains the logic for
    calling and adding callbacks.

    A callback can be one of two different forms.  "Notification callbacks" are
    callbacks that are called when something happens - for example, when a new
    part of a multipart message is encountered by the parser.  "Data callbacks"
    are called when we get some sort of data - for example, part of the body of
    a multipart chunk.  Notification callbacks are called with no parameters,
    whereas data callbacks are called with three, as follows::

        data_callback(data, start, end)

    The "data" parameter is a bytestring (i.e. "foo" on Python 2, or b"foo" on
    Python 3).  "start" and "end" are integer indexes into the "data" string
    that represent the data of interest.  Thus, in a data callback, the slice
    `data[start:end]` represents the data that the callback is "interested in".
    The callback is not passed a copy of the data, since copying severely hurts
    performance.
    """
    def __init__(self) -> None:
        self.logger: logging.Logger = logging.getLogger(__name__)
        self.callbacks: Dict[str, Callable] = {}

    def callback(self,
                 name: str,
                 # todo: data is Optional[bytes]?
                 data: Optional[Any]=None,
                 start: Optional[int]=None,
                 end: Optional[int]=None) -> None:
        # todo: remoeve this shitty method
        func = self.callbacks.get("on_" + name)

        if func is None:
            return None
        elif data is not None:
            # Don't do anything if we have start == end.
            if start is not None and start == end:
                return None

            self.logger.debug("Calling %s with data[%d:%d]", name, start, end)
            func(data, start, end)
        else:
            self.logger.debug("Calling %s with no data", name)
            func()

    def close(self):
        pass                # pragma: no cover

    def finalize(self):
        pass                # pragma: no cover

    def write(self, data: bytes) -> int:
        raise NotImplementedError

    def __repr__(self):
        return "%s()" % self.__class__.__name__


class OctetStreamParser(BaseParser):
    """This parser parses an octet-stream request body and calls callbacks when
    incoming data is received.  Callbacks are as follows:

    .. list-table::
       :widths: 15 10 30
       :header-rows: 1

       * - Callback Name
         - Parameters
         - Description
       * - on_start
         - None
         - Called when the first data is parsed.
       * - on_data
         - data, start, end
         - Called for each data chunk that is parsed.
       * - on_end
         - None
         - Called when the parser is finished parsing all data.

    :param callbacks: A dictionary of callbacks.  See the documentation for
                      :class:`BaseParser`.

    :param max_size: The maximum size of body to parse.  Defaults to infinity -
                     i.e. unbounded.
    """
    def __init__(self,
                 callbacks: Dict[str, Callable]=None,
                 max_size: int=MAX_INT) -> None:
        super(OctetStreamParser, self).__init__()
        self.callbacks = empty_dict_if_none(callbacks)
        self._started = False

        if not isinstance(max_size, Number) or max_size < 1:
            raise ValueError("max_size must be a positive number, not %r" %
                             max_size)
        self.max_size: int = max_size
        self._current_size: int = 0

    def write(self, data: bytes) -> int:
        """Write some data to the parser, which will perform size verification,
        and then pass the data to the underlying callback.
        """
        if not self._started:
            self.callback('start')
            self._started = True

        # Truncate data length.
        data_len = len(data)
        if (self._current_size + data_len) > self.max_size:
            # We truncate the length of data that we are to process.
            new_size = int(self.max_size - self._current_size)
            self.logger.warning("Current size is %d (max %d), so truncating "
                                "data length from %d to %d",
                                self._current_size, self.max_size, data_len,
                                new_size)
            data_len = new_size

        # Increment size, then callback, in case there's an exception.
        self._current_size += data_len
        self.callback('data', data, 0, data_len)
        return data_len

    def finalize(self) -> None:
        """Finalize this parser, which signals to that we are finished parsing,
        and sends the on_end callback.
        """
        self.callback('end')

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


def query_string_parser_internal_write(
        logger: logging.Logger,
        state: int,
        strict_parsing: bool,
        found_sep: bool,
        data: bytes,
        length: int,
        on_field_start: Callable[[], None],
        on_field_end: Callable[[], None],
        on_field_name: Callable[[bytes, int, int], None],
        on_field_data: Callable[[bytes, int, int], None]
) -> Tuple[int, bool]:
    strict_parsing = strict_parsing

    i = 0
    while i < length:
        ch = data[i]

        # Depending on our state...
        if state == STATE_BEFORE_FIELD:
            # If the 'found_sep' flag is set, we've already encountered
            # and skipped a single seperator.  If so, we check our strict
            # parsing flag and decide what to do.  Otherwise, we haven't
            # yet reached a seperator, and thus, if we do, we need to skip
            # it as it will be the boundary between fields that's supposed
            # to be there.
            if ch == AMPERSAND or ch == SEMICOLON:
                if found_sep:
                    # If we're parsing strictly, we disallow blank chunks.
                    if strict_parsing:
                        e = QuerystringParseError(
                            "Skipping duplicate ampersand/semicolon at "
                            "%d" % i
                        )
                        e.offset = i
                        raise e
                    else:
                        logger.debug(
                            f"Skipping duplicate ampersand/semicolon at {i}")
                else:
                    # This case is when we're skipping the (first)
                    # seperator between fields, so we just set our flag
                    # and continue on.
                    found_sep = True
            else:
                # Emit a field-start event, and go to that state.  Also,
                # reset the "found_sep" flag, for the next time we get to
                # this state.
                on_field_start()
                i -= 1
                state = STATE_FIELD_NAME
                found_sep = False

        elif state == STATE_FIELD_NAME:
            # Try and find a seperator - we ensure that, if we do, we only
            # look for the equal sign before it.
            sep_pos = data.find(b'&', i)
            if sep_pos == -1:
                sep_pos = data.find(b';', i)

            # See if we can find an equals sign in the remaining data.  If
            # so, we can immedately emit the field name and jump to the
            # data state.
            if sep_pos != -1:
                equals_pos = data.find(b'=', i, sep_pos)
            else:
                equals_pos = data.find(b'=', i)

            if equals_pos != -1:
                # Emit this name.
                on_field_name(data, i, equals_pos)

                # Jump i to this position.  Note that it will then have 1
                # added to it below, which means the next iteration of this
                # loop will inspect the character after the equals sign.
                i = equals_pos
                state = STATE_FIELD_DATA
            else:
                # No equals sign found.
                if not strict_parsing:
                    # See also comments in the STATE_FIELD_DATA case below.
                    # If we found the seperator, we emit the name and just
                    # end - there's no data callback at all (not even with
                    # a blank value).
                    if sep_pos != -1:
                        on_field_name(data, i, sep_pos)
                        on_field_end()

                        i = sep_pos - 1
                        state = STATE_BEFORE_FIELD
                    else:
                        # Otherwise, no seperator in this block, so the
                        # rest of this chunk must be a name.
                        on_field_name(data, i, length)
                        i = length

                else:
                    # We're parsing strictly.  If we find a seperator,
                    # this is an error - we require an equals sign.
                    if sep_pos != -1:
                        e = QuerystringParseError(
                            "When strict_parsing is True, we require an "
                            "equals sign in all field chunks. Did not "
                            "find one in the chunk that starts at %d" %
                            (i,)
                        )
                        e.offset = i
                        raise e

                    # No seperator in the rest of this chunk, so it's just
                    # a field name.
                    on_field_name(data, i, length)
                    i = length

        elif state == STATE_FIELD_DATA:
            # Try finding either an ampersand or a semicolon after this
            # position.
            sep_pos = data.find(b'&', i)
            if sep_pos == -1:
                sep_pos = data.find(b';', i)

            # If we found it, callback this bit as data and then go back
            # to expecting to find a field.
            if sep_pos != -1:
                on_field_data(data, i, sep_pos)
                on_field_end()

                # Note that we go to the separator, which brings us to the
                # "before field" state.  This allows us to properly emit
                # "field_start" events only when we actually have data for
                # a field of some sort.
                i = sep_pos - 1
                state = STATE_BEFORE_FIELD

            # Otherwise, emit the rest as data and finish.
            else:
                on_field_data(data, i, length)
                i = length

        else:
            msg = "Reached an unknown state %d at %d" % (state, i)
            logger.warning(msg)
            e = QuerystringParseError(msg)
            e.offset = i
            raise e

        i += 1

    return state, found_sep


def always_none(*args, **kwargs) -> None:
    return None


class QuerystringParser(BaseParser):
    """This is a streaming querystring parser.  It will consume data, and call
    the callbacks given when it has data.

    .. list-table::
       :widths: 15 10 30
       :header-rows: 1

       * - Callback Name
         - Parameters
         - Description
       * - on_field_start
         - None
         - Called when a new field is encountered.
       * - on_field_name
         - data, start, end
         - Called when a portion of a field's name is encountered.
       * - on_field_data
         - data, start, end
         - Called when a portion of a field's data is encountered.
       * - on_field_end
         - None
         - Called when the end of a field is encountered.
       * - on_end
         - None
         - Called when the parser is finished parsing all data.

    :param callbacks: A dictionary of callbacks.  See the documentation for
                      :class:`BaseParser`.

    :param strict_parsing: Whether or not to parse the body strictly.  Defaults
                           to False.  If this is set to True, then the behavior
                           of the parser changes as the following: if a field
                           has a value with an equal sign (e.g. "foo=bar", or
                           "foo="), it is always included.  If a field has no
                           equals sign (e.g. "...&name&..."), it will be
                           treated as an error if 'strict_parsing' is True,
                           otherwise included.  If an error is encountered,
                           then a
                           :class:`multipart.exceptions.QuerystringParseError`
                           will be raised.

    :param max_size: The maximum size of body to parse.  Defaults to infinity -
                     i.e. unbounded.
    """
    def __init__(self,
                 callbacks: Dict[str, Callable]=None,
                 strict_parsing: bool=False,
                 max_size: int=MAX_INT) -> None:
        super().__init__()
        self.state = STATE_BEFORE_FIELD

        self.callbacks = empty_dict_if_none(callbacks)

        # Max-size stuff
        if not isinstance(max_size, Number) or max_size < 1:
            raise ValueError("max_size must be a positive number, not %r" %
                             max_size)
        self.max_size: int = max_size
        self._current_size: int = 0
        self._found_sep: bool = False
        self.on_field_start = callbacks.get('on_field_start', always_none)
        self.on_field_end = callbacks.get('on_field_end', always_none)
        self.on_field_name = callbacks.get('on_field_name', always_none)
        self.on_field_data = callbacks.get('on_field_data', always_none)
        self.on_end = callbacks.get('on_end', always_none)

        # Should parsing be strict?
        self.strict_parsing: bool = strict_parsing

    def write(self, data: bytes) -> int:
        """Write some data to the parser, which will perform size verification,
        parse into either a field name or value, and then pass the
        corresponding data to the underlying callback.  If an error is
        encountered while parsing, a QuerystringParseError will be raised.  The
        "offset" attribute of the raised exception will be set to the offset in
        the input data chunk (NOT the overall stream) that caused the error.
        """
        # Handle sizing.
        data_len = len(data)
        if (self._current_size + data_len) > self.max_size:
            # We truncate the length of data that we are to process.
            new_size = int(self.max_size - self._current_size)
            self.logger.warning("Current size is %d (max %d), so truncating "
                                "data length from %d to %d",
                                self._current_size, self.max_size, data_len,
                                new_size)
            data_len = new_size

        length: int = 0

        try:
            self.state, self._found_sep = query_string_parser_internal_write(
                self.logger,
                self.state,
                self.strict_parsing,
                self._found_sep,
                data,
                data_len,
                self.on_field_start,
                self.on_field_end,
                self.on_field_name,
                self.on_field_data
            )
            length = len(data)
        finally:
            self._current_size += length

        return length

    def finalize(self) -> None:
        """Finalize this parser, which signals to that we are finished parsing,
        if we're still in the middle of a field, an on_field_end callback, and
        then the on_end callback.
        """
        # If we're currently in the middle of a field, we finish it.
        if self.state == STATE_FIELD_DATA:
            self.on_field_end()
        self.on_end()

    def __repr__(self) -> str:
        return "%s(strict_parsing=%r, max_size=%r)" % (
            self.__class__.__name__,
            self.strict_parsing,
            self.max_size)


class MultipartParser(BaseParser):
    """This class is a streaming multipart/form-data parser.

    .. list-table::
       :widths: 15 10 30
       :header-rows: 1

       * - Callback Name
         - Parameters
         - Description
       * - on_part_begin
         - None
         - Called when a new part of the multipart message is encountered.
       * - on_part_data
         - data, start, end
         - Called when a portion of a part's data is encountered.
       * - on_part_end
         - None
         - Called when the end of a part is reached.
       * - on_header_begin
         - None
         - Called when we've found a new header in a part of a multipart
           message
       * - on_header_field
         - data, start, end
         - Called each time an additional portion of a header is read (i.e. the
           part of the header that is before the colon; the "Foo" in
           "Foo: Bar").
       * - on_header_value
         - data, start, end
         - Called when we get data for a header.
       * - on_header_end
         - None
         - Called when the current header is finished - i.e. we've reached the
           newline at the end of the header.
       * - on_headers_finished
         - None
         - Called when all headers are finished, and before the part data
           starts.
       * - on_end
         - None
         - Called when the parser is finished parsing all data.


    :param boundary: The multipart boundary.  This is required, and must match
                     what is given in the HTTP request - usually in the
                     Content-Type header.

    :param callbacks: A dictionary of callbacks.  See the documentation for
                      :class:`BaseParser`.

    :param max_size: The maximum size of body to parse.  Defaults to infinity -
                     i.e. unbounded.
    """

    def __init__(self,
                 boundary: Union[str, bytes],
                 callbacks: Dict[str, Callable]=None,
                 max_size: int=MAX_INT) -> None:
        super().__init__()
        self.state = STATE_START
        self.index = self.flags = 0

        self.callbacks = empty_dict_if_none(callbacks)

        if not isinstance(max_size, Number) or max_size < 1:
            raise ValueError("max_size must be a positive number, not %r" %
                             max_size)
        self.max_size: int = max_size
        self._current_size: int = 0

        # Setup marks.  These are used to track the state of data recieved.
        self.marks = {}

        # TODO: Actually use this rather than the dumb version we currently use
        # # Precompute the skip table for the Boyer-Moore-Horspool algorithm.
        # skip = [len(boundary) for x in range(256)]
        # for i in range(len(boundary) - 1):
        #     skip[ord_char(boundary[i])] = len(boundary) - i - 1
        #
        # # We use a tuple since it's a constant, and marginally faster.
        # self.skip = tuple(skip)

        self.boundary: bytes = b'\r\n--' + (
            boundary.encode('latin-1') if isinstance(boundary, str) else boundary
        )

        # Get a set of characters that belong to our boundary.
        self.boundary_chars: Set[int] = frozenset(self.boundary)

        # We also create a lookbehind list.
        # Note: the +8 is since we can have, at maximum, "\r\n--" + boundary +
        # "--\r\n" at the final boundary, and the length of '\r\n--' and
        # '--\r\n' is 8 bytes.
        self.lookbehind: List[int] = [NULL for _ in range(len(self.boundary) + 8)]

    def write(self, data: bytes) -> int:
        """Write some data to the parser, which will perform size verification,
        and then parse the data into the appropriate location (e.g. header,
        data, etc.), and pass this on to the underlying callback.  If an error
        is encountered, a MultipartParseError will be raised.  The "offset"
        attribute on the raised exception will be set to the offset of the byte
        in the input chunk that caused the error.

        :param data: a bytestring
        """
        # Handle sizing.
        data_len = len(data)
        if (self._current_size + data_len) > self.max_size:
            # We truncate the length of data that we are to process.
            new_size = int(self.max_size - self._current_size)
            self.logger.warning("Current size is %d (max %d), so truncating "
                                "data length from %d to %d",
                                self._current_size, self.max_size, data_len,
                                new_size)
            data_len = new_size

        length: int = 0
        try:
            length = self._internal_write(data, data_len)
        finally:
            self._current_size += length

        return length

    def _internal_write(self, data, length) -> int:
        # Get values from locals.
        boundary = self.boundary

        # Get our state, flags and index.  These are persisted between calls to
        # this function.
        state = self.state
        index = self.index
        flags = self.flags

        # Our index defaults to 0.
        i = 0

        # Set a mark.
        def set_mark(name):
            self.marks[name] = i

        # Remove a mark.
        def delete_mark(name, reset=False):
            self.marks.pop(name, None)

        # Helper function that makes calling a callback with data easier. The
        # 'remaining' parameter will callback from the marked value until the
        # end of the buffer, and reset the mark, instead of deleting it.  This
        # is used at the end of the function to call our callbacks with any
        # remaining data in this chunk.
        def data_callback(name, remaining=False):
            marked_index = self.marks.get(name)
            if marked_index is None:
                return

            # If we're getting remaining data, we ignore the current i value
            # and just call with the remaining data.
            if remaining:
                self.callback(name, data, marked_index, length)
                self.marks[name] = 0

            # Otherwise, we call it from the mark to the current byte we're
            # processing.
            else:
                self.callback(name, data, marked_index, i)
                self.marks.pop(name, None)

        # For each byte...
        while i < length:
            c = data[i]

            if state == STATE_START:
                # Skip leading newlines
                if c == CR or c == LF:
                    i += 1
                    self.logger.debug("Skipping leading CR/LF at %d", i)
                    continue

                # index is used as in index into our boundary.  Set to 0.
                index = 0

                # Move to the next state, but decrement i so that we re-process
                # this character.
                state = STATE_START_BOUNDARY
                i -= 1

            elif state == STATE_START_BOUNDARY:
                # Check to ensure that the last 2 characters in our boundary
                # are CRLF.
                if index == len(boundary) - 2:
                    if c != CR:
                        # Error!
                        msg = "Did not find CR at end of boundary (%d)" % (i,)
                        self.logger.warning(msg)
                        e = MultipartParseError(msg)
                        e.offset = i
                        raise e

                    index += 1

                elif index == len(boundary) - 2 + 1:
                    if c != LF:
                        msg = "Did not find LF at end of boundary (%d)" % (i,)
                        self.logger.warning(msg)
                        e = MultipartParseError(msg)
                        e.offset = i
                        raise e

                    # The index is now used for indexing into our boundary.
                    index = 0

                    # Callback for the start of a part.
                    self.callback('part_begin')

                    # Move to the next character and state.
                    state = STATE_HEADER_FIELD_START

                else:
                    # Check to ensure our boundary matches
                    if c != boundary[index + 2]:
                        msg = "Did not find boundary character %r at index " \
                              "%d" % (c, index + 2)
                        self.logger.warning(msg)
                        e = MultipartParseError(msg)
                        e.offset = i
                        raise e

                    # Increment index into boundary and continue.
                    index += 1

            elif state == STATE_HEADER_FIELD_START:
                # Mark the start of a header field here, reset the index, and
                # continue parsing our header field.
                index = 0

                # Set a mark of our header field.
                set_mark('header_field')

                # Move to parsing header fields.
                state = STATE_HEADER_FIELD
                i -= 1

            elif state == STATE_HEADER_FIELD:
                # If we've reached a CR at the beginning of a header, it means
                # that we've reached the second of 2 newlines, and so there are
                # no more headers to parse.
                if c == CR:
                    delete_mark('header_field')
                    state = STATE_HEADERS_ALMOST_DONE
                    i += 1
                    continue

                # Increment our index in the header.
                index += 1

                # Do nothing if we encounter a hyphen.
                if c == HYPHEN:
                    pass

                # If we've reached a colon, we're done with this header.
                elif c == COLON:
                    # A 0-length header is an error.
                    if index == 1:
                        msg = "Found 0-length header at %d" % (i,)
                        self.logger.warning(msg)
                        e = MultipartParseError(msg)
                        e.offset = i
                        raise e

                    # Call our callback with the header field.
                    data_callback('header_field')

                    # Move to parsing the header value.
                    state = STATE_HEADER_VALUE_START

                else:
                    # Lower-case this character, and ensure that it is in fact
                    # a valid letter.  If not, it's an error.
                    cl = lower_char(c)
                    if cl < LOWER_A or cl > LOWER_Z:
                        msg = "Found non-alphanumeric character %r in " \
                              "header at %d" % (c, i)
                        self.logger.warning(msg)
                        e = MultipartParseError(msg)
                        e.offset = i
                        raise e

            elif state == STATE_HEADER_VALUE_START:
                # Skip leading spaces.
                if c == SPACE:
                    i += 1
                    continue

                # Mark the start of the header value.
                set_mark('header_value')

                # Move to the header-value state, reprocessing this character.
                state = STATE_HEADER_VALUE
                i -= 1

            elif state == STATE_HEADER_VALUE:
                # If we've got a CR, we're nearly done our headers.  Otherwise,
                # we do nothing and just move past this character.
                if c == CR:
                    data_callback('header_value')
                    self.callback('header_end')
                    state = STATE_HEADER_VALUE_ALMOST_DONE

            elif state == STATE_HEADER_VALUE_ALMOST_DONE:
                # The last character should be a LF.  If not, it's an error.
                if c != LF:
                    msg = "Did not find LF character at end of header " \
                          "(found %r)" % (c,)
                    self.logger.warning(msg)
                    e = MultipartParseError(msg)
                    e.offset = i
                    raise e

                # Move back to the start of another header.  Note that if that
                # state detects ANOTHER newline, it'll trigger the end of our
                # headers.
                state = STATE_HEADER_FIELD_START

            elif state == STATE_HEADERS_ALMOST_DONE:
                # We're almost done our headers.  This is reached when we parse
                # a CR at the beginning of a header, so our next character
                # should be a LF, or it's an error.
                if c != LF:
                    msg = "Did not find LF at end of headers (found %r)" % (c,)
                    self.logger.warning(msg)
                    e = MultipartParseError(msg)
                    e.offset = i
                    raise e

                self.callback('headers_finished')
                state = STATE_PART_DATA_START

            elif state == STATE_PART_DATA_START:
                # Mark the start of our part data.
                set_mark('part_data')

                # Start processing part data, including this character.
                state = STATE_PART_DATA
                i -= 1

            elif state == STATE_PART_DATA:
                # We're processing our part data right now.  During this, we
                # need to efficiently search for our boundary, since any data
                # on any number of lines can be a part of the current data.
                # We use the Boyer-Moore-Horspool algorithm to efficiently
                # search through the remainder of the buffer looking for our
                # boundary.

                # Save the current value of our index.  We use this in case we
                # find part of a boundary, but it doesn't match fully.
                prev_index = index

                # Set up variables.
                boundary_length = len(boundary)
                boundary_end = boundary_length - 1
                data_length = length
                boundary_chars = self.boundary_chars

                # If our index is 0, we're starting a new part, so start our
                # search.
                if index == 0:
                    # Search forward until we either hit the end of our buffer,
                    # or reach a character that's in our boundary.
                    i += boundary_end
                    while i < data_length - 1 and data[i] not in boundary_chars:
                        i += boundary_length

                    # Reset i back the length of our boundary, which is the
                    # earliest possible location that could be our match (i.e.
                    # if we've just broken out of our loop since we saw the
                    # last character in our boundary)
                    i -= boundary_end
                    c = data[i]

                # Now, we have a couple of cases here.  If our index is before
                # the end of the boundary...
                if index < boundary_length:
                    # If the character matches...
                    if boundary[index] == c:
                        # If we found a match for our boundary, we send the
                        # existing data.
                        if index == 0:
                            data_callback('part_data')

                        # The current character matches, so continue!
                        index += 1
                    else:
                        index = 0

                # Our index is equal to the length of our boundary!
                elif index == boundary_length:
                    # First we increment it.
                    index += 1

                    # Now, if we've reached a newline, we need to set this as
                    # the potential end of our boundary.
                    if c == CR:
                        flags |= FLAG_PART_BOUNDARY

                    # Otherwise, if this is a hyphen, we might be at the last
                    # of all boundaries.
                    elif c == HYPHEN:
                        flags |= FLAG_LAST_BOUNDARY

                    # Otherwise, we reset our index, since this isn't either a
                    # newline or a hyphen.
                    else:
                        index = 0

                # Our index is right after the part boundary, which should be
                # a LF.
                elif index == boundary_length + 1:
                    # If we're at a part boundary (i.e. we've seen a CR
                    # character already)...
                    if flags & FLAG_PART_BOUNDARY:
                        # We need a LF character next.
                        if c == LF:
                            # Unset the part boundary flag.
                            flags &= (~FLAG_PART_BOUNDARY)

                            # Callback indicating that we've reached the end of
                            # a part, and are starting a new one.
                            self.callback('part_end')
                            self.callback('part_begin')

                            # Move to parsing new headers.
                            index = 0
                            state = STATE_HEADER_FIELD_START
                            i += 1
                            continue

                        # We didn't find an LF character, so no match.  Reset
                        # our index and clear our flag.
                        index = 0
                        flags &= (~FLAG_PART_BOUNDARY)

                    # Otherwise, if we're at the last boundary (i.e. we've
                    # seen a hyphen already)...
                    elif flags & FLAG_LAST_BOUNDARY:
                        # We need a second hyphen here.
                        if c == HYPHEN:
                            # Callback to end the current part, and then the
                            # message.
                            self.callback('part_end')
                            self.callback('end')
                            state = STATE_END
                        else:
                            # No match, so reset index.
                            index = 0

                # If we have an index, we need to keep this byte for later, in
                # case we can't match the full boundary.
                if index > 0:
                    self.lookbehind[index - 1] = c

                # Otherwise, our index is 0.  If the previous index is not, it
                # means we reset something, and we need to take the data we
                # thought was part of our boundary and send it along as actual
                # data.
                elif prev_index > 0:
                    # Callback to write the saved data.
                    lb_data = join_bytes(self.lookbehind)
                    self.callback('part_data', lb_data, 0, prev_index)

                    # Overwrite our previous index.
                    prev_index = 0

                    # Re-set our mark for part data.
                    set_mark('part_data')

                    # Re-consider the current character, since this could be
                    # the start of the boundary itself.
                    i -= 1

            elif state == STATE_END:
                # Do nothing and just consume a byte in the end state.
                if c not in (CR, LF):
                    self.logger.warning("Consuming a byte '0x%x' in the end state",
                                        c)

            else:                   # pragma: no cover (error case)
                # We got into a strange state somehow!  Just stop processing.
                msg = "Reached an unknown state %d at %d" % (state, i)
                self.logger.warning(msg)
                e = MultipartParseError(msg)
                e.offset = i
                raise e

            # Move to the next byte.
            i += 1

        # We call our callbacks with any remaining data.  Note that we pass
        # the 'remaining' flag, which sets the mark back to 0 instead of
        # deleting it, if it's found.  This is because, if the mark is found
        # at this point, we assume that there's data for one of these things
        # that has been parsed, but not yet emitted.  And, as such, it implies
        # that we haven't yet reached the end of this 'thing'.  So, by setting
        # the mark to 0, we cause any data callbacks that take place in future
        # calls to this function to start from the beginning of that buffer.
        data_callback('header_field', True)
        data_callback('header_value', True)
        data_callback('part_data', True)

        # Save values to locals.
        self.state = state
        self.index = index
        self.flags = flags

        # Return our data length to indicate no errors, and that we processed
        # all of it.
        return length

    def finalize(self) -> None:
        """Finalize this parser, which signals to that we are finished parsing.

        Note: It does not currently, but in the future, it will verify that we
        are in the final state of the parser (i.e. the end of the multipart
        message is well-formed), and, if not, throw an error.
        """
        # TODO: verify that we're in the state STATE_END, otherwise throw an
        # error or otherwise state that we're not finished parsing.
        pass

    def __repr__(self) -> str:
        return "%s(boundary=%r)" % (self.__class__.__name__, self.boundary)


class FormParser(object):
    """This class is the all-in-one form parser.  Given all the information
    necessary to parse a form, it will instantiate the correct parser, create
    the proper :class:`Field` and :class:`File` classes to store the data that
    is parsed, and call the two given callbacks with each field and file as
    they become available.

    :param content_type: The Content-Type of the incoming request.  This is
                         used to select the appropriate parser.

    :param on_field: The callback to call when a field has been parsed and is
                     ready for usage.  See above for parameters.

    :param on_file: The callback to call when a file has been parsed and is
                    ready for usage.  See above for parameters.

    :param on_end: An optional callback to call when all fields and files in a
                   request has been parsed.  Can be None.

    :param boundary: If the request is a multipart/form-data request, this
                     should be the boundary of the request, as given in the
                     Content-Type header, as a bytestring.

    :param file_name: If the request is of type application/octet-stream, then
                      the body of the request will not contain any information
                      about the uploaded file.  In such cases, you can provide
                      the file name of the uploaded file manually.

    :param file_class: The class to use for uploaded files.  Defaults to
                      :class:`File`, but you can provide your own class if you
                      wish to customize behaviour.  The class will be
                      instantiated as file_class(file_name, field_name), and it
                      must provide the following functions::
                          file_instance.write(data)
                          file_instance.finalize()
                          file_instance.close()

    :param field_class: The class to use for uploaded fields.  Defaults to
                       :class:`Field`, but you can provide your own class if
                       you wish to customize behaviour.  The class will be
                       instantiated as field_class(field_name), and it must
                       provide the following functions::
                           field_instance.write(data)
                           field_instance.finalize()
                           field_instance.close()

    :param config: Configuration to use for this FormParser.  The default
                   values are taken from the DEFAULT_CONFIG value, and then
                   any keys present in this dictionary will overwrite the
                   default values.

    """
    #: This is the default configuration for our form parser.
    #: Note: all file sizes should be in bytes.
    DEFAULT_CONFIG = {
        'MAX_BODY_SIZE': MAX_INT,
        'MAX_MEMORY_FILE_SIZE': 1 * 1024 * 1024,
        'UPLOAD_DIR': None,
        'UPLOAD_KEEP_FILENAME': False,
        'UPLOAD_KEEP_EXTENSIONS': False,

        # Error on invalid Content-Transfer-Encoding?
        'UPLOAD_ERROR_ON_BAD_CTE': False,
    }

    def __init__(self,
                 content_type: str,
                 on_field,
                 on_file,
                 on_end=None,
                 boundary=None,
                 file_name=None,
                 file_class=File,
                 field_class=Field,
                 config=None) -> None:

        self.logger = logging.getLogger(__name__)

        # Save variables.
        self.content_type = content_type
        self.boundary = boundary
        self.bytes_received = 0

        # Save callbacks.
        self.on_field = on_field
        self.on_file = on_file
        self.on_end = on_end

        # Save classes.
        self.file_class = File
        self.field_class = Field

        # Set configuration options.
        self.config = self.DEFAULT_CONFIG.copy()
        self.config.update(empty_dict_if_none(config))

        max_body_size = self.config['MAX_BODY_SIZE']

        assert isinstance(max_body_size, int)

        # Depending on the Content-Type, we instantiate the correct parser.
        if content_type == 'application/octet-stream':
            f: Any = None

            def on_start() -> None:
                nonlocal f
                f = file_class(file_name, None, config=self.config)

            def on_data(data, start, end) -> None:
                nonlocal f
                f.write(data[start:end])

            def on_end() -> None:
                nonlocal f
                # Finalize the file itself.
                f.finalize()

                # Call our callback.
                on_file(f)

                # Call the on-end callback.
                if self.on_end is not None:
                    self.on_end()

            callbacks = {
                'on_start': on_start,
                'on_data': on_data,
                'on_end': on_end,
            }

            self.parser: BaseParser = OctetStreamParser(callbacks,
                                                        max_size=max_body_size)

        elif (content_type == 'application/x-www-form-urlencoded' or
              content_type == 'application/x-url-encoded'):

            name_buffer = []

            f = None

            def on_field_start() -> None:
                pass

            def on_field_name(data, start: int, end: int) -> None:
                name_buffer.append(data[start:end])

            def on_field_data(data, start: int, end: int) -> None:
                nonlocal f
                if f is None:
                    f = field_class(b''.join(name_buffer))
                    del name_buffer[:]
                f.write(data[start:end])

            def on_field_end():
                nonlocal f
                # Finalize and call callback.
                if f is None:
                    # If we get here, it's because there was no field data.
                    # We create a field, set it to None, and then continue.
                    f = field_class(b''.join(name_buffer))
                    del name_buffer[:]
                    f.set_none()

                f.finalize()
                on_field(f)
                f = None

            def on_end() -> None:
                if self.on_end is not None:
                    self.on_end()

            # Setup callbacks.
            callbacks = {
                'on_field_start': on_field_start,
                'on_field_name': on_field_name,
                'on_field_data': on_field_data,
                'on_field_end': on_field_end,
                'on_end': on_end,
            }

            # Instantiate parser.
            self.parser = QuerystringParser(
                callbacks=callbacks,
                max_size=max_body_size
            )

        elif content_type == 'multipart/form-data':
            if boundary is None:
                self.logger.error("No boundary given")
                raise FormParserError("No boundary given")

            header_name = []
            header_value = []
            headers = {}

            f = None
            writer = None
            is_file = False

            def on_part_begin() -> None:
                pass

            def on_part_data(data, start: int, end: int) -> None:
                nonlocal writer
                assert writer is not None
                bytes_processed = writer.write(data[start:end])
                # TODO: check for error here.
                return bytes_processed

            def on_part_end() -> None:
                nonlocal f
                nonlocal is_file
                f.finalize()
                if is_file:
                    on_file(f)
                else:
                    on_field(f)

            def on_header_field(data, start: int, end: int) -> None:
                header_name.append(data[start:end])

            def on_header_value(data, start: int, end: int) -> None:
                header_value.append(data[start:end])

            def on_header_end() -> None:
                headers[b''.join(header_name)] = b''.join(header_value)
                del header_name[:]
                del header_value[:]

            def on_headers_finished() -> None:
                nonlocal is_file
                nonlocal f
                nonlocal writer
                # Reset the 'is file' flag.
                is_file = False

                # Parse the content-disposition header.
                # TODO: handle mixed case
                content_disp = headers.get(b'Content-Disposition')
                disp, options = parse_options_header(content_disp)

                # Get the field and filename.
                field_name = options.get(b'name')
                file_name = options.get(b'filename')
                # TODO: check for errors

                # Create the proper class.
                if file_name is None:
                    f = field_class(field_name)
                else:
                    f = file_class(file_name, field_name, config=self.config)
                    is_file = True

                # Parse the given Content-Transfer-Encoding to determine what
                # we need to do with the incoming data.
                # TODO: check that we properly handle 8bit / 7bit encoding.
                transfer_encoding = headers.get(b'Content-Transfer-Encoding',
                                                b'7bit')

                if (transfer_encoding == b'binary' or
                        transfer_encoding == b'8bit' or
                        transfer_encoding == b'7bit'):
                    writer = f

                elif transfer_encoding == b'base64':
                    writer = Base64Decoder(f)

                elif transfer_encoding == b'quoted-printable':
                    writer = QuotedPrintableDecoder(f)

                else:
                    self.logger.warning("Unknown Content-Transfer-Encoding: "
                                        "%r", transfer_encoding)
                    if self.config['UPLOAD_ERROR_ON_BAD_CTE']:
                        raise FormParserError(
                            'Unknown Content-Transfer-Encoding "{0}"'.format(
                                transfer_encoding
                            )
                        )
                    else:
                        # If we aren't erroring, then we just treat this as an
                        # unencoded Content-Transfer-Encoding.
                        writer = f

            def on_end() -> None:
                assert writer is not None
                writer.finalize()
                if self.on_end is not None:
                    self.on_end()

            # These are our callbacks for the parser.
            callbacks = {
                'on_part_begin': on_part_begin,
                'on_part_data': on_part_data,
                'on_part_end': on_part_end,
                'on_header_field': on_header_field,
                'on_header_value': on_header_value,
                'on_header_end': on_header_end,
                'on_headers_finished': on_headers_finished,
                'on_end': on_end,
            }

            # Instantiate a multipart parser.
            self.parser = MultipartParser(boundary,
                                          callbacks,
                                          max_size=max_body_size)

        else:
            self.logger.warning("Unknown Content-Type: %r", content_type)
            raise FormParserError("Unknown Content-Type: {0}".format(
                content_type
            ))

    def write(self, data: bytes) -> int:
        """
        Write some data.  The parser will forward this to the appropriate
        underlying parser.
        """
        self.bytes_received += len(data)
        # TODO: check the parser's return value for errors?
        return self.parser.write(data)

    def finalize(self) -> None:
        if self.parser is not None and hasattr(self.parser, 'finalize'):
            self.parser.finalize()

    def close(self) -> None:
        if self.parser is not None and hasattr(self.parser, 'close'):
            self.parser.close()

    def __repr__(self) -> str:
        return "%s(content_type=%r, parser=%r)" % (
            self.__class__.__name__,
            self.content_type,
            self.parser)


def create_form_parser(headers,
                       on_field: Callable,
                       on_file: Callable,
                       config: Dict[str, Any]=None):
    """This function is a helper function to aid in creating a FormParser
    instances.  Given a dictionary-like headers object, it will determine
    the correct information needed, instantiate a FormParser with the
    appropriate values and given callbacks, and then return the corresponding
    parser.

    :param headers: A dictionary-like object of HTTP headers.  The only
                    required header is Content-Type.

    :param on_field: Callback to call with each parsed field.

    :param on_file: Callback to call with each parsed file.

    :param config: Configuration variables to pass to the FormParser.
    """
    content_type = headers.get('Content-Type')
    if content_type is None:
        logging.getLogger(__name__).warning("No Content-Type header given")
        raise ValueError("No Content-Type header given!")

    # Boundaries are optional (the FormParser will raise if one is needed
    # but not given).
    content_type, params = parse_options_header(content_type)
    boundary = params.get(b'boundary')

    # We need content_type to be a string, not a bytes object.
    content_type = content_type.decode('latin-1')

    # File names are optional.
    file_name = headers.get('X-File-Name')

    # Instantiate a form parser.
    form_parser = FormParser(content_type,
                             on_field,
                             on_file,
                             boundary=boundary,
                             file_name=file_name,
                             config=empty_dict_if_none(config))

    # Return our parser.
    return form_parser


def parse_form(headers,
               input_stream,
               on_field: Callable,
               on_file: Callable) -> None:
    """This function is useful if you just want to parse a request body,
    without too much work.  Pass it a dictionary-like object of the request's
    headers, and a file-like object for the input stream, along with two
    callbacks that will get called whenever a field or file is parsed.

    :param headers: A dictionary-like object of HTTP headers.  The only
                    required header is Content-Type.

    :param input_stream: A file-like object that represents the request body.
                         The read() method must return bytestrings.

    :param on_field: Callback to call with each parsed field.

    :param on_file: Callback to call with each parsed file.
    """

    # Create our form parser.
    parser = create_form_parser(headers, on_field, on_file)

    # Read chunks of 100KiB and write to the parser, but never read more than
    # the given Content-Length, if any.
    content_length = headers.get('Content-Length')
    if content_length is not None:
        content_length = int(content_length)
    else:
        content_length = float('inf')
    bytes_read = 0

    while True:
        # Read only up to the Content-Length given.
        max_readable = min(content_length - bytes_read, 1048576)
        buff = input_stream.read(max_readable)

        # Write to the parser and update our length.
        parser.write(buff)
        bytes_read += len(buff)

        # If we get a buffer that's smaller than the size requested, or if we
        # have read up to our content length, we're done.
        if len(buff) != max_readable or bytes_read == content_length:
            break

    # Tell our parser that we're done writing data.
    parser.finalize()
