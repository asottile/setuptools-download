from __future__ import annotations

import enum
import hashlib
import io
import os.path
import platform
import re
import secrets
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from typing import NamedTuple

from setuptools import Command
from setuptools import Distribution

_Key = enum.Enum('_Key', 'os_name sys_platform platform_machine _internal')
_KEY_DISPLAY = f'({"|".join(k for k in _Key.__members__ if k != "_internal")})'


def _default_env() -> dict[_Key, str]:
    return {
        _Key.os_name: os.name,
        _Key.sys_platform: sys.platform,
        _Key.platform_machine: platform.machine(),
        _Key._internal: 'hellohello',
    }


class Marker(NamedTuple):
    """simplified PEP 508 Marker with only `and`"""
    orig: str
    parts: tuple[tuple[_Key, str], ...]

    def evaluate(self, env: dict[_Key, str]) -> bool:
        return all(env[k] == v for k, v in self.parts)

    @classmethod
    def parse(cls, s: str) -> Marker:
        if ' or ' in s:
            raise NotImplementedError(
                'Marker only supports `and` -- maybe repeat `marker =`?',
            )

        ret = []
        chunks = s.split(' and ')
        for chunk in chunks:
            parts = chunk.split()
            if (
                    len(parts) != 3 or
                    parts[0] not in _Key.__members__ or
                    parts[1] != '==' or
                    not parts[2].startswith('"') or
                    not parts[2].endswith('"')
            ):
                raise ValueError(
                    f'invalid marker part: '
                    f'expected: `{_KEY_DISPLAY} == "..."` got: `{chunk}`',
                )
            ret.append((_Key[parts[0]], parts[2][1:-1]))
        return cls(orig=s, parts=tuple(ret))

    def __repr__(self) -> str:
        return f'{type(self).__name__}.parse({self.orig!r})'


def _extract_noop(bts: bytes, src: str) -> bytes:
    return bts


def _extract_tar(bts: bytes, src: str) -> bytes:
    with io.BytesIO(bts) as bio:
        with tarfile.open(fileobj=bio) as tarf:
            member = tarf.extractfile(src)
            assert member is not None, src
            return member.read()


def _extract_zip(bts: bytes, src: str) -> bytes:
    with io.BytesIO(bts) as bio:
        with zipfile.ZipFile(bio) as zipf:
            return zipf.read(src)


_EXTRACT = {'noop': _extract_noop, 'tar': _extract_tar, 'zip': _extract_zip}


class Archive(NamedTuple):
    strategy: str = 'noop'
    path: str = ''


class File(NamedTuple):
    path: str
    url: str
    sha256: str
    archive: Archive = Archive()
    group: str | None = None
    markers: tuple[Marker, ...] = ()


_SECTION = re.compile(r'^\[([^]\n]+)\]$', re.MULTILINE)


def _parse(s: str | None, section: str) -> tuple[File, ...]:
    parts = iter(_SECTION.split((s or '').strip()))

    junk = next(parts)
    if junk != '':
        raise ValueError(f'{section}: unexpected value: {junk!r}')

    ret = []
    for path, body in zip(parts, parts):
        values = {}
        markers = []
        for line in body.strip().splitlines():
            k, v = line.split('=', 1)
            k, v = k.strip(), v.strip()

            if k == 'marker':
                markers.append(Marker.parse(v))
            elif k in {'url', 'sha256', 'extract', 'extract_path', 'group'}:
                if k in values:
                    raise ValueError(f'{section}[{path}]{k}: duplicate key')
                else:
                    values[k] = v
            else:
                raise ValueError(f'{section}[{path}]{k}: unexpected key')

        url = values.get('url')
        sha256 = values.get('sha256')
        extract = values.get('extract')
        extract_path = values.get('extract_path')
        group = values.get('group')

        if url is None or sha256 is None:
            raise ValueError(f'{section}[{path}]: missing `url` + `sha256`')

        if bool(extract) != bool(extract_path):
            raise ValueError(
                f'{section}[{path}]: missing `extract` + `extract_path`',
            )

        if extract is not None and extract not in _EXTRACT:
            raise ValueError(
                f'{section}[{path}]extract: unexpected value {extract!r}, '
                f'({", ".join(sorted(_EXTRACT.keys() - {"noop"}))})',
            )

        if extract is not None and extract_path is not None:
            archive = Archive(strategy=extract, path=extract_path)
        else:
            archive = Archive()

        if bool(group) != bool(markers):
            raise ValueError(f'{section}[{path}]: missing `group` + `marker`')

        ret.append(
            File(
                path=path,
                url=url,
                sha256=sha256,
                archive=archive,
                group=group,
                markers=tuple(markers),
            ),
        )

    return tuple(ret)


