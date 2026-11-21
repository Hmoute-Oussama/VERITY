from __future__ import annotations

import pytest

from verity.evidence_bundle import canonical_json, export_evidence_bundle, sign_bundle, verify_bundle
from verity.trust import add_trusted_key, load_trusted_keys, remove_trusted_key, revoke_trusted_key, rotate_trusted_key


def test_evidence_bundle_is_canonical_and_reproducible(engine):
    bundle = export_evidence_bundle(engine)

    assert bundle["schema"] == "verity.evidence-bundle.v1"
    assert len(bundle["integrity"]["digest"]) == 64
    assert "reproducibility" in bundle
    assert canonical_json(bundle).startswith(b"{")


def test_ed25519_bundle_signature_verifies(engine):
    cryptography = pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    bundle = sign_bundle(export_evidence_bundle(engine), private_pem)
    assert len(bundle["signature"]["key_id"]) == 32
    assert verify_bundle(bundle, public_pem) is True
    bundle["claims"].append({"tampered": True})
    assert verify_bundle(bundle, public_pem) is False


def test_local_trust_store_round_trip(engine):
    public_pem = "-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----\n"
    key_id = add_trusted_key(engine.root, "release", public_pem)

    assert load_trusted_keys(engine.root)[key_id]["name"] == "release"
    assert remove_trusted_key(engine.root, key_id) is True
    assert key_id not in load_trusted_keys(engine.root)


def test_trust_store_revoke_and_rotate(engine):
    old_id = add_trusted_key(engine.root, "old", "old-public-key")
    assert revoke_trusted_key(engine.root, old_id, "compromised") is True
    assert load_trusted_keys(engine.root)[old_id]["status"] == "revoked"

    new_id = rotate_trusted_key(engine.root, old_id, "new", "new-public-key")
    assert new_id != old_id
    assert load_trusted_keys(engine.root)[new_id]["status"] == "trusted"
