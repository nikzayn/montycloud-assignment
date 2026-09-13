# Image Service

The image upload and storage module of an Instagram-like app. Users upload images with metadata, list and search them, view or download them, and delete their own. Built on API Gateway + Lambda + S3 + DynamoDB in Python, with LocalStack for local development.

## Approaches

### Approach - 1

Client sends one multipart/form-data request with the file and metadata. Lambda validates it, writes the file to S3, writes metadata to DynamoDB, and returns 201. The image is visible immediately.

- One request; works directly from curl and Postman
- File validated before anything is stored
- No PENDING state, no S3 trigger
- Capped around 4 MB (Lambda's 6 MB payload limit, minus base64 overhead)
- Every upload runs a Lambda and occupies an execution slot
- API Gateway must treat multipart/form-data as a binary media type, or bytes get corrupted

[[approach-1](https://github.com/nikzayn/montycloud-assignment/blob/main/assets/approach-1.png)]

### Approach - 2 (Choosen)

Client sends metadata as JSON. The API saves it as PENDING and returns a signed S3 form. The client sends the file straight to S3. An S3 event triggers a Lambda that checks the file and marks it ACTIVE.

- Bytes never touch API Gateway or Lambda, so uploads scale with S3
- Files up to 5 GB (we cap at 10 MB); S3 enforces size and type from the signed policy
- Two client requests, so it's more awkward from curl and Postman
- Needs a PENDING → ACTIVE lifecycle, an S3 trigger, and TTL cleanup of abandoned uploads
- File is validated after it lands in S3; bad files get deleted
- In terms of scalability, Approach 2 is best suited and the standard AWS pattern for uploads

[[approach-2](https://github.com/nikzayn/montycloud-assignment/blob/main/assets/approach-2.png)]

## Design Thought Process



### Problem:

I started from what the module actually does: store an image, store its metadata, and let people search it. Those are two very different workloads. Metadata is small and needs querying, so DynamoDB. The image is a big blob nobody queries, so S3. Once I split them that way, the real design question became: how do the bytes get into S3?

### Upload Decision

My first instinct was the obvious one: post the file to my API, let a Lambda write it to S3. I dropped that once I looked at the limits. API Gateway caps a request at 10 MB, Lambda at 6 MB, and binary bodies get base64-encoded on the way in, which costs another third. So I'd be capped around 4 MB, which is small for photos today. And every upload would occupy a Lambda while it shuffles bytes, so upload traffic, which is the heaviest traffic in an app like this, would compete for the same concurrency as my read endpoints.

So I went with pre-signed uploads. The client asks my API for permission, and then talks to S3 directly. The bytes never touch my compute.

### Working Steps

It's two steps where I will use POST /images actually just takes the metadata, writes it to DynamoDB as PENDING state, and returns a pre-signed POST form. I chose a pre-signed POST over a PUT deliberately: a POST policy lets me sign constraints into it, so S3 itself rejects anything over 10 MB or of the wrong content type. Bad uploads get refused before I store or pay for a single byte.

Then the client posts the file straight to S3. I deliberately don't have the client tell me it finished, because a client can crash, forget, or lie. Instead S3 fires an ObjectCreated event that triggers a Lambda. That Lambda reads the first sixteen bytes, checks the magic number really matches the declared type, and flips the record to ACTIVE. If it doesn't match, it deletes the object and marks it REJECTED.

## Quick start

You need Python 3.8+ (3.12 recommended, matching the Lambda runtime) and Docker.

```bash
# 1. Install dependencies
python -m venv .venv && source .venv/bin/activate
make install                       # = pip install -r requirements-dev.txt

# 2. Run the unit tests (no Docker needed)
make test                          # = python -m pytest

# 3. Start LocalStack. The current image needs a free auth token from https://app.localstack.cloud
# Note: I am attaching the auth token here just for experimentation purpose, but it is not recommended 
# for production and real world feature deployment.
export LOCALSTACK_AUTH_TOKEN=ls-nePimOXU-zAtI-9813-0882-qaxUtayA710f 
#    ...or skip the token by using an older community image:
#    export LOCALSTACK_IMAGE=localstack/localstack:4.0
make up                            # = docker compose up -d

# 4. Deploy table, bucket, Lambdas and API into LocalStack
make deploy                        # = python scripts/deploy_local.py

# 5. Try it
python scripts/client.py upload /{directory-of-your-choice}/_.jpg --user alice --title "Cat" --tags cute
python scripts/client.py list --tag cat
python scripts/client.py download <image_id> -o cat-copy.jpg
python scripts/client.py delete <image_id> --user alice
```



## API reference


| Method   | Path                          | Purpose                                       | Auth     |
| -------- | ----------------------------- | --------------------------------------------- | -------- |
| `POST`   | `/images`                     | Create image metadata and get an upload form  | required |
| `GET`    | `/images`                     | List images, filter by `user_id` and/or `tag` | —        |
| `GET`    | `/images/{image_id}`          | View one image's metadata (+ download URL)    | —        |
| `GET`    | `/images/{image_id}/download` | Download the file (302 redirect to S3)        | —        |
| `DELETE` | `/images/{image_id}`          | Delete an image you own                       | required |


**Authentication.** In production the caller's identity comes from a Cognito authorizer (JWT). Locally, send an `X-User-Id: alice` header instead. This only works because the deploy script sets `ALLOW_HEADER_AUTH=true`, which must never be enabled in a real deployment.

**Errors** always have the same shape:

```json
{ "error": { "code": "VALIDATION_ERROR", "message": "'content_type' must be one of: image/jpeg, image/png, image/gif, image/webp" } }
```


| Status | `code`                             | When                                                    |
| ------ | ---------------------------------- | ------------------------------------------------------- |
| 400    | `VALIDATION_ERROR` / `BAD_REQUEST` | Invalid body, query parameter or `next_token`           |
| 401    | `UNAUTHORIZED`                     | No caller identity on an endpoint that needs one        |
| 403    | `FORBIDDEN`                        | Deleting someone else's image                           |
| 404    | `NOT_FOUND`                        | No such image                                           |
| 409    | `CONFLICT`                         | Downloading an image whose upload hasn't completed      |
| 500    | `INTERNAL_ERROR`                   | Unexpected failure (details are logged, never returned) |


In the examples below, set `API` to your base URL first: `API=$(cat .api_url)`.

## Postman usage



### Upload metadata to dynamoDB, for example

```
curl --location 'http://localhost:4566/restapis/{API_ID}/local/_user_request_/images' \
--header 'X-User-Id: alice' \
--header 'Content-Type: application/json' \
--data '{
    "title": "Spongebob",
    "description": "spongebob mimics duck",
    "content_type": "image/gif",
    "tags": [
        "spongebob",
        "tv"
    ]
}'
```



### Once, this process is done it will create a presigned URL with 15 minutes of expiration

```
curl -X POST '[http://localhost:4566/images-bucket](http://localhost:4566/images-bucket)'   
  -F 'Content-Type=image/jpeg'   
  -F 'key=...'   
  -F 'x-amz-algorithm=AWS4-HMAC-SHA256'   
  -F 'x-amz-credential=...'   
  -F 'x-amz-date=...'   
  -F 'policy=...'   
  -F 'x-amz-signature=...'   
  -F 'x-amz-security-token=...'
  -F 'file=@/path/to/your-image.jpg'
```



### Then after successfully upload, you can list the items

```
curl --location 'http://localhost:4566/restapis/{API_ID}/local/_user_request_/images'
```



### Delete the item

```
curl --location --request DELETE 'http://localhost:4566/restapis/{API_ID}/local/_user_request_/images/bbddeca4-5c0f-4da0-b723-4352cf27e422' \
--header 'X-User-Id: alice'
```


## Demo

### Postman Walkthrough
[[Postman](https://github.com/nikzayn/montycloud-assignment/blob/main/assets/postman-demo.mov)]

### CLI Walkthrough
[[CLI](https://github.com/nikzayn/montycloud-assignment/blob/main/assets/cli-demo.mov)]