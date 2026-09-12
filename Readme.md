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

[approach-1](https://github.com/nikzayn/montycloud-assignment/blob/main/assets/approach-1.png)

### Approach - 2 (Choosen)

Client sends metadata as JSON. The API saves it as PENDING and returns a signed S3 form. The client sends the file straight to S3. An S3 event triggers a Lambda that checks the file and marks it ACTIVE.

- Bytes never touch API Gateway or Lambda, so uploads scale with S3
- Files up to 5 GB (we cap at 10 MB); S3 enforces size and type from the signed policy
- Two client requests, so it's more awkward from curl and Postman
- Needs a PENDING → ACTIVE lifecycle, an S3 trigger, and TTL cleanup of abandoned uploads
- File is validated after it lands in S3; bad files get deleted
- In terms of scalability, Approach 2 is best suited and the standard AWS pattern for uploads

[approach-2](https://github.com/nikzayn/montycloud-assignment/blob/main/assets/approach-2.png)

## Design Thought Process

### Problem:

I started from what the module actually does: store an image, store its metadata, and let people search it. Those are two very different workloads. Metadata is small and needs querying, so DynamoDB. The image is a big blob nobody queries, so S3. Once I split them that way, the real design question became: how do the bytes get into S3?

### Upload Decision

My first instinct was the obvious one: post the file to my API, let a Lambda write it to S3. I dropped that once I looked at the limits. API Gateway caps a request at 10 MB, Lambda at 6 MB, and binary bodies get base64-encoded on the way in, which costs another third. So I'd be capped around 4 MB, which is small for photos today. And every upload would occupy a Lambda while it shuffles bytes, so upload traffic, which is the heaviest traffic in an app like this, would compete for the same concurrency as my read endpoints.

So I went with pre-signed uploads. The client asks my API for permission, and then talks to S3 directly. The bytes never touch my compute.

### Working Steps

It's two steps where I will use POST /images actually just takes the metadata, writes it to DynamoDB as PENDING state, and returns a pre-signed POST form. I chose a pre-signed POST over a PUT deliberately: a POST policy lets me sign constraints into it, so S3 itself rejects anything over 10 MB or of the wrong content type. Bad uploads get refused before I store or pay for a single byte.

Then the client posts the file straight to S3. I deliberately don't have the client tell me it finished, because a client can crash, forget, or lie. Instead S3 fires an ObjectCreated event that triggers a Lambda. That Lambda reads the first sixteen bytes, checks the magic number really matches the declared type, and flips the record to ACTIVE. If it doesn't match, it deletes the object and marks it REJECTED.