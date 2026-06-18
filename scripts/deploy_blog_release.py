#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import re
import shutil
import ssl
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath


API_ROOT = "https://api.github.com"
ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
MAX_ARCHIVE_SIZE = 50 * 1024 * 1024
MAX_EXPANDED_SIZE = 250 * 1024 * 1024
MAX_MEMBERS = 10_000


def parse_args():
    parser = argparse.ArgumentParser(
        description="Safely deploy the latest static blog GitHub Release."
    )
    parser.add_argument("--repository", required=True, help="GitHub owner/repository")
    parser.add_argument("--root", type=Path, default=Path("/srv/blog"))
    parser.add_argument("--keep", type=int, default=5)
    return parser.parse_args()


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.parse.urlparse(newurl).hostname
        if host not in ALLOWED_DOWNLOAD_HOSTS:
            raise urllib.error.URLError(f"refusing redirect to untrusted host: {host}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def opener():
    context = ssl.create_default_context()
    return urllib.request.build_opener(
        SafeRedirectHandler(), urllib.request.HTTPSHandler(context=context)
    )


def request_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "all-in-one-blog-deployer/1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_json(url):
    request = urllib.request.Request(url, headers=request_headers())
    with opener().open(request, timeout=30) as response:
        return json.load(response)


def download(url, destination, limit):
    host = urllib.parse.urlparse(url).hostname
    if host not in ALLOWED_DOWNLOAD_HOSTS:
        raise RuntimeError(f"untrusted asset host: {host}")
    request = urllib.request.Request(url, headers=request_headers())
    total = 0
    with opener().open(request, timeout=60) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise RuntimeError(f"download exceeds {limit} bytes")
            output.write(chunk)


def release_assets(release):
    assets = {asset["name"]: asset["browser_download_url"] for asset in release["assets"]}
    required = {"blog.tar.gz", "blog.tar.gz.sha256"}
    missing = required - assets.keys()
    if missing:
        raise RuntimeError(f"release is missing assets: {', '.join(sorted(missing))}")
    return assets


def expected_checksum(checksum_file):
    line = checksum_file.read_text(encoding="ascii").strip()
    match = re.fullmatch(r"([0-9a-f]{64})\s+\*?blog\.tar\.gz", line)
    if not match:
        raise RuntimeError("invalid checksum file")
    return match.group(1)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_members(archive):
    members = archive.getmembers()
    if len(members) > MAX_MEMBERS:
        raise RuntimeError("archive contains too many entries")
    expanded_size = 0
    paths = set()
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"unsafe archive path: {member.name}")
        normalized = str(path)
        if normalized in paths:
            raise RuntimeError(f"duplicate archive path: {member.name}")
        paths.add(normalized)
        if not (member.isdir() or member.isfile()):
            raise RuntimeError(f"unsupported archive entry: {member.name}")
        expanded_size += member.size
        if expanded_size > MAX_EXPANDED_SIZE:
            raise RuntimeError("expanded archive is too large")
    return members


def extract_archive(archive_path, destination):
    with tarfile.open(archive_path, mode="r:gz") as archive:
        members = validate_members(archive)
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o755)
                target.chmod(0o755)
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"cannot read archive entry: {member.name}")
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o644)
    index = destination / "index.html"
    error_page = destination / "404.html"
    if not index.is_file() or not error_page.is_file():
        raise RuntimeError("release must contain index.html and 404.html")


def activate(root, release_name):
    temporary_link = root / f".current-{os.getpid()}"
    temporary_link.symlink_to(Path("releases") / release_name)
    os.replace(temporary_link, root / "current")


def prune_releases(root, keep, active_name):
    releases = sorted(
        (path for path in (root / "releases").iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    retained = 0
    for release in releases:
        if release.name == active_name or retained < keep:
            retained += 1
            continue
        shutil.rmtree(release)


def main():
    args = parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        raise ValueError("repository must use owner/name format")
    if args.keep < 2:
        raise ValueError("keep must be at least 2")

    root = args.root.resolve()
    releases_root = root / "releases"
    releases_root.mkdir(parents=True, exist_ok=True, mode=0o755)
    release = fetch_json(f"{API_ROOT}/repos/{args.repository}/releases/latest")
    tag = release["tag_name"]
    if not re.fullmatch(r"blog-[0-9a-f]{40}", tag):
        raise RuntimeError(f"unexpected release tag: {tag}")
    destination = releases_root / tag

    current = root / "current"
    if destination.is_dir() and current.is_symlink() and current.resolve() == destination:
        print(f"already-current={tag}")
        return

    assets = release_assets(release)
    with tempfile.TemporaryDirectory(prefix=".blog-download-", dir=root) as temp:
        temp_path = Path(temp)
        archive = temp_path / "blog.tar.gz"
        checksum = temp_path / "blog.tar.gz.sha256"
        download(assets["blog.tar.gz.sha256"], checksum, 4096)
        download(assets["blog.tar.gz"], archive, MAX_ARCHIVE_SIZE)
        if sha256(archive) != expected_checksum(checksum):
            raise RuntimeError("release checksum mismatch")

        staging = releases_root / f".{tag}-{os.getpid()}"
        try:
            staging.mkdir(mode=0o755)
            extract_archive(archive, staging)
            if destination.exists():
                shutil.rmtree(staging)
            else:
                os.replace(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    activate(root, tag)
    prune_releases(root, args.keep, tag)
    print(f"deployed={tag}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"deployment failed: {error}", file=sys.stderr)
        raise SystemExit(1)
