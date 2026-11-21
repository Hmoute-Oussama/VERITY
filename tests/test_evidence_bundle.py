from __future__ import annotations

import pytest

from verity.evidence_bundle import canonical_json, export_evidence_bundle, sign_bundle, verify_bundle


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
    assert verify_bundle(bundle, public_pem) is True
    bundle["claims"].append({"tampered": True})
    assert verify_bundle(bundle, public_pem) is False
