"""ADV-019: token hashes come only from the paired_devices block."""
import auth

H = "a" * 64
B = "b" * 64


def test_hash_under_paired_devices_is_accepted():
    cfg = (
        "paired_devices:\n"
        "  dev-1:\n"
        "    device_name: Phone\n"
        f"    token_hash: {H}\n"
        "profiles:\n"
        "  x: 1\n"
    )
    assert auth._paired_device_hashes(cfg) == frozenset({H})


def test_token_hash_outside_paired_devices_is_ignored():
    # A token_hash injected under an unrelated top-level key must NOT count.
    cfg = (
        "system:\n"
        f"  token_hash: {B}\n"
        "paired_devices:\n"
        "  dev-1:\n"
        f"    token_hash: {H}\n"
        "wifi:\n"
        f"  token_hash: {B}\n"
    )
    assert auth._paired_device_hashes(cfg) == frozenset({H})


def test_empty_paired_devices_yields_nothing():
    assert auth._paired_device_hashes("paired_devices: {}\nprofiles:\n") == frozenset()
    assert auth._paired_device_hashes("system:\n  a: 1\n") == frozenset()


def test_block_ends_at_next_top_level_key():
    cfg = (
        "paired_devices:\n"
        "  dev-1:\n"
        f"    token_hash: {H}\n"
        "other:\n"
        f"  token_hash: {B}\n"
    )
    assert auth._paired_device_hashes(cfg) == frozenset({H})


def test_device_id_level_token_hash_is_not_accepted():
    # A token_hash at the device-id indent (2 spaces) is not a device attribute.
    cfg = "paired_devices:\n" f"  token_hash: {B}\n"
    assert auth._paired_device_hashes(cfg) == frozenset()


def test_multiple_devices():
    cfg = (
        "paired_devices:\n"
        "  d1:\n"
        f"    token_hash: {H}\n"
        "  d2:\n"
        f"    token_hash: {B}\n"
    )
    assert auth._paired_device_hashes(cfg) == frozenset({H, B})


def test_cookie_is_not_accepted():
    # Only a Bearer header yields a token now.
    assert auth._extract_token("Bearer xyz") == "xyz"
    assert auth._extract_token(None) is None
