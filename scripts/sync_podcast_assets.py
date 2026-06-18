#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import html
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


FEED_URL = os.environ.get("FEED_URL", "https://feeds.simplecast.com/EaEV0pvl")
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "episodes")).resolve()
MANIFEST_DIR = Path(os.environ.get("MANIFEST_DIR", "manifests")).resolve()
PUBLIC_DIR = Path(os.environ.get("PUBLIC_DIR", "public")).resolve()
FEED_SNAPSHOT_PATH = MANIFEST_DIR / "feed-snapshot.json"
UPLOAD_MANIFEST_PATH = MANIFEST_DIR / "upload-manifest.ndjson"
GENERATED_FEED_PATH = PUBLIC_DIR / "feed.xml"
OBJECT_PREFIX = os.environ.get("PODCAST_OBJECT_PREFIX", "episodes").strip("/")
ARTWORK_PREFIX = os.environ.get("PODCAST_ARTWORK_PREFIX", "artwork").strip("/")
AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("CDK_DEFAULT_REGION") or "us-east-1"
UPLOAD_CONCURRENCY = max(1, int(os.environ.get("UPLOAD_CONCURRENCY", "3") or "3"))
FETCH_RETRIES = max(1, int(os.environ.get("FETCH_RETRIES", "3") or "3"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL")
USER_AGENT = "aws-hosted-podcast-migration/1.0"
CHUNK_SIZE = 1024 * 1024

ManifestState = dict[str, set[str]]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync The Nikos Show Simplecast assets to static S3 hosting.",
    )
    parser.add_argument("--download-only", action="store_true", help="Download audio to the local episodes cache only.")
    parser.add_argument("--upload-only", action="store_true", help="Upload only files already present in the local cache.")
    parser.add_argument("--artwork-only", action="store_true", help="Sync artwork without syncing episode audio.")
    parser.add_argument("--skip-artwork", action="store_true", help="Skip artwork syncing.")
    parser.add_argument("--generate-feed", action="store_true", help="Generate public/feed.xml with S3/CloudFront URLs.")
    parser.add_argument("--upload-feed", action="store_true", help="Upload generated feed.xml to the podcast bucket.")
    parser.add_argument("--verify", action="store_true", help="Verify expected object keys against the bucket after syncing.")
    parser.add_argument(
        "--add-new-feed-url",
        action="store_true",
        help="Add or update itunes:new-feed-url using NEW_FEED_URL. Use only after testing the new feed.",
    )
    return parser.parse_args(argv)


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")


def normalize_prefix(prefix: str) -> str:
    return prefix.strip("/")


def sleep_before_retry(attempt: int) -> None:
    time.sleep(attempt)


def open_url_with_retry(url: str, context: str):
    last_error: BaseException | None = None

    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            response = urlopen(request, timeout=120)
            status = getattr(response, "status", 200)
            if 200 <= status < 300:
                return response

            response.close()
            retryable = status == 429 or status >= 500
            if not retryable or attempt == FETCH_RETRIES:
                raise RuntimeError(f"{context}: HTTP {status}")

            last_error = RuntimeError(f"{context}: HTTP {status}")
        except HTTPError as error:
            last_error = error
            retryable = error.code == 429 or error.code >= 500
            if not retryable or attempt == FETCH_RETRIES:
                raise RuntimeError(f"{context}: HTTP {error.code}") from error
        except URLError as error:
            last_error = error
            if attempt == FETCH_RETRIES:
                raise RuntimeError(f"{context}: {error.reason}") from error
        except TimeoutError as error:
            last_error = error
            if attempt == FETCH_RETRIES:
                raise RuntimeError(f"{context}: timed out") from error

        print(f"{context} failed on attempt {attempt}/{FETCH_RETRIES}; retrying...", flush=True)
        sleep_before_retry(attempt)

    raise RuntimeError(f"{context}: {last_error}")


def read_url_text(url: str, context: str) -> str:
    with open_url_with_retry(url, context) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def escape_regexp(value: str) -> str:
    return re.escape(value)


def decode_xml(value: str) -> str:
    return html.unescape(value)


def strip_cdata(value: str) -> str:
    return re.sub(r"^<!\[CDATA\[|\]\]>$", "", value.strip())


def get_tag_value(xml: str, tag_name: str) -> str | None:
    pattern = rf"<{escape_regexp(tag_name)}\b[^>]*>([\s\S]*?)</{escape_regexp(tag_name)}>"
    match = re.search(pattern, xml, flags=re.IGNORECASE)
    if not match:
        return None

    return decode_xml(strip_cdata(match.group(1)))


def get_tag_values(xml: str, tag_name: str) -> list[str]:
    pattern = rf"<{escape_regexp(tag_name)}\b[^>]*>([\s\S]*?)</{escape_regexp(tag_name)}>"
    values = []
    for match in re.finditer(pattern, xml, flags=re.IGNORECASE):
        value = decode_xml(strip_cdata(match.group(1)))
        if value:
            values.append(value)
    return values


def get_attribute(tag: str, attr_name: str) -> str | None:
    pattern = rf"{escape_regexp(attr_name)}\s*=\s*[\"']([^\"']+)[\"']"
    match = re.search(pattern, tag, flags=re.IGNORECASE)
    return decode_xml(match.group(1)) if match else None


def get_self_closing_tag_attribute(xml: str, tag_name: str, attr_name: str) -> str | None:
    pattern = rf"<{escape_regexp(tag_name)}\b[^>]*{escape_regexp(attr_name)}\s*=\s*[\"']([^\"']+)[\"'][^>]*/?>"
    match = re.search(pattern, xml, flags=re.IGNORECASE)
    return decode_xml(match.group(1)) if match else None


def get_channel_xml(feed_xml: str) -> str:
    match = re.search(r"<channel\b[^>]*>([\s\S]*?)</channel>", feed_xml, flags=re.IGNORECASE)
    if not match:
        return feed_xml
    return re.sub(r"<item\b[\s\S]*?</item>", "", match.group(1), flags=re.IGNORECASE)


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(char for char in normalized if not unicodedata.combining(char))
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    return slug[:90]


def get_extension(url: str, default: str) -> str:
    ext = Path(urlparse(url).path).suffix.replace(".", "").lower()
    if 2 <= len(ext) <= 5:
        return ext
    return default


def guess_mime_type(extension: str) -> str | None:
    extension = extension.lower().lstrip(".")
    explicit_types = {
        "mp3": "audio/mpeg",
        "m4a": "audio/mp4",
        "aac": "audio/aac",
        "wav": "audio/wav",
        "flac": "audio/flac",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }
    return explicit_types.get(extension) or mimetypes.types_map.get(f".{extension}")


def parse_boolean_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip().lower()
    return stripped or None


def parse_number(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def parse_categories(xml: str) -> list[str]:
    simple_categories = get_tag_values(xml, "category")
    itunes_categories = [
        decode_xml(match.group(1))
        for match in re.finditer(
            r"<itunes:category\b[^>]*text\s*=\s*[\"']([^\"']+)[\"'][^>]*/?>",
            xml,
            flags=re.IGNORECASE,
        )
    ]
    seen: set[str] = set()
    categories = []
    for value in [*simple_categories, *itunes_categories]:
        stripped = value.strip()
        if stripped and stripped not in seen:
            categories.append(stripped)
            seen.add(stripped)
    return categories


def parse_channel_metadata(feed_xml: str) -> dict[str, Any]:
    channel_xml = get_channel_xml(feed_xml)
    image_url = get_self_closing_tag_attribute(channel_xml, "itunes:image", "href")
    if image_url is None:
        image_url = get_tag_value(channel_xml, "url")

    return {
        "title": get_tag_value(channel_xml, "title"),
        "description": get_tag_value(channel_xml, "description"),
        "link": get_tag_value(channel_xml, "link"),
        "language": get_tag_value(channel_xml, "language"),
        "copyright": get_tag_value(channel_xml, "copyright"),
        "pubDate": get_tag_value(channel_xml, "pubDate"),
        "lastBuildDate": get_tag_value(channel_xml, "lastBuildDate"),
        "managingEditor": get_tag_value(channel_xml, "managingEditor"),
        "webMaster": get_tag_value(channel_xml, "webMaster"),
        "generator": get_tag_value(channel_xml, "generator"),
        "imageUrl": image_url,
        "explicit": parse_boolean_text(get_tag_value(channel_xml, "itunes:explicit")),
        "author": get_tag_value(channel_xml, "itunes:author") or get_tag_value(channel_xml, "author"),
        "subtitle": get_tag_value(channel_xml, "itunes:subtitle"),
        "summary": get_tag_value(channel_xml, "itunes:summary"),
        "categories": parse_categories(channel_xml),
    }


def parse_episodes(feed_xml: str) -> list[dict[str, Any]]:
    item_matches = re.findall(r"<item\b[\s\S]*?</item>", feed_xml, flags=re.IGNORECASE)
    episodes: list[dict[str, Any]] = []
    for index, item in enumerate(item_matches):
        enclosure_match = re.search(r"<enclosure\b[^>]*>", item, flags=re.IGNORECASE)
        enclosure = enclosure_match.group(0) if enclosure_match else None
        audio_url = get_attribute(enclosure, "url") if enclosure else None
        episodes.append(
            {
                "index": index,
                "title": get_tag_value(item, "title"),
                "description": get_tag_value(item, "description"),
                "pubDate": get_tag_value(item, "pubDate"),
                "guid": get_tag_value(item, "guid"),
                "link": get_tag_value(item, "link"),
                "audioUrl": audio_url,
                "audioType": get_attribute(enclosure, "type") if enclosure else None,
                "audioLength": get_attribute(enclosure, "length") if enclosure else None,
                "duration": get_tag_value(item, "itunes:duration"),
                "explicit": parse_boolean_text(get_tag_value(item, "itunes:explicit")),
                "author": get_tag_value(item, "itunes:author") or get_tag_value(item, "author"),
                "episode": parse_number(get_tag_value(item, "itunes:episode")),
                "season": parse_number(get_tag_value(item, "itunes:season")),
                "imageUrl": get_self_closing_tag_attribute(item, "itunes:image", "href"),
                "subtitle": get_tag_value(item, "itunes:subtitle"),
                "summary": get_tag_value(item, "itunes:summary"),
                "categories": parse_categories(item),
            }
        )
    return episodes


def get_episode_filename(episode: dict[str, Any]) -> str:
    safe_title = slugify(episode.get("title") or "") or f"episode-{episode['index'] + 1}"
    audio_url = episode.get("audioUrl")
    extension = get_extension(audio_url, "mp3") if audio_url else "mp3"
    return f"{episode['index'] + 1:03d}-{safe_title}.{extension}"


def get_bucket_key(filename: str, prefix: str = OBJECT_PREFIX) -> str:
    normalized = normalize_prefix(prefix)
    return f"{normalized}/{filename}" if normalized else filename


def get_episode_bucket_key(episode: dict[str, Any]) -> str:
    return get_bucket_key(get_episode_filename(episode), OBJECT_PREFIX)


def get_artwork_filename(source_url: str) -> str:
    url_hash = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:16]
    basename = Path(urlparse(source_url).path).stem
    safe_name = slugify(basename) or "artwork"
    extension = get_extension(source_url, "jpg")
    return f"{url_hash}-{safe_name}.{extension}"


def get_artwork_bucket_key(source_url: str) -> str:
    return get_bucket_key(get_artwork_filename(source_url), ARTWORK_PREFIX)


def path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def load_upload_state(manifest_path: Path) -> ManifestState:
    state: ManifestState = {
        "episode_bucket_keys": set(),
        "episode_local_paths": set(),
        "artwork_bucket_keys": set(),
    }
    if not manifest_path.exists():
        return state

    with manifest_path.open("r", encoding="utf-8") as manifest:
        for line in manifest:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue

            event_name = event.get("event")
            asset_type = event.get("assetType")
            bucket_key = event.get("bucketKey")
            source_path = event.get("sourcePath")

            if event_name == "uploaded" and asset_type in (None, "episode"):
                if isinstance(bucket_key, str) and bucket_key:
                    state["episode_bucket_keys"].add(bucket_key)
                if isinstance(source_path, str) and source_path:
                    state["episode_local_paths"].add(source_path)
                continue

            if event_name in ("artwork_uploaded", "uploaded") and asset_type == "artwork":
                if isinstance(bucket_key, str) and bucket_key:
                    state["artwork_bucket_keys"].add(bucket_key)

    return state


class ManifestWriter:
    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path
        self.lock = threading.Lock()

    def append(self, event: dict[str, Any]) -> None:
        with self.lock:
            with self.manifest_path.open("a", encoding="utf-8") as manifest:
                manifest.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
                manifest.write("\n")


def aws_command(args: list[str]) -> list[str]:
    command = ["aws"]
    profile = os.environ.get("AWS_PROFILE")
    if profile:
        command.extend(["--profile", profile])
    if AWS_REGION:
        command.extend(["--region", AWS_REGION])
    command.extend(args)
    return command


def run_aws(args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        aws_command(args),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"AWS CLI failed: {' '.join(args)}\n{detail}")
    return result


def run_aws_json(args: list[str]) -> Any:
    result = run_aws([*args, "--output", "json"])
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def s3_uri(bucket_name: str, bucket_key: str) -> str:
    return f"s3://{bucket_name}/{bucket_key}"


def upload_local_file(bucket_name: str, bucket_key: str, local_path: Path, content_type: str | None) -> tuple[int, bool]:
    args = ["s3", "cp", str(local_path), s3_uri(bucket_name, bucket_key), "--only-show-errors"]
    if content_type:
        args.extend(["--content-type", content_type])
    file_size = local_path.stat().st_size
    run_aws(args)

    deleted_local_file = False
    try:
        local_path.unlink()
        deleted_local_file = True
    except OSError:
        deleted_local_file = False

    return file_size, deleted_local_file


def stream_url_to_s3(source_url: str, bucket_name: str, bucket_key: str, content_type: str | None) -> tuple[int | None, str | None]:
    last_error: BaseException | None = None
    for attempt in range(1, FETCH_RETRIES + 1):
        process: subprocess.Popen[bytes] | None = None
        try:
            with open_url_with_retry(source_url, f"Failed to stream {source_url}") as response:
                response_type = response.headers.get("content-type")
                content_length = response.headers.get("content-length")
                upload_type = content_type or response_type
                args = ["s3", "cp", "-", s3_uri(bucket_name, bucket_key), "--only-show-errors"]
                if upload_type:
                    args.extend(["--content-type", upload_type])

                process = subprocess.Popen(
                    aws_command(args),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                if process.stdin is None:
                    raise RuntimeError("AWS CLI stdin was not available.")

                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    process.stdin.write(chunk)

                process.stdin.close()
                stdout = process.stdout.read().decode("utf-8", errors="replace") if process.stdout else ""
                stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
                returncode = process.wait()
                if returncode != 0:
                    detail = stderr.strip() or stdout.strip()
                    raise RuntimeError(f"AWS CLI failed while uploading {bucket_key}: {detail}")

                size_bytes = int(content_length) if content_length and content_length.isdigit() else None
                return size_bytes, upload_type
        except (BrokenPipeError, RuntimeError, OSError) as error:
            last_error = error
            if process and process.poll() is None:
                process.kill()
                process.wait()
            if attempt == FETCH_RETRIES:
                raise
            print(f"Upload of {bucket_key} failed on attempt {attempt}/{FETCH_RETRIES}; retrying...", flush=True)
            sleep_before_retry(attempt)

    raise RuntimeError(f"Upload failed for {bucket_key}: {last_error}")


def save_url_to_disk(source_url: str, local_path: Path) -> None:
    with open_url_with_retry(source_url, f"Failed to download {source_url}") as response:
        with local_path.open("wb") as output:
            shutil.copyfileobj(response, output, CHUNK_SIZE)


def upload_episode_file(
    bucket_name: str,
    episode: dict[str, Any],
    local_path: Path,
    bucket_key: str,
    manifest_writer: ManifestWriter,
) -> None:
    audio_url = episode.get("audioUrl") or ""
    mime_type = guess_mime_type(get_extension(audio_url, "mp3")) if audio_url else None
    size_bytes, deleted_local_file = upload_local_file(bucket_name, bucket_key, local_path, mime_type)
    manifest_writer.append(
        {
            "event": "uploaded",
            "timestamp": now_iso(),
            "episodeIndex": episode["index"] + 1,
            "title": episode.get("title"),
            "guid": episode.get("guid"),
            "pubDate": episode.get("pubDate"),
            "sourceType": "local",
            "sourcePath": str(local_path),
            "sourceUrl": audio_url,
            "bucketName": bucket_name,
            "bucketKey": bucket_key,
            "mimeType": mime_type,
            "sizeBytes": size_bytes,
            "artworkUrl": episode.get("imageUrl"),
            "deletedLocalFile": deleted_local_file,
        }
    )


def upload_remote_episode_file(
    bucket_name: str,
    episode: dict[str, Any],
    bucket_key: str,
    manifest_writer: ManifestWriter,
) -> None:
    audio_url = episode.get("audioUrl")
    if not audio_url:
        return

    mime_type = episode.get("audioType") or guess_mime_type(get_extension(audio_url, "mp3"))
    size_bytes, uploaded_mime_type = stream_url_to_s3(audio_url, bucket_name, bucket_key, mime_type)
    manifest_writer.append(
        {
            "event": "uploaded",
            "timestamp": now_iso(),
            "episodeIndex": episode["index"] + 1,
            "title": episode.get("title"),
            "guid": episode.get("guid"),
            "pubDate": episode.get("pubDate"),
            "sourceType": "remote",
            "sourcePath": str(OUTPUT_DIR / get_episode_filename(episode)),
            "sourceUrl": audio_url,
            "bucketName": bucket_name,
            "bucketKey": bucket_key,
            "mimeType": uploaded_mime_type,
            "sizeBytes": size_bytes,
            "artworkUrl": episode.get("imageUrl"),
            "deletedLocalFile": True,
        }
    )


def upload_artwork_file(bucket_name: str, source_url: str, manifest_writer: ManifestWriter) -> None:
    bucket_key = get_artwork_bucket_key(source_url)
    extension = get_extension(source_url, "jpg")
    mime_type = guess_mime_type(extension)
    size_bytes, uploaded_mime_type = stream_url_to_s3(source_url, bucket_name, bucket_key, mime_type)
    manifest_writer.append(
        {
            "event": "artwork_uploaded",
            "assetType": "artwork",
            "timestamp": now_iso(),
            "sourceType": "remote",
            "sourcePath": None,
            "sourceUrl": source_url,
            "bucketName": bucket_name,
            "bucketKey": bucket_key,
            "mimeType": uploaded_mime_type,
            "sizeBytes": size_bytes,
        }
    )


def write_feed_snapshot(feed_xml: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    channel = parse_channel_metadata(feed_xml)
    episodes = [episode for episode in parse_episodes(feed_xml) if episode.get("audioUrl")]
    snapshot = {
        "fetchedAt": now_iso(),
        "feedUrl": FEED_URL,
        "channel": channel,
        "episodes": episodes,
    }
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    FEED_SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return channel, episodes


def collect_media_thumbnail_urls(feed_xml: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"<media:thumbnail\b[^>]*\burl\s*=\s*[\"']([^\"']+)[\"'][^>]*/?>", feed_xml, flags=re.IGNORECASE):
        urls.append(decode_xml(match.group(1)))
    return urls


def collect_artwork_urls(channel: dict[str, Any], episodes: list[dict[str, Any]], feed_xml: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    candidate_urls = [
        channel.get("imageUrl"),
        *(episode.get("imageUrl") for episode in episodes),
        *collect_media_thumbnail_urls(feed_xml),
    ]
    for url in candidate_urls:
        if isinstance(url, str) and url and url not in seen:
            urls.append(url)
            seen.add(url)
    return urls


def sync_episode_audio(bucket_name: str | None, episodes: list[dict[str, Any]], mode: str, state: ManifestState) -> None:
    if mode == "download-only" or bucket_name is None:
        if bucket_name is None and mode != "download-only":
            print("No PODCAST_BUCKET_NAME set. Falling back to local downloads only.")
        print(f"Saving to: {OUTPUT_DIR}")
        for episode in episodes:
            local_path = OUTPUT_DIR / get_episode_filename(episode)
            if path_exists(local_path):
                print(f"[{episode['index'] + 1}/{len(episodes)}] skipping existing: {local_path.name}")
                continue
            print(f"[{episode['index'] + 1}/{len(episodes)}] downloading: {local_path.name}")
            save_url_to_disk(episode["audioUrl"], local_path)
        return

    manifest_writer = ManifestWriter(UPLOAD_MANIFEST_PATH)
    print(f"Uploading to bucket: {bucket_name}")
    print(f"Object prefix: {OBJECT_PREFIX or '(root)'}")
    print(f"Upload manifest: {UPLOAD_MANIFEST_PATH}")

    def handle_episode(episode: dict[str, Any]) -> None:
        filename = get_episode_filename(episode)
        local_path = OUTPUT_DIR / filename
        bucket_key = get_episode_bucket_key(episode)
        local_exists = path_exists(local_path)

        if bucket_key in state["episode_bucket_keys"] or str(local_path) in state["episode_local_paths"]:
            if local_exists:
                local_path.unlink()
                print(f"[{episode['index'] + 1}/{len(episodes)}] cleaned existing local file: {filename}", flush=True)
            else:
                print(f"[{episode['index'] + 1}/{len(episodes)}] already uploaded: {filename}", flush=True)
            return

        if local_exists:
            print(f"[{episode['index'] + 1}/{len(episodes)}] uploading local file: {filename}", flush=True)
            upload_episode_file(bucket_name, episode, local_path, bucket_key, manifest_writer)
            return

        if mode == "upload-only":
            print(f"[{episode['index'] + 1}/{len(episodes)}] missing locally, skipping: {filename}", flush=True)
            return

        print(f"[{episode['index'] + 1}/{len(episodes)}] streaming upload: {filename}", flush=True)
        upload_remote_episode_file(bucket_name, episode, bucket_key, manifest_writer)

    run_with_concurrency(episodes, UPLOAD_CONCURRENCY, handle_episode)


def sync_artwork(bucket_name: str, artwork_urls: list[str], state: ManifestState) -> None:
    if not artwork_urls:
        print("No artwork URLs found.")
        return

    manifest_writer = ManifestWriter(UPLOAD_MANIFEST_PATH)
    print(f"Syncing {len(artwork_urls)} unique artwork files to prefix: {ARTWORK_PREFIX or '(root)'}")

    def handle_artwork(indexed_url: tuple[int, str]) -> None:
        index, source_url = indexed_url
        bucket_key = get_artwork_bucket_key(source_url)
        if bucket_key in state["artwork_bucket_keys"]:
            print(f"[artwork {index}/{len(artwork_urls)}] already uploaded: {Path(bucket_key).name}", flush=True)
            return
        print(f"[artwork {index}/{len(artwork_urls)}] streaming upload: {Path(bucket_key).name}", flush=True)
        upload_artwork_file(bucket_name, source_url, manifest_writer)

    run_with_concurrency(list(enumerate(artwork_urls, start=1)), UPLOAD_CONCURRENCY, handle_artwork)


def run_with_concurrency(items: list[Any], limit: int, handler: Any) -> None:
    if not items:
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(limit, len(items))) as executor:
        futures = [executor.submit(handler, item) for item in items]
        for future in concurrent.futures.as_completed(futures):
            future.result()


def object_public_url(bucket_name: str, bucket_key: str) -> str:
    base_url = (PUBLIC_BASE_URL or f"https://{bucket_name}.s3.{AWS_REGION}.amazonaws.com").rstrip("/")
    encoded_key = "/".join(quote(part) for part in bucket_key.split("/"))
    return f"{base_url}/{encoded_key}"


def replace_xml_url(xml: str, old_url: str, new_url: str) -> str:
    replacements = {
        old_url: new_url,
        html.escape(old_url, quote=True): html.escape(new_url, quote=True),
        old_url.replace("&", "&amp;"): new_url.replace("&", "&amp;"),
    }
    updated = xml
    for old_value, new_value in replacements.items():
        updated = updated.replace(old_value, new_value)
    return updated


def add_or_update_new_feed_url(feed_xml: str, new_feed_url: str) -> str:
    escaped_url = html.escape(new_feed_url, quote=False)
    tag = f"    <itunes:new-feed-url>{escaped_url}</itunes:new-feed-url>"
    pattern = r"<itunes:new-feed-url\b[^>]*>[\s\S]*?</itunes:new-feed-url>"
    if re.search(pattern, feed_xml, flags=re.IGNORECASE):
        return re.sub(pattern, tag.strip(), feed_xml, count=1, flags=re.IGNORECASE)
    return re.sub(r"(<channel\b[^>]*>)", f"\\1\n{tag}", feed_xml, count=1, flags=re.IGNORECASE)


def remove_new_feed_url(feed_xml: str) -> str:
    pattern = r"\n?[ \t]*<itunes:new-feed-url\b[^>]*>[\s\S]*?</itunes:new-feed-url>[ \t]*"
    return re.sub(pattern, "", feed_xml, count=1, flags=re.IGNORECASE)


def generate_feed_xml(
    feed_xml: str,
    bucket_name: str,
    episodes: list[dict[str, Any]],
    artwork_urls: list[str],
    add_new_feed_url: bool,
) -> str:
    updated_xml = feed_xml
    for episode in episodes:
        source_url = episode.get("audioUrl")
        if source_url:
            updated_xml = replace_xml_url(updated_xml, source_url, object_public_url(bucket_name, get_episode_bucket_key(episode)))

    for source_url in artwork_urls:
        updated_xml = replace_xml_url(updated_xml, source_url, object_public_url(bucket_name, get_artwork_bucket_key(source_url)))

    if add_new_feed_url:
        new_feed_url = os.environ.get("NEW_FEED_URL")
        if not new_feed_url:
            raise RuntimeError("NEW_FEED_URL is required with --add-new-feed-url.")
        updated_xml = add_or_update_new_feed_url(updated_xml, new_feed_url)
    else:
        updated_xml = remove_new_feed_url(updated_xml)

    return updated_xml


def write_generated_feed(
    feed_xml: str,
    bucket_name: str,
    episodes: list[dict[str, Any]],
    artwork_urls: list[str],
    add_new_feed_url: bool,
) -> Path:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    generated = generate_feed_xml(feed_xml, bucket_name, episodes, artwork_urls, add_new_feed_url)
    GENERATED_FEED_PATH.write_text(generated, encoding="utf-8")
    print(f"Generated feed: {GENERATED_FEED_PATH}")
    return GENERATED_FEED_PATH


def upload_generated_feed(bucket_name: str, feed_path: Path) -> None:
    if not feed_path.exists():
        raise RuntimeError(f"Generated feed does not exist: {feed_path}")
    run_aws(
        [
            "s3",
            "cp",
            str(feed_path),
            s3_uri(bucket_name, "feed.xml"),
            "--only-show-errors",
            "--content-type",
            "application/xml; charset=utf-8",
        ]
    )
    print(f"Uploaded feed: {object_public_url(bucket_name, 'feed.xml')}")


def expected_keys(episodes: list[dict[str, Any]], artwork_urls: list[str]) -> tuple[set[str], set[str]]:
    episode_keys = {get_episode_bucket_key(episode) for episode in episodes if episode.get("audioUrl")}
    artwork_keys = {get_artwork_bucket_key(source_url) for source_url in artwork_urls}
    return episode_keys, artwork_keys


def list_bucket_keys(bucket_name: str, prefix: str) -> set[str]:
    keys = run_aws_json(
        [
            "s3api",
            "list-objects-v2",
            "--bucket",
            bucket_name,
            "--prefix",
            prefix,
            "--query",
            "Contents[].Key",
        ]
    )
    if keys is None:
        return set()
    if not isinstance(keys, list):
        raise RuntimeError(f"Unexpected list-objects-v2 response for prefix {prefix}: {keys!r}")
    return {key for key in keys if isinstance(key, str)}


def verify_assets(bucket_name: str, episodes: list[dict[str, Any]], artwork_urls: list[str], include_artwork: bool) -> None:
    episode_keys, artwork_keys = expected_keys(episodes, artwork_urls if include_artwork else [])
    manifest_state = load_upload_state(UPLOAD_MANIFEST_PATH)
    bucket_episode_keys = list_bucket_keys(bucket_name, OBJECT_PREFIX)
    missing_manifest_episodes = episode_keys - manifest_state["episode_bucket_keys"]
    missing_bucket_episodes = episode_keys - bucket_episode_keys

    print(f"Expected episodes: {len(episode_keys)}")
    print(f"Manifest episode uploads: {len(manifest_state['episode_bucket_keys'] & episode_keys)}")
    print(f"Bucket episode objects: {len(bucket_episode_keys & episode_keys)}")

    failures: list[str] = []
    if missing_manifest_episodes:
        failures.append(f"{len(missing_manifest_episodes)} episode keys missing from manifest")
    if missing_bucket_episodes:
        failures.append(f"{len(missing_bucket_episodes)} episode keys missing from bucket")

    if include_artwork:
        bucket_artwork_keys = list_bucket_keys(bucket_name, ARTWORK_PREFIX)
        missing_manifest_artwork = artwork_keys - manifest_state["artwork_bucket_keys"]
        missing_bucket_artwork = artwork_keys - bucket_artwork_keys
        print(f"Expected artwork: {len(artwork_keys)}")
        print(f"Manifest artwork uploads: {len(manifest_state['artwork_bucket_keys'] & artwork_keys)}")
        print(f"Bucket artwork objects: {len(bucket_artwork_keys & artwork_keys)}")
        if missing_manifest_artwork:
            failures.append(f"{len(missing_manifest_artwork)} artwork keys missing from manifest")
        if missing_bucket_artwork:
            failures.append(f"{len(missing_bucket_artwork)} artwork keys missing from bucket")

    if failures:
        raise RuntimeError("; ".join(failures))

    print("Manifest and bucket verification passed.")


def determine_mode(args: argparse.Namespace) -> str:
    selected_modes = [
        mode
        for enabled, mode in [
            (args.download_only, "download-only"),
            (args.upload_only, "upload-only"),
            (args.artwork_only, "artwork-only"),
        ]
        if enabled
    ]
    if len(selected_modes) > 1:
        raise RuntimeError("Choose only one of --download-only, --upload-only, or --artwork-only.")
    return selected_modes[0] if selected_modes else "sync"


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    mode = determine_mode(args)
    bucket_name = os.environ.get("PODCAST_BUCKET_NAME") or os.environ.get("S3_BUCKET")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching RSS feed: {FEED_URL}")
    feed_xml = read_url_text(FEED_URL, "Failed to fetch feed")
    channel, episodes = write_feed_snapshot(feed_xml)
    if not episodes:
        raise RuntimeError("No episodes found. Could not find enclosure URLs in the RSS feed.")

    artwork_urls = collect_artwork_urls(channel, episodes, feed_xml)
    print(f"Found {len(episodes)} episodes.")
    print(f"Found {len(artwork_urls)} unique artwork URLs.")
    print(f"Saving metadata snapshot to: {FEED_SNAPSHOT_PATH}")

    if mode in ("upload-only", "artwork-only") and bucket_name is None:
        raise RuntimeError("PODCAST_BUCKET_NAME is required for upload modes.")
    if args.upload_feed and bucket_name is None:
        raise RuntimeError("PODCAST_BUCKET_NAME is required with --upload-feed.")
    if (args.generate_feed or args.upload_feed or args.verify) and bucket_name is None:
        raise RuntimeError("PODCAST_BUCKET_NAME is required to generate, upload, or verify the feed.")

    state = load_upload_state(UPLOAD_MANIFEST_PATH)

    if mode != "artwork-only":
        sync_episode_audio(bucket_name, episodes, mode, state)

    if bucket_name and not args.skip_artwork and mode != "download-only":
        state = load_upload_state(UPLOAD_MANIFEST_PATH)
        sync_artwork(bucket_name, artwork_urls, state)

    generated_feed_path: Path | None = None
    if args.generate_feed or args.upload_feed:
        generated_feed_path = write_generated_feed(feed_xml, bucket_name, episodes, artwork_urls, args.add_new_feed_url)

    if args.upload_feed:
        upload_generated_feed(bucket_name, generated_feed_path or GENERATED_FEED_PATH)

    if args.verify:
        verify_assets(bucket_name, episodes, artwork_urls, include_artwork=not args.skip_artwork)

    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(error, file=sys.stderr)
        sys.exit(1)
