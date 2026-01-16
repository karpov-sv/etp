import json
import shlex


class Command:
    """
    Parse a text command into name, args, and keyword arguments.

    Supported formats:
    - simple: whitespace tokens with key=value pairs (default).
    - sms: semicolon-delimited tokens (name;key=value;flag).
    - json: JSON object with name/args/kwargs (or list form).
    """

    def __init__(self, string=None, format="simple"):
        """
        Initialize and parse a command string.

        Args:
            string: Raw command text (preserved in self.raw).
            format: One of "simple", "sms", or "json".
        """
        self.name = ""
        self.raw = ""
        self.args = []
        self.kwargs = {}
        self.format = format
        self._parts = []

        if string is not None:
            self.parse(string, format=format)

    @classmethod
    def create(cls, *args, **kwargs):
        self = cls()
        if args and len(args):
            self.name = args[0] if args[0] is not None else ""
            args = args[1:]
        self.args = list(args)
        self.kwargs = dict(kwargs)

        return self

    def get(self, key, value=None):
        """Return a keyword argument or a default value."""
        return self.kwargs.get(key, value)

    def has_key(self, key):
        """Return True if a keyword argument is present."""
        return key in self.kwargs

    def __contains__(self, key):
        """Return True if a keyword argument is present (container syntax)."""
        return key in self.kwargs

    def parse(self, string, format=None):
        """
        Parse a command string into fields for the selected format.

        This resets previous parsing state.
        """
        fmt = format or self.format
        self.format = fmt
        self.raw = string
        self.name = ""
        self.args = []
        self.kwargs = {}
        self._parts = []

        if fmt == "simple":
            self._parse_simple(string)
        elif fmt == "sms":
            self._parse_sms(string)
        elif fmt == "json":
            self._parse_json(string)
        else:
            raise ValueError("Unknown command format: %s" % fmt)

    def to_string(self, format=None):
        """
        Serialize the command back into a string.

        Args:
            format: One of "simple", "sms", or "json". Defaults to the format
                used for parsing.
        """
        fmt = format or self.format
        if fmt == "simple":
            return self._to_simple()
        if fmt == "sms":
            return self._to_sms()
        if fmt == "json":
            return self._to_json()
        raise ValueError("Unknown command format: %s" % fmt)

    def _parse_simple(self, string):
        """Parse whitespace tokens with key=value pairs."""
        lexer = shlex.shlex(string, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
        if not tokens:
            return

        start = 0
        if "=" not in tokens[0]:
            self.name = tokens[0]
            start = 1

        for token in tokens[start:]:
            if "=" in token:
                key, value = token.split("=", 1)
                self.kwargs[key] = value
                self._parts.append(("kw", key, value))
            else:
                self.args.append(token)
                self._parts.append(("arg", token))

    def _parse_sms(self, string):
        """Parse semicolon-delimited SMS tokens."""
        text = string.strip()
        if not text:
            return

        parts = [part for part in text.split(";") if part != ""]
        if parts:
            self.name = parts[0]

        for token in parts[1:]:
            if "=" in token:
                key, value = token.split("=", 1)
                self.kwargs[key] = value
                self._parts.append(("kw", key, value))
            else:
                self.args.append(token)
                self._parts.append(("arg", token))

    def _parse_json(self, string):
        """Parse a JSON object or list payload."""
        payload = json.loads(string)
        if isinstance(payload, dict):
            # Two optional but hard-coded keys
            self.name = payload.pop("name", "") or ""
            self.args = [str(item) for item in payload.pop("args", [])]
            # The rest goes directly into kwargs
            self.kwargs = payload
        elif isinstance(payload, list):
            if payload:
                self.name = str(payload[0])
                self.args = [str(item) for item in payload[1:]]
        else:
            self.name = str(payload)

    def _to_simple(self):
        """Serialize to the simple whitespace/key=value format."""
        def escape_token(value):
            if value == "":
                return '""'
            needs_quotes = any(ch.isspace() or ch in ('"', "\\") for ch in value)
            if not needs_quotes:
                return value
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            return '"' + escaped + '"'

        tokens = []
        if self.name:
            tokens.append(self.name)

        parts = self._parts or []
        if not parts:
            for arg in self.args:
                tokens.append(escape_token(arg))
            for key, value in self.kwargs.items():
                tokens.append("%s=%s" % (key, escape_token(value)))
        else:
            for part in parts:
                if part[0] == "arg":
                    tokens.append(escape_token(part[1]))
                elif part[0] == "kw":
                    tokens.append("%s=%s" % (part[1], escape_token(part[2])))
        return " ".join(tokens)

    def _to_sms(self):
        """Serialize to the SMS semicolon-delimited format."""
        parts = []
        if self.name:
            parts.append(self.name)

        sms_parts = self._parts or []
        if not sms_parts:
            for key, value in self.kwargs.items():
                parts.append("%s=%s" % (key, value))
            for arg in self.args:
                parts.append(arg)
        else:
            for part in sms_parts:
                if part[0] == "kw":
                    parts.append("%s=%s" % (part[1], part[2]))
                elif part[0] == "arg":
                    parts.append(part[1])

        return ";".join(parts)

    def _to_json(self):
        """Serialize to the JSON object format."""
        payload = {
            "name": self.name,
            "args": list(self.args),
            **dict(self.kwargs)
        }
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
