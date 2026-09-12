#!/usr/bin/env python3
"""Tiny command-line client for trying the Image Service end to end.

    python scripts/client.py upload photo.jpg --user alice --title "Sunset" --tags travel,beach
    python scripts/client.py list [--user alice] [--tag travel] [--limit 10] [--next-token TOKEN]
    python scripts/client.py get IMAGE_ID
    python scripts/client.py download IMAGE_ID [-o out.jpg]
    python scripts/client.py delete IMAGE_ID --user alice

The API base URL comes from --api, the API_URL env var, or the .api_url file
written by `make deploy`.
"""
import argparse
import json
import mimetypes
import os
import pathlib
import sys
import time

import requests  # pyright: ignore[reportMissingModuleSource]

ROOT = pathlib.Path(__file__).resolve().parent.parent
TIMEOUT = 60  # generous: LocalStack's first Lambda call pulls the runtime image
mimetypes.add_type("image/webp", ".webp")


def base_url(args):
    url = args.api or os.environ.get("API_URL")
    if not url and (ROOT / ".api_url").exists():
        url = (ROOT / ".api_url").read_text().strip()
    if not url:
        sys.exit("API URL unknown: run `make deploy` first, or pass --api URL")
    return url.rstrip("/")


def show(response):
    print(f"HTTP {response.status_code}")
    if response.content:
        try:
            print(json.dumps(response.json(), indent=2))
        except ValueError:
            print(response.text)


def upload(args):
    api, path = base_url(args), pathlib.Path(args.file)
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    # Step 1: send the metadata; get back the image_id and a pre-signed upload form.
    created = requests.post(
        f"{api}/images",
        headers={"X-User-Id": args.user},
        json={
            "title": args.title or path.stem,
            "description": args.description,
            "content_type": content_type,
            "tags": [t for t in args.tags.split(",") if t],
        },
        timeout=TIMEOUT,
    )
    if created.status_code != 201:
        show(created)
        sys.exit(1)
    image_id = created.json()["image"]["image_id"]
    form = created.json()["upload"]
    print(f"[1/3] metadata saved, image_id={image_id}")

    # Step 2: send the bytes straight to S3 (they never pass through Lambda).
    with path.open("rb") as file:
        s3_response = requests.post(
            form["url"], data=form["fields"], files={form["file_field"]: (path.name, file, content_type)},
            timeout=TIMEOUT,
        )
    if s3_response.status_code not in (200, 201, 204):
        print(f"S3 rejected the upload: HTTP {s3_response.status_code}\n{s3_response.text}")
        sys.exit(1)
    print("[2/3] file uploaded to S3")

    # Step 3: S3 triggers a Lambda that verifies the file and marks the image ACTIVE.
    image = {}
    for _ in range(60):
        image = requests.get(f"{api}/images/{image_id}", timeout=TIMEOUT).json()["image"]
        if image["status"] != "PENDING":
            break
        time.sleep(1)
    print(f"[3/3] status={image.get('status')}")
    print(json.dumps(image, indent=2))


def list_images(args):
    params = {"user_id": args.user, "tag": args.tag, "limit": args.limit, "next_token": args.next_token}
    show(requests.get(f"{base_url(args)}/images", params={k: v for k, v in params.items() if v}, timeout=TIMEOUT))


def get(args):
    show(requests.get(f"{base_url(args)}/images/{args.image_id}", timeout=TIMEOUT))


def download(args):
    response = requests.get(f"{base_url(args)}/images/{args.image_id}/download", timeout=TIMEOUT)
    if response.status_code != 200:
        show(response)
        sys.exit(1)
    output = args.output or f"{args.image_id}{mimetypes.guess_extension(response.headers.get('Content-Type', '')) or ''}"
    pathlib.Path(output).write_bytes(response.content)
    print(f"Saved {len(response.content)} bytes to {output}")


def delete(args):
    show(requests.delete(f"{base_url(args)}/images/{args.image_id}", headers={"X-User-Id": args.user}, timeout=TIMEOUT))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", help="API base URL (default: contents of .api_url)")
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("upload", help="upload an image with metadata")
    p.add_argument("file")
    p.add_argument("--user", required=True)
    p.add_argument("--title")
    p.add_argument("--description", default="")
    p.add_argument("--tags", default="", help="comma-separated, e.g. travel,beach")
    p.set_defaults(func=upload)

    p = commands.add_parser("list", help="list images (newest first)")
    p.add_argument("--user", help="filter by owner")
    p.add_argument("--tag", help="filter by tag")
    p.add_argument("--limit", type=int)
    p.add_argument("--next-token")
    p.set_defaults(func=list_images)

    p = commands.add_parser("get", help="show one image's metadata")
    p.add_argument("image_id")
    p.set_defaults(func=get)

    p = commands.add_parser("download", help="download the image file")
    p.add_argument("image_id")
    p.add_argument("-o", "--output")
    p.set_defaults(func=download)

    p = commands.add_parser("delete", help="delete an image you own")
    p.add_argument("image_id")
    p.add_argument("--user", required=True)
    p.set_defaults(func=delete)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
