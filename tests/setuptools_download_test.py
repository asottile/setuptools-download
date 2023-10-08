from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from typing import NamedTuple

import ephemeral_port_reserve
import pytest
import re_assert

from setuptools_download import _extract_noop
from setuptools_download import _extract_tar
from setuptools_download import _extract_zip
from setuptools_download import _filter
from setuptools_download import _parse
from setuptools_download import Archive
from setuptools_download import File
from setuptools_download import Marker


@pytest.fixture(scope='session', autouse=True)
def set_coverage_instrumentation():
    if 'PWD' in os.environ:  # pragma: no branch
        rcfile = os.path.join(os.environ['PWD'], 'setup.cfg')
        os.environ['COVERAGE_PROCESS_START'] = rcfile


def test_marker_repr():
    expected = "Marker.parse('os_name == \"nt\"')"
    assert repr(Marker.parse('os_name == "nt"')) == expected


def test_marker_does_not_support_or():
    with pytest.raises(NotImplementedError) as excinfo:
        Marker.parse('os_name == "nt" or os_name == "darwin"')
    msg, = excinfo.value.args
    assert msg == 'Marker only supports `and` -- maybe repeat `marker =`?'


@pytest.mark.parametrize(
    's',
    (
        pytest.param('os_name ==', id='wrong number of parts'),
        pytest.param('os_name == "with spaces"', id='spaces in value'),
        pytest.param('extra == "wat"', id='unknown key'),
        pytest.param('os_name != "nt"', id='== only'),
        pytest.param('os_name == nt', id='value is not quoted'),
    ),
)
def test_marker_parse_invalid(s):
    with pytest.raises(ValueError) as excinfo:
        Marker.parse(s)
    msg, = excinfo.value.args
    assert msg == (
        f'invalid marker part: '
        f'expected: `(os_name|sys_platform|platform_machine) == "..."` '
        f'got: `{s}`'
    )


def test_extract_noop():
    assert _extract_noop(b'file contents', '') == b'file contents'


def test_extract_zip():
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, 'w') as zipf:
        zipf.writestr('example/file', b'file contents')
    assert _extract_zip(bio.getvalue(), 'example/file') == b'file contents'


def test_extract_tar():
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode='w:gz') as tarf:
        fileobj = io.BytesIO(b'file contents')
        info = tarfile.TarInfo('example/file')
        info.size = len(fileobj.getvalue())
        tarf.addfile(info, fileobj)
    assert _extract_tar(bio.getvalue(), 'example/file') == b'file contents'


@pytest.mark.parametrize('s', ('', None))
def test_parse_trivial(s):
    assert _parse(s, 'example') == ()


def test_parse():
    src = '''\
[f1]
url = https://example.com/f1
sha256 = deadbeef
[f2]
url = https://example.com/f2.tar.gz
sha256 = cafecafe
extract = tar
extract_path = release-1.0/f2
[f3.exe]
url = https://example.com/f3.exe
sha256 = baadbaad
group = f3-binary
marker = sys_platform == "cygwin"
marker = sys_platform == "win32"
[f3]
url = https://example.com/f3
sha256 = daaddaad
group = f3-binary
marker = sys_platform == "linux"
'''
    ret = _parse(src, 'example')
    assert ret == (
        File(
            path='f1',
            url='https://example.com/f1',
            sha256='deadbeef',
        ),
        File(
            path='f2',
            url='https://example.com/f2.tar.gz',
            sha256='cafecafe',
            archive=Archive(strategy='tar', path='release-1.0/f2'),
        ),
        File(
            path='f3.exe',
            url='https://example.com/f3.exe',
            sha256='baadbaad',
            group='f3-binary',
            markers=(
                Marker.parse('sys_platform == "cygwin"'),
                Marker.parse('sys_platform == "win32"'),
            ),
        ),
        File(
            path='f3',
            url='https://example.com/f3',
            sha256='daaddaad',
            group='f3-binary',
            markers=(
                Marker.parse('sys_platform == "linux"'),
            ),
        ),
    )


def test_parse_unexpected_leading_text():
    src = '''\
junk
[f1]
url = https://example.com/f1
sha256 = deadbeef
'''
    with pytest.raises(ValueError) as excinfo:
        _parse(src, 'example')
    msg, = excinfo.value.args
    assert msg == "example: unexpected value: 'junk\\n'"


def test_parse_unexpected_key():
    src = '''\
[f1]
wat = 1
'''
    with pytest.raises(ValueError) as excinfo:
        _parse(src, 'example')
    msg, = excinfo.value.args
    assert msg == 'example[f1]wat: unexpected key'


def test_parse_duplicate_key():
    src = '''\
[f1]
url = https://example.com/f1
url = https://example.com/f2
sha256 = deadbeef
'''
    with pytest.raises(ValueError) as excinfo:
        _parse(src, 'example')
    msg, = excinfo.value.args
    assert msg == 'example[f1]url: duplicate key'


