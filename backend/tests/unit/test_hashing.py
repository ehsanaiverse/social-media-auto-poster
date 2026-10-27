"""Tests for content hashing."""

import hashlib

import pytest

from social_media_auto_poster.services.hashing import sha256_file


def test_known_value_for_abc(tmp_path):
    path = tmp_path / "abc.txt"
    path.write_bytes(b"abc")

    assert sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_known_value_for_empty_file(tmp_path):
    path = tmp_path / "empty"
    path.write_bytes(b"")

    assert sha256_file(path) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_small_chunks_give_the_same_hash(tmp_path):
    data = bytes(range(256)) * 41  # Not a multiple of the chunk size.
    path = tmp_path / "data.bin"
    path.write_bytes(data)

    assert sha256_file(path, chunk_size=7) == hashlib.sha256(data).hexdigest()


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        sha256_file(tmp_path / "missing.jpg")