def _filter(src: tuple[File, ...], section: str) -> tuple[File, ...]:
    env = _default_env()

    ret = [
        f for f in src
        if not f.markers or any(m.evaluate(env) for m in f.markers)
    ]

    seen = set()
    seen_groups = set()
    for f in ret:
        if f.path in seen:
            raise ValueError(f'{section}: {f.path} matched multiple times!')
        else:
            seen.add(f.path)

        if f.group is not None:
            if f.group in seen_groups:
                raise ValueError(
                    f'{section}: group={f.group} matched multiple times!',
                )
            else:
                seen_groups.add(f.group)

    for group in sorted({f.group for f in src if f.group is not None}):
        if group not in seen_groups:
            raise ValueError(
                f'`{group}` is not supported on your platform\n\n'
                f'(debug info):\n'
                f'- os_name={os.name!r}\n'
                f'- sys_platform={sys.platform!r}\n'
                f'- platform_machine={platform.machine()!r}',
            )

    return tuple(ret)


def _download(f: File, base: str) -> None:
    req = urllib.request.urlopen(f.url)
    contents = req.read()
    got = hashlib.sha256(contents).hexdigest()
    if not secrets.compare_digest(got, f.sha256):
        raise ValueError(f'{f.path}: checksum mismatch {got=} {f.sha256=}')

    contents = _EXTRACT[f.archive.strategy](contents, f.archive.path)

    dest = os.path.join(base, f.path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'wb') as fb:
        fb.write(contents)


def _init_options(cmd: Command, options: tuple[tuple[str, str], ...]) -> None:
    for src, opt in options:
        cmd.set_undefined_options(src, (opt, opt))


class setuptools_download(Command):
    user_options = [
        ('download-data-files=', None, ''),
        ('download-scripts=', None, ''),
    ]

    def initialize_options(self) -> None:
        self.download_data_files: str | None = None
        self.parsed_data_files: tuple[File, ...] | None = None

        self.download_scripts: str | None = None
        self.parsed_scripts: tuple[File, ...] | None = None

        self.build_temp: str | None = None
        self.download_dir: str | None = None

    def _parse(self, attr: str) -> tuple[File, ...]:
        return _filter(_parse(getattr(self, attr), attr), attr)

    def finalize_options(self) -> None:
        self.parsed_data_files = self._parse('download_data_files')
        self.parsed_scripts = self._parse('download_scripts')

        _init_options(self, (('build', 'build_temp'),))
        assert self.build_temp is not None
        self.download_dir = os.path.join(self.build_temp, 'download')

    def run(self) -> None:
        assert self.download_dir is not None
        assert self.parsed_data_files is not None
        assert self.parsed_scripts is not None

        todo: tuple[tuple[str, tuple[File, ...]], ...] = (
            ('data_files', self.parsed_data_files),
            ('scripts', self.parsed_scripts),
        )

        for subdir, files in todo:
            base = os.path.join(self.download_dir, subdir)
            for f in files:
                print(f'=> downloading {f.path}...')
                _download(f, base)


class install_setuptools_download(Command):
    def initialize_options(self) -> None:
        self.parsed_data_files: tuple[File, ...] | None = None
        self.parsed_scripts: tuple[File, ...] | None = None
        self.download_dir: str | None = None
        self.install_data: str | None = None
        self.install_scripts: str | None = None

    def finalize_options(self) -> None:
        _init_options(
            self,
            (
                (setuptools_download.__name__, 'parsed_data_files'),
                (setuptools_download.__name__, 'parsed_scripts'),
                (setuptools_download.__name__, 'download_dir'),
                ('install', 'install_data'),
                ('install', 'install_scripts'),
            ),
        )

    def run(self) -> None:
        assert self.parsed_data_files is not None
        assert self.parsed_scripts is not None
        assert self.download_dir is not None
        assert self.install_data is not None
        assert self.install_scripts is not None

        for f in self.parsed_data_files:
            src = os.path.join(self.download_dir, 'data_files', f.path)
            dest = os.path.join(self.install_data, f.path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy(src, dest)

        for f in self.parsed_scripts:
            src = os.path.join(self.download_dir, 'scripts', f.path)
            dest = os.path.join(self.install_scripts, f.path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy(src, dest)

            # make scripts executable
            mode = os.stat(dest).st_mode
            mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            os.chmod(dest, mode)


def finalize_distribution(dist: Distribution) -> None:
    dist.get_command_class('build').sub_commands.append(
        (setuptools_download.__name__, None),
    )
    dist.get_command_class('install').sub_commands.append(
        (install_setuptools_download.__name__, None),
    )
