# Instagram API notes (research for Milestone 5, not implemented)

What Meta's official documentation says about publishing one image with a caption to the developer's **own** Instagram professional account, read on **2026-11-19**. Nothing here is built. `CLAUDE.md` still says LinkedIn is the only platform in scope. Section 13 holds a **proposed** decision (D48) that would change that once the developer accepts it.

**How to read this file**

- **[V]** VERIFIED: seen on the official page named in brackets, on 2026-11-19.
- **[U]** UNVERIFIED: from a blog or forum, an inference, or general knowledge. Do not rely on it until it is checked.
- `[S12]` and similar refer to the source list at the end. All Meta pages were read in their Markdown form (the page URL with `.md` appended), which Meta publishes for each page.
- Each section separates **Facts**, **Proposed design** (our choices, not Meta's) and **Open** (unresolved points).

---

## 1. API choice

**Facts**

- Meta now documents **three** setups [V, S1, S5]:

  | | Unified Login | Instagram Login | Facebook Login |
  |---|---|---|---|
  | User logs in with | Instagram or Facebook | Instagram | Facebook |
  | Facebook Page | Not required | **Not required** | **Required** |
  | Business portfolio | **Required** | Not required | Depends on configuration |
  | Token | Business Integration System User token | Instagram User token | Facebook User or Page token |
  | Host | `graph.facebook.com` | `graph.instagram.com` | `graph.facebook.com` |
  | Publishing permissions | `instagram_basic`, `instagram_content_publish` | `instagram_business_basic`, `instagram_business_content_publish` | `instagram_basic`, `instagram_content_publish`, `pages_read_engagement` |

  Source for the table: S5 (comparison table) and S12 (permissions).
- Unified Login: "the recommended setup for new integrations. New Instagram API features are released only for this setup." [V, S5]. It needs "A Meta app owned by a business portfolio" [V, S7]. Whether its token expires "depends on your configuration" [V, S6].
- Instagram Login: "This API setup does not require a Facebook Page to be linked to the Instagram professional account." It "cannot access ads or tagging." [V, S2]
- The old Instagram Login scope names (`business_basic`, `business_content_publish`, …) were "deprecated on **January 27, 2025**". Use the `instagram_business_*` names. [V, S2, S4]
- **Basic Display API:** "Starting on December 4th, 2024, Instagram Basic Display API will no longer be available … all Instagram Basic Display API requests will fail from that day onwards." [V, S24]. It is gone. It never served publishing: the blog describes it only as sharing "Profile, Images etc" [V, S24]. Third-party sites say no API supports personal (non-professional) accounts since then [U].
- Only professional accounts are served: "An Instagram professional account can represent a business or creator." [V, S1]. A personal account gets `(#10) Access is denied because the instagram account type is not business or creator.` [V, S7]

**Recommendation (proposed):** use **Instagram API with Instagram Login** (`graph.instagram.com`, Instagram User token, no Facebook Page, no business portfolio). It is the smallest setup for one account the developer owns, and its token works like the LinkedIn one (60 days, generated in the dashboard).

**Open:** Meta releases new features only for Unified Login. Whether Instagram Login will be deprecated later is not stated [U]. Check S5 and the changelog before implementing.

## 2. Account and app setup

**Facts**

- **App type:** "Your app must be a business type app to be able to add the Instagram product." The creation flow is: use case **Other**, then type **Business** [V, S9]. A non-Business app must be replaced by a new one [V, S3].
- Adding the **Instagram** product adds **API setup with Instagram login** automatically [V, S9].
- For token generation, "Add an Instagram account. **This account must be public.**" [V, S9, step 7]
- **Access levels:** "Standard Access … If your app only serves your Instagram professional account or an account you manage, Standard Access is all your app needs." Advanced Access "requires App Review and Business Verification" [V, S1]. Business Login lists "Standard Access if your app serves Instagram professional accounts you own or manage and have added to your app in the App Dashboard" [V, S4].
- **App Review table** [V, S8]: "My app is only for a business I own or manage. | No login or Instagram Login | Standard Access | **Not required**".
- **Development mode:** apps "can only request permissions from role users"; "All newly created apps start out in Development mode" [V, S10]. Testers "can grant the app any permission while it is in development" [V, S11]. An app not linked to a verified business can have up to 50 testers [V, S11].
- "If your app serves only your Instagram professional accounts, or accounts you manage, you do not need to implement a login flow. However, you will need to configure the business login settings in the App Dashboard to obtain an Instagram app ID and an Instagram app secret, as well as obtain long-lived access tokens." [V, S1]

**Answer: can I publish to my own account without App Review?** Per S8 and S1, **yes**: Standard Access, Development mode, the developer's own account added in the dashboard [V]. Two statements on other pages conflict with this; see "Open".

**Open**

- **Contradiction 1:** S9 step 10 says "Instagram requires successful completion of the App Review process before your app can access live data." S8 says review is "Not required" for one's own business. The first real post will show which is right.
- **Contradiction 2, important:** S10 says "Any data generated while an app is in Development mode, **such as test posts, can only be seen by role users**." Whether this applies to Instagram media published through the API, so that the post would be hidden from the public, is not stated on any Instagram page [U]. Check it at the first real post: view the post while logged out, or from an account without an app role.
- S9 step 2 requires a verified business "before you can publish your app" (switch it to Live). We stay in Development mode, so this probably does not apply [U].

## 3. Tokens

**Facts**

- The App Dashboard **Generate token** button gives a token that is "long-lived and … valid for **60 days**". Business-login tokens are short-lived (1 hour). [V, S3]
- Exchanging a short-lived token: `GET https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret=…&access_token=…` returns `access_token`, `token_type: bearer`, and `expires_in` (seconds, for example `5184000`). This call needs the **app secret** and must be server-side only. [V, S18, S4]
- **Refresh:** `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=<long-lived>` [V, S19]. Rules [V, S4, S19]:
  - the token must be "at least 24 hours old" and not expired;
  - the user must have granted `instagram_business_basic`;
  - "Refreshed tokens are valid for 60 days from the date at which they are refreshed";
  - "Tokens that have not been refreshed in 60 days will expire and can no longer be refreshed."

  No app secret is needed for a refresh.
- **The user's ID:** `GET https://graph.instagram.com/v27.0/me?fields=user_id,username` [V, S3]. `account_type` can be `Business` or `Media_Creator` [V, S3].
- **Error shapes (Graph API) [V, S21]:** `{"error": {"message", "type", "code", "error_subcode", "error_user_title", "error_user_msg", "fbtrace_id"}}`.
  - Code `190` means the token expired; "OAuthException" without a subcode means the token is "expired, revoked, or invalid".
  - Subcodes: `463` expired, `467` invalid token, `460` password changed, `458` app not installed, `459`/`464` the user must log in on the website.
  - The Instagram error page's sample also includes `is_transient` [V, S20].
- The OAuth code exchange returns a different shape: `{"error_type": "OAuthException", "code": 400, "error_message": "…"}` [V, S4].

**Open:** whether `graph.instagram.com` returns code 190 with these subcodes for expired Instagram tokens. S21 is the Facebook Graph API page [U]. Whether the old token stops working once a refresh is done [U]. Whether a debug or introspection endpoint exists for Instagram User tokens [U].

## 4. Single-image publish flow (Instagram Login, `graph.instagram.com`)

**Facts**

1. **Create a container:** `POST https://graph.instagram.com/<API_VERSION>/<IG_ID>/media` [V, S12, S13].
   - `image_url` is required. "We will cURL the image using the URL that you specify so the image must be on a public server."
   - `caption` and `alt_text` (up to 1,000 characters) are optional.
   - The guide's example sends JSON with `Authorization: Bearer <ACCESS_TOKEN>`. The reference examples put `access_token` in the query string instead.
   - The response is `{"id": "<IG_CONTAINER_ID>"}`.
2. **Check status:** `GET https://graph.instagram.com/<API_VERSION>/<IG_CONTAINER_ID>?fields=status_code,status` [V, S16]. `status_code` values [V, S16, S12]:
   - `EXPIRED`: "not published within 24 hours and has expired";
   - `ERROR`: "failed to complete the publishing process" (`status` then holds an error subcode);
   - `FINISHED`: "ready to be published";
   - `IN_PROGRESS`: "still in the publishing process";
   - `PUBLISHED`: "The container's media object has been published."

   Meta recommends "querying a container's status once per minute, for no more than 5 minutes" [V, S12].
3. **Publish:** `POST https://graph.instagram.com/<API_VERSION>/<IG_ID>/media_publish` with `creation_id=<IG_CONTAINER_ID>`. The response is `{"id": "<IG_MEDIA_ID>"}`; `id` is "Always returned" [V, S14]. It can also contain `config_issue`, which reports a partial success: `CAPTION_NOT_ATTACHED` ("the media published without its caption") or `USER_TAGGING_FAILURE`. Treat these "as a signal to reconcile rather than as an error" [V, S14].

- **Container limits:** "Containers expire after 24 hours"; an account "can only create 400 containers within a rolling 24 hour period" [V, S13].
- **Encoding:** "We strongly recommended … URLs that contain only US ASCII characters, or the request will fail" [V, S13]. List and dict parameters must be URL-encoded [V, S12].
- **API version:** latest is **v27.0** (introduced 2026-11-04). v26.0 was introduced 2026-07-29. v25.0 is available until 2028-07-29. No minimum support period is stated. [V, S23]
- No idempotency key is documented for `media_publish` [V, by its absence from S14]. Whether publishing the same container twice creates two posts is not documented [U].

**Proposed:** send every value in the POST body (form or JSON) and the token only in the `Authorization` header, never in a URL, so that neither the caption nor the token can end up in a logged URL. Keep the API version in **one constant**, as with `Linkedin-Version`.

**Open:** whether Meta fetches the image when the container is created or again at `media_publish`. "At the time of the attempt" [S12] is ambiguous. Whether an image container is ever `IN_PROGRESS`, or goes straight to `FINISHED` [U].

## 5. Image rules

**Facts** (feed image, S13 "Image Specifications", S12 "Limitations") [V]:

| Rule | Value |
|---|---|
| Format | **JPEG only**. "Extended JPEG formats such as MPO and JPS are not supported." |
| File size | 8 MB maximum. The error text says "less than 8 MiB" (S20) |
| Aspect ratio | 4:5 to 1.91:1 (error `36003`/`2207009` otherwise) |
| Width | min 320 (scaled up), max 1440 (scaled down), so **not** a rejection |
| Height | "Varies, depending on width and aspect ratio" |
| Colour space | sRGB; other spaces "will have their color spaces converted to sRGB" (not a rejection) |

- **Uploading bytes directly: not available for feed images.**
  - Image containers take only `image_url` [V, S13].
  - The resumable protocol (`rupload.facebook.com`, `upload_type=resumable`) is documented only "to upload large videos" and "Only for apps that have implemented Facebook Login for Business" [V, S12]. Its host is listed "For resumable video uploads" [V, S12]. Every resumable example is a video, reel, story or carousel video [V, S13].
  - Conclusion: with Instagram Login, an image **must** be at a public URL that Meta can fetch.

**Open:** whether progressive JPEGs, CMYK JPEGs or JPEGs with large EXIF blocks are accepted [U]. Whether a 1.91:1 or 4:5 limit is inclusive [U]. Whether "8 MB" means 8,000,000 or 8,388,608 bytes: use the smaller number to be safe.

## 6. Caption rules

**Facts:** "Maximum 2200 characters, 30 hashtags, and 20 @ tags." Mentioned users "receive a notification when the container is published." [V, S13]. Errors: `36004`/`2207010` caption too long; `100`/`2207040` more than 20 @ tags [V, S20].

**Open:**

- How characters are counted (UTF-16 units, code points, or bytes) is not documented [U].
- What counts as a hashtag or a mention (for example `#` inside a word, or an email address) is not documented [U].
- No escaping rules are documented (unlike LinkedIn's "little" format) [V, by absence].

**Proposed:** count UTF-16 units like LinkedIn (D39), which never undercounts. Count `#` + word-character tokens and `@` + username tokens with a conservative pattern. Reject in **validation** for every mode, as D45 does for LinkedIn.

## 7. Rate limits

**Facts**

- **Publishing limit, with conflicting numbers:**
  - "Instagram accounts are limited to **100** API-published posts within a 24-hour moving period" (S12, Rate Limit);
  - "**50** published posts within a 24-hour period" (S12, carousel section, and S14);
  - `content_publishing_limit` returns `quota_total` "(currently `50`)" and `quota_duration` `86400` (S15). [V]

  So treat **50** as the limit. S12 adds: "We recommend that your app also enforce the publishing rate limit, especially if your app allows app users to schedule posts." [V]
- `GET /<IG_ID>/content_publishing_limit?fields=quota_usage,config[&since=<unix ts, ≤ 24h old>]` → `{"data": [{"quota_usage": 2, "config": {"quota_total": 50, "quota_duration": 86400}}]}` [V, S15].
- **General limit (non-messaging):** "Calls within 24 hours = 4800 * Number of Impressions", per app and user pair [V, S1, S22]. Throttled responses carry `X-Business-Use-Case-Usage` with `call_count`, `total_cputime`, `total_time` and `estimated_time_to_regain_access`. The Instagram throttling error code is **80002** [V, S22]. Codes 4, 17, 32 and 613 are platform limits [V, S22].
- At the publishing limit: HTTP 400, code `9`, subcode `2207042` [V, S20].

**Open:** an account with few impressions (a new account) could have a low call budget under the 4800 × impressions formula [U].

## 8. Public URL for local images (options only, no dependency chosen)

**What Meta requires [V, S12, S13]:** a URL on "a publicly accessible server" that Meta can "cURL" at "the time of the attempt", preferably US-ASCII only. Meta's docs say **nothing** about presigned or query-string URLs, either for or against [V, by absence].

**Reports [U]:**

- A third-party guide says Meta fetches with the `facebookexternalhit` user agent, and that a presigned URL which expires before the fetch, robots or bot blocking, redirects, a wrong `Content-Type` or an empty body cause `9004`/`2207052` (S-U1).
- Forum threads, seen only in search summaries and not read in full, mention S3 URLs failing, one of them intermittently with signed URLs (S-U2).

Presigned URLs must be tested with `--upload-test` before relying on them.

| | (a) Cloudflare R2 presigned GET | (b) AWS S3 presigned GET | (c) Other |
|---|---|---|---|
| Presigned support | GET, HEAD, PUT, DELETE; 1 s to 7 days; SigV4 (`AWS4-HMAC-SHA256`); endpoint `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`; region `auto`; **not** with custom domains [V, S25] | Valid up to 7 days with IAM-user keys; shorter if signed with temporary credentials; can be used repeatedly until it expires [V, S31] | c1: R2 **public** bucket via `r2.dev`: "rate-limited and should only be used for development purposes" [V, S29]; via a custom domain: needs a domain on Cloudflare, and the URL never expires [V, S29]. c2: a temporary tunnel (cloudflared or ngrok) serving one local file [U]: exposes the PC, needs a running process. c3: another S3-compatible store (for example Backblaze B2) [U] |
| Bucket stays private | Yes. Presigned URLs need no public bucket; CORS only for browsers [V, S25] | Yes, "without updating your bucket policy" [V, S31] | c1 no; c2 n/a |
| Credentials | API token "Object Read & Write", **scoped to one bucket**; Access Key ID + Secret Access Key; "You will not be able to access your Secret Access Key again" [V, S28] | IAM user access key; restrict with the `s3:signatureAge` bucket policy (example: deny signatures older than 10 min) [V, S31] | — |
| Cleanup | `DeleteObject` free [V, S26]; lifecycle rules per **prefix**, by days, "typically removed … within 24 hours" of expiry [V, S27]; `ListObjectsV2` supported [V, S30] | DELETE free [V, S32]; lifecycle rules exist [U] | c1 as (a); c2 nothing stored |
| Cost at 1–5 posts/day (≤ 150/month, ≤ 8 MB each, deleted within an hour) | Storage $0.015/GB-month, 10 GB-month free; Class A $4.50/M (1M free); Class B $0.36/M (10M free); **egress free** [V, S26] → **$0** within the free tier [inference] | GET $0.0004/1,000 [V]; DELETE free [V]; first 100 GB/month transfer out free [V, S32]; storage and PUT prices not read (≈ $0.023/GB-month, $0.005/1,000 PUT) [U] → well under $0.01/month [inference]. Free Tier is credit-based and time-limited [V, S32] | c1 as (a); c2 free [U] |
| New Python dependency? | Not needed: SigV4 query signing is HMAC-SHA256 over a canonical request, possible with `hmac`/`hashlib` + `httpx` [U: amount of work; AWS's documented algorithm]. Or `boto3` (a new dependency) | Same | c2 needs an external binary |
| Integrity of the upload | `Content-MD5` supported on PutObject [V, S30]; SHA-256 checksum headers not stated for PutObject [V, S30] | SigV4 uploads support SHA-256 checksums [V, S31] | — |

**Security and privacy, whatever the option (proposed):**

- Object key `ig-tmp/<uuid4>.jpg`: random, never the post name or caption.
- URL lifetime about 1 hour: enough for container creation, status checks and `media_publish` [U: whether Meta fetches again later].
- Delete the object as soon as the container is `PUBLISHED`, `ERROR` or `EXPIRED`, or the job ends.
- Record the key in the job row **before** uploading, so a crash leaves a known orphan.
- A lifecycle rule on `ig-tmp/` (1 day) as a backstop.
- **Never log or store the presigned URL**: it is a bearer credential [V, S25, S31], and Meta's error message for `9004` echoes the URI (`{uri}`) [V, S20].
- The image becomes reachable by anyone holding the URL until it expires or is deleted. Meta keeps its own copy once published.

## 9. Lost-response recovery

**Facts relevant here [V]:**

- A container's `status_code` becomes `PUBLISHED` once its media is published, and `EXPIRED` if it is not published within 24 hours (S16).
- "If you are able to create a container … but the `POST /<IG_ID>/media_publish` endpoint does not return the published media ID, you can get the container's publishing status by querying `GET /<IG_CONTAINER_ID>?fields=status_code`" (S12).
- `GET /<IG_ID>/media` lists the newest media (up to 10K) with `since`/`until` time filters (S13).
- IG Media fields include `id`, `timestamp` (ISO 8601 UTC), `permalink`, `media_type` (`IMAGE` …) and `caption` (S17). However, S17 marks `caption` (and `media_product_type`) as "Available for Instagram API with Facebook Login only", and captions omit `@` in some cases.
- No documented field links a container to its media ID [V, by absence in S16].

**Consequence:** unlike LinkedIn (no `r_member_social`, D36), the container status **is** a documented, read-only way to check an uncertain publish. CLAUDE.md allows this ("unless the platform offers a safe way to check").

**Proposed outcome rules** (same style as D36/D39; **no automatic retries** of `media_publish`, ever):

| Step / answer | Result |
|---|---|
| Any local check fails (format, size, aspect ratio, caption, token, publishing quota) | `FAILED`: nothing uploaded |
| Upload to the bucket fails (any error, including timeouts) | `FAILED`: no container, no post; delete the object best-effort |
| `POST /media` (container) fails in any way, including timeouts | `FAILED`: an unpublished container cannot be seen and expires after 24 h |
| Container `ERROR`, `EXPIRED`, or still `IN_PROGRESS` after the wait | `FAILED`: never published (`status` subcode kept) |
| **Container ID committed to the job row** (before the next step) | — |
| `media_publish`: `ConnectError` / `ConnectTimeout` | `FAILED`: request never sent |
| `media_publish`: `200` with `id` | `PUBLISHED` with that media ID. With `config_issue` it is still `PUBLISHED`, never retried; the issue is recorded and shown (e.g. "published without its caption") |
| `media_publish`: documented refusal: code `9`/`2207042` (limit), `9007`/`2207027` (not ready), `25`/`2207050` (account restricted), `4`/`2207051` (spam block), `190` (token), `4`/`17`/`80002` (throttled), `10`/`200`–`299` (permission) | One read-only status check: `PUBLISHED` → see next row; otherwise `FAILED` |
| `media_publish`: anything else: `ReadTimeout`, `WriteTimeout`, `RemoteProtocolError`, dropped connection, 5xx, code `-1`/`-2`/`1`/`2`, `24`/`2207008` ("does not exist or has been expired", which Meta calls temporary), `200` without `id`, any undocumented answer | One read-only status check: `PUBLISHED` → see next row; `ERROR`/`EXPIRED` → `FAILED`; `FINISHED`, `IN_PROGRESS` or unreadable → `NEEDS_REVIEW` |
| Status `PUBLISHED` but no media ID in hand | List `/<IG_ID>/media?since=<container created − 5 min>&fields=id,timestamp,media_type,permalink`. **Exactly one** `IMAGE` → `PUBLISHED` with that ID. Zero, or more than one (for example the developer also posted from the phone) → `NEEDS_REVIEW` with the container ID noted; `resolve --published <media id>` |
| Crash while `PUBLISHING` (reconciliation), **no** container ID stored | `FAILED`: `media_publish` is only called after the ID is committed, so it was never called |
| Crash while `PUBLISHING`, container ID stored | Status check as above: `PUBLISHED` → find the ID; `ERROR`/`EXPIRED` → `FAILED`; `FINISHED`/`IN_PROGRESS`/unreadable → `NEEDS_REVIEW` |
| A `NEEDS_REVIEW` Instagram job checked again later | Read-only status check: `PUBLISHED` → `PUBLISHED`; `EXPIRED` (more than 24 h after creation) → `FAILED` "container expired; never published" |

- Error texts keep the HTTP status, `code` and `error_subcode` only, **never** `message`, `error_user_msg` or `error_user_title`. They can contain the presigned URI or the caption length (S20). This is the same rule as D39(4).
- Other errors seen when creating the container (all `FAILED`) [V, S20]:
  - `36000`/`2207004` image larger than 8 MiB;
  - `36001`/`2207005` unsupported format;
  - `36003`/`2207009` aspect ratio;
  - `36004`/`2207010` caption too long;
  - `100`/`2207040` too many @ tags;
  - `9004`/`2207052` "media could not be fetched";
  - `-2`/`2207003` download timeout.

**Open:**

- Whether a `media_publish` whose answer was lost can still complete after the status check said `FINISHED`. Not documented, hence `NEEDS_REVIEW`, not `FAILED`.
- Whether an `EXPIRED` container can still be read, or returns `24`/`2207008` [U].
- Whether `caption` and `timestamp` are returned by `/<IG_ID>/media` with Instagram Login (S17 is inconsistent) [U].
- Reconciliation and resume currently make **no network call**. Adding a read-only Instagram status check there is a design change (it needs the token at startup, and must not run when nothing is in `PUBLISHING`).

## 10. Fit with our design

- **Duplicate key:** already `platform` + media SHA-256 + caption SHA-256 (D20); `Platform.INSTAGRAM` exists in `models.py`. Instagram jobs are checked against Instagram history only. No change is needed.
- **Validation, per platform, in every mode** (like the caption rule, D45):
  - add the Instagram caption rule to `CAPTION_RULES` (2,200 characters, 30 hashtags, 20 mentions);
  - add an image rule per platform: Instagram accepts JPEG only (reject PNG; reject MPO, detectable by an APP2 `MPF` segment [U]), at most 8,000,000 bytes (as built, D49), and an aspect ratio from 4:5 to 1.91:1;
  - read width and height from the JPEG header with the existing standard-library reader (`platforms/linkedin/image_size.py`, D37; move it to a shared module). No Pillow.
  - `scan`, simulated runs, uploads and the **dashboard** form then refuse the same posts. The dashboard should show the rules of the selected platform(s).
- **"Hashed bytes are the uploaded bytes" (D33, M4 4.2):** this holds as long as **nothing is converted**. The bytes put in the bucket are exactly the `PostContent` bytes that were hashed. (Meta then re-encodes on its side: scaling, sRGB. That is outside our control.)
  - Converting PNG to JPEG would need Pillow (not allowed without approval, D37) and would upload bytes that differ from the hashed ones. That would need a second "derived" hash and would change what a duplicate means.
  - **Proposed:** no conversion. Instagram rejects PNG in validation; the user exports a JPEG.
- **Scheduling (D46):**
  - `check_schedulable` must apply the Instagram rules when scheduling (today it adds LinkedIn's pixel limit for real schedules).
  - The unattended `run --real --scheduled` must, **before anything is uploaded to the bucket**, run every local check, the token check (`GET /me`) and the publishing-quota check (`content_publishing_limit`). Every refusal must leave nothing behind.
  - 15-minute runs could in theory reach 96 per day, above the 50-post limit, so the quota check is required (S12 asks apps that schedule to enforce it).
  - The content-change rule, `MISSED` and the 6-hour window are unchanged.
  - **Open:** whether "at most one post per run" applies across platforms or per platform.
- **One post to several platforms:** one job per platform, with independent outcomes, hashes checked per platform, and separate `incoming/` folders as today.
  - A LinkedIn failure never affects the Instagram job, and the reverse.
  - The dashboard's "post to LinkedIn and Instagram" would add the files to each platform's `incoming/` (and create one schedule per platform), validated per platform first.
  - A PNG is valid for LinkedIn but not for Instagram, and a caption of 2,500 characters is valid for LinkedIn but not for Instagram. The form must say which platform refuses.
- **New stored data (proposed):** nullable job columns for the container ID and the bucket object key. This is migration 3, with the D46 backup rule generalised (`app.db.bak-v2`). The container ID is committed **before** `media_publish`; the object key before the upload. Neither is a secret. The token and the presigned URL are never stored, logged or put in graph state.
- **Real mode:** `--real` only, never from `.env` (D36/D38/D43). The D38 safeguards (data folders, archive vs history, "only simulated") apply to Instagram as well.

## 11. Open questions (summary)

1. Are posts from a Development-mode app visible to the public? (S10 vs the Instagram pages.) Test this at the first real post.
2. App Review: S9 says it is required "to access live data", S8 says it is not required for one's own business.
3. Is the publishing limit 50 or 100 per 24 h? Assume 50, and read `content_publishing_limit`.
4. Does Meta accept presigned (query-string) URLs reliably? Does it fetch at container creation or at publish? How long must the URL live?
5. How are the caption's characters, hashtags and mentions counted?
6. Are progressive, CMYK or large-EXIF JPEGs accepted? Is the aspect-ratio range inclusive? Is 8 MB decimal or binary?
7. Can a lost `media_publish` complete after a `FINISHED` status read? Is an `EXPIRED` container still readable?
8. Are `caption` and `timestamp` returned for `/media` under Instagram Login?
9. Does `graph.instagram.com` use code 190 and its subcodes for expired tokens? Does a refresh invalidate the old token?
10. Will Instagram Login be deprecated in favour of Unified Login?
11. Token refresh: keep it manual (as with LinkedIn), or let the app refresh it? The latter means the app writes a secret somewhere: `.env`, or a separate Git-ignored file.
12. Is a webhooks server needed? Meta's guides "assume" one; publishing as documented does not use it [U].
13. Does R2 need a payment method to activate the free tier? [U]

## 12. Your setup checklist (things only you can do)

1. **Make the Instagram account professional and public:** Instagram app → Settings → Account type and tools → Switch to professional account (Creator or Business). Help: <https://help.instagram.com/502981923235522>. The dashboard requires the account to be public (S9).
2. **Register as a Meta developer** (if not already): <https://developers.facebook.com/documentation/development/register>.
3. **Create a Meta app:** <https://developers.facebook.com/apps> → Create App → use case **Other** → type **Business** → name and contact email (S9). Leave it in Development mode.
4. **Add the Instagram product:** Set up → **API setup with Instagram login** (S9, step 6).
5. **Add your Instagram account** under *Instagram → API setup with Instagram login → Generate access tokens* (or *App roles → Roles*), log in to Instagram, and accept the tester invitation if one is sent (S9, step 7; S11).
6. **Generate a token:** click **Generate token** next to the account and copy it. It is valid for 60 days (S3). Note today's date.
7. **Do not submit for App Review** and do not switch to Live (S8: not required for your own account).
8. **Create the image bucket** (only once D48 picks an option):
   - **R2:** Cloudflare dashboard <https://dash.cloudflare.com/> → R2 → create a private bucket (e.g. `autoposter-ig`) → add a lifecycle rule for prefix `ig-tmp/`, delete after 1 day (S27) → *Manage API tokens* → create a token with **Object Read & Write**, scoped to that bucket only → copy the Access Key ID and Secret Access Key right away (shown once, S28) → note your Account ID. Docs: <https://developers.cloudflare.com/r2/>.
   - **or S3:** a private bucket with Block Public Access on, an IAM user with `s3:PutObject`/`GetObject`/`DeleteObject` on that bucket only, an access key, a lifecycle rule for `ig-tmp/`, and optionally the `s3:signatureAge` policy (S31).
9. **Add to `backend\.env`** (never committed; placeholder names would go in `.env.example`). These names are proposed:

   ```text
   INSTAGRAM_ACCESS_TOKEN=...          # from step 6
   INSTAGRAM_TOKEN_CREATED=YYYY-MM-DD  # the day you generated it
   AUTOPOSTER_R2_ACCOUNT_ID=...        # or the S3 equivalents
   AUTOPOSTER_R2_ACCESS_KEY_ID=...
   AUTOPOSTER_R2_SECRET_ACCESS_KEY=...
   AUTOPOSTER_R2_BUCKET=autoposter-ig
   ```

   The Instagram app ID and secret are **not** needed for a dashboard token: they are only used for the login-code exchange, and a refresh does not use them (S18, S19).
10. **Calendar reminder:** renew or refresh the token before 60 days. A refresh is possible from 24 hours after it was issued (S4).

## 13. D48 — accepted (2026-11-19)

D48 is **accepted** and recorded in `docs/decisions.md` (D48), with the spike results. What the spike changed from the draft that was here:

- **API and token.** The token from "API setup with Unified login" is a System User token (`EAA…`, never expires) that works on **`graph.facebook.com`**, not `graph.instagram.com`. Its scopes are `instagram_basic` and `instagram_content_publish`. There is no 60-day expiry and no refresh, so sections 3 and 12 (token dates, renewal) do not apply to this token.
- **Identity.** `me` is not the Instagram account with this token. The Instagram user ID is configured (`INSTAGRAM_USER_ID`) instead of read from `/me` (section 2, sections 4–9).
- **Confirmed:** the publishing limit, container creation and `media_publish`; the post is publicly visible from a Development-mode app (section 11). `GET /{ig-user-id}/media` returns `caption` and `timestamp` (`+0000`); `caption` is missing when the caption is empty (section 9).
- **Media host (section 8):** S3-compatible storage, Backblaze B2 (private bucket), with path-style URLs and presigned GET URLs valid 1 hour. Sections 8 and 12 compared R2 and S3; B2 replaces both. Requests are signed with hand-written SigV4 over `httpx` (no new dependency), tested against AWS's SigV4 test vectors. The R2 keys live only in `backend/.env` and are never logged.
- **Still unverified,** to check on the first real run:
  - Meta accepts a presigned URL;
  - the container status after `media_publish`;
  - the error a second `media_publish` returns.
- **Confirmation phrase:** per platform, `publish to Instagram`.
- **Still refused in code:** the D49 guards stay until the real-Instagram step lifts them.

## Sources (all read 2026-11-19)

Meta (official; Markdown form = URL + `.md`):

- S1 Instagram Platform overview — <https://developers.facebook.com/documentation/instagram-platform/overview>
- S2 Instagram API with Instagram Login — <https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login>
- S3 Get started (Instagram Login) — <https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/get-started>
- S4 Business Login for Instagram — <https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/business-login>
- S5 Instagram API with Unified Login — <https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login>
- S6 Unified Login: authentication — <https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/authentication>
- S7 Unified Login: eligibility and troubleshooting — <https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/eligibility-and-troubleshooting>
- S8 App Review for Instagram API — <https://developers.facebook.com/documentation/instagram-platform/app-review>
- S9 Create a Meta app for the Instagram API — <https://developers.facebook.com/documentation/instagram-platform/create-an-instagram-app>
- S10 App modes — <https://developers.facebook.com/documentation/development/build-and-test/app-modes>
- S11 App roles — <https://developers.facebook.com/documentation/development/build-and-test/app-roles>
- S12 Content Publishing — <https://developers.facebook.com/documentation/instagram-platform/content-publishing>
- S13 IG User Media — <https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media>
- S14 IG User Media Publish — <https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media_publish>
- S15 IG User Content Publishing Limit — <https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit>
- S16 IG Container — <https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-container>
- S17 IG Media — <https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media>
- S18 Access Token — <https://developers.facebook.com/documentation/instagram-platform/reference/access_token>
- S19 Refresh Access Token — <https://developers.facebook.com/documentation/instagram-platform/reference/refresh_access_token>
- S20 Instagram Platform error codes — <https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/error-codes>
- S21 Graph API error handling — <https://developers.facebook.com/docs/graph-api/guides/error-handling>
- S22 Graph API rate limiting — <https://developers.facebook.com/docs/graph-api/overview/rate-limiting>
- S23 Graph API changelog (versions) — <https://developers.facebook.com/docs/graph-api/changelog>
- S24 Update on Instagram Basic Display API (Meta blog, 2024-09-04) — <https://developers.facebook.com/blog/post/2024/09/04/update-on-instagram-basic-display-api>

Cloudflare and AWS (official):

- S25 R2 presigned URLs — <https://developers.cloudflare.com/r2/api/s3/presigned-urls/>
- S26 R2 pricing — <https://developers.cloudflare.com/r2/pricing/>
- S27 R2 object lifecycles — <https://developers.cloudflare.com/r2/buckets/object-lifecycles/>
- S28 R2 API tokens — <https://developers.cloudflare.com/r2/api/tokens/>
- S29 R2 public buckets — <https://developers.cloudflare.com/r2/buckets/public-buckets/>
- S30 R2 S3 API compatibility — <https://developers.cloudflare.com/r2/api/s3/api/>
- S31 S3 presigned URLs — <https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html>
- S32 S3 pricing — <https://aws.amazon.com/s3/pricing/> (the S3 Standard storage and PUT prices were not in the text that could be read)

Unofficial (UNVERIFIED):

- S-U1 Ayrshare, "Meta media crawler blocked" — <https://www.ayrshare.com/docs/help-center/technical-support/meta_media_crawler_blocked>
- S-U2 Meta developer community threads on `2207052` with S3 URLs, seen only in search summaries — <https://developers.facebook.com/community/threads/269924622604896/>
