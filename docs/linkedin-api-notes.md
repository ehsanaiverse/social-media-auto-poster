# LinkedIn API notes (Milestone 4)

What the official LinkedIn documentation (Microsoft Learn) says about the APIs this project uses, as read on **2026-10-28**. Re-check the linked pages before changing the integration; LinkedIn changes them often. Decisions built on these notes are in `docs/decisions.md` (D36) and the binding rules in `CLAUDE.md` ("Milestone 4 requirements").

Scope: posting **one image with a caption** to the **developer's own personal profile**. Company pages, video, documents, mentions, and scheduling are out of scope.

## Access: LinkedIn products and scopes

| Need | LinkedIn product | Scope | Access |
| --- | --- | --- | --- |
| Create posts as the member | Share on LinkedIn | `w_member_social` | Added from the app's Products tab in the Developer Portal; no approval step described |
| Find the member's ID (author URN) | Sign In with LinkedIn using OpenID Connect | `openid profile` | Same: requested from the Products tab |

- `w_member_social` is **write-only**: "tokens with only `w_member_social` permissions would be unable to perform a GET call for rest/images" on the versioned API (Images API page). The same page adds that `w_member_social` "is sufficient to make legacy GET calls to rest/images". Whether image status can be read is verified by `linkedin-check` before the first real post (D36).
- Listing a member's own posts needs `r_member_social`, which is "**restricted** and is available to **approved users only**" (Posts API page). So the app **cannot check** whether an uncertain publish went live: such jobs stay `NEEDS_REVIEW` until the developer checks LinkedIn and runs `resolve`.
- Company pages would need `w_organization_social` (Community Management API, page admin role). Not used.

## Who is posting: the author URN

- `GET https://api.linkedin.com/v2/userinfo` with the access token (OpenID Connect) returns `sub`, the member identifier.
- Author URN: `urn:li:person:{sub}`.

## Authentication

- OAuth 2.0 authorization-code ("3-legged") flow: `https://www.linkedin.com/oauth/v2/authorization`, token from `https://www.linkedin.com/oauth/v2/accessToken`.
- "Currently, all access tokens are issued with a **60-day lifespan**." Programmatic refresh tokens are "available for a limited set of partners"; otherwise the member authorizes again.
- Requesting a different scope "invalidates all previous access tokens".
- The Developer Portal has a **token generator** (`https://www.linkedin.com/developers/tools/oauth/token-generator`) that creates a token by hand. **This project uses it** (D36): the token goes in `backend\.env` as `LINKEDIN_ACCESS_TOKEN`, with the optional `LINKEDIN_TOKEN_CREATED` date for expiry warnings. Renew it before 60 days.
- The client secret and the access token must never be shared, put in URLs, logged, or committed.

## Versioning (one constant)

- Every versioned call (`https://api.linkedin.com/rest/...`) needs `Linkedin-Version: YYYYMM` and `X-Restli-Protocol-Version: 2.0.0`. "The latest version is not applied by default": a missing or sunset version is an error.
- Versions are released monthly and "supported and stable for a minimum of one year before sunset".
- Latest on 2026-10-28: **`202610`** (October 2026). This project keeps it in **one constant** in the LinkedIn adapter.
- **When to update the constant:** at least once a year (before the version is 12 months old), or immediately when LinkedIn announces its sunset. Update it, run the tests, then run `linkedin-check` before the next real run. Deprecation notices appear at the top of the API pages (for example, version `202511` sunsets on 2026-11-16).

## Verified with the developer's own token (2026-10-28)

`linkedin-check --upload-test`, run against the real LinkedIn with a token from the Developer Portal's token generator:

- `GET /v2/userinfo` accepted the token and returned the member (`sub`).
- `POST /rest/images?action=initializeUpload` and the `PUT` upload worked.
- `GET /rest/images/{urn}` **could be read** with this token and returned `AVAILABLE`. So the Images API is used for real posts; the Assets API fallback is not needed.

## Uploading the image (Images API)

1. `POST https://api.linkedin.com/rest/images?action=initializeUpload` with `{"initializeUploadRequest": {"owner": "urn:li:person:{sub}"}}`. The response `value` has `uploadUrl`, `uploadUrlExpiresAt`, and `image` (`urn:li:image:...`).
2. `PUT` the image bytes to `uploadUrl` with the `Authorization: Bearer` header (upload instructions on the Assets API page; the Images API page links to them). Expect `201`.
3. Image status (`WAITING_UPLOAD`, `PROCESSING`, `AVAILABLE`, `PROCESSING_FAILED`) via `GET https://api.linkedin.com/rest/images/{urn}`; see the `w_member_social` caveat above.

