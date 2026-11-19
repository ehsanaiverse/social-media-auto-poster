"""SigV4 against AWS's published examples: the SigV4 test suite (get-vanilla, post-vanilla), the signing-key
example from the IAM docs, and the S3 docs' header-signed GET and presigned URL examples."""

from social_media_auto_poster.platforms import sigv4

SUITE_KEY, SUITE_SECRET = "AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
SUITE_DATE = "20150830T123600Z"
S3_KEY, S3_SECRET = "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


def _suite(method: str) -> str:
    return sigv4.authorization(
        SUITE_KEY, SUITE_SECRET, method=method, path="/", params=[],
        headers={"Host": "example.amazonaws.com", "X-Amz-Date": SUITE_DATE},
        payload_hash=sigv4.EMPTY_SHA256, amz_date=SUITE_DATE, region="us-east-1", service="service",
    )


def test_suite_get_vanilla():
    canonical, _ = sigv4.canonical_request(
        "GET", "/", [], {"Host": "example.amazonaws.com", "X-Amz-Date": SUITE_DATE}, sigv4.EMPTY_SHA256
    )
    assert sigv4.sha256_hex(canonical.encode()) == "bb579772317eb040ac9ed261061d46c1f17a8133879d6129b6e1c25292927e63"
    assert _suite("GET") == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
        "SignedHeaders=host;x-amz-date, Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"
    )


def test_suite_post_vanilla():
    assert _suite("POST").endswith("Signature=5da7c1a2acd57cee7505fc6676e4e544621c30862966e37dddb68e92efbe5d6b")


def test_signing_key_examples():
    # The current docs use 20150830; older versions of the same page used 20120215.
    assert sigv4.signing_key(SUITE_SECRET, "20150830", "us-east-1", "iam").hex() == (
        "c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9"
    )
    assert sigv4.signing_key(SUITE_SECRET, "20120215", "us-east-1", "iam").hex() == (
        "f4780e2d9f65fa895f9c67b32ce1baf0b0d8a43505a000a1a9e090d414db404d"
    )


def test_s3_get_object_with_signed_headers():
    date = "20130524T000000Z"
    header = sigv4.authorization(
        S3_KEY, S3_SECRET, method="GET", path="/test.txt", params=[],
        headers={"Host": "examplebucket.s3.amazonaws.com", "Range": "bytes=0-9",
                 "x-amz-content-sha256": sigv4.EMPTY_SHA256, "x-amz-date": date},
        payload_hash=sigv4.EMPTY_SHA256, amz_date=date, region="us-east-1", service="s3",
    )
    assert header == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def test_s3_presigned_url():
    query = sigv4.presigned_query(
        S3_KEY, S3_SECRET, method="GET", host="examplebucket.s3.amazonaws.com", path="/test.txt",
        amz_date="20130524T000000Z", region="us-east-1", service="s3", expires_s=86400,
    )
    assert query.endswith("&X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")
    assert "X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2F20130524%2Fus-east-1%2Fs3%2Faws4_request" in query


def test_query_and_path_encoding():
    assert sigv4.canonical_query([("b", "2"), ("a", "x y"), ("a", "1")]) == "a=1&a=x%20y&b=2"
    assert sigv4.uri_encode("ig-tmp/a b.jpg", keep_slash=True) == "ig-tmp/a%20b.jpg"