@pytest.mark.parametrize(
    's',
    (
        '[f1]\nsha256 = deadbeef\n',
        '[f1]\nurl = https://example.com/f\n',
    ),
)
def test_parse_missing_url_or_sha256(s):
    with pytest.raises(ValueError) as excinfo:
        _parse(s, 'example')
    msg, = excinfo.value.args
    assert msg == 'example[f1]: missing `url` + `sha256`'


@pytest.mark.parametrize(
    's',
    (
        '[f1]\n'
        'url = https://example.com/f1.tar.gz\n'
        'sha256=deadbeef\n'
        'extract = tar\n',

        '[f1]\n'
        'url = https://example.com/f1.tar.gz\n'
        'sha256=deadbeef\n'
        'extract_path = release-1.0/f1\n',
    ),
)
def test_parse_missing_extract_or_extract_path(s):
    with pytest.raises(ValueError) as excinfo:
        _parse(s, 'example')
    msg, = excinfo.value.args
    assert msg == 'example[f1]: missing `extract` + `extract_path`'


def test_parse_unexpected_extract_type():
    src = '''\
[f1]
url = https://example.com/f1.ar
sha256 = deadbeef
extract = ar
extract_path = release-v1.0/f1
'''
    with pytest.raises(ValueError) as excinfo:
        _parse(src, 'example')
    msg, = excinfo.value.args
    assert msg == "example[f1]extract: unexpected value 'ar', (tar, zip)"


@pytest.mark.parametrize(
    's',
    (
        '[f1]\n'
        'url = https://example.com/f1\n'
        'sha256 = deadbeef\n'
        'group = f1-binary\n',
        '[f1]\n'
        'url = https://example.com/f1\n'
        'sha256 = deadbeef\n'
        'marker = sys_platform == "linux"\n',
    ),
)
def test_parse_missing_group_or_marker(s):
    with pytest.raises(ValueError) as excinfo:
        _parse(s, 'example')
    msg, = excinfo.value.args
    assert msg == 'example[f1]: missing `group` + `marker`'


def test_filter_no_markers():
    src = (
        File(path='f1', url='https://example.com/f1', sha256='deadbeef'),
    )
    assert _filter(src, 'example') == src


def test_filter_only_one_matching():
    src = (
        File(
            path='f1', url='https://example.com/f1', sha256='deadbeef',
            group='f1-binary',
            markers=(Marker.parse('_internal == "hellohello"'),),
        ),
        File(
            path='f1', url='https://example.com/f1', sha256='deadbeef',
            group='f1-binary',
            markers=(Marker.parse('_internal == "never"'),),
        ),
    )
    ret = _filter(src, 'example')
    assert ret == (
        File(
            path='f1', url='https://example.com/f1', sha256='deadbeef',
            group='f1-binary',
            markers=(Marker.parse('_internal == "hellohello"'),),
        ),
    )


def test_filter_duplicate_files():
    src = (
        File(path='f1', url='https://example.com/f1', sha256='deadbeef'),
        File(path='f1', url='https://example.com/f1', sha256='deadbeef'),
    )
    with pytest.raises(ValueError) as excinfo:
        _filter(src, 'example')
    msg, = excinfo.value.args
    assert msg == 'example: f1 matched multiple times!'


def test_filter_duplicate_files_per_group():
    src = (
        File(
            path='f1.exe', url='https://example.com/f1.exe', sha256='baadbaad',
            group='f1-binary',
            markers=(Marker.parse('_internal == "hellohello"'),),
        ),
        File(
            path='f1', url='https://example.com/f1', sha256='deadbeef',
            group='f1-binary',
            markers=(Marker.parse('_internal == "hellohello"'),),
        ),
    )
    with pytest.raises(ValueError) as excinfo:
        _filter(src, 'example')
    msg, = excinfo.value.args
    assert msg == 'example: group=f1-binary matched multiple times!'


def test_filter_missing_file_for_group():
    src = (
        File(
            path='f1.exe', url='https://example.com/f1.exe', sha256='baadbaad',
            group='f1-binary',
            markers=(Marker.parse('_internal == "never.exe"'),),
        ),
        File(
            path='f1', url='https://example.com/f1', sha256='deadbeef',
            group='f1-binary',
            markers=(Marker.parse('_internal == "never"'),),
        ),
    )
    with pytest.raises(ValueError) as excinfo:
        _filter(src, 'example')
    msg, = excinfo.value.args
    re_assert.Matches(
        r'`f1-binary` is not supported on your platform\n'
        r'\n'
        r'\(debug info\):\n'
        r"- os_name='.+'\n"
        r"- sys_platform='.+'\n"
        r"- platform_machine='.+'$",
    ).assert_matches(msg)