- Formats: JPG, GIF, PNG. Limit: "Images with less than **36,152,320 pixels**".
- The Images API has **no synchronous upload**. The Assets API page warns that a post created before its image finished processing "won't be visible to members".
- **Fallback (only if image status cannot be confirmed):** the older Assets API (`POST /rest/assets?action=registerUpload` with `SYNCHRONOUS_UPLOAD` and recipe `urn:li:digitalmediaRecipe:feedshare-image`) returns only after processing. Its asset ID can be used as `urn:li:image:{id}`. LinkedIn says the Assets API "will be deprecated in the future".
- An image that is uploaded but never used in a post is not visible to anyone.

## Creating the post (Posts API)

```http
POST https://api.linkedin.com/rest/posts
Authorization: Bearer {token}
Linkedin-Version: 202610
X-Restli-Protocol-Version: 2.0.0
Content-Type: application/json

{
  "author": "urn:li:person:{sub}",
  "commentary": "{escaped caption}",
  "visibility": "PUBLIC",
  "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
  "content": {"media": {"id": "urn:li:image:{id}"}},
  "lifecycleState": "PUBLISHED",
  "isReshareDisabledByAuthor": false
}
```

- Success: **`201`**, and "the response header `x-restli-id` contains the Post ID such as `urn:li:share:...` or `urn:li:ugcPost:...`". That ID is the `platform_post_id`.
- The Posts API replaces the older `ugcPosts` API.
- No idempotency key is documented: sending the same request twice can create two posts.
- Documented errors: 400 (`INVALID_URN_TYPE`, `MISSING_FIELD`, `INVALID_VALUE_FOR_FIELD`, `FIELD_LENGTH_TOO_LONG`, `INVALID_VALUE_BLANK_FIELD`), 401 (`EMPTY_ACCESS_TOKEN`), 403 (`ACCESS_DENIED`), 404, 409 (`CONFLICT`, "Retry the request"), 422, 429 (`TOO_MANY_REQUESTS`), 500, 503. This project never retries a post creation (D36), whatever the docs suggest.

## Caption text ("little" format)

- `commentary` uses the **little** text format. Reserved characters must be escaped with a backslash "even if those characters are not used in one of the supported elements or templates": `| { } @ [ ] ( ) < > # \ * _ ~`.
- `#word` is a hashtag; `@[Name](urn)` is a mention.
- **This project (D36):** escape every reserved character, except `#` that starts a hashtag (`#word`), so hashtags keep working. `@` is always escaped (plain text; no mentions).
- **Length:** LinkedIn documents "The maximum length of the text of a UGC Post is **3000 characters**" (UGC Post API page; the limit was raised from 1,300 to 3,000 in July 2021). The Posts API page gives no number, only the `FIELD_LENGTH_TOO_LONG` error. This project rejects captions over 3,000 characters in real mode before anything is uploaded; how characters are counted (before or after escaping, code points or UTF-16 units) is settled with tests in slice 4.3.

## Rate limits

- Share on LinkedIn page: **150 requests per member per day**, 100,000 per application per day (UTC). One post here takes about four requests.

## Sources

- [Share on LinkedIn](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin)
- [Sign In with LinkedIn using OpenID Connect](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/sign-in-with-linkedin-v2)
- [LinkedIn 3-legged OAuth flow](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow)
- [API versioning](https://learn.microsoft.com/en-us/linkedin/marketing/versioning)
- [Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api)
- [Post schema](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/post-api-schema)
- [Images API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/images-api)
- [Assets API (upload details, synchronous upload)](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/vector-asset-api)
- [little text format](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/little-text-format)
- [UGC Post API (3,000-character limit)](https://learn.microsoft.com/en-us/linkedin/compliance/integrations/shares/ugc-post-api)
- [Marketing API changes 2021 (1,300 → 3,000)](https://learn.microsoft.com/en-us/linkedin/marketing/integrations/archived-recent-changes/2021/marketing-api-changes?view=li-lms-2025-02)
