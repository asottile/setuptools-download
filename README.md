[![build status](https://github.com/asottile/setuptools-download/actions/workflows/main.yml/badge.svg)](https://github.com/asottile/setuptools-download/actions/workflows/main.yml)
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/asottile/setuptools-download/main.svg)](https://results.pre-commit.ci/latest/github/asottile/setuptools-download/main)

setuptools-download
===================

setuptools plugin to download external files

## usage

this plugin is intended to be used through setuptools declarative metadata:

the value of `download_*` is an ini-like string with the section being the
filename.

### file settings

- `url` (required): url to download the file from
- `sha256` (required): checksum of the downloaded file
- `group` + `marker` (optional or required together)
    - `group`: a name for a mutually exclusive group
    - `marker`: a [PEP 508 marker expression] (can be specified multiple times)
        - only supports: `os_name`, `sys_platform`, `platform_machine`
- `extract` + `extract_path` (optional or required together)
    - `extract`: how to extract the downloaded file (`zip` or `tar`)
    - `extract_path`: path to extract from archive

[PEP 508 marker expression]: https://peps.python.org/pep-0508/#environment-markers

### example

```ini
[options]
setup_requires = setuptools-download

[setuptools_download]
download_data_files =
    [share/example/data.txt]
    url = https://example.com/data.txt
    sha256 = aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    [share/example/embedded]
    url = https://example.com/release-1.0.tar.gz
    sha256 = bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
    extract = tar
    extract_path = release-v1.0/share/example/embedded
download_scripts =
    [example-tool]
    group = example-tool-binary
    marker = sys_platform = "linux" and platform_machine = "x86_64"
    url = https://example.com/example-tool-linux-x86-64
    sha256 = ccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
    [example-tool]
    group = example-tool-binary
    marker = sys_platform = "darwin" and platform_machine = "x86_64"
    marker = sys_platform = "darwin" and platform_machine = "arm64"
    url = https://example.com/example-tool-darwin-x86-64
    sha256 = ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
    [example-tool.exe]
    group = example-tool-binary
    marker = sys_platform = "win32" and platform_machine = "AMD64"
    url = https://example.com/example-tool-win32.exe
    sha256 = eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
```