def _sha256(f):
    with open(f, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


class Server(NamedTuple):
    host: str
    plain_sha256: str
    archive_sha256: str
    bin_posix_sha256: str
    bin_windows_sha256: str


@pytest.fixture(scope='session')
def file_server(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp('file_server')

    plain = tmp_path.joinpath('plain')
    plain.write_bytes(b'hello hello')
    plain_sha256 = _sha256(plain)

    archive = tmp_path.joinpath('archive.zip')
    with zipfile.ZipFile(archive, 'w') as zipf:
        zipf.writestr('path/to/f1', 'hello hello')
    archive_sha256 = _sha256(archive)

    bin_posix = tmp_path.joinpath('bin')
    bin_posix.write_bytes(b'#!/usr/bin/env python3\nprint("hello posix")\n')
    bin_posix_sha256 = _sha256(bin_posix)

    bin_windows = tmp_path.joinpath('bin.py')
    bin_windows.write_bytes(b'print("hello windows")\n')
    bin_windows_sha256 = _sha256(bin_windows)

    port = ephemeral_port_reserve.reserve()
    host = f'http://127.0.0.1:{port}'
    proc = subprocess.Popen(
        (sys.executable, '-m', 'http.server', str(port)),
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        for _ in range(20):  # pragma: no cover (retries not always needed)
            if proc.poll() is not None:
                raise AssertionError('server died!?')

            try:
                resp = urllib.request.urlopen(f'{host}/plain')
            except OSError:
                pass
            else:
                if resp.read() == b'hello hello':
                    break
            time.sleep(.05)

        yield Server(
            host=host,
            plain_sha256=plain_sha256,
            archive_sha256=archive_sha256,
            bin_posix_sha256=bin_posix_sha256,
            bin_windows_sha256=bin_windows_sha256,
        )
    finally:
        proc.kill()
        proc.wait()


_SETUP_PY = 'from setuptools import setup; setup(name="t", version="1")'


def test_integration_data_file(file_server, tmp_path):
    setup_cfg = f'''\
[setuptools_download]
download_data_files =
    [share/example/f]
    url = {file_server.host}/plain
    sha256 = {file_server.plain_sha256}
'''
    tmp_path.joinpath('setup.cfg').write_text(setup_cfg)
    tmp_path.joinpath('setup.py').write_text(_SETUP_PY)

    subprocess.check_call(
        (sys.executable, '-m', 'build', '--no-isolation', '--wheel'),
        cwd=tmp_path,
    )

    wheel = tmp_path.joinpath('dist', 't-1-py3-none-any.whl')
    with zipfile.ZipFile(wheel) as zipf:
        zipf.read('t-1.data/data/share/example/f') == b'hello hello'


def test_integration_checksum_mismatch(file_server, tmp_path):
    setup_cfg = f'''\
[setuptools_download]
download_data_files =
    [share/example/f]
    url = {file_server.host}/plain
    sha256 = deadbeefdeadbeef
'''
    tmp_path.joinpath('setup.cfg').write_text(setup_cfg)
    tmp_path.joinpath('setup.py').write_text(_SETUP_PY)

    proc = subprocess.run(
        (sys.executable, '-m', 'build', '--no-isolation', '--wheel'),
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.returncode == 1
    expected = (
        f'ValueError: share/example/f: '
        f"checksum mismatch got='{file_server.plain_sha256}' "
        f"f.sha256='deadbeefdeadbeef'"
    )
    assert expected in proc.stdout


def test_integration_archive(file_server, tmp_path):
    setup_cfg = f'''\
[setuptools_download]
download_data_files =
    [share/example/f]
    url = {file_server.host}/archive.zip
    sha256 = {file_server.archive_sha256}
    extract = zip
    extract_path = path/to/f1
'''
    tmp_path.joinpath('setup.cfg').write_text(setup_cfg)
    tmp_path.joinpath('setup.py').write_text(_SETUP_PY)

    subprocess.check_call(
        (sys.executable, '-m', 'build', '--no-isolation', '--wheel'),
        cwd=tmp_path,
    )

    wheel = tmp_path.joinpath('dist', 't-1-py3-none-any.whl')
    with zipfile.ZipFile(wheel) as zipf:
        zipf.read('t-1.data/data/share/example/f') == b'hello hello'


def test_integration_scripts_with_markers(file_server, tmp_path):
    setup_cfg = f'''\
[setuptools_download]
download_scripts =
    [bin]
    group = bin-binary
    marker = sys_platform == "linux"
    url = {file_server.host}/bin
    sha256 = {file_server.bin_posix_sha256}
    [bin.py]
    group = bin-binary
    marker = sys_platform == "win32"
    url = {file_server.host}/bin.py
    sha256 = {file_server.bin_windows_sha256}
'''
    tmp_path.joinpath('setup.cfg').write_text(setup_cfg)
    tmp_path.joinpath('setup.py').write_text(_SETUP_PY)

    subprocess.check_call(
        (sys.executable, '-m', 'build', '--no-isolation', '--wheel'),
        cwd=tmp_path,
    )

    wheel = tmp_path.joinpath('dist', 't-1-py3-none-any.whl')
    with zipfile.ZipFile(wheel) as zipf:
        if sys.platform == 'win32':  # pragma: win32 cover
            data = zipf.read('t-1.data/scripts/bin.py')
            assert data == b'print("hello windows")\n'
        else:  # pragma: win32 no cover
            info, = (
                info
                for info in zipf.filelist
                if info.filename == 't-1.data/scripts/bin'
            )
            assert info.external_attr >> 16 == 0o100755
            data = zipf.read('t-1.data/scripts/bin')
            assert data == b'#!/usr/bin/env python3\nprint("hello posix")\n'
