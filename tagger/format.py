# tagger/format.py - filename template formatting (copy of 2_tagger/backend/format.py)

import re
import hashlib
from typing import Dict, Callable
from pathlib import Path


class Info:
    def __init__(self, path: Path, output_ext: str):
        self.path = path
        self.output_ext = output_ext


def hash_file(info: Info, algo: str = 'sha1') -> str:
    try:
        hash_obj = hashlib.new(algo)
    except ValueError:
        raise ValueError(f"'{algo}' is invalid hash algorithm")
    with open(info.path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            hash_obj.update(chunk)
    return hash_obj.hexdigest()


def get_name(info: Info, *args) -> str:
    return info.path.stem


def get_extension(info: Info, *args) -> str:
    return info.path.suffix[1:] if info.path.suffix else ''


def get_output_extension(info: Info, *args) -> str:
    return info.output_ext


pattern = re.compile(r'\[([\w:]+)\]')

available_formats: Dict[str, Callable[..., str]] = {
    'name': get_name,
    'extension': get_extension,
    'hash': hash_file,
    'output_extension': get_output_extension,
}


def format_match(match: re.Match, info: Info) -> str:
    content = match.group(1)
    parts = content.split(':')
    name, args = parts[0], parts[1:]
    if name not in available_formats:
        return match.group(0)
    try:
        return available_formats[name](info, *args)
    except Exception as e:
        print(f"Format error for [{content}]: {e}")
        return match.group(0)


def format_string(template: str, info: Info) -> str:
    return pattern.sub(lambda m: format_match(m, info), template)
